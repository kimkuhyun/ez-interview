"""
면접 스트림 AI 에이전트 (LangGraph 기반)

역할: 면접자의 답변을 받아 후속 질문 3개를 자동 생성
구조: LangGraph StateGraph 기반 (load_rag → determine_phase → generate_questions → validate_questions)
"""

from typing import TypedDict, List, Annotated, Literal
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langgraph.graph import StateGraph, END
import operator

from app.config.config import Config


# ========================================
# State 정의
# ========================================
class InterviewState(TypedDict):
    """면접 진행 상태"""
    
    # 세션 정보
    session_id: str  # RAG 검색용 (이력서/포트폴리오)
    question_id: str  # 현재 대질문 (q1, q2, ...)
    
    # 대화 기록
    messages: Annotated[List[dict], operator.add]  # 자동 append
    history_text: str  # LLM에 전달할 히스토리 문자열
    
    # JD 원문 (DB에서 조회)
    jd_text: str
    
    # RAG 컨텍스트 (동적 검색)
    rag_context: str  # 동적으로 생성되는 관련 컨텍스트
    resume_available: bool  # 이력서 RAG 검색 가능 여부
    jd_available: bool  # JD 존재 여부
    portfolio_available: bool  # 포트폴리오 RAG 검색 가능 여부
    
    # 생성된 질문
    current_questions: List[str]  # 현재 표시 중인 후속 질문 3개
    asked_questions: List[str]  # 이미 물어본 질문들 (중복 방지)
    
    # 면접자 답변
    interviewee_answer: str
    
    # 재생성 제어
    regen_history: List[str]  # 재생성 시도 질문들 (임시 저장)
    is_regen: bool
    regen_count: int  # 현재 답변에 대한 재생성 횟수


# ========================================
# LLM 초기화 (페르소나별 모델)
# ========================================

# 검증자: 정확성 중시 (낮은 temperature)
validator_llm = ChatOpenAI(
    model="gpt-4o-mini",
    temperature=0.3,
    openai_api_key=Config.OPENAI_API_KEY,
)

# 탐구자: 깊이와 창의성 (높은 temperature)
deep_diver_llm = ChatOpenAI(
    model="solar-pro",
    temperature=0.8,
    api_key=Config.SOLAR_API_KEY,
    base_url="https://api.upstage.ai/v1",
)

# 전환자: 균형잡힌 판단 (중간 temperature)
topic_shifter_llm = ChatOpenAI(
    model="gpt-4o-mini",
    temperature=0.5,
    openai_api_key=Config.OPENAI_API_KEY,
)

parser = StrOutputParser()


# ========================================
# 노드 함수들
# ========================================

def load_rag_node(state: InterviewState) -> InterviewState:
    """
    1️⃣ RAG 문서 존재 여부 확인
    
    - session_id가 있으면 이력서/포트폴리오 RAG 검색 가능 (resume_available, portfolio_available)
    - jd_text가 있으면 JD 사용 가능 (jd_available)
    """
    print(f"🔍 RAG 문서 확인 중...")
    
    # session_id로 RAG 검색 가능 여부 (이력서/포트폴리오)
    session_id = state.get("session_id", "")
    state["resume_available"] = bool(session_id and session_id.strip())
    state["portfolio_available"] = bool(session_id and session_id.strip())
    
    # JD 원문 존재 여부
    jd_text = state.get("jd_text", "")
    state["jd_available"] = bool(jd_text and jd_text.strip())
    
    # 로그 출력
    docs = []
    if state["resume_available"]: 
        docs.append(f"이력서(RAG 검색)")
    if state["portfolio_available"]: 
        docs.append(f"포트폴리오(RAG 검색)")
    if state["jd_available"]: 
        docs.append(f"JD({len(jd_text)}자)")
    
    if docs:
        print(f"✅ RAG 문서 확인 완료: {', '.join(docs)}")
    else:
        print("⚠️  RAG 문서 없음")
    
    # rag_context는 질문 생성 시 동적 생성
    state["rag_context"] = ""
    
    return state


def preprocess_input_node(state: InterviewState) -> InterviewState:
    """
    2️⃣ 입력 전처리
    
    history 리스트를 문자열로 변환 (최근 30개만 사용)
    """
    messages = state.get("messages", [])
    
    # 이전 대화 기록을 문자열로 변환
    if messages and isinstance(messages, (list, tuple)):
        # 최근 30개 메시지만 사용 (토큰 절약 + 속도 향상)
        recent_messages = messages[-30:] if len(messages) > 30 else messages
        
        history_text = "\n".join(
            f"{m.get('role','')}: {m.get('content','')}" for m in recent_messages
        )
        
        # 잘린 경우 표시
        if len(messages) > 30:
            omitted_count = len(messages) - 30
            history_text = f"(이전 대화 {omitted_count}개 생략...)\n\n" + history_text
    else:
        history_text = '(이전 대화 없음)'
    
    state["history_text"] = history_text
    
    return state


def generate_questions_node(state: InterviewState) -> InterviewState:
    """
    3️⃣ 후속 질문 생성 (GPT-4 호출)
    
    3-Persona 시스템으로 후속 질문 3개 생성
    - 검증자: 이력서 vs 답변 일치성 검증
    - 탐구자: 답변의 구체성 파고들기
    - 전환자: 주제 전환/유지 판단
    """
    from app.utils.rag_retriever import search_similar_chunks
    
    history_text = state.get("history_text", "")
    interviewee_answer = state.get("interviewee_answer", "")
    asked_questions = state.get("asked_questions", [])
    regen_history = state.get("regen_history", [])  # 재생성 시도 질문들
    session_id = state.get("session_id")
    
    # RAG 검색 쿼리 구성
    print(f"🔍 RAG 검색 시작...")
    
    # 최근 질문 추출 (맥락 제공)
    messages = state.get("messages", [])
    last_question = ""
    if messages and len(messages) > 0:
        # 마지막 면접관 질문 찾기
        for msg in reversed(messages):
            if msg.get("role") == "interviewer":
                last_question = msg.get("content", "")
                break
    
    # 검색 쿼리 구성
    enhanced_query = f"""
최근 면접관 질문: {last_question}

면접자 답변: {interviewee_answer}

검증 필요 정보: 프로젝트 기간, 담당 역할, 사용 기술 스택, 팀 구성, 성과 지표, 구체적 수치
"""
    
    print(f"   📝 검색 쿼리: {enhanced_query[:100]}...")
    
    rag_context = ""
    
    # 1. 이력서 임베딩 검색 (top_k=3)
    if state.get("resume_available"):
        chunks = search_similar_chunks(
            query=enhanced_query,
            session_id=session_id,
            doc_type="resume",
            top_k=3
        )

        if chunks:
            text = "\n".join([c["content"] for c in chunks])
            rag_context += f"[이력서 관련 부분]\n{text}\n\n"
            print(f"   ✅ 이력서: {len(chunks)}개 청크 검색")
        else:
            print(f"   ⚠️  이력서 검색 결과 없음")
    
    # 2. 포트폴리오 임베딩 검색 (top_k=5)
    if state.get("portfolio_available"):
        chunks = search_similar_chunks(
            query=enhanced_query,
            session_id=session_id,
            doc_type="portfolio",
            top_k=5
        )

        if chunks:
            text = "\n".join([c["content"] for c in chunks])
            rag_context += f"[포트폴리오 관련 부분]\n{text}\n\n"
            print(f"   ✅ 포트폴리오: {len(chunks)}개 청크 검색")
        else:
            print(f"   ⚠️  포트폴리오 검색 결과 없음")

    # 3. JD 원문 추가
    jd_text = state.get("jd_text", "")
    if state.get("jd_available") and jd_text:
        rag_context += f"[JD (채용공고)]\n{jd_text}\n\n"
        print(f"   ✅ JD: {len(jd_text)}자 포함")
    
    # 결과가 아무것도 없을 경우
    if not rag_context:
        rag_context = "[참고: 관련 문서 없음]\n"
        print("   ⚠️  RAG 검색 결과 없음")
    
    # 최근 물어본 질문만 프롬프트에 포함 (최대 10개)
    recent_asked = asked_questions[-10:] if len(asked_questions) > 10 else asked_questions
    
    # 재생성 히스토리 디버그
    if regen_history:
        print(f"   🔄 재생성 히스토리 ({len(regen_history)}개 질문):")
        for i, q in enumerate(regen_history[-9:], 1):
            print(f"      [{i}] {q[:50]}...")
    else:
        print(f"   ℹ️  재생성 히스토리 없음")
    
    # 공통 컨텍스트 구성
    asked_questions_text = "\n".join(f"- {q}" for q in recent_asked) if recent_asked else "(없음)"
    regen_history_text = "\n".join(f"[{i//3 + 1}차 시도] {q}" for i, q in enumerate(regen_history[-9:])) if regen_history else "(없음)"
    
    common_context = f"""
[면접 자료]
{rag_context}

[이전 대화]
{history_text}

[면접자의 답변]
{interviewee_answer}

**이미 물어본 질문들** (중복 방지):
{asked_questions_text}

**재생성 시도 질문들** (절대 반복 금지):
{regen_history_text}
"""
    
    # ========================================
    # 페르소나 1️⃣: 검증자 (Validator)
    # ========================================
    validator_prompt = ChatPromptTemplate.from_messages([
        ("system", """당신은 **거짓말과 과장을 잡아내는 검증 전문가**입니다.

**당신의 유일한 임무**: 면접자의 답변이 이력서와 일치하는지 검증하는 질문 1개 생성

⚠️ **중요**: 다른 역할(탐구, 전환)은 다른 전문가가 담당합니다. 오직 **검증**만 하세요.

---

## 📌 절대 원칙

### 1️⃣ RAG 검색 결과 이해
- **이력서/포트폴리오**: 관련 부분만 검색된 발췌본
  - 발췌본에 없다고 안 한 일은 아님 → 열린 질문으로 확인

### 2️⃣ 이전 대화 필수 확인
- **[이전 대화]를 먼저 읽고** 이미 답변받은 내용은 다시 묻지 마세요
- 예: Q2에서 "SaaS 전환" 답변받음 → "어떤 프로젝트?" 금지

### 3️⃣ 답변 기반 검증
- **[면접자의 답변]에 나온 내용**만 검증

---

## 검증 체크리스트 (우선순위대로)

1. ❌ **존재성 검증**: 답변에 나온 경험이 이력서에 있는가?
2. ❌ **과장 검증**: 역할/책임이 부풀려졌는가?
3. ❌ **구체성 검증**: 막연한 표현의 실체 확인
4. ❌ **경력 대비 현실성**: 주니어가 시니어급 책임 주장?

**핵심**: 답변에서 이력서보다 **더 많은 것**을 주장하면 바로 증거를 요구하세요.

**재생성 시**: 다른 검증 포인트로 순환 (프로젝트 → 역할 → 기간)

---

## 📤 출력 형식

⚠️ **필수**: 질문은 **한 문장으로만** 작성하세요.
- ✅ 좋은 예: "방금 말씀하신 'DB 스크립트 작성'은 이력서의 어느 프로젝트에서 하신 건가요?"
- ❌ 나쁜 예: 여러 줄에 걸친 설명이나 부연 설명 추가

**출력**: 질문 1개, 한 문장, 번호 없이, 존중하는 톤"""),
        ("user", common_context)
    ])
    
    # ========================================
    # 페르소나 2️⃣: 탐구자 (Deep Diver)
    # ========================================
    deep_diver_prompt = ChatPromptTemplate.from_messages([
        ("system", """당신은 **답변의 깊이를 파고드는 탐구 전문가**입니다.

**당신의 유일한 임무**: 면접자의 답변을 더 깊게 파고드는 질문 1개 생성

⚠️ **중요**: 다른 역할(검증, 전환)은 다른 전문가가 담당합니다. 오직 **탐구**만 하세요.

---

## 📌 절대 원칙

### 1️⃣ 답변에 집중
- **[면접자의 답변]에서 언급한 내용**만 깊게 파세요
- 답변에 없는 새 주제는 금지

### 2️⃣ 구체성 요구
- 막연한 표현 → 구체적 수치/방식 요구
- 기술 용어 → 실제 사용 경험 확인

---

## 🎯 탐구 전략

1. **막연한 표현 → 구체화**
   - 어떤 도구/방식을 사용했는지
   - 얼마나 개선되었는지 (수치)
   
2. **기술 용어 → 실제 경험**
   - 어떤 전략/방법을 사용했는지
   - 문제 상황을 어떻게 해결했는지
   
3. **결과 언급 → 측정 지표**
   - 어떤 지표로 측정했는지
   - 개선 정도는 얼마나 되는지

**재생성 시**: 다른 세부사항 탐구 (방식 → 결과 → 문제점)

---

## 📤 출력 형식

⚠️ **필수 규칙**:
1. 질문은 **한 문장으로만** 작성
2. **괄호나 예시를 절대 포함하지 마세요** (예: 같은 거 금지)
3. 쌍따옴표나 인용 부호 사용 금지
4. 핵심만 간결하게 물어보세요

- ✅ 좋은 예: "어떤 도구로 자동화했고 시간은 얼마나 절약되었나요?"
- ❌ 나쁜 예: "어떤 도구(예: Jenkins, GitHub Actions)로 자동화했나요?"
- ❌ 나쁜 예: 여러 줄에 걸친 설명

**출력**: 질문 1개, 한 문장, 번호 없이, 존중하는 톤"""),
        ("user", common_context)
    ])
    
    # ========================================
    # 페르소나 3️⃣: 전환자 (Topic Shifter)
    # ========================================
    topic_shifter_prompt = ChatPromptTemplate.from_messages([
        ("system", """당신은 **다양한 관점을 제시하는 전략 전문가**입니다.

**당신의 유일한 임무**: 주제 유지 vs 전환을 판단하여 질문 1개 생성

⚠️ **중요**: 다른 역할(검증, 탐구)은 다른 전문가가 담당합니다. 오직 **전환 판단**만 하세요.

---

## 🎯 판단 기준

### ✅ 주제 유지 (같은 내용을 다른 관점에서)
- **검증자**가 이력서 일치 확인, **탐구자**가 깊이 파기를 했다면
- 당신은 **다른 각도**에서 같은 주제를 봐주세요
- 가능한 관점: 협업, 의사결정, 문제해결, 학습, 비즈니스 영향 등
- **창의적으로** 다양한 각도를 시도하세요

⚠️ **중요**: 갑자기 다른 주제로 점프하지 마세요. 이전 답변과 연결고리를 만드세요.
- 나쁜 예: Python 스크립트 얘기 중 → 갑자기 "신규 입사자 교육은 어떻게 했나요?"
- 좋은 예: "변경 이력 관리나 문서화를 경험하셨다고 했는데, 이것이 팀 전체 효율성에 어떤 도움이 되었나요?"

### ✅ 주제 전환 (완전히 새로운 주제로)
- 현재 주제를 충분히 파악했다고 판단되면
- 이력서의 다른 프로젝트/기술/경험으로 전환
- 단, **자연스러운 브릿지**를 사용하세요

---

## 📤 출력 형식

⚠️ **필수**: 질문은 **한 문장으로만** 작성하세요.

**출력**: 질문 1개, 한 문장, 번호 없이, 존중하는 톤"""),
        ("user", common_context)
    ])
    
    # ========================================
    # 병렬 호출 (3개 모델 동시 실행)
    # ========================================
    import asyncio
    
    async def generate_all_questions():
        """3개 페르소나 질문을 병렬로 생성"""
        validator_chain = validator_prompt | validator_llm | parser
        deep_diver_chain = deep_diver_prompt | deep_diver_llm | parser
        topic_shifter_chain = topic_shifter_prompt | topic_shifter_llm | parser
        
        # 병렬 실행
        results = await asyncio.gather(
            validator_chain.ainvoke({}),
            deep_diver_chain.ainvoke({}),
            topic_shifter_chain.ainvoke({}),
            return_exceptions=True
        )
        
        return results
    
    try:
        # 비동기 실행
        questions = asyncio.run(generate_all_questions())
        
        # 에러 체크 및 질문 정리
        final_questions = []
        for i, q in enumerate(questions, 1):
            if isinstance(q, Exception):
                print(f"❌ 질문 {i} 생성 오류: {q}")
                final_questions.append(f"질문 생성 중 오류가 발생했습니다.")
            else:
                # 그대로 사용 (이미 LLM이 한 문장으로 생성)
                final_questions.append(q.strip())
        
        state["current_questions"] = final_questions
        print(f"✅ 질문 생성 완료 (병렬 처리)")
        print(f"   1. [검증자] {final_questions[0][:50]}...")
        print(f"   2. [탐구자] {final_questions[1][:50]}...")
        print(f"   3. [전환자] {final_questions[2][:50]}...")
        
    except Exception as e:
        print(f"❌ 질문 생성 오류: {e}")
        state["current_questions"] = [
            "죄송합니다. AI 질문 생성 중 오류가 발생했습니다.",
            "잠시 후 다시 시도해주세요.",
            "또는 직접 질문을 입력해주세요."
        ]
    
    return state


def finalize_questions_node(state: InterviewState) -> InterviewState:
    """
    4️⃣ 질문 확정
    
    생성된 질문을 asked_questions에 추가
    """
    current_questions = state.get("current_questions", [])
    asked_questions = state.get("asked_questions", [])
    
    # 생성된 질문을 이미 물어본 질문 리스트에 추가
    asked_questions.extend(current_questions)
    state["asked_questions"] = asked_questions
    
    print(f"📝 질문 확정 완료 (누적: {len(asked_questions)}개)")
    
    return state


# ========================================
# LangGraph 구성
# ========================================

def create_interview_graph():
    """
    LangGraph 생성 및 컴파일
    """
    workflow = StateGraph(InterviewState)
    
    # 노드 추가
    workflow.add_node("load_rag", load_rag_node)
    workflow.add_node("preprocess_input", preprocess_input_node)
    workflow.add_node("generate_questions", generate_questions_node)
    workflow.add_node("finalize_questions", finalize_questions_node)
    
    # 엣지 연결 (단순 선형 플로우)
    workflow.set_entry_point("load_rag")
    workflow.add_edge("load_rag", "preprocess_input")
    workflow.add_edge("preprocess_input", "generate_questions")
    workflow.add_edge("generate_questions", "finalize_questions")
    workflow.add_edge("finalize_questions", END)
    
    # 컴파일
    graph = workflow.compile()
    
    print("✅ LangGraph 컴파일 완료")
    
    return graph


# ========================================
# StreamAgent 클래스 (기존 API 호환)
# ========================================

class StreamAgent:
    """
    면접 스트림 AI 에이전트 (LangGraph 기반)
    
    기존 LCEL 방식과 동일한 API 제공
    """
    
    def __init__(self):
        self.graph = create_interview_graph()
        print("🎯 StreamAgent (LangGraph) 초기화 완료")
    
    def generate_followups(self, text, question_id, history=None, session_id=None, 
                          jd_text="", regen=False):
        """
        후속 질문 3개 생성 (기존 Flask API와 호환)
        
        Args:
            text (str): 면접자의 최신 답변
            question_id (str): 현재 질문 ID (q1, q2, ...)
            history (list or str, optional): 이전 대화 기록
            session_id (str, optional): 세션 ID (RAG 검색용 - 이력서/포트폴리오)
            jd_text (str, optional): JD 원문 (DB에서 조회)
            regen (bool, optional): 재생성 여부
        
        Returns:
            list[str]: 후속 질문 리스트 (최대 3개)
        
        Note:
            - 이력서/포트폴리오는 RAG 임베딩 검색으로 처리 (session_id 사용)
            - JD는 DB에서 조회한 전체 원문 사용
        """
        try:
            # 재생성 히스토리 관리
            if not hasattr(self, '_regen_history'):
                self._regen_history = []
            
            # 재생성 횟수 체크
            current_regen_count = getattr(self, '_regen_count', 0)
            
            # 재생성이면 카운트 증가 + 이전 시도 저장, 아니면 초기화
            if regen:
                current_regen_count += 1
                # 이전 시도 질문을 히스토리에 추가
                if hasattr(self, '_last_questions') and self._last_questions:
                    self._regen_history.extend(self._last_questions)
            else:
                current_regen_count = 0  # 새 답변이면 카운트 리셋
                self._regen_history = []  # 히스토리도 초기화
            
            # 초기 상태 구성
            initial_state = {
                "session_id": session_id or "",
                "question_id": question_id or "",
                "messages": history if isinstance(history, list) else [],
                "history_text": "",
                "jd_text": jd_text or "",
                "rag_context": "",
                "current_questions": [],
                "asked_questions": getattr(self, '_asked_questions_cache', []),
                "interviewee_answer": text or "",
                "regen_history": self._regen_history,  # 재생성 히스토리 전달
                "is_regen": regen,
                "regen_count": current_regen_count
            }
            
            # 재생성 횟수 제한 (최대 2회)
            MAX_REGEN_COUNT = 2
            
            if regen and current_regen_count > MAX_REGEN_COUNT:
                print(f"⚠️ 재생성 제한 도달 ({current_regen_count}/{MAX_REGEN_COUNT}). 이전 질문 반환.")
                # 카운트는 유지 (더 이상 재생성 못하게)
                return getattr(self, '_last_questions', [
                    "재생성 횟수가 초과되었습니다.",
                    "새로운 답변을 입력하거나 질문을 선택해주세요.",
                    "면접을 계속 진행해주세요."
                ])
            
            print(f"🔄 질문 생성 (재생성: {regen}, 카운트: {current_regen_count}/{MAX_REGEN_COUNT})")
            
            # LangGraph 실행
            result = self.graph.invoke(initial_state)
            
            # asked_questions 캐싱 (재사용)
            self._asked_questions_cache = result.get("asked_questions", [])
            
            # 생성된 질문 저장 (재생성 제한 시 반환용)
            questions = result.get("current_questions", [])
            self._last_questions = questions
            
            # 재생성 카운트 저장
            self._regen_count = current_regen_count
            
            return questions
            
        except Exception as e:
            print(f"❌ LangGraph 오류: {e}")
            import traceback
            traceback.print_exc()
            
            return [
                "죄송합니다. AI 질문 생성 중 오류가 발생했습니다.",
                "잠시 후 다시 시도해주세요.",
                "또는 직접 질문을 입력해주세요."
            ]
