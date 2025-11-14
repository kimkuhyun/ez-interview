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
from difflib import SequenceMatcher
import operator

from app.config.config import Config
from app.utils.interview_store import get_all_documents_by_session


# ---------- 문자열 유사도 계산 ----------
def string_similarity(s1: str, s2: str) -> float:
    """
    두 문자열의 유사도 계산 (Levenshtein 거리 기반)
    
    Args:
        s1: 첫 번째 문자열
        s2: 두 번째 문자열
    
    Returns:
        float: 유사도 (0.0 ~ 1.0)
    """
    # 공백 제거 및 소문자 변환
    s1 = s1.strip().lower()
    s2 = s2.strip().lower()
    
    # SequenceMatcher로 유사도 계산
    return SequenceMatcher(None, s1, s2).ratio()


# ========================================
# State 정의
# ========================================
class InterviewState(TypedDict):
    """면접 진행 상태"""
    
    # 세션 정보
    session_id: str
    question_id: str  # 현재 대질문 (q1, q2, ...)
    
    # 대화 기록
    messages: Annotated[List[dict], operator.add]  # 자동 append
    history_text: str  # LLM에 전달할 히스토리 문자열
    
    # RAG 컨텍스트 (캐싱)
    rag_context: str  # 이력서 + JD 전체 텍스트
    
    # 면접 단계
    interview_phase: Literal["경력검증", "기술심화", "역량평가"]
    
    # 생성된 질문
    current_questions: List[str]  # 현재 표시 중인 후속 질문 3개
    asked_questions: List[str]  # 이미 물어본 질문들 (중복 방지)
    
    # 면접자 답변
    interviewee_answer: str
    
    # 검증
    validation_passed: bool
    error_count: int
    
    # 재생성 여부
    is_regen: bool


# ========================================
# LLM 초기화
# ========================================
llm = ChatOpenAI(
    model="gpt-4o-mini",
    temperature=0.7,
    openai_api_key=Config.OPENAI_API_KEY,
)

parser = StrOutputParser()


# ========================================
# 노드 함수들
# ========================================

def load_rag_node(state: InterviewState) -> InterviewState:
    """
    1️⃣ RAG 캐싱 (세션 시작 시 1회만)
    
    이력서/JD 전체를 한 번만 로드하여 캐싱
    """
    if not state.get("rag_context"):
        session_id = state.get("session_id")
        
        if session_id:
            print(f"🔍 RAG 로딩 중... (session_id: {session_id})")
            
            resume = get_all_documents_by_session(session_id, "resume")
            jd = get_all_documents_by_session(session_id, "jd")
            
            if resume or jd:
                rag_context = ""
                
                if resume:
                    rag_context += f"[이력서]\n{resume}\n\n"
                
                if jd:
                    rag_context += f"[모집공고]\n{jd}\n\n"
                
                state["rag_context"] = rag_context
                print(f"✅ RAG 캐싱 완료 (이력서: {len(resume)}자, JD: {len(jd)}자)")
            else:
                print("⚠️  RAG 문서 없음 (빈 컨텍스트 사용)")
                state["rag_context"] = "[참고: 이력서 및 모집공고 정보 없음]\n"
        else:
            print("⚠️  session_id 없음 (RAG 캐싱 생략)")
            state["rag_context"] = "[참고: 이력서 및 모집공고 정보 없음]\n"
    
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


def determine_phase_node(state: InterviewState) -> InterviewState:
    """
    3️⃣ 면접 단계 결정 (이력서/자소서 검증 중심)
    
    질문 개수 기반으로 면접 단계 결정
    - 0-4개: 경력검증 (이력서 내용 사실 확인)
    - 5-9개: 기술심화 (프로젝트 구체적 검증)
    - 10개 이상: 역량평가 (문제해결·협업 능력)
    """
    asked_count = len(state.get("asked_questions", []))
    
    if asked_count < 5:
        phase = "경력검증"
    elif asked_count < 10:
        phase = "기술심화"
    else:
        phase = "역량평가"
    
    state["interview_phase"] = phase
    print(f"📍 면접 단계: {phase} (누적 질문: {asked_count}개)")
    
    return state


def generate_questions_node(state: InterviewState) -> InterviewState:
    """
    4️⃣ 후속 질문 생성 (GPT-4 호출)
    
    현재 단계에 맞는 후속 질문 3개 생성
    """
    phase = state.get("interview_phase", "기술역량")
    rag_context = state.get("rag_context", "")
    history_text = state.get("history_text", "")
    interviewee_answer = state.get("interviewee_answer", "")
    asked_questions = state.get("asked_questions", [])
    
    # 단계별 가이드라인 (참고용, 강제 아님)
    phase_guidelines = {
        "경력검증": "이력서/자소서 내용 사실 확인 중심",
        "기술심화": "기술적 깊이와 구체적 구현 디테일 파악",
        "역량평가": "문제해결, 협업, 학습 능력 평가"
    }
    
    # 최근 물어본 질문만 프롬프트에 포함 (최대 10개)
    recent_asked = asked_questions[-10:] if len(asked_questions) > 10 else asked_questions
    
    # 프롬프트 생성
    prompt_template = ChatPromptTemplate.from_messages([
        ("system", """너는 날카로운 기술 면접관이야. 
면접자의 답변을 듣고 자연스럽게 대화를 이어가되, **모순이나 불일치가 보이면 즉시 지적**해.
이력서는 거짓이 많으니까 철저하게 검증해야 해."""),
        ("user", """
{rag_context}

[이전 대화 흐름]
{history_text}

[면접자가 방금 한 답변]
{interviewee_answer}

---

🎯 **질문 생성 전 필수 체크리스트**:

1️⃣ **모순 검증 (최우선)**:
   - 이력서 vs 답변: "이력서엔 'A'라고 했는데, 방금 'B'라고 하셨는데 어느 게 맞나요?"
   - 이전 답변 vs 지금 답변: "아까 'X'라고 하셨는데, 지금은 'Y'라고 하시네요. 설명 부탁드립니다."
   - 시간/규모 불일치: "이력서엔 6개월이라고 했는데, 방금 1년이라고 하셨는데요?"
   
   ⚠️ **모순이 발견되면 반드시 질문 중 하나는 모순 지적이어야 함**

2️⃣ **애매한 답변 구체화**:
   - "여러 기술을 사용했다" → "구체적으로 어떤 기술인가요?"
   - "팀원들과 협업했다" → "몇 명이었고, 당신의 역할은 뭐였나요?"
   - "성능이 개선됐다" → "정확히 몇 %나 개선됐나요?"

3️⃣ **이력서 내용 깊이 검증**:
   - 이력서에 적힌 프로젝트/기술을 **구체적으로** 물어봐
   - "이력서에 'Spring Boot로 API 개발'이라고 했는데, 어떤 API를 만들었나요?"
   - "이력서에 'DB 최적화'라고 했는데, 정확히 무엇을 최적화했나요?"

4️⃣ **정량적 정보 요구**:
   - 기간, 인원, 규모, 성과를 숫자로 물어봐
   - "몇 명이서 했나요?", "얼마나 걸렸나요?", "몇 %나 개선됐나요?"

---

**질문 생성 순서**:
① 모순 발견됐나? → 그걸 먼저 질문
② 애매한 답변 있나? → 구체화 요구
③ 이력서 내용 검증 필요? → 깊이 파고들기

**면접 단계 참고** (현재: {phase} - {phase_guideline}):
- 참고만 해. 모순 검증이 최우선.

[이미 물어본 질문들 - 절대 중복 금지]
{asked_questions_text}

⚠️ 출력 형식:
- 각 질문은 한 줄로, 번호 없이 순수 질문만 3개
- 모순이 있으면 반드시 포함
- 예: 이력서에는 Python을 3년 사용했다고 했는데, 방금 1년이라고 하셨는데 어느 게 맞나요?
""")
    ])
    
    # LLM 호출
    chain = prompt_template | llm | parser
    
    try:
        ai_reply = chain.invoke({
            "rag_context": rag_context,
            "phase": phase,
            "phase_guideline": phase_guidelines[phase],
            "history_text": history_text,
            "interviewee_answer": interviewee_answer,
            "asked_questions_text": "\n".join(f"- {q}" for q in recent_asked) if recent_asked else "(없음)"
        })
        
        # 질문 파싱
        questions = [
            q.strip("-• ").strip()
            for q in ai_reply.split("\n")
            if q.strip()
        ][:3]  # 최대 3개
        
        state["current_questions"] = questions
        print(f"✅ 질문 생성 완료 ({len(questions)}개)")
        for i, q in enumerate(questions, 1):
            print(f"   {i}. {q}")
        
    except Exception as e:
        print(f"❌ 질문 생성 오류: {e}")
        state["current_questions"] = [
            "죄송합니다. AI 질문 생성 중 오류가 발생했습니다.",
            "잠시 후 다시 시도해주세요.",
            "또는 직접 질문을 입력해주세요."
        ]
    
    return state


def validate_questions_node(state: InterviewState) -> InterviewState:
    """
    5️⃣ 질문 검증 (중복 방지)
    
    문자열 유사도로 중복 검증 (빠른 방식)
    """
    questions = state.get("current_questions", [])
    asked = state.get("asked_questions", [])
    
    # 최근 20개만 비교 (성능 최적화)
    recent_asked = asked[-10:] if len(asked) > 10 else asked
    
    is_duplicate = False
    
    for q in questions:
        for prev in recent_asked:
            similarity = string_similarity(q, prev)
            
            if similarity > 0.90:  # 90% 이상 유사하면 중복
                print(f"⚠️  중복 감지: '{q[:30]}...' vs '{prev[:30]}...' (유사도: {similarity:.2f})")
                is_duplicate = True
                break
        
        if is_duplicate:
            break
    
    if is_duplicate:
        state["validation_passed"] = False
        state["error_count"] = state.get("error_count", 0) + 1
        print(f"❌ 검증 실패 (재시도)")
    else:
        state["validation_passed"] = True
        state["error_count"] = 0
        print("✅ 질문 검증 통과")
    
    # 재시도 1회 이상이면 강제 통과
    if state.get("error_count", 0) >= 1:
        print("⚠️  재시도 한계 도달, 질문 강제 사용")
        state["validation_passed"] = True
    
    return state


def finalize_questions_node(state: InterviewState) -> InterviewState:
    """
    6️⃣ 질문 확정
    
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
# 조건부 라우팅
# ========================================

def should_retry(state: InterviewState) -> str:
    """
    검증 결과에 따라 다음 노드 결정
    
    Returns:
        "retry": 재생성
        "done": 완료
    """
    validation_passed = state.get("validation_passed", False)
    error_count = state.get("error_count", 0)
    
    if not validation_passed and error_count < 2:
        return "retry"
    else:
        return "done"


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
    workflow.add_node("determine_phase", determine_phase_node)
    workflow.add_node("generate_questions", generate_questions_node)
    workflow.add_node("validate_questions", validate_questions_node)
    workflow.add_node("finalize_questions", finalize_questions_node)
    
    # 엣지 연결
    workflow.set_entry_point("load_rag")
    workflow.add_edge("load_rag", "preprocess_input")
    workflow.add_edge("preprocess_input", "determine_phase")
    workflow.add_edge("determine_phase", "generate_questions")
    workflow.add_edge("generate_questions", "validate_questions")
    
    # 조건부 엣지: 검증 결과에 따라 분기
    workflow.add_conditional_edges(
        "validate_questions",
        should_retry,
        {
            "retry": "generate_questions",  # 재생성
            "done": "finalize_questions"     # 완료
        }
    )
    
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
    
    def generate_followups(self, text, question_id, history=None, session_id=None, regen=False):
        """
        후속 질문 3개 생성 (기존 Flask API와 호환)
        
        Args:
            text (str): 면접자의 최신 답변
            question_id (str): 현재 질문 ID (q1, q2, ...)
            history (list or str, optional): 이전 대화 기록
            session_id (str, optional): 세션 ID (RAG 검색용)
            regen (bool, optional): 재생성 여부
        
        Returns:
            list[str]: 후속 질문 리스트 (최대 3개)
        """
        try:
            # 초기 상태 구성
            initial_state = {
                "session_id": session_id or "",
                "question_id": question_id or "",
                "messages": history if isinstance(history, list) else [],
                "history_text": "",
                "rag_context": "",
                "interview_phase": "경력검증",
                "current_questions": [],
                "asked_questions": getattr(self, '_asked_questions_cache', []),
                "interviewee_answer": text or "",
                "validation_passed": False,
                "error_count": 0,
                "is_regen": regen
            }
            
            # LangGraph 실행
            result = self.graph.invoke(initial_state)
            
            # asked_questions 캐싱 (재사용)
            self._asked_questions_cache = result.get("asked_questions", [])
            
            # 생성된 질문 반환
            questions = result.get("current_questions", [])
            
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
    
    def as_runnable(self):
        """Runnable 체인 반환 (다른 에이전트와 조합 가능)"""
        return self.graph
    
    def analyze_answer(self, text, criteria=None):
        """답변 분석 (추후 구현)"""
        # TODO: 답변 분석 로직 구현
        pass
