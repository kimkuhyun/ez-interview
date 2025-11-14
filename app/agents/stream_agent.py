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
from app.utils.interview_store import get_all_documents_by_session


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
    
    # 재생성 제어
    is_regen: bool
    regen_count: int  # 현재 답변에 대한 재생성 횟수


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
        ("system", """당신은 경험 많은 기술 면접관입니다.
면접자의 답변을 경청하며 자연스러운 대화를 이어가세요.
답변의 깊이를 파악하고, 더 구체적인 정보가 필요한 부분을 탐색하세요."""),
        ("user", """
[면접 자료]
{rag_context}

[이전 대화]
{history_text}

[면접자의 답변]
{interviewee_answer}

**면접 단계**: {phase} ({phase_guideline})

**후속 질문 생성 가이드라인**:

1. **⚠️ 모순 및 불일치 확인 (최우선)**
   - 이력서/JD와 답변 내용을 면밀히 비교
   - 이전 답변과 현재 답변 간 일관성 확인
   - 숫자, 기간, 기술 스택, 역할 등 구체적 정보의 차이 주의
   - 발견 시 부드럽게 확인: "조금 전에 말씀하신 내용과 다른 것 같은데, 확인 부탁드립니다"

2. **토픽 전환 판단 (중요)**
   
   **다음 상황에서는 새로운 주제로 전환하세요:**
   ✅ 같은 기술/프로젝트에 대해 2-3번 질문하여 충분히 파악함
   ✅ 지원자가 해당 주제에 대해 구체적이고 명확한 답변을 여러 차례 제공함
   ✅ 더 물어봐도 새로운 정보를 얻기 어려울 것 같음
   ✅ 이력서에 아직 탐색하지 않은 중요한 경험/기술이 남아있음
   
   **다음 상황에서는 같은 주제 깊이 파기:**
   ⚠️ 답변이 막연하거나 표면적임 (구체성 필요)

        
   ⚠️ 이력서 내용과 불일치 의심 (검증 필요)
   ⚠️ 핵심 기술/경험인데 아직 2개 미만 질문

3. **답변 내용 깊이 파악**
   - 막연한 답변은 구체적인 예시나 상황을 요청
   - 기술적 용어는 실제 사용 경험과 이해도 확인
   - "여러", "많이", "다양한" 등의 표현은 구체적 수치나 사례 요청

4. **정보 수집 우선순위**
   - 기술적 세부사항: 어떤 기술을 왜 선택했는지, 어떻게 구현했는지
   - 문제 해결 과정: 어떤 어려움이 있었고 어떻게 해결했는지
   - 협업 및 역할: 팀 구성, 본인의 기여도, 의사결정 과정
   - 정량적 성과: 구체적인 수치, 개선율, 영향 범위

**이미 물어본 질문들** (중복 방지):
{asked_questions_text}

---

**출력 형식**:
- 번호 없이 질문 3개만 출력
- 각 질문은 한 줄로 작성
- 자연스럽고 존중하는 톤 유지
- 실제 면접 상황처럼 구어체 사용 가능

예시:
방금 말씀하신 API 최적화 부분이 흥미로운데, 구체적으로 어떤 방식으로 개선하셨나요?
팀 프로젝트라고 하셨는데, 전체 팀 구성은 어떻게 되었고 본인은 어떤 역할을 맡으셨나요?
6개월간 진행하셨다고 했는데, 그 기간 동안 가장 어려웠던 기술적 챌린지는 무엇이었나요?
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
    workflow.add_node("finalize_questions", finalize_questions_node)
    
    # 엣지 연결 (단순 선형 플로우)
    workflow.set_entry_point("load_rag")
    workflow.add_edge("load_rag", "preprocess_input")
    workflow.add_edge("preprocess_input", "determine_phase")
    workflow.add_edge("determine_phase", "generate_questions")
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
            # 재생성 횟수 체크
            current_regen_count = getattr(self, '_regen_count', 0)
            
            # 재생성이면 카운트 증가, 아니면 초기화
            if regen:
                current_regen_count += 1
            else:
                current_regen_count = 0
            
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
                "is_regen": regen,
                "regen_count": current_regen_count
            }
            
            # 재생성 횟수 제한 (최대 2회)
            MAX_REGEN_COUNT = 2
            
            if regen and current_regen_count > MAX_REGEN_COUNT:
                print(f"⚠️ 재생성 제한 도달 ({current_regen_count}/{MAX_REGEN_COUNT}). 이전 질문 반환.")
                # 이전에 생성된 질문 그대로 반환
                return getattr(self, '_last_questions', [
                    "재생성 횟수가 초과되었습니다.",
                    "새로운 답변을 입력하거나 질문을 선택해주세요.",
                    "면접을 계속 진행해주세요."
                ])
            
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
    
    def as_runnable(self):
        """Runnable 체인 반환 (다른 에이전트와 조합 가능)"""
        return self.graph
    
    def analyze_answer(self, text, criteria=None):
        """답변 분석 (추후 구현)"""
        # TODO: 답변 분석 로직 구현
        pass
