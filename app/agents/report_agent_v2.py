"""
리포트 생성 에이전트 V2 - Solar Pro 2 적극 활용 + 질의 설계 에이전트 + 병렬 분석 + 디베이트 로그 노출

개선 사항 요약:
1. Solar Pro 2 Chat/Reasoning 하이브리드 활용 (기존 유지)
2. 질의 설계 에이전트: 사용자 프롬프트 기반 벡터 검색 질의 최적화 (기존 유지)
3. GPT는 임베딩/리트리버 전용 (임베딩이 GPT이므로) (기존 유지)
4. Solar Pro 2로 모든 분석/평가 작업 수행 (비용 절감) (기존 유지)
5. Retry 메커니즘: 품질 부족 시 질의 재설계 (기존 유지)
6. 리트리버 이후 4개 LLM 작업(인터뷰 분석/역량 평가/증거 매핑/요약)을 병렬 실행
7. 인터뷰 분석 결과에 A/B 디베이트 로그(debate_log)를 추가하고, SSE 스트림과 최종 리포트에서 활용
8. ReportOut 스키마 제약 완화로 스키마 ValidationError 발생 가능성 감소
"""

from __future__ import annotations
from typing import List, Dict, Optional, Any, AsyncGenerator
from uuid import uuid4
from datetime import datetime, timezone
import os
import asyncio

from pydantic import BaseModel, Field, ValidationError, StringConstraints
from typing_extensions import Annotated, TypedDict
from annotated_types import Ge, Le

from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI

# ==================== 로거 설정 ====================
import logging
logger = logging.getLogger(__name__)
if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    logger.addHandler(_handler)
logger.setLevel(logging.DEBUG)

# LangGraph 임포트
from langgraph.graph import StateGraph, END

# Vector DB 리트리버 임포트
from app.utils.rag_retriever import search_similar_chunks
from app.utils.interview_store import retrieve_interview_context

# ==================== 환경 설정 ====================
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
SOLAR_API_KEY = os.getenv("SOLAR_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "o3-mini")

# Upstage Solar Pro 2 설정 (OpenAI 호환 API)
SOLAR_MODEL = os.getenv("SOLAR_MODEL", "solar-pro2")  # 또는 "solar-pro-2" (최신 버전)
SOLAR_BASE_URL = "https://api.upstage.ai/v1"

# 병렬 분석 타임아웃 (환경변수로 덮어쓰기 가능)
# 개별 LLM 작업 타임아웃: 30초 → 90초 증가 (Solar API 응답 시간 고려)
PARALLEL_ANALYSIS_TASK_TIMEOUT_SEC = int(
    os.getenv("PARALLEL_ANALYSIS_TASK_TIMEOUT_SEC", "90")
)
# 전체 병렬 블록 타임아웃: 120초 → 180초 증가 (3분)
PARALLEL_ANALYSIS_TIMEOUT_SEC = int(os.getenv("PARALLEL_ANALYSIS_TIMEOUT_SEC", "300"))


def _llm_gpt() -> ChatOpenAI:
    """GPT-4o 모델"""
    return ChatOpenAI(
        model_name=OPENAI_MODEL,
        #temperature=0.1,
        timeout=90,
        api_key=OPENAI_API_KEY,
    )


def _llm_solar_chat() -> ChatOpenAI:
    """
    Solar Pro 2 Chat 모드 (일반 분석/평가 작업)

    - 빠른 응답 속도
    - 비용 효율적
    - 일반 대화, 요약, 분석용
    """
    return ChatOpenAI(
        model=SOLAR_MODEL,
        temperature=0.2,
        timeout=90,  # 180 → 90초로 감소 (성능 개선)
        api_key=SOLAR_API_KEY,
        base_url=SOLAR_BASE_URL,
    )

def _llm_solar_planner() -> ChatOpenAI:
    """
    질의 설계 전용 Solar 클라이언트 (짧은 타임아웃)
    - query_planner에서만 사용
    """
    return ChatOpenAI(
        model=SOLAR_MODEL,
        temperature=0.1,
        max_retries=1,
        timeout=15,  # 기존 90 → 15초 정도로 강하게 줄임
        api_key=SOLAR_API_KEY,
        base_url=SOLAR_BASE_URL,
    )

def _llm_solar_reasoning() -> ChatOpenAI:
    """
    Solar Pro 2 Reasoning 모드 (복잡한 추론/검증 작업)

    - Chain-of-Thought 기반 멀티스텝 추론
    - 코드 작성, 수학 문제 풀이, 품질 검증용
    - reasoning_effort: 'minimal', 'low', 'medium', 'high' 지원
    """
    return ChatOpenAI(
        model=SOLAR_MODEL,
        temperature=0.1,
        timeout=60,  # 240 → 60초로 대폭 감소 (1분) - 성능 개선
        api_key=SOLAR_API_KEY,
        base_url=SOLAR_BASE_URL,
        reasoning_effort="high",  # "medium" → "low"로 변경 (속도 2-3배 개선)
    )


# ==================== 스키마 정의 ====================
KeyStr = Annotated[str, StringConstraints(strip_whitespace=True)]
NonEmpty = Annotated[str, StringConstraints(min_length=1)]
E_ID = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^E\d{2,3}$")]
COMP_ID = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^C\d{2,3}$")]
Score = Annotated[int, Ge(0), Le(100)]
Weight = Annotated[int, Ge(0), Le(100)]


class Competency(BaseModel):
    key: KeyStr
    label: NonEmpty


class ScoreItem(BaseModel):
    key: KeyStr
    value: Score


class WeightItem(BaseModel):
    key: KeyStr
    value: Weight


class Headline(BaseModel):
    summary: NonEmpty
    tag: List[str] = Field(min_length=1, max_length=5, description="키워드 태그 3-5개")
    # 인터뷰 분석 지표 (validation_node에서 추가됨)
    contradiction_score: Optional[Score] = Field(
        default=None, description="답변 모순도 (0=일관적, 100=모순적)"
    )
    depth_score: Optional[Score] = Field(
        default=None, description="답변 깊이 (0=피상적, 100=깊이있음)"
    )
    reliability_score: Optional[Score] = Field(
        default=None, description="신뢰도 (0=낮음, 100=높음)"
    )


class TalkSummaryItem(BaseModel):
    주제: NonEmpty
    발언요약: NonEmpty


class TalkSummary(BaseModel):
    items: List[TalkSummaryItem] = Field(min_length=1)


class JDCoverRow(BaseModel):
    jid: COMP_ID
    요구사항: NonEmpty
    기대치: NonEmpty
    충족도: NonEmpty
    근거: List[E_ID]


class EvidenceRow(BaseModel):
    eid: E_ID
    출처: NonEmpty
    내용: NonEmpty
    jid: COMP_ID


class ConvKV(BaseModel):
    # NonEmpty 제약을 제거해 통계값이 비어도 전체 리포트가 깨지지 않도록 완화
    k: str
    v: str


class ReportOut(BaseModel):
    """최종 리포트 출력 스키마 (입출력 고정) - 일부 제약 완화"""

    # 축 개수는 1개 이상이면 허용 (UI는 5개를 기본 가정하되, 스키마는 느슨하게)
    axes: List[Competency] = Field(min_length=1)
    scores: List[ScoreItem] = Field(min_length=1)
    weights: List[WeightItem] = Field(min_length=1)

    headline: Headline
    talkSummary: TalkSummary

    # 통계/커버리지는 없어도 리포트가 동작하도록 기본 빈 리스트 허용
    convStats: List[ConvKV] = Field(default_factory=list)
    jdCoverage: List[JDCoverRow] = Field(default_factory=list)
    evidence: List[EvidenceRow] = Field(default_factory=list)


# ==================== 질의 설계 스키마 ====================
class QueryPlan(BaseModel):
    """질의 설계 에이전트 출력"""

    resume_query: NonEmpty  # 이력서 검색용 질의
    jd_query: NonEmpty  # JD 검색용 질의
    interview_query: NonEmpty  # 인터뷰 로그 분석용 힌트
    focus_areas: List[str] = Field(default_factory=list)  # 집중 분석 영역
    reasoning: NonEmpty  # 질의 설계 근거


class ValidationFeedback(BaseModel):
    """검증 피드백 (재시도용)"""

    quality_score: Annotated[float, Ge(0.0), Le(1.0)]
    is_sufficient: bool
    missing_aspects: List[str] = Field(default_factory=list)
    query_hint: Optional[str] = None  # 다음 질의 힌트


# ==================== 에러 처리 ====================
class ReportError(Exception):
    def __init__(
        self, code: str, message: str, http: int = 400, details: Dict[str, Any] = None
    ):
        super().__init__(message)
        self.code, self.message, self.http = code, message, http
        self.details = details or {}


def _error(code: str, message: str, http: int = 400, details: Dict = None) -> Dict:
    return {
        "status": "failed",
        "code": code,
        "message": message,
        "http": http,
        "trace_id": uuid4().hex,
        "timestamp": datetime.now(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
        "details": details or {},
    }


# ==================== 질의 설계 에이전트 ====================
def _query_planner_prompt() -> ChatPromptTemplate:
    """질의 설계 프롬프트 (Solar Reasoning 사용)"""
    return ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "당신은 벡터 검색 질의 설계 에이전트입니다.\n"
                "- 이력서/공고/인터뷰 로그 검색을 위한 최적의 질의를 설계합니다.\n"
                "- 사용자 프롬프트가 있으면 그것을 최우선으로 반영\n"
                "- 없으면 기본 평가 축(axes_keys)을 기반으로 질의 생성\n"
                "- 구체적이고 핵심적인 키워드 중심\n"
                "- 반드시 JSON 형식으로만 답변합니다. 자연어 설명은 금지입니다.\n\n"
                "출력 필드:\n"
                "- resume_query: string\n"
                "- jd_query: string\n"
                "- interview_query: string\n"
                "- focus_areas: string 리스트 (최대 5개)\n"
                "- reasoning: string\n"
            ),
            (
                "human",
                "평가 축(axes_keys): {axes_keys}\n"
                "사용자 요구사항(user_prompt): {user_prompt}\n"
                "이전 피드백(query_hint): {query_hint}\n\n"
                "위 정보를 반영하여 벡터검색에 사용할 질의를 설계하세요.\n"
                "반드시 JSON 하나만 출력하세요. 주변에 다른 텍스트를 넣지 마세요."
            ),
        ]
    )


def design_queries(
    axes_keys: List[str], user_prompt: Optional[str], query_hint: Optional[str] = None
) -> QueryPlan:
    """질의 설계 (Solar Chat 모드로 변경 - 속도 개선)"""
    # Reasoning 대신 Chat 모드 사용 (질의 설계는 복잡한 추론 불필요)
    chain = _query_planner_prompt() | _llm_solar_planner().with_structured_output(
        QueryPlan, method="json_mode"
    )

    result = chain.invoke(
        {
            "axes_keys": ", ".join(axes_keys),
            "user_prompt": user_prompt or "[NO_USER_PROMPT]",
            "query_hint": query_hint or "[NO_QUERY_HINT]",
        }
    )

    return result


# ==================== 리트리버 ====================
def _extract_qa_pairs(log_text: str) -> List[Dict]:
    """인터뷰 로그에서 질문-답변 쌍 추출"""
    qa_pairs = []
    lines = log_text.split("\n")
    current_q = None
    current_a: List[str] = []
    q_num = 0

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # 질문 패턴 감지
        if any(
            line.startswith(prefix)
            for prefix in ["Q:", "q:", "면접관:", "Question:", "Q.", "[면접관]"]
        ):
            if current_q and current_a:
                q_num += 1
                qa_pairs.append(
                    {
                        "q_num": str(q_num),
                        "question": current_q,
                        "answer": " ".join(current_a),
                    }
                )
                current_a = []

            for prefix in ["Q:", "q:", "면접관:", "Question:", "Q.", "[면접관]"]:
                if line.startswith(prefix):
                    current_q = line[len(prefix) :].strip()
                    break

        # 답변 패턴 감지
        elif any(
            line.startswith(prefix)
            for prefix in ["A:", "a:", "지원자:", "Answer:", "A.", "[지원자]"]
        ):
            for prefix in ["A:", "a:", "지원자:", "Answer:", "A.", "[지원자]"]:
                if line.startswith(prefix):
                    answer_text = line[len(prefix) :].strip()
                    current_a.append(answer_text)
                    break

        # 답변 계속
        elif current_q and current_a:
            current_a.append(line)

    # 마지막 Q&A 저장
    if current_q and current_a:
        q_num += 1
        qa_pairs.append(
            {
                "q_num": str(q_num),
                "question": current_q,
                "answer": " ".join(current_a),
            }
        )

    return qa_pairs


def retrieve_contexts(
    session_id: str, query_plan: QueryPlan, axes_keys: List[str]
) -> tuple:
    """
    VectorDB와 interview_store를 활용한 컨텍스트 구성

    Args:
        session_id: 세션 ID
        query_plan: 질의 설계 결과
        axes_keys: 핵심역량 키 리스트

    Returns:
        (jd_ctx, resume_ctx, log_ctx, comp_id_list, eid_list, qa_pairs)
    """
    print(f"\n{'=' * 80}")
    print("🔍 [Retriever] 시작")
    print(f"   Session ID: {session_id}")
    print(f"   핵심역량: {', '.join(axes_keys)}")
    print(f"{'=' * 80}\n")

    print("📋 [질의 설계 결과]")
    print(f"   Resume Query: '{query_plan.resume_query}'")
    print(f"   JD Query: '{query_plan.jd_query}'")
    print(f"   Interview Query: '{query_plan.interview_query}'")
    print(f"   Focus Areas: {', '.join(query_plan.focus_areas)}")
    print(f"   설계 근거: {query_plan.reasoning[:100]}...\n")

    # 초기화 (예외 발생 시에도 변수가 정의되도록)
    resume_chunks: List[str] = []
    jd_chunks: List[str] = []
    resume_results: List = []
    jd_results: List = []

    # 1. Vector DB에서 이력서 검색
    print("📄 [이력서 검색]")
    print(f"   질의: '{query_plan.resume_query}'")
    print("   Top-K: 10")
    try:
        resume_results = search_similar_chunks(
            query_plan.resume_query, session_id=session_id, top_k=10
        )

        print(f"\n   검색 결과 (총 {len(resume_results)}개):")
        for idx, result in enumerate(resume_results, 1):
            chunk_text = result[0]
            chunk_type = result[1] if len(result) > 1 else "unknown"
            similarity = result[2] if len(result) > 2 else "N/A"
            preview = chunk_text[:100].replace("\n", " ")
            print(
                f"   [{idx}] Type: {chunk_type:10s} | Similarity: {similarity} | Preview: {preview}..."
            )

        resume_chunks = [r[0] for r in resume_results if len(r) > 1 and r[1] == "resume"]
        resume_ctx = "\n\n".join(resume_chunks) if resume_chunks else ""

        print(f"\n   ✅ 이력서 chunk {len(resume_chunks)}개 선택 (총 {len(resume_ctx)} 글자)")
        if resume_chunks:
            print("   📊 첫 번째 chunk 미리보기:")
            print(f"      {resume_chunks[0][:200].replace(chr(10), ' ')}...\n")
    except Exception as e:
        print(f"   ⚠️ Vector DB 이력서 검색 실패: {e}\n")
        resume_ctx = ""
        resume_results = []

    # 2. Vector DB에서 JD 검색
    print("📋 [JD 검색]")
    print(f"   질의: '{query_plan.jd_query}'")
    print("   Top-K: 10")
    try:
        jd_results = search_similar_chunks(
            query_plan.jd_query, session_id=session_id, top_k=10
        )

        print(f"\n   검색 결과 (총 {len(jd_results)}개):")
        for idx, result in enumerate(jd_results, 1):
            chunk_text = result[0]
            chunk_type = result[1] if len(result) > 1 else "unknown"
            similarity = result[2] if len(result) > 2 else "N/A"
            preview = chunk_text[:100].replace("\n", " ")
            print(
                f"   [{idx}] Type: {chunk_type:10s} | Similarity: {similarity} | Preview: {preview}..."
            )

        jd_chunks = [r[0] for r in jd_results if len(r) > 1 and r[1] == "jd"]
        jd_ctx = "\n\n".join(jd_chunks) if jd_chunks else ""

        print(f"\n   ✅ JD chunk {len(jd_chunks)}개 선택 (총 {len(jd_ctx)} 글자)")
        if jd_chunks:
            print("   📊 첫 번째 chunk 미리보기:")
            print(f"      {jd_chunks[0][:200].replace(chr(10), ' ')}...\n")
    except Exception as e:
        print(f"   ⚠️ Vector DB JD 검색 실패: {e}\n")
        jd_ctx = ""
        jd_results = []

    # 3. interview_store에서 인터뷰 로그 조회
    print("💬 [인터뷰 로그 조회]")
    print(f"   Session ID: {session_id}")
    try:
        log_ctx = retrieve_interview_context(session_id)
        if not log_ctx:
            print("   ⚠️ interview_store에서 로그 없음\n")
            log_ctx = ""
        else:
            print(f"   ✅ 로그 조회 완료: {len(log_ctx)} 글자")
            print("   📊 로그 미리보기:")
            print(f"      {log_ctx[:200].replace(chr(10), ' ')}...\n")
    except Exception as e:
        print(f"   ⚠️ interview_store 조회 실패: {e}\n")
        log_ctx = ""

    # 4. 질문-답변 쌍 추출
    print("❓ [질문-답변 쌍 추출]")
    qa_pairs = _extract_qa_pairs(log_ctx)
    print(f"   ✅ {len(qa_pairs)}개 추출")

    if qa_pairs:
        print("\n   QA 상세:")
        for qa in qa_pairs[:5]:
            q_preview = qa["question"][:60]
            a_preview = qa["answer"][:80]
            print(
                f"   Q{qa['q_num']}: {q_preview}{'...' if len(qa['question']) > 60 else ''}"
            )
            print(
                f"   A{qa['q_num']}: {a_preview}{'...' if len(qa['answer']) > 80 else ''}"
            )
            print()
        if len(qa_pairs) > 5:
            print(f"   ... 외 {len(qa_pairs) - 5}개 질문-답변\n")

    # 5. 증거 ID 생성
    print("🏷️ [증거 ID 생성]")
    total_evidence = len(resume_results) + len(jd_results) + len(qa_pairs)
    eid_list = [
        f"E{i:02d}" for i in range(1, max(total_evidence + 1, 15))
    ]  # 최소 15개
    print(f"   이력서 chunk: {len(resume_results)}개")
    print(f"   JD chunk: {len(jd_results)}개")
    print(f"   QA pairs: {len(qa_pairs)}개")
    print(f"   총 증거 ID: {len(eid_list)}개 ({eid_list[0]} ~ {eid_list[-1]})\n")

    # 6. 역량 ID 생성
    print("🎯 [역량 ID 생성]")
    comp_id_list = [f"C{i + 1:02d}" for i in range(len(axes_keys))]
    print(f"   총 {len(comp_id_list)}개:")
    for cid, key in zip(comp_id_list, axes_keys):
        print(f"   {cid}: {key}")

    print(f"\n{'=' * 80}")
    print("✅ [Retriever] 완료 - 요약")
    print(
        f"   이력서: {len(resume_chunks)}개 chunk ({len(resume_ctx)} 글자)"
    )
    print(f"   JD: {len(jd_chunks)}개 chunk ({len(jd_ctx)} 글자)")
    print(f"   인터뷰 로그: {len(log_ctx)} 글자, {len(qa_pairs)}개 QA")
    print(f"   증거 ID: {len(eid_list)}개")
    print(f"   역량 ID: {len(comp_id_list)}개")
    print(f"{'=' * 80}\n")

    return jd_ctx, resume_ctx, log_ctx, comp_id_list, eid_list, qa_pairs


# ==================== 인터뷰 분석 (Solar Reasoning + 디베이트 로그) ====================
class DebateTurn(BaseModel):
    """에이전트 A/B 디베이트 턴"""

    speaker: NonEmpty  # 예: "AgentA", "AgentB"
    content: NonEmpty


class InterviewAnalysisOut(BaseModel):
    """인터뷰 로그 분석 결과"""

    contradiction_score: Annotated[int, Ge(0), Le(100)]
    depth_score: Annotated[int, Ge(0), Le(100)]
    reliability_score: Annotated[int, Ge(0), Le(100)]
    positive_aspects: List[str] = Field(min_length=1)
    negative_aspects: List[str] = Field(min_length=1)
    final_comment: NonEmpty
    # 디베이트 패턴 대화 로그 (프론트에서 직접 사용)
    debate_log: List[DebateTurn] = Field(
        default_factory=list,
        description="AgentA/AgentB가 주고받은 디베이트 대화 턴 리스트",
    )


def _interview_analysis_prompt() -> ChatPromptTemplate:
    """인터뷰 분석 프롬프트 (Solar Reasoning)"""
    return ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "당신은 인터뷰 로그를 심층 분석하는 전문가입니다. Chain-of-Thought 추론을 통해 체계적으로 분석하세요.\n\n"
                "**분석 단계:**\n"
                "1. 답변 간 일관성 검토 (모순 여부)\n"
                "2. 답변의 깊이 평가 (구체성, 전문성)\n"
                "3. 신뢰도 판단 (과장, 회피, 진실성)\n\n"
                "**점수 기준:**\n"
                "- contradiction_score: 0(매우 일관적) ~ 100(매우 모순적)\n"
                "- depth_score: 0(피상적) ~ 100(매우 깊이 있음)\n"
                "- reliability_score: 0(신뢰 불가) ~ 100(매우 신뢰)\n\n"
                "**디베이트 방식 분석:**\n"
                "- 두 명의 가상 에이전트 AgentA, AgentB가 서로 토론하면서 평가합니다.\n"
                "- AgentA는 해당 포지션의 직무 담당 팀장(Hiring Manager) 역할로, 팀 성과, 당장 필요한 역량, 실무 기여도 중심으로 본다. “이 사람을 우리 팀에 넣었을 때 프로젝트/업무가 돌아가는가?”에 집중.\n"
                "- AgentB는  인사/조직 관점의 HR 평가 담당자 역할로, 조직 문화 적합성, 보상·직급 체계, 장기 성장 가능성, 리스크 관리에 집중. “회사 전체 관점에서 채용이 일관되고 안전한가?”를 본다.\n"
                "- 두 에이전트는 실제 회사 내부 채용위원회 회의를 시뮬레이션한다는 전제 하에, 서로의 논리를 보완·검증하는 방식으로 토론합니다.\n"
                "- 4~10턴 정도의 짧은 디베이트로 구성하고, 각 턴은 한 문단 이내로 작성합니다.\n\n"
                "**출력 필드:**\n"
                "- contradiction_score: int\n"
                "- depth_score: int\n"
                "- reliability_score: int\n"
                "- positive_aspects: 긍정적 측면 1-5개 (간결하게)\n"
                "- negative_aspects: 부정적 측면 1-5개 (간결하게)\n"
                "- final_comment: 종합 코멘트 (2-3문장)\n"
                "- debate_log: AgentA/AgentB 디베이트 로그 배열\n"
                "  예: [\n"
                "    {{\"speaker\": \"AgentA\", \"content\": \"로그 전반에서 깊이 있는 기술 설명이 부족한 것 같습니다.\"}},\n"
                "    {{\"speaker\": \"AgentB\", \"content\": \"일부 답변은 구체적인 수치와 사례가 부족합니다.\"}}\n"
                "  ]",
            ),
            (
                "human",
                "[인터뷰 로그]\n{log}\n\n"
                "[분석 힌트]\n{interview_query}\n\n"
                "위 로그를 단계별로 분석하고 결과를 JSON 형식으로 반환하세요.\n"
                "특히 AgentA/AgentB의 디베이트 과정을 debate_log 배열에 기록하세요.",
            ),
        ]
    )


async def analyze_interview(log_ctx: str, interview_query: str) -> InterviewAnalysisOut:
    """인터뷰 분석 (Solar Reasoning, async)"""
    chain = _interview_analysis_prompt() | _llm_solar_reasoning().with_structured_output(
        InterviewAnalysisOut, method="json_mode"
    )

    max_len = 4000  # 6000 → 4000으로 대폭 감소 (성능 개선)
    log_short = log_ctx[:max_len] + "..." if len(log_ctx) > max_len else log_ctx

    result = await chain.ainvoke(
        {
            "log": log_short,
            "interview_query": interview_query or "중요 질문/답변, 모순, 깊이에 주목해서 분석",
        }
    )

    return result


# ==================== 핵심역량 평가 (Solar Chat) ====================
class CompetencyEvalOut(BaseModel):
    scores: List[ScoreItem]
    weights: List[WeightItem]
    headline: Headline
    reasoning: List[str] = Field(default_factory=list)


def _competency_prompt() -> ChatPromptTemplate:
    """핵심역량 평가 프롬프트 (Solar Chat)"""
    return ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "당신은 엄격한 면접 평가 전문가입니다. 이력서와 인터뷰 로그를 바탕으로 후보자의 핵심역량을  **보수적이고 엄격하게** 평가하세요.\n\n"
                "각 역량은 한글일 수도 있습니다. key는 그대로 JSON에 사용하세요.\n"
                "**평가 원칙:**\n"
                "1. 실제 증거에 기반 (추측 금지)\n"
                "2. 구체적 성과와 수치 필요\n"
                "3. 증거 약하면 낮은 점수\n"
                "4. 가중치 합계 정확히 100\n"
                "5. 역량 간 명확한 차별화 (최소 20점 편차)\n\n"
                "**점수 기준:**\n"
                "- 0-30: 증거 없음 또는 매우 부족\n"
                "- 31-50: 기본적 수준, 구체성 부족\n"
                "- 51-65: 보통 수준, 일부 구체적 사례 있음\n"
                "- 66-75: 양호, 명확한 성과 입증\n"
                "- 76-85: 우수, 복수의 구체적 성과와 수치\n"
                "- 86-95: 탁월, 예외적인 성과와 깊이\n\n"
                "- 95-100: 최고 수준 (거의 부여하지 않음)\n\n"
                "**필수 출력:**\n"
                "- scores: List[{{key: str, value: int}}]\n"
                "- weights: List[{{key: str, value: int}}] (합계 100)\n"
                "- headline: {{summary: str, tag: List[str]}}\n"
                "- reasoning: List[str] (각 역량 평가 근거)",
            ),
            (
                "human",
                "**평가 축:** {axes_str}\n"
                "**사용자 요구사항:** {user_prompt}\n\n"
                "[이력서]\n{resume}\n\n"
                "[인터뷰로그]\n{log}\n\n"
                "위 자료를 바탕으로 역량을 평가하세요.\n\n"
                "결과를 JSON 형식으로 반환하세요.",
            ),
        ]
    )


async def evaluate_competency(
    resume: str, log: str, axes_keys: List[str], user_prompt: Optional[str]
) -> CompetencyEvalOut:
    """핵심역량 평가 (Solar Chat, async)"""
    chain = _competency_prompt() | _llm_solar_reasoning().with_structured_output(
        CompetencyEvalOut, method="json_mode"
    )

    max_len = 3500  # 5000 → 3500으로 감소 (성능 개선)
    resume_short = resume[:max_len] + "..." if len(resume) > max_len else resume
    log_short = log[:max_len] + "..." if len(log) > max_len else log

    result = await chain.ainvoke(
        {
            "axes_str": ", ".join(axes_keys),
            "user_prompt": user_prompt or "없음",
            "resume": resume_short,
            "log": log_short,
        }
    )

    # 가중치 정규화
    weights_dict = {w.key: w.value for w in result.weights}
    total_w = sum(weights_dict.values()) or 1
    if total_w != 100:
        factor = 100 / total_w
        weights_dict = {k: int(round(v * factor)) for k, v in weights_dict.items()}
        diff = 100 - sum(weights_dict.values())
        if diff != 0:
            max_key = max(weights_dict, key=weights_dict.get)
            weights_dict[max_key] += diff
        result.weights = [WeightItem(key=k, value=v) for k, v in weights_dict.items()]

    return result


# ==================== JD 커버리지 + 증거 매핑 (Solar Chat) ====================
class CompetencyEvidenceOut(BaseModel):
    competencyCoverage: List[JDCoverRow]
    evidence: List[EvidenceRow]


def _competency_evidence_prompt() -> ChatPromptTemplate:
    """증거 매핑 프롬프트 (Solar Chat)"""
    return ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "당신은 핵심역량과 증거를 매핑하는 전문가입니다.\n\n"
                "**작업:**\n"
                "1. 각 핵심역량에 대해 관련 증거(Exx)를 이력서와 인터뷰 로그에서 찾으세요\n"
                "2. 충족도는 증거의 강도에 따라 평가: 적합/보통/부족\n"
                "3. **모든 핵심역량은 최소 1개 이상의 EID를 갖도록 노력**\n"
                "4. 각 증거는 1-2문장으로 요약 (20-30단어)\n\n"
                "**필수 출력 형식 (CRITICAL):**\n"
                "- competencyCoverage: 배열(Array)\n"
                "  [{{\"jid\": \"C01\", \"요구사항\": \"문제해결 능력\", \"기대치\": \"복잡한 문제 해결\", \"충족도\": \"적합\", \"근거\": [\"E01\", \"E02\"]}}, ...]\n"
                "- evidence: 배열(Array)\n"
                "  [{{\"eid\": \"E01\", \"출처\": \"인터뷰로그 Q1\", \"내용\": \"SQL 최적화 경험\", \"jid\": \"C01\"}}, ...]\n"
                "- 근거 배열은 문자열 리스트여야 합니다. [{{\"EID\": \"E01\"}}] 형식 사용 금지.",
            ),
            (
                "human",
                "[핵심역량]\n{competencies}\n\n"
                "[이력서]\n{resume}\n\n"
                "[인터뷰로그]\n{log}\n\n"
                "역량 ID: {competency_details}\n"
                "사용 가능 EID: {eid_list}\n\n"
                "JSON 형태로 출력하세요.\n"
                "competencyCoverage와 evidence를 **배열(Array) 형식**으로 반환하세요.\n"
                "모든 내용을 한국어로 작성하세요.",
            ),
        ]
    )


async def map_competency_evidence(
    axes_keys: List[str], resume: str, log: str, eid_list: List[str]
) -> CompetencyEvidenceOut:
    """증거 매핑 (Solar Chat, async)"""
    competency_names = {
        "문제해결능력": "문제해결능력",
        "커뮤니케이션": "커뮤니케이션",
        "학습능력": "학습능력",
        "협업능력": "협업능력",
        "전문성": "전문성",
    }

    competency_details = "\n".join(
        [
            f"C{i + 1:02d} ({axes_keys[i]}): {competency_names.get(axes_keys[i], axes_keys[i])}"
            for i in range(len(axes_keys))
        ]
    )

    competencies_text = "\n".join(
        [
            f"{competency_names.get(key, key)}: {key.replace('_', ' ')}"
            for key in axes_keys
        ]
    )

    chain = _competency_evidence_prompt() | _llm_solar_reasoning().with_structured_output(
        CompetencyEvidenceOut, method="json_mode"
    )

    max_len = 3000  # 4000 → 3000으로 감소 (성능 개선)
    resume_short = resume[:max_len] + "..." if len(resume) > max_len else resume
    log_short = log[:max_len] + "..." if len(log) > max_len else log

    result = await chain.ainvoke(
        {
            "competencies": competencies_text,
            "resume": resume_short,
            "log": log_short,
            "competency_details": competency_details,
            "eid_list": ", ".join(eid_list),
        }
    )

    return result


# ==================== 인터뷰 요약 (Solar Chat) ====================
class QASummary(BaseModel):
    q_num: int | str
    question_short: NonEmpty
    answer_summary: NonEmpty


class InterviewSummaryOut(BaseModel):
    overall: str
    qa_summaries: List[QASummary] = Field(default_factory=list)
    positive: List[str] = Field(default_factory=list)
    negative: List[str] = Field(default_factory=list)
    stats: Dict[str, Any]


def _interview_summary_prompt() -> ChatPromptTemplate:
    """인터뷰 요약 프롬프트 (Solar Chat)"""
    return ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "당신은 인터뷰 분석 전문가입니다. 인터뷰 로그를 분석하여 요약하세요.\n\n"
                "**작업:**\n"
                "1. 전체 인터뷰 흐름을 2-3문장으로 요약 (overall)\n"
                "2. 각 질문에 대한 답변을 1~3줄로 요약 (qa_summaries)\n"
                "3. 긍정 의견: 추천하는 이유와 근거 200-300자, (positive: List[str])\n"
                "4. 부정 의견: 비추천하는 이유와 근거 200-300자, (negative: List[str])\n"
                "5. 통계: 총 질문 수, 평균 답변 길이 등 (stats)\n\n"
                "**qa_summaries 형식:**\n"
                "- 각 항목: q_num=1, question_short=\"자기소개?\", answer_summary=\"...\"\n"
                "- question_short는 5~10단어 이내\n"
                "- answer_summary는 1~3줄\n",
            ),
            (
                "human",
                "[인터뷰 로그]\n{log}\n\n"
                "[질문-답변 쌍]\n{qa_pairs}\n\n"
                "overall, qa_summaries(배열, 필수), positive, negative, stats를 모두 포함하는 JSON을 반환하세요.",
            ),
        ]
    )


async def summarize_interview(
    log: str, qa_pairs: List[Dict]
) -> InterviewSummaryOut:
    """인터뷰 요약 (Solar Reasoning, async)"""
    chain = _interview_summary_prompt() | _llm_solar_reasoning().with_structured_output(
        InterviewSummaryOut, method="json_mode"
    )

    qa_str = "\n\n".join(
        [
            f"Q{qa['q_num']}: {qa['question'][:100]}...\n"
            f"A{qa['q_num']}: {qa['answer'][:200]}..."
            for qa in qa_pairs
        ]
    )

    max_len = 3500  # 5000 → 3500으로 감소 (성능 개선)
    log_short = log[:max_len] + "..." if len(log) > max_len else log

    result = await chain.ainvoke({"log": log_short, "qa_pairs": qa_str})

    return result


# ==================== 검증 (Solar Reasoning) ====================
class ValidationResult(BaseModel):
    """검증 결과"""

    quality_score: Annotated[float, Ge(0.0), Le(1.0)]
    is_sufficient: bool
    grade: str  # A+, A, B+, B, C, D, F
    issues: List[str] = Field(default_factory=list)
    suggestions: List[str] = Field(default_factory=list)
    missing_aspects: List[str] = Field(default_factory=list)
    query_hint: Optional[str] = None


def _validation_prompt() -> ChatPromptTemplate:
    """검증 프롬프트 (Solar Reasoning)"""
    return ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "당신은 리포트 품질 검증 전문가입니다. 생성된 리포트를 엄격하게 평가하세요.\n\n"
                "**평가 항목:**\n"
                "1. 헤드라인 품질\n"
                "2. 요약 완성도\n"
                "3. 의견 균형성\n"
                "4. 역량 평가 타당성\n"
                "5. 증거 매핑 적절성\n\n"
                "**품질 기준:**\n"
                "- quality_score: 0.0~1.0\n"
                "- is_sufficient: 0.70 이상이면 True\n"
                "- grade: A+ (0.95+), A (0.85+), B+ (0.75+), B (0.65+), C (0.55+), D/F (0.55 미만)\n"
                "- issues, suggestions, missing_aspects, query_hint 포함",
            ),
            (
                "human",
                "[원본 데이터 요약]\n"
                "JD 길이: {jd_len}자\n"
                "이력서 길이: {resume_len}자\n"
                "인터뷰 로그 길이: {log_len}자\n\n"
                "[생성된 리포트]\n{report_summary}\n\n"
                "위 리포트의 품질을 평가하세요.\n"
                "품질이 부족하면 query_hint에 다음 검색에서 어떤 정보를 더 가져와야 하는지 명시하세요.\n\n"
                "결과를 JSON 형식으로 반환하세요.",
            ),
        ]
    )


def validate_report(
    report: Dict, jd_ctx: str, resume_ctx: str, log_ctx: str
) -> ValidationResult:
    """리포트 검증 (Solar Reasoning) - 품질 점수 0.7 미만 시 재시도"""
    import json

    chain = _validation_prompt() | _llm_gpt().with_structured_output(
        ValidationResult, method="json_mode"
    )

    report_summary = {
        "헤드라인": report.get("headline", {}),
        "점수": report.get("scores", []),
        "가중치": report.get("weights", []),
        "요약 항목 수": len(report.get("talkSummary", {}).get("items", [])),
        "JD 커버리지 수": len(report.get("jdCoverage", [])),
        "증거 수": len(report.get("evidence", [])),
    }

    result = chain.invoke(
        {
            "jd_len": len(jd_ctx or ""),
            "resume_len": len(resume_ctx or ""),
            "log_len": len(log_ctx or ""),
            "report_summary": json.dumps(report_summary, ensure_ascii=False, indent=2),
        }
    )

    return result


# ==================== LangGraph State 정의 ====================
class ReportState(TypedDict):
    """리포트 생성 워크플로우 상태"""

    # 입력 (고정)
    session_id: str
    axes_keys: List[str]
    user_prompt: Optional[str]

    # 질의 설계
    query_plan: Optional[Dict[str, Any]]
    query_hint: Optional[str]
    retry_count: int

    # 리트리버 결과
    jd_ctx: Optional[str]
    resume_ctx: Optional[str]
    log_ctx: Optional[str]
    comp_id_list: Optional[List[str]]
    eid_list: Optional[List[str]]
    qa_pairs: Optional[List[Dict]]

    # 분석 결과
    interview_analysis: Optional[Dict[str, Any]]
    competency_eval: Optional[Dict[str, Any]]
    evidence_mapping: Optional[Dict[str, Any]]
    interview_summary: Optional[Dict[str, Any]]

    # 검증 결과
    validation_result: Optional[Dict[str, Any]]

    # 최종 리포트
    report: Optional[Dict[str, Any]]

    # 에이전트 로그
    agent_logs: List[Dict[str, str]]

    # 에러
    error: Optional[str]


# ==================== LangGraph 노드 구현 ====================
def query_planner_node(state: ReportState) -> ReportState:
    '''노드 1: 질의 설계'''
    state["agent_logs"].append(
        {
            "agent": "질의설계",
            "message": f"사용자 요구사항 분석 중... (재시도: {state['retry_count']})",
        }
    )

    try:
        query_plan = design_queries(
            state["axes_keys"], state["user_prompt"], state.get("query_hint")
        )

        state["query_plan"] = query_plan.model_dump()
        state["agent_logs"].append(
            {
                "agent": "질의설계",
                "message": (
                    "✅ 질의 설계 완료\n"
                    f"   이력서: {query_plan.resume_query[:50]}...\n"
                    f"   JD: {query_plan.jd_query[:50]}...\n"
                    f"   근거: {query_plan.reasoning[:100]}..."
                ),
            }
        )
    except Exception as e:
        error_msg = str(e)
        if "timeout" in error_msg.lower() or "timed out" in error_msg.lower():
            state["error"] = (
                "질의 설계 타임아웃: AI 응답 시간이 초과되었습니다. 잠시 후 다시 시도해주세요."
            )
        else:
            state["error"] = f"질의 설계 오류: {error_msg}"
        state["agent_logs"].append(
            {
                "agent": "질의설계",
                "message": f"❌ 오류: {state['error']}",
            }
        )

    return state


def retriever_node(state: ReportState) -> ReportState:
    """노드 2: 리트리버"""
    state["agent_logs"].append(
        {
            "agent": "리트리버",
            "message": "VectorDB 및 인터뷰 로그 검색 중...",
        }
    )

    try:
        query_plan = QueryPlan(**state["query_plan"])
        (
            jd_ctx,
            resume_ctx,
            log_ctx,
            comp_id_list,
            eid_list,
            qa_pairs,
        ) = retrieve_contexts(state["session_id"], query_plan, state["axes_keys"])

        state["jd_ctx"] = jd_ctx
        state["resume_ctx"] = resume_ctx
        state["log_ctx"] = log_ctx
        state["comp_id_list"] = comp_id_list
        state["eid_list"] = eid_list
        state["qa_pairs"] = qa_pairs
        logger.debug(
            "[retriever_node] 완료 | resume_len=%d, jd_len=%d, log_len=%d, qa=%d, EID=%d, CID=%d",
            len(resume_ctx or ""),
            len(jd_ctx or ""),
            len(log_ctx or ""),
            len(qa_pairs or []),
            len(eid_list or []),
            len(comp_id_list or []),
        )

        state["agent_logs"].append(
            {
                "agent": "검색 에이전트",
                "message": (
                    "✅ 검색 완료 - "
                    f"이력서 {len(resume_ctx)}자, JD {len(jd_ctx)}자, 로그 {len(log_ctx)}자"
                ),
            }
        )
    except Exception as e:
        state["error"] = f"리트리버 오류: {str(e)}"
        state["agent_logs"].append(
            {
                "agent": "검색 에이전트",
                "message": f"❌ 오류: {str(e)}",
            }
        )

    return state



import asyncio

def analysis_parallel_node(state: ReportState) -> ReportState:
    """
    노드 3: 병렬 분석 노드 (동기 래퍼)
    - 인터뷰 분석(Reasoning)
    - 핵심역량 평가(Chat)
    - 증거 매핑(Chat)
    - 인터뷰 요약(Chat)
    을 asyncio.gather로 병렬 실행

    LangGraph는 동기 노드를 기대하므로, async 작업을 동기 함수로 래핑
    """
    state["agent_logs"].append(
        {
            "agent": "리포트 분석 에이전트",
            "message": "인터뷰 분석/역량 평가/증거 매핑/요약 병렬 실행 중...",
        }
    )

    async def _run_parallel_analysis():
        """실제 병렬 분석 로직 (async)"""
        # 실행 중인 task들을 추적하여 타임아웃 시 명시적으로 cancel
        tasks = []

        try:
            # --- 입력 준비 ---
            query_plan = (
                QueryPlan(**state["query_plan"]) if state.get("query_plan") else None
            )
            interview_query = query_plan.interview_query if query_plan else ""

            log_ctx = state.get("log_ctx") or ""
            resume_ctx = state.get("resume_ctx") or ""
            axes_keys = state.get("axes_keys") or []
            user_prompt = state.get("user_prompt")
            eid_list = state.get("eid_list") or []
            qa_pairs = state.get("qa_pairs") or []

            # --- 병렬 실행 (개별 타임아웃 제거, 전체 타임아웃만 사용) ---
            # 개별 Task에 타임아웃을 걸면 가장 짧은 Task가 먼저 실패하므로,
            # 전체 타임아웃만 사용하여 모든 Task가 최대한 완료되도록 함
            logger.debug("[analysis_parallel_node] 병렬 분석 시작 - 타임아웃: %d초", PARALLEL_ANALYSIS_TIMEOUT_SEC)

            task1 = asyncio.create_task(analyze_interview(log_ctx, interview_query))
            task2 = asyncio.create_task(evaluate_competency(resume_ctx, log_ctx, axes_keys, user_prompt))
            task3 = asyncio.create_task(map_competency_evidence(axes_keys, resume_ctx, log_ctx, eid_list))
            task4 = asyncio.create_task(summarize_interview(log_ctx, qa_pairs))

            tasks = [task1, task2, task3, task4]

            # 전체 타임아웃과 함께 gather 실행
            analysis, comp_eval, evidence_map, interview_sum = await asyncio.wait_for(
                asyncio.gather(*tasks),
                timeout=PARALLEL_ANALYSIS_TIMEOUT_SEC,
            )

            logger.debug("[analysis_parallel_node] 병렬 분석 완료")

            # --- State 저장 ---
            state["interview_analysis"] = analysis.model_dump()
            state["competency_eval"] = comp_eval.model_dump()
            state["evidence_mapping"] = evidence_map.model_dump()
            state["interview_summary"] = interview_sum.model_dump()

            # --- 로그 ---
            state["agent_logs"].append(
                {
                    "agent": "인터뷰 분석 에이전트",
                    "message": (
                        "✅ 인터뷰 분석 완료 - "
                        f"모순도 {analysis.contradiction_score}%, "
                        f"깊이 {analysis.depth_score}%, "
                        f"신뢰도 {analysis.reliability_score}%"
                    ),
                }
            )

            scores_str = ", ".join(
                [f"{s.key}: {s.value}점" for s in comp_eval.scores]
            )
            state["agent_logs"].append(
                {
                    "agent": "핵심역량평가 에이전트",
                    "message": f"✅ 평가 완료 - {scores_str}",
                }
            )

            state["agent_logs"].append(
                {
                    "agent": "증거매핑 에이전트",
                    "message": (
                        "✅ 매핑 완료 - "
                        f"역량 {len(evidence_map.competencyCoverage)}개, "
                        f"증거 {len(evidence_map.evidence)}개"
                    ),
                }
            )

            state["agent_logs"].append(
                {
                    "agent": "요약생성",
                    "message": (
                        "✅ 요약 완료 - "
                        f"QA {len(interview_sum.qa_summaries)}개 요약"
                    ),
                }
            )

        except asyncio.TimeoutError:
            # 타임아웃 발생 시 모든 pending task를 명시적으로 cancel하여 "Task pending" 경고 방지
            logger.warning("[analysis_parallel_node] 타임아웃 발생 (%d초), Task 정리 시작...", PARALLEL_ANALYSIS_TIMEOUT_SEC)

            # 어떤 Task가 완료되지 않았는지 로깅
            task_status = [
                ("인터뷰분석", task1),
                ("핵심역량평가", task2),
                ("증거매핑", task3),
                ("인터뷰요약", task4),
            ]
            for name, task in task_status:
                status = "완료" if task.done() else "미완료"
                logger.warning(f"  - {name}: {status}")

            for task in tasks:
                if not task.done():
                    task.cancel()

            # Task cancel 완료 대기 (최대 5초)
            try:
                await asyncio.wait_for(
                    asyncio.gather(*tasks, return_exceptions=True),
                    timeout=5.0
                )
                logger.debug("[analysis_parallel_node] Task 정리 완료")
            except asyncio.TimeoutError:
                logger.error("[analysis_parallel_node] Task 정리 타임아웃")

            state["error"] = f"병렬 분석 타임아웃: {PARALLEL_ANALYSIS_TIMEOUT_SEC}초 안에 분석을 완료하지 못했습니다."
            state["agent_logs"].append(
                {
                    "agent": "병렬 분석 에이전트",
                    "message": f"❌ 오류: {state['error']}",
                }
            )
        except Exception as e:
            # 예외 발생 시에도 pending task cleanup
            logger.warning("[analysis_parallel_node] 예외 발생, Task 정리 시작: %s", str(e))
            for task in tasks:
                if not task.done():
                    task.cancel()

            try:
                await asyncio.wait_for(
                    asyncio.gather(*tasks, return_exceptions=True),
                    timeout=5.0
                )
                logger.debug("[analysis_parallel_node] Task 정리 완료")
            except asyncio.TimeoutError:
                logger.error("[analysis_parallel_node] Task 정리 타임아웃")

            error_msg = str(e)
            state["error"] = f"병렬 분석 노드 오류: {error_msg}"
            state["agent_logs"].append(
                {
                    "agent": "병렬 분석 에이전트",
                    "message": f"❌ 오류: {state['error']}",
                }
            )

        return state

    # 동기 함수에서 async 함수 실행
    # 기존 이벤트 루프 확인
    try:
        loop = asyncio.get_running_loop()
        # 이미 실행 중인 루프가 있으면 새 스레드에서 실행
        logger.debug("[analysis_parallel_node] 기존 이벤트 루프 감지, 새 루프 생성")
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(
                lambda: asyncio.run(_run_parallel_analysis())
            )
            return future.result()
    except RuntimeError:
        # 실행 중인 루프가 없으면 새로 만들어서 실행
        logger.debug("[analysis_parallel_node] 새 이벤트 루프에서 실행")
        try:
            loop = asyncio.get_event_loop()
            if loop.is_closed():
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        return loop.run_until_complete(_run_parallel_analysis())



def validation_node(state: ReportState) -> ReportState:
    """노드 4: 검증 및 리포트 조립"""
    state["agent_logs"].append(
        {
            "agent": "검증 에이전트",
            "message": "리포트 조립 및 품질 검증 중 (Solar Reasoning)...",
        }
    )

    try:
        comp_eval = state["competency_eval"] or {}
        evidence_map = state["evidence_mapping"] or {}
        interview_sum = state["interview_summary"] or {}
        interview_analysis = state["interview_analysis"] or {}
        
        comp_reasoning = comp_eval.get("reasoning") or []

        # talkSummary 조립
                # talkSummary 조립
        def _to_text(x):
            if isinstance(x, str):
                return x
            if isinstance(x, list):
                # 리스트면 줄바꿈으로 합침
                return "\n".join(str(i) for i in x)
            return str(x)

        talk_items: List[Dict[str, str]] = []

        # overall
        overall_raw = interview_sum.get("overall") or ""
        overall = _to_text(overall_raw)
        if overall.strip():
            talk_items.append({"주제": "인터뷰 요약", "발언요약": overall})

        # Q/A 요약
        for qa in interview_sum.get("qa_summaries", []) or []:
            q = (qa.get("question_short") or "질문").strip() or "질문"
            a_raw = qa.get("answer_summary") or "답변"
            a = _to_text(a_raw)
            talk_items.append({"주제": q, "발언요약": a})

        # 긍/부정 의견 (List[str] 또는 str 모두 허용)
        positive_raw = interview_sum.get("positive") or []
        positive_text = _to_text(positive_raw)
        if positive_text.strip():
            talk_items.append({"주제": "긍정 의견", "발언요약": positive_text})

        negative_raw = interview_sum.get("negative") or []
        negative_text = _to_text(negative_raw)
        if negative_text.strip():
            talk_items.append({"주제": "부정 의견", "발언요약": negative_text})

        # 인터뷰 분석 코멘트 (혹시라도 나중에 리스트로 나와도 방어)
        final_comment_raw = interview_analysis.get("final_comment") or ""
        final_comment = _to_text(final_comment_raw)
        if final_comment.strip():
            talk_items.append({"주제": "AI 분석", "발언요약": final_comment})

        if not talk_items:
            talk_items.append(
                {
                    "주제": "요약 없음",
                    "발언요약": "요약 데이터를 생성하지 못했습니다.",
                }
            )


        axes_keys = state["axes_keys"] or []
        report: Dict[str, Any] = {
            "axes": [
                {
                    "key": k,
                    "label": k.replace("_", " ").title(),
                }
                for k in axes_keys
            ],
            "scores": comp_eval.get("scores", []),
            "weights": comp_eval.get("weights", []),
            "headline": {
                **(comp_eval.get("headline") or {}),
                "contradiction_score": interview_analysis.get(
                    "contradiction_score"
                ),
                "depth_score": interview_analysis.get("depth_score"),
                "reliability_score": interview_analysis.get(
                    "reliability_score"
                ),
            },
            "talkSummary": {"items": talk_items},
            "convStats": [
                {"k": str(k), "v": str(v)}
                for k, v in (interview_sum.get("stats") or {}).items()
            ],
            "jdCoverage": evidence_map.get("competencyCoverage", []),
            "evidence": evidence_map.get("evidence", []),
            "competency_reasoning": comp_reasoning,
        }

        # 품질 검증
        validation = validate_report(
            report,
            state.get("jd_ctx") or "",
            state.get("resume_ctx") or "",
            state.get("log_ctx") or "",
        )
        state["validation_result"] = validation.model_dump()

        state["agent_logs"].append(
            {
                "agent": "검증 에이전트",
                "message": (
                    f"품질: {validation.quality_score:.2f} | 등급: {validation.grade} | "
                    f"{'✅ 통과' if validation.is_sufficient else '⚠️ 재시도 필요'}"
                ),
            }
        )

        # 품질 부족 시 재시도 (최대 2회)
        if not validation.is_sufficient and state["retry_count"] < 2:
            hint = validation.query_hint or {}
            state["query_hint"] = hint.get("resume_query") if isinstance(hint, dict) else str(hint)
            state["agent_logs"].append(
                {"agent": "검증 에이전트", "message": f"재시도 {state['retry_count'] + 1}/2"}
            )
        else:
            # 최종 스키마 검증 (완화된 ReportOut 기준)
            try:
                ReportOut(**report)
            except ValidationError as ve:
                state["error"] = f"스키마 검증 실패: {str(ve)}"
                state["agent_logs"].append(
                    {
                        "agent": "검증 에이전트",
                        "message": "❌ 스키마 검증 실패",
                    }
                )
                return state

            # 성공 시 메타데이터 및 디베이트 로그 첨부
            report.update(
                {
                    "status": "ok",
                    "report_id": uuid4().hex,
                    "created_at": datetime.now(timezone.utc)
                    .isoformat()
                    .replace("+00:00", "Z"),
                    "validation": validation.model_dump(),
                }
            )

            # 인터뷰 분석 디베이트 로그를 최종 리포트에도 포함 (스키마 외 필드)
            debate_log = interview_analysis.get("debate_log") or []
            report["debate_log"] = debate_log

            state["report"] = report
            state["agent_logs"].append(
                {
                    "agent": "검증 에이전트",
                    "message": "✅ 리포트 생성 완료!",
                }
            )

    except Exception as e:
        error_msg = str(e)
        if "timeout" in error_msg.lower() or "timed out" in error_msg.lower():
            state["error"] = (
                "검증 타임아웃: AI 응답 시간이 초과되었습니다. 잠시 후 다시 시도해주세요."
            )
        else:
            state["error"] = f"검증 오류: {error_msg}"
        state["agent_logs"].append(
            {
                "agent": "검증 에이전트",
                "message": f"❌ 오류: {state['error']}",
            }
        )

    return state


def retry_router_node(state: ReportState) -> str:
    """노드 5: 재시도 라우터"""
    validation = state.get("validation_result")

    if not validation:
        return "end"

    if validation["is_sufficient"] or state["retry_count"] >= 2:
        return "end"
    else:
        state["retry_count"] += 1
        return "retry"


# ==================== LangGraph 워크플로우 구성 ====================
def build_report_graph() -> StateGraph:
    """리포트 생성 LangGraph 구성"""
    logger.debug("[build_report_graph] LangGraph 워크플로우 컴파일 시작")
    workflow = StateGraph(ReportState)

    # 노드 추가
    workflow.add_node("query_planner", query_planner_node)
    workflow.add_node("retriever", retriever_node)
    workflow.add_node("analysis", analysis_parallel_node)
    workflow.add_node("validation", validation_node)

    # 엣지 정의 (리트리버 이후 병렬 분석 노드 1개로 묶음)
    workflow.set_entry_point("query_planner")
    workflow.add_edge("query_planner", "retriever")
    workflow.add_edge("retriever", "analysis")
    workflow.add_edge("analysis", "validation")

    # 조건부 라우팅
    workflow.add_conditional_edges(
        "validation",
        retry_router_node,
        {
            "retry": "query_planner",
            "end": END,
        },
    )

    return workflow.compile()


# ==================== 메인 생성 함수 ====================
async def create_report_async(
    session_id: str, axes_keys: List[str], user_prompt: Optional[str] = None
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    리포트 생성 - LangGraph 기반 (비동기 스트리밍)

    Args:
        session_id: 세션 ID
        axes_keys: 핵심역량 키 리스트
        user_prompt: 사용자 커스텀 프롬프트 (선택)

    Yields:
        - type: "log"    -> 에이전트 진행 로그
        - type: "debate" -> 인터뷰 분석 A/B 디베이트 턴 리스트
        - type: "error"  -> 에러 메시지
        - type: "report" -> 최종 리포트
    """
    logger.debug(
        "[create_report_async] 시작 | session_id=%s, axes_keys=%d, user_prompt=%s",
        session_id,
        len(axes_keys or []),
        "있음" if user_prompt else "없음",
    )
    graph = build_report_graph()
    logger.debug("[create_report_async] 그래프 생성 완료")

    initial_state: ReportState = {
        "session_id": session_id,
        "axes_keys": axes_keys,
        "user_prompt": user_prompt,
        "query_plan": None,
        "query_hint": None,
        "retry_count": 0,
        "jd_ctx": None,
        "resume_ctx": None,
        "log_ctx": None,
        "comp_id_list": None,
        "eid_list": None,
        "qa_pairs": None,
        "interview_analysis": None,
        "competency_eval": None,
        "evidence_mapping": None,
        "interview_summary": None,
        "validation_result": None,
        "report": None,
        "agent_logs": [],
        "error": None,
    }

    # 디베이트 로그는 한 번만 스트리밍
    logger.debug("[create_report_async] 초기 상태 준비 완료, 스트리밍 시작")
    debate_sent = False

    async for event in graph.astream(initial_state):
        for _, state in event.items():
            # 진행 로그 스트리밍 (마지막 1개)
            if "agent_logs" in state and state["agent_logs"]:
                new_logs = state["agent_logs"][-1:]
                for log in new_logs:
                    logger.debug(
                        "[create_report_async] 로그 이벤트 | agent=%s, message='%s...'",
                        log.get("agent"),
                        (log.get("message") or "")[:80],
                    )
                    yield {
                        "type": "log",
                        "agent": log["agent"],
                        "message": log["message"],
                    }

            # 디베이트 로그 스트리밍 (인터뷰 분석 완료 시 1회)
            if (
                not debate_sent
                and state.get("interview_analysis")
                and isinstance(state["interview_analysis"], dict)
            ):
                debate = state["interview_analysis"].get("debate_log") or []
                if debate:
                    debate_sent = True
                    logger.debug(
                        "[create_report_async] 디베이트 로그 전송 | turns=%d",
                        len(debate),
                    )
                    yield {
                        "type": "debate",
                        "agent": "인터뷰분석",
                        "turns": debate,
                    }

            # 에러 발생 시 종료
            if state.get("error"):
                logger.debug("[create_report_async] 오류 이벤트 | message=%s", state["error"])
                yield {
                    "type": "error",
                    "message": state["error"],
                }
                return

            # 최종 리포트
            if state.get("report"):
                logger.debug("[create_report_async] 리포트 이벤트 전송")
                yield {
                    "type": "report",
                    "data": state["report"],
                }


# ==================== 동기 래퍼 (기존 호환성) ====================
def create_report(
    session_id: str,
    axes_keys: List[str],
    resume_text: str = "",
    jd_text: str = "",
    log_text: str = "",
    user_prompt: Optional[str] = None,
) -> Dict[str, Any]:
    """
    리포트 생성 - 동기 버전 (기존 호환성 유지)

    Args:
        session_id: 세션 ID
        axes_keys: 핵심역량 키 리스트
        resume_text: 폴백용 (현재 사용하지 않음)
        jd_text: 폴백용 (현재 사용하지 않음)
        log_text: 폴백용 (현재 사용하지 않음)
        user_prompt: 사용자 커스텀 프롬프트 (선택)

    Returns:
        리포트 결과 딕셔너리 (ReportOut 스키마 기반, + debate_log 등 메타 필드 포함)
    """
    logger.debug(
        "[create_report] 시작 | session_id=%s, axes_keys=%d, user_prompt=%s",
        session_id,
        len(axes_keys or []),
        "있음" if user_prompt else "없음",
    )
    import asyncio as _asyncio

    async def _run():
        final_report = None
        async for event in create_report_async(session_id, axes_keys, user_prompt):
            if event["type"] == "error":
                logger.debug("[create_report] 오류 수신 | message=%s", event["message"])
                return _error("AGENT_ERROR", event["message"])
            elif event["type"] == "report":
                final_report = event["data"]
                logger.debug(
                    "[create_report] 리포트 수신 완료 | report_id=%s",
                    final_report.get("report_id"),
                )

        return final_report or _error("NO_REPORT", "리포트 생성 실패")

    try:
        loop = _asyncio.get_event_loop()
    except RuntimeError:
        loop = _asyncio.new_event_loop()
        _asyncio.set_event_loop(loop)

    return loop.run_until_complete(_run())


# ==================== 사용 예시 ====================
if __name__ == "__main__":
    # 예시 1: 기본 사용
    result = create_report(
        session_id="test_session_123",
        axes_keys=[
            "문제해결",
            "커뮤니케이션",
            "학습능력",
            "협업능력",
            "전문성",
        ],
    )
    print(result)

    # 예시 2: 사용자 프롬프트 포함
    result = create_report(
        session_id="test_session_123",
        axes_keys=[
            "문제해결",
            "커뮤니케이션",
            "학습능력",
            "협업능력",
            "전문성",
        ],
        user_prompt=(
            "특히 기술적 깊이와 문제해결 능력을 중점적으로 평가해주세요. "
            "Spring Boot, Docker, AWS 경험이 얼마나 구체적인지 확인하고 싶습니다."
        ),
    )
    print(result)
