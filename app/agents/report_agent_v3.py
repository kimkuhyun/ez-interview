"""
리포트 생성 에이전트 V3 - 포트폴리오 통합 + 디베이트 검증 패턴 + PDF 템플릿 지원

개선 사항 요약:
1. 포트폴리오 입력 통합: 이력서, JD와 함께 포트폴리오도 벡터 검색 및 분석에 사용
2. 디베이트 패턴 검증: analysis_parallel 이후 각 분석 결과를 디베이트 방식으로 검증
3. PDF 템플릿 지원: HTML 템플릿에 필요한 모든 필드 생성 (지원자명, 포지션, 일자, 채용권고 등)
4. 사용자 프롬프트 렌즈 최적화: 단계적 분석을 통해 프롬프트를 렌즈 관점으로 변환
5. 에이전트 로그에 검증 대화 추가: 디베이트 과정을 실시간 스트리밍

구조:
query_planner → retriever → analysis_parallel → debate_validation → final_validation → (retry or end)
"""

from __future__ import annotations
from typing import List, Dict, Optional, Any, AsyncGenerator
from uuid import uuid4
from datetime import datetime, timezone
import os
import asyncio

from pydantic import BaseModel, Field, ValidationError, StringConstraints, model_validator
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

# Upstage Solar Pro 2 설정
SOLAR_MODEL = os.getenv("SOLAR_MODEL", "solar-pro2")
SOLAR_BASE_URL = "https://api.upstage.ai/v1"

# 병렬 분석 타임아웃
PARALLEL_ANALYSIS_TASK_TIMEOUT_SEC = int(
    os.getenv("PARALLEL_ANALYSIS_TASK_TIMEOUT_SEC", "90")
)
PARALLEL_ANALYSIS_TIMEOUT_SEC = int(os.getenv("PARALLEL_ANALYSIS_TIMEOUT_SEC", "300"))

# 디베이트 검증 타임아웃
DEBATE_VALIDATION_TIMEOUT_SEC = int(os.getenv("DEBATE_VALIDATION_TIMEOUT_SEC", "120"))


def _llm_gpt() -> ChatOpenAI:
    """GPT-4o 모델"""
    return ChatOpenAI(
        model=OPENAI_MODEL,
        timeout=90,
        api_key=OPENAI_API_KEY,
    )


def _llm_solar_chat() -> ChatOpenAI:
    """Solar Pro 2 Chat 모드"""
    return ChatOpenAI(
        model=SOLAR_MODEL,
        temperature=0.1,
        timeout=90,
        api_key=SOLAR_API_KEY,
        base_url=SOLAR_BASE_URL,
    )


def _llm_solar_planner() -> ChatOpenAI:
    """질의 설계 전용 Solar"""
    return ChatOpenAI(
        model=SOLAR_MODEL,
        temperature=0.1,
        max_retries=2,
        timeout=60,
        api_key=SOLAR_API_KEY,
        base_url=SOLAR_BASE_URL,
    )


def _llm_solar_reasoning() -> ChatOpenAI:
    """Solar Pro 2 Reasoning 모드"""
    return ChatOpenAI(
        model=SOLAR_MODEL,
        temperature=0.1,
        timeout=60,
        api_key=SOLAR_API_KEY,
        base_url=SOLAR_BASE_URL,
        reasoning_effort="high"
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
    summary: NonEmpty  # 기존 1줄 요약 (헤드라인용)
    overall_summary: NonEmpty = Field(
        description="인터뷰 전반에 대한 평가 (150-200자)"
    )
    tag: List[str] = Field(min_length=1, max_length=5, description="키워드 태그 3-5개")
    contradiction_score: Optional[Score] = Field(
        default=None, description="답변 모순도 (0=일관적, 100=모순적)"
    )
    contradiction_reason: Optional[str] = Field(
        default=None, description="모순 정도 점수 이유 (1-2문장)"
    )
    depth_score: Optional[Score] = Field(
        default=None, description="답변 깊이 (0=피상적, 100=깊이있음)"
    )
    depth_reason: Optional[str] = Field(
        default=None, description="대화 깊이 점수 이유 (1-2문장)"
    )
    reliability_score: Optional[Score] = Field(
        default=None, description="신뢰도 (0=낮음, 100=높음)"
    )
    reliability_reason: Optional[str] = Field(
        default=None, description="리포트 신뢰도 점수 이유 (1-2문장)"
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
    충족도: NonEmpty = "정보 부족"
    근거: List[E_ID]

    @model_validator(mode="before")
    @classmethod
    def _normalize_fields(cls, data: Any):
        """
        LLM이 필드명을 잘못 생성하는 경우를 보정:
        - '충도도' → '충족도'
        """
        if isinstance(data, dict):
            if "충도도" in data and "충족도" not in data:
                data = dict(data)
                data["충족도"] = data.pop("충도도")
        return data


class EvidenceRow(BaseModel):
    eid: E_ID
    출처: NonEmpty
    내용: NonEmpty
    jid: Optional[COMP_ID] = None

    @model_validator(mode="before")
    @classmethod
    def _normalize_jid(cls, data: Any):
        """
        LLM이 jid를 빈 문자열 등으로 반환하는 경우 None으로 정규화하여
        패턴 검증 오류를 방지.
        """
        if isinstance(data, dict):
            jid = data.get("jid")
            if isinstance(jid, str) and not jid.strip():
                data = dict(data)
                data["jid"] = None
        return data


class ConvKV(BaseModel):
    k: str
    v: str


class CompetencyComment(BaseModel):
    """역량별 코멘트"""
    key: KeyStr  # 역량 키 (예: "technical_depth")
    comment: NonEmpty = Field(
        description="역량별 점수 이유 (2-3문장)"
    )


class QuestionTypeRatio(BaseModel):
    """질문 유형별 비율"""
    type: NonEmpty  # 예: "사실관계파악", "기술질문", "커뮤니케이션질문"
    ratio: str  # 예: "35%"


class InterviewStats(BaseModel):
    """대화 통계"""
    duration: str = Field(description="면접 시간 (예: 45분)")
    followup_ratio: str = Field(description="질문당 후속 질문 비율 (예: 2.3회)")
    question_type_ratios: List[QuestionTypeRatio] = Field(
        default_factory=list,
        description="질문 유형별 비율 (사실관계파악, 기술질문, 커뮤니케이션질문 등)"
    )


class ReportMetadata(BaseModel):
    """PDF 템플릿용 메타데이터"""
    candidate_name: NonEmpty
    position_applied: NonEmpty  # JD에서 추출
    interview_date: str  # YYYY-MM-DD
    report_date: str  # YYYY-MM-DD
    session_id: NonEmpty
    interviewer_name: Optional[str] = "AI 평가 시스템"


class FinalRecommendation(BaseModel):
    """최종 코멘트 및 채용 권고"""
    final_comment: NonEmpty = Field(
        description="최종 코멘트 (250-300자)"
    )
    hiring_decision: NonEmpty  # 예: "조건부 채용 (3개월 수습 후 정규 전환 검토)"
    decision_reasons: List[str] = Field(min_length=1, max_length=5)


class ReportOut(BaseModel):
    """최종 리포트 출력 스키마 (PDF 템플릿 지원)"""

    # 기본 정보
    metadata: ReportMetadata

    # 축 개수는 1개 이상
    axes: List[Competency] = Field(min_length=1)
    scores: List[ScoreItem] = Field(min_length=1)
    weights: List[WeightItem] = Field(min_length=1)

    headline: Headline
    talkSummary: TalkSummary

    # 통계/커버리지
    convStats: List[ConvKV] = Field(default_factory=list)
    jdCoverage: List[JDCoverRow] = Field(default_factory=list)
    evidence: List[EvidenceRow] = Field(default_factory=list)

    # 역량별 코멘트
    competency_comments: List[CompetencyComment] = Field(
        default_factory=list,
        description="각 역량별 점수 이유 (2-3문장)"
    )

    # 대화 통계
    interview_stats: Optional[InterviewStats] = Field(
        default=None,
        description="면접 시간, 질문 비율 등 통계"
    )

    # 최종 권고
    recommendation: FinalRecommendation


# ==================== 질의 설계 스키마 ====================
class QueryPlan(BaseModel):
    """질의 설계 에이전트 출력 (포트폴리오 추가)"""
    resume_query: NonEmpty
    jd_query: NonEmpty
    portfolio_query: Optional[NonEmpty] = None  # 포트폴리오 검색용
    interview_query: NonEmpty
    focus_areas: List[str] = Field(default_factory=list)
    reasoning: NonEmpty


class ValidationFeedback(BaseModel):
    """검증 피드백"""
    quality_score: Annotated[float, Ge(0.0), Le(1.0)]
    is_sufficient: bool
    missing_aspects: List[str] = Field(default_factory=list)
    query_hint: Optional[str] = None


# ==================== 디베이트 패턴 스키마 ====================
class DebateTurn(BaseModel):
    """에이전트 디베이트 턴"""
    speaker: NonEmpty  # "AgentA" 또는 "AgentB"
    role: NonEmpty  # "Hiring Manager" 또는 "HR Evaluator"
    content: NonEmpty


class DebateLog(BaseModel):
    """디베이트 로그"""
    topic: NonEmpty  # 예: "인터뷰 분석 검증"
    turns: List[DebateTurn] = Field(min_length=1)
    conclusion: NonEmpty


class DebateValidationResult(BaseModel):
    """디베이트 검증 결과"""
    is_valid: bool
    quality_score: Annotated[float, Ge(0.0), Le(1.0)]
    issues: List[str] = Field(default_factory=list)
    suggestions: List[str] = Field(default_factory=list)
    debate_log: DebateLog


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


# ==================== 사용자 프롬프트 렌즈 최적화 ====================
class OptimizedPrompt(BaseModel):
    """최적화된 사용자 프롬프트"""
    original_prompt: str
    lens_perspective: NonEmpty  # 렌즈 관점으로 변환된 프롬프트
    key_focus_areas: List[str] = Field(min_length=1, max_length=5)
    reasoning: NonEmpty


def _prompt_optimizer_prompt() -> ChatPromptTemplate:
    """사용자 프롬프트를 렌즈 관점으로 최적화"""
    return ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "당신은 채용 평가 프롬프트 최적화 전문가입니다.\n\n"
                "**중요: 모든 분석과 출력은 반드시 한국어로 작성하세요.**\n\n"
                "**역할:**\n"
                "사용자가 입력한 자유로운 형식의 프롬프트를 분석하고,\n"
                "이를 '평가 렌즈(evaluation lens)' 관점으로 변환합니다.\n\n"
                "**렌즈 관점이란:**\n"
                "- 단순 키워드 나열이 아닌, '어떤 관점에서 평가할 것인가'에 초점\n"
                "- 예: '코딩 실력' → '문제 해결 시 코드 품질과 최적화 전략을 얼마나 고려하는가'\n"
                "- 예: 'AWS 경험' → '클라우드 인프라 설계 시 비용/확장성/보안을 균형있게 고려하는가'\n\n"
                "**변환 단계:**\n"
                "1. 사용자 프롬프트에서 핵심 키워드 추출\n"
                "2. 각 키워드를 평가 질문/관점으로 변환\n"
                "3. 5개 이하의 핵심 초점 영역(key_focus_areas)으로 정리\n"
                "4. 최종적으로 벡터 검색과 LLM 분석에 적합한 형태로 재구성\n\n"
                "**출력:**\n"
                "- original_prompt: 원본 (string)\n"
                "- lens_perspective: 렌즈 관점으로 변환된 프롬프트 (string, 2-3문장)\n"
                "- key_focus_areas: 핵심 초점 영역 리스트 (array of strings)\n"
                "- reasoning: 변환 근거 (string, 2-3문장)",
            ),
            (
                "human",
                "사용자 프롬프트:\n{user_prompt}\n\n"
                "평가 축:\n{axes_keys}\n\n"
                "위 프롬프트를 렌즈 관점으로 최적화하여 JSON 형식으로 반환하세요.",
            ),
        ]
    )


def optimize_user_prompt(user_prompt: str, axes_keys: List[str]) -> OptimizedPrompt:
    """사용자 프롬프트 최적화 (Solar Reasoning)"""
    if not user_prompt or user_prompt.strip() == "":
        # 프롬프트가 없으면 기본 렌즈 생성
        return OptimizedPrompt(
            original_prompt="",
            lens_perspective=f"제출된 이력서, JD, 인터뷰 로그를 바탕으로 {', '.join(axes_keys)} 관점에서 후보자의 역량을 종합적으로 평가하세요.",
            key_focus_areas=axes_keys[:5],
            reasoning="사용자 프롬프트가 없어 기본 평가 렌즈를 생성했습니다.",
        )

    chain = _prompt_optimizer_prompt() | _llm_solar_reasoning().with_structured_output(
        OptimizedPrompt, method="json_mode"
    )

    result = chain.invoke(
        {
            "user_prompt": user_prompt,
            "axes_keys": ", ".join(axes_keys),
        }
    )

    return result


# ==================== 질의 설계 에이전트 (포트폴리오 통합) ====================
def _query_planner_prompt() -> ChatPromptTemplate:
    """질의 설계 프롬프트 (포트폴리오 추가)"""
    return ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "당신은 벡터 검색 질의 설계 에이전트입니다.\n\n"
                "한국어로 작성하세요\n\n"
                "**역할:**\n"
                "- 이력서, JD, 포트폴리오, 인터뷰 로그에서 평가에 필요한 정보를 찾을 수 있도록 질의를 설계합니다.\n"
                "- 최적화된 렌즈 관점(lens_perspective)을 반영하여 질의를 구성합니다.\n"
                "- 포트폴리오가 있으면 portfolio_query를 별도로 설계합니다.\n\n"
                "**출력 필드:**\n"
                "- resume_query: string\n"
                "- jd_query: string\n"
                "- portfolio_query: string (포트폴리오가 있을 때만)\n"
                "- interview_query: string\n"
                "- focus_areas: string 리스트 (최대 5개)\n"
                "- reasoning: string\n",
            ),
            (
                "human",
                "평가 축(axes_keys): {axes_keys}\n"
                "렌즈 관점(lens_perspective): {lens_perspective}\n"
                "핵심 초점(key_focus_areas): {key_focus_areas}\n"
                "포트폴리오 존재 여부: {has_portfolio}\n"
                "이전 피드백(query_hint): {query_hint}\n\n"
                "위 정보를 반영하여 벡터검색에 사용할 질의를 설계하세요.\n"
                "JSON 형식으로 출력하세요.",
            ),
        ]
    )


def design_queries(
    axes_keys: List[str],
    lens_perspective: str,
    key_focus_areas: List[str],
    has_portfolio: bool,
    query_hint: Optional[str] = None,
) -> QueryPlan:
    """질의 설계 (포트폴리오 포함)"""
    chain = _query_planner_prompt() | _llm_solar_planner().with_structured_output(
        QueryPlan, method="json_mode"
    )

    result = chain.invoke(
        {
            "axes_keys": ", ".join(axes_keys),
            "lens_perspective": lens_perspective,
            "key_focus_areas": ", ".join(key_focus_areas),
            "has_portfolio": "있음" if has_portfolio else "없음",
            "query_hint": query_hint or "[NO_QUERY_HINT]",
        }
    )

    return result


# ==================== 리트리버 (포트폴리오 통합) ====================
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
    session_id: str, query_plan: QueryPlan, axes_keys: List[str], has_portfolio: bool
) -> tuple:
    """
    VectorDB와 interview_store를 활용한 컨텍스트 구성 (포트폴리오 통합)

    Returns:
        (jd_ctx, resume_ctx, portfolio_ctx, log_ctx, comp_id_list, eid_list, qa_pairs)
    """
    print(f"\n{'=' * 80}")
    print("🔍 [Retriever] 시작 (포트폴리오 포함)")
    print(f"   Session ID: {session_id}")
    print(f"   핵심역량: {', '.join(axes_keys)}")
    print(f"   포트폴리오: {'있음' if has_portfolio else '없음'}")
    print(f"{'=' * 80}\n")

    # 초기화
    resume_chunks: List[str] = []
    jd_chunks: List[str] = []
    portfolio_chunks: List[str] = []
    resume_results: List = []
    jd_results: List = []
    portfolio_results: List = []

    # 1. 이력서 검색
    print("📄 [이력서 검색]")
    print(f"   질의: '{query_plan.resume_query}'")
    try:
        resume_results = search_similar_chunks(
            query_plan.resume_query,
            session_id=session_id,
            doc_type="resume",
            top_k=10,
        )
        # search_similar_chunks 는 dict 리스트를 반환: {"content", "doc_type", "score"}
        resume_chunks = [
            r.get("content", "")
            for r in resume_results
            if r.get("doc_type") == "resume" and r.get("content")
        ]
        resume_ctx = "\n\n".join(resume_chunks) if resume_chunks else ""
        print(f"    이력서 chunk {len(resume_chunks)}개 선택 (총 {len(resume_ctx)} 글자)\n")
    except Exception as e:
        print(f"   ⚠️ 이력서 검색 실패: {e}\n")
        resume_ctx = ""

    # 2. JD 검색
    print("📋 [JD 검색]")
    print(f"   질의: '{query_plan.jd_query}'")
    try:
        jd_results = search_similar_chunks(
            query_plan.jd_query,
            session_id=session_id,
            doc_type="jd",
            top_k=10,
        )
        jd_chunks = [
            r.get("content", "")
            for r in jd_results
            if r.get("doc_type") == "jd" and r.get("content")
        ]
        jd_ctx = "\n\n".join(jd_chunks) if jd_chunks else ""
        print(f"    JD chunk {len(jd_chunks)}개 선택 (총 {len(jd_ctx)} 글자)\n")
    except Exception as e:
        print(f"   ⚠️ JD 검색 실패: {e}\n")
        jd_ctx = ""

    # 3. 포트폴리오 검색 (있을 경우)
    portfolio_ctx = ""
    if has_portfolio and query_plan.portfolio_query:
        print("📁 [포트폴리오 검색]")
        print(f"   질의: '{query_plan.portfolio_query}'")
        try:
            portfolio_results = search_similar_chunks(
                query_plan.portfolio_query,
                session_id=session_id,
                doc_type="portfolio",
                top_k=10,
            )
            portfolio_chunks = [
                r.get("content", "")
                for r in portfolio_results
                if r.get("doc_type") == "portfolio" and r.get("content")
            ]
            portfolio_ctx = "\n\n".join(portfolio_chunks) if portfolio_chunks else ""
            print(f"    포트폴리오 chunk {len(portfolio_chunks)}개 선택 (총 {len(portfolio_ctx)} 글자)\n")
        except Exception as e:
            print(f"   ⚠️ 포트폴리오 검색 실패: {e}\n")

    # 4. 인터뷰 로그
    print("💬 [인터뷰 로그 조회]")
    try:
        log_ctx = retrieve_interview_context(session_id)
        if not log_ctx:
            log_ctx = ""
            print("   ⚠️ 로그 없음\n")
        else:
            print(f"    로그 조회 완료: {len(log_ctx)} 글자\n")
    except Exception as e:
        print(f"   ⚠️ 로그 조회 실패: {e}\n")
        log_ctx = ""

    # 5. QA 추출
    print("❓ [질문-답변 쌍 추출]")
    qa_pairs = _extract_qa_pairs(log_ctx)
    print(f"    {len(qa_pairs)}개 추출\n")

    # 6. 증거 ID
    total_evidence = len(resume_results) + len(jd_results) + len(portfolio_results) + len(qa_pairs)
    eid_list = [f"E{i:02d}" for i in range(1, max(total_evidence + 1, 15))]

    # 7. 역량 ID
    comp_id_list = [f"C{i + 1:02d}" for i in range(len(axes_keys))]

    # 데이터 검증
    if not resume_ctx and not jd_ctx and not log_ctx:
        print(f"\n⚠️⚠️⚠️ [경고] 세션 {session_id}에 데이터가 없습니다!")
        print(f"   - 이력서: {len(resume_chunks)}개")
        print(f"   - JD: {len(jd_chunks)}개")
        print(f"   - 로그: {'없음' if not log_ctx else '있음'}")
        print(f"   세션 ID가 올바른지 확인하세요.\n")

    print(f"{'=' * 80}")
    print(" [Retriever] 완료")
    print(f"   이력서: {len(resume_chunks)}개 chunk ({len(resume_ctx)} 글자)")
    print(f"   JD: {len(jd_chunks)}개 chunk ({len(jd_ctx)} 글자)")
    print(f"   포트폴리오: {len(portfolio_chunks)}개 chunk ({len(portfolio_ctx)} 글자)")
    print(f"   인터뷰 로그: {len(log_ctx)} 글자, {len(qa_pairs)}개 QA")
    print(f"{'=' * 80}\n")

    return jd_ctx, resume_ctx, portfolio_ctx, log_ctx, comp_id_list, eid_list, qa_pairs


# ==================== 인터뷰 분석 (디베이트 패턴) ====================
class InterviewAnalysisOut(BaseModel):
    """인터뷰 로그 분석 결과"""
    contradiction_score: Annotated[int, Ge(0), Le(100)]
    contradiction_reason: NonEmpty = Field(description="모순 정도 점수 이유 (1-2문장)")
    depth_score: Annotated[int, Ge(0), Le(100)]
    depth_reason: NonEmpty = Field(description="대화 깊이 점수 이유 (1-2문장)")
    reliability_score: Annotated[int, Ge(0), Le(100)]
    reliability_reason: NonEmpty = Field(description="리포트 신뢰도 점수 이유 (1-2문장)")
    positive_aspects: List[str] = Field(min_length=1)
    negative_aspects: List[str] = Field(min_length=1)
    final_comment: NonEmpty
    debate_log: List[DebateTurn] = Field(
        default_factory=list,
        description="AgentA(Hiring Manager) vs AgentB(HR Evaluator) 디베이트 로그",
    )


def _interview_analysis_prompt() -> ChatPromptTemplate:
    """인터뷰 분석 프롬프트 (디베이트 패턴)"""
    return ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "당신은 인터뷰 로그를 심층 분석하는 전문가입니다.\n\n"
                "한국어로 작성하세요\n\n"
                "**분석 단계:**\n"
                "1. 답변 간 일관성 검토 (모순, 과장, 회피, 거짓 등)\n"
                "2. 답변의 깊이 평가 (구체성, 전문성)\n"
                "3. 인터뷰 로그의 신뢰도 판단\n\n"
                "**점수 기준:**\n"
                "- contradiction_score: 0(매우 일관적) ~ 100(매우 모순적)\n"
                "- depth_score: 0(피상적) ~ 100(매우 깊이 있음)\n"
                "- reliability_score: 0(신뢰 불가) ~ 100(매우 신뢰)\n\n"
                "**디베이트 방식:**\n"
                "- AgentA (Hiring Manager): 팀 성과, 실무 기여도, 즉시 투입 가능성 중심\n"
                "- AgentB (HR Evaluator): 조직 문화 적합성, 장기 성장 가능성, 리스크 관리 중심\n"
                "- 10-15턴 디베이트, 각 턴은 한 문단 이내\n\n"
                "**출력:**\n"
                "- contradiction_score: number (0-100)\n"
                "- contradiction_reason: string (모순 정도 점수 이유, 1-2문장)\n"
                "- depth_score: number (0-100)\n"
                "- depth_reason: string (대화 깊이 점수 이유, 1-2문장)\n"
                "- reliability_score: number (0-100)\n"
                "- reliability_reason: string (리포트 신뢰도 점수 이유, 1-2문장)\n"
                "- positive_aspects: array of strings (1-5개)\n"
                "- negative_aspects: array of strings (1-5개)\n"
                "- final_comment: string (7-10문장)\n"
                "- debate_log: array of objects [{{speaker: string, role: string, content: string}}, ...]",
            ),
            (
                "human",
                "[인터뷰 로그]\n{log}\n\n"
                "[포트폴리오]\n{portfolio}\n\n"
                "[분석 힌트]\n{interview_query}\n\n"
                "위 자료를 디베이트 방식으로 분석하고 JSON으로 반환하세요.",
            ),
        ]
    )


async def analyze_interview(
    log_ctx: str,
    portfolio_ctx: str,
    interview_query: str,
    feedback: Optional[List[str]] = None
) -> InterviewAnalysisOut:
    """인터뷰 분석 (포트폴리오 포함, Solar Reasoning, async, 피드백 반영)"""

    # 피드백이 있으면 프롬프트에 추가
    if feedback:
        feedback_text = "\n\n**이전 시도 피드백:**\n" + "\n".join(f"- {fb}" for fb in feedback)
        interview_query = (interview_query or "중요 질문/답변, 모순, 깊이에 주목") + feedback_text

    chain = _interview_analysis_prompt() | _llm_gpt().with_structured_output(
        InterviewAnalysisOut, method="json_mode"
    )

    max_len = 4000
    log_short = log_ctx[:max_len] + "..." if len(log_ctx) > max_len else log_ctx
    portfolio_short = portfolio_ctx[:max_len] + "..." if len(portfolio_ctx) > max_len else portfolio_ctx

    result = await chain.ainvoke(
        {
            "log": log_short,
            "portfolio": portfolio_short,
            "interview_query": interview_query,
        }
    )

    return result


# ==================== 핵심역량 평가 (포트폴리오 통합) ====================
class CompetencyEvalOut(BaseModel):
    scores: List[ScoreItem]
    weights: List[WeightItem]
    headline: Headline
    reasoning: List[str] = Field(default_factory=list)
    competency_comments: List[CompetencyComment] = Field(
        default_factory=list,
        description="각 역량별 점수 이유 (2-3문장)"
    )


def _competency_prompt() -> ChatPromptTemplate:
    """핵심역량 평가 프롬프트 (포트폴리오 추가)"""
    return ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "당신은 엄격한 면접 평가 전문가입니다.\n\n"
                "한국어로 작성하세요\n\n"
                "**평가 원칙:**\n"
                "1. 실제 증거에 기반 (추측 금지)\n"
                "2. 구체적 성과와 수치 필요\n"
                "3. 증거 약하면 과감하게 낮은 점수 (10점 이하도 가능)\n"
                "4. 가중치 합계 정확히 100\n"
                "5. 역량 간 명확한 차별화 (최소 20점 편차)\n\n"
                "**렌즈 관점 반영:**\n"
                "- lens_perspective에 제시된 평가 관점을 우선 반영\n"
                "- 단순 키워드 매칭이 아닌, '왜 그 역량이 중요한가'를 고려\n\n"
                "**점수 기준:**\n"
                "- 0-30: 증거 없음 또는 매우 부족\n"
                "- 31-50: 기본적 수준\n"
                "- 51-65: 보통 수준\n"
                "- 66-75: 양호\n"
                "- 76-85: 우수\n"
                "- 86-95: 탁월\n"
                "- 95-100: 최고 수준 (거의 부여하지 않음)\n\n"
                "**출력 (JSON 형식으로 반환하세요):**\n"
                "- scores: array of objects [{{key: string, value: number}}, ...]\n"
                "- weights: array of objects [{{key: string, value: number}}, ...]\n"
                "- headline: object {{summary: string, overall_summary: string, tag: array}}\n"
                "  * summary: 헤드라인용 1줄 요약\n"
                "  * overall_summary: 인터뷰 전반 평가 (150-200자)\n"
                "  * tag: 키워드 태그 3-5개\n"
                "- reasoning: array of strings (각 역량별 평가 근거 1-2문장씩)\n"
                "- competency_comments: array of objects [{{key: string, comment: string}}, ...] (각 역량별 점수 이유 2-3문장)",
            ),
            (
                "human",
                "**평가 축:** {axes_str}\n"
                "**렌즈 관점:** {lens_perspective}\n\n"
                "[이력서]\n{resume}\n\n"
                "[포트폴리오]\n{portfolio}\n\n"
                "[인터뷰로그]\n{log}\n\n"
                "위 자료를 바탕으로 역량을 평가하세요. JSON 형식으로 반환하세요.",
            ),
        ]
    )


async def evaluate_competency(
    resume: str,
    portfolio: str,
    log: str,
    axes_keys: List[str],
    lens_perspective: str,
    feedback: Optional[List[str]] = None
) -> CompetencyEvalOut:
    """핵심역량 평가 (포트폴리오 포함, Solar Reasoning, async, 피드백 반영)"""

    # 피드백이 있으면 렌즈 관점에 추가
    if feedback:
        feedback_text = "\n\n**이전 시도 피드백 (반드시 반영):**\n" + "\n".join(f"- {fb}" for fb in feedback)
        lens_perspective = lens_perspective + feedback_text

    chain = _competency_prompt() | _llm_solar_reasoning().with_structured_output(
        CompetencyEvalOut, method="json_mode"
    )

    max_len = 3500
    resume_short = resume[:max_len] + "..." if len(resume) > max_len else resume
    portfolio_short = portfolio[:max_len] + "..." if len(portfolio) > max_len else portfolio
    log_short = log[:max_len] + "..." if len(log) > max_len else log

    result = await chain.ainvoke(
        {
            "axes_str": ", ".join(axes_keys),
            "lens_perspective": lens_perspective,
            "resume": resume_short,
            "portfolio": portfolio_short,
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


# ==================== 증거 매핑 (포트폴리오 통합) ====================
class CompetencyEvidenceOut(BaseModel):
    competencyCoverage: List[JDCoverRow]
    evidence: List[EvidenceRow]


def _competency_evidence_prompt() -> ChatPromptTemplate:
    """증거 매핑 프롬프트 (포트폴리오 추가)"""
    return ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "당신은 핵심역량과 증거를 매핑하는 전문가입니다.\n\n"
                "한국어로 작성하세요\n\n"
                "**작업:**\n"
                "1. 각 핵심역량에 대해 관련 증거(Exx)를 이력서, 포트폴리오, 인터뷰 로그에서 찾으세요\n"
                "2. 충족도는 증거의 강도에 따라: 적합/보통/부족\n"
                "3. 모든 핵심역량은 최소 1개 이상의 EID를 갖도록 노력\n"
                "4. 각 증거는 1-2문장으로 요약 (20-30단어)\n"
                "5. 문장의 맥락을 보고 적절한 JID에 매핑 (단순 키워드 매칭 금지)\n\n"
                "**출력 형식:**\n"
                "- competencyCoverage: array of objects [{{jid: string, 요구사항: string, 기대치: string, 충족도: string, 근거: array of strings}}, ...]\n"
                "- evidence: array of objects [{{eid: string, 출처: string, 내용: string, jid: string}}, ...]",
            ),
            (
                "human",
                "[핵심역량]\n{competencies}\n\n"
                "[이력서]\n{resume}\n\n"
                "[포트폴리오]\n{portfolio}\n\n"
                "[인터뷰로그]\n{log}\n\n"
                "역량 ID: {competency_details}\n"
                "사용 가능 EID: {eid_list}\n\n"
                "JSON 형태로 출력하세요. 한국어로 작성하세요.",
            ),
        ]
    )


async def map_competency_evidence(
    axes_keys: List[str],
    resume: str,
    portfolio: str,
    log: str,
    eid_list: List[str],
    feedback: Optional[List[str]] = None
) -> CompetencyEvidenceOut:
    """증거 매핑 (포트폴리오 포함, Solar Reasoning, async, 피드백 반영)"""
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

    # 피드백이 있으면 프롬프트에 추가
    if feedback:
        feedback_text = "\n\n**이전 시도 피드백 (반드시 개선):**\n" + "\n".join(f"- {fb}" for fb in feedback)
        competencies_text = competencies_text + feedback_text

    chain = _competency_evidence_prompt() | _llm_solar_reasoning().with_structured_output(
        CompetencyEvidenceOut, method="json_mode"
    )

    max_len = 3000
    resume_short = resume[:max_len] + "..." if len(resume) > max_len else resume
    portfolio_short = portfolio[:max_len] + "..." if len(portfolio) > max_len else portfolio
    log_short = log[:max_len] + "..." if len(log) > max_len else log

    result = await chain.ainvoke(
        {
            "competencies": competencies_text,
            "resume": resume_short,
            "portfolio": portfolio_short,
            "log": log_short,
            "competency_details": competency_details,
            "eid_list": ", ".join(eid_list),
        }
    )

    return result


# ==================== 인터뷰 요약 ====================
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
    interview_stats: Optional[InterviewStats] = Field(
        default=None,
        description="대화 통계 (면접 시간, 질문당 후속 질문 비율, 질문 유형 비율)"
    )


def _interview_summary_prompt() -> ChatPromptTemplate:
    """인터뷰 요약 프롬프트"""
    return ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "당신은 인터뷰 분석 전문가입니다.\n\n"
                "한국어로 작성하세요\n\n"
                "**작업:**\n"
                "1. 전체 인터뷰 흐름 요약 (overall, 2-3문장)\n"
                "2. 각 질문 답변 요약 (qa_summaries, 1-3줄)\n"
                "3. 긍정 의견: 추천 이유와 근거 200-300자 (positive)\n"
                "4. 부정 의견: 비추천 이유와 근거 200-300자 (negative)\n"
                "5. 통계: 총 질문 수, 평균 답변 길이 등 key는 한글로 작성할것 (stats)\n"
                "6. 대화 통계 (interview_stats):\n"
                "   - duration: 면접 시간 (예: \"45분\")\n"
                "   - followup_ratio: 질문당 후속 질문 비율 (예: \"2.3회\")\n"
                "   - question_type_ratios: 질문 유형별 비율 (사실관계파악, 기술질문, 커뮤니케이션질문 등)\n\n"
                "**출력:**\n"
                "- overall: string (2-3문장)\n"
                "- qa_summaries: array of objects [{{q_num: number|string, question_short: string, answer_summary: string}}, ...]\n"
                "- positive: array of strings (각 200-300자)\n"
                "- negative: array of strings (각 200-300자)\n"
                "- stats: object (총 질문 수, 평균 답변 길이 등)\n"
                "- interview_stats: object {{duration: string, followup_ratio: string, question_type_ratios: array of objects [{{type: string, ratio: string}}, ...]}}",
            ),
            (
                "human",
                "[인터뷰 로그]\n{log}\n\n"
                "[질문-답변 쌍]\n{qa_pairs}\n\n"
                "JSON으로 반환하세요.",
            ),
        ]
    )


async def summarize_interview(log: str, qa_pairs: List[Dict]) -> InterviewSummaryOut:
    """인터뷰 요약 (Solar Chat, async)"""
    chain = _interview_summary_prompt() | _llm_solar_chat().with_structured_output(
        InterviewSummaryOut, method="json_mode"
    )

    qa_str = "\n\n".join(
        [
            f"Q{qa['q_num']}: {qa['question'][:100]}...\n"
            f"A{qa['q_num']}: {qa['answer'][:200]}..."
            for qa in qa_pairs
        ]
    )

    max_len = 3500
    log_short = log[:max_len] + "..." if len(log) > max_len else log

    result = await chain.ainvoke({"log": log_short, "qa_pairs": qa_str})

    return result


# ==================== 최종 권고 생성 ====================
def _final_recommendation_prompt() -> ChatPromptTemplate:
    """최종 채용 권고 생성"""
    return ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "당신은 채용 의사결정 전문가입니다.\n\n"
                "한국어로 작성하세요\n\n"
                "**작업:**\n"
                "분석된 모든 데이터를 종합하여 최종 채용 권고를 작성하세요.\n\n"
                "**출력:**\n"
                "- final_comment: string (250-300자, 종합 평가 및 권고 사항)\n"
                "- hiring_decision: string (한 문장, 예: '조건부 채용 (3개월 수습)', '즉시 채용 권고', '비추천')\n"
                "- decision_reasons: array of strings (결정 이유 1-5개)",
            ),
            (
                "human",
                "[인터뷰 분석]\n"
                "모순도: {contradiction}%, 깊이: {depth}%, 신뢰도: {reliability}%\n\n"
                "[역량 점수]\n{scores}\n\n"
                "[긍정 의견]\n{positive}\n\n"
                "[부정 의견]\n{negative}\n\n"
                "위 자료를 바탕으로 최종 채용 권고를 작성하세요. JSON으로 반환하세요.",
            ),
        ]
    )


async def generate_final_recommendation(
    contradiction: int,
    depth: int,
    reliability: int,
    scores: List[ScoreItem],
    positive: List[str],
    negative: List[str],
) -> FinalRecommendation:
    """최종 권고 생성 (Solar Reasoning, async)"""
    chain = _final_recommendation_prompt() | _llm_solar_reasoning().with_structured_output(
        FinalRecommendation, method="json_mode"
    )

    result = await chain.ainvoke(
        {
            "contradiction": contradiction,
            "depth": depth,
            "reliability": reliability,
            "scores": ", ".join([f"{s.key}: {s.value}점" for s in scores]),
            "positive": "\n".join(positive),
            "negative": "\n".join(negative),
        }
    )

    return result


# ==================== 메타데이터 추출 ====================
def _metadata_extraction_prompt() -> ChatPromptTemplate:
    """JD에서 메타데이터 추출"""
    return ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "당신은 JD에서 채용 포지션 정보를 추출하는 전문가입니다.\n\n"
                "한국어로 작성하세요\n\n"
                "**작업:**\n"
                "JD에서 다음 정보를 추출하세요:\n"
                "- position_applied: 지원 포지션명 (예: 'AI 에이전트 개발자', 'Backend Developer')\n\n"
                "만약 JD가 비어있거나 포지션명을 찾을 수 없으면:\n"
                "- position_applied: '일반 포지션'으로 반환\n\n"
                "**출력:**\n"
                "{{\"position_applied\": string}}"
            ),
            (
                "human",
                "[JD]\n{jd}\n\n"
                "위 JD에서 포지션명을 추출하세요. JSON으로 반환하세요.",
            ),
        ]
    )


def extract_position_from_jd(jd_ctx: str) -> str:
    """JD에서 포지션명 추출 (Solar Chat)"""
    if not jd_ctx or jd_ctx.strip() == "":
        return "일반 포지션"

    chain = _metadata_extraction_prompt() | _llm_solar_chat().with_structured_output(
        dict, method="json_mode"
    )

    try:
        max_len = 2000
        jd_short = jd_ctx[:max_len] + "..." if len(jd_ctx) > max_len else jd_ctx
        result = chain.invoke({"jd": jd_short})
        return result.get("position_applied", "일반 포지션")
    except Exception as e:
        logger.warning(f"포지션 추출 실패: {e}")
        return "일반 포지션"


# ==================== 디베이트 검증 노드 ====================
def _debate_validation_prompt() -> ChatPromptTemplate:
    """디베이트 검증 프롬프트"""
    return ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "당신은 분석 품질 검증 전문가입니다.\n\n"
                "한국어로 작성하세요\n\n"
                "**역할:**\n"
                "두 명의 가상 에이전트가 디베이트 방식으로 분석 결과를 검증합니다.\n"
                "- AgentA (Hiring Manager): 실무 기여도, 즉시 투입 가능성, 팀 성과 중심\n"
                "- AgentB (HR Evaluator): 조직 적합성, 장기 성장, 리스크 관리 중심\n\n"
                "**검증 항목:**\n"
                "{validation_focus}\n\n"
                "**디베이트 절차:**\n"
                "1. AgentA가 먼저 분석 결과를 검토하고 의견 제시 (2-3문장)\n"
                "2. AgentB가 다른 관점에서 반론 또는 보완 의견 제시 (2-3문장)\n"
                "3. 5-10턴 반복하여 품질 이슈를 도출\n"
                "4. 최종 결론: is_valid, quality_score, issues, suggestions\n\n"
                "**출력:**\n"
                "- is_valid: boolean (분석 품질이 충분한지)\n"
                "- quality_score: number (0.0-1.0)\n"
                "- issues: array of strings (발견된 문제 리스트)\n"
                "- suggestions: array of strings (개선 제안 리스트)\n"
                "- debate_log: object {{topic: string, turns: array of objects [{{speaker: string, role: string, content: string}}], conclusion: string}}",
            ),
            (
                "human",
                "[분석 결과]\n{analysis_result}\n\n"
                "[원본 데이터 요약]\n{data_summary}\n\n"
                "위 분석 결과를 디베이트 방식으로 검증하세요. JSON으로 반환하세요.",
            ),
        ]
    )


async def validate_analysis_with_debate(
    topic: str,
    validation_focus: str,
    analysis_result: Dict[str, Any],
    data_summary: str,
) -> DebateValidationResult:
    """디베이트 방식 분석 검증 (Solar Reasoning, async)"""
    chain = _debate_validation_prompt() | _llm_gpt().with_structured_output(
        DebateValidationResult, method="json_mode"
    )

    import json

    result = await chain.ainvoke(
        {
            "validation_focus": validation_focus,
            "analysis_result": json.dumps(analysis_result, ensure_ascii=False, indent=2)[:2000],
            "data_summary": data_summary[:1000],
        }
    )

    return result


def debate_validation_node(state: ReportState) -> ReportState:
    """노드 3.5: 디베이트 검증 (병렬 분석 후 실행)"""
    state["agent_logs"].append(
        {
            "agent": "디베이트 검증 에이전트",
            "message": "분석 결과 디베이트 검증 시작...",
        }
    )

    async def _run_debate_validation():
        tasks = []
        validation_results = []

        try:
            # 데이터 요약 생성
            data_summary = (
                f"이력서: {len(state.get('resume_ctx') or '')}자\n"
                f"JD: {len(state.get('jd_ctx') or '')}자\n"
                f"포트폴리오: {len(state.get('portfolio_ctx') or '')}자\n"
                f"인터뷰 로그: {len(state.get('log_ctx') or '')}자"
            )

            # 1. 인터뷰 분석 검증
            if state.get("interview_analysis"):
                task1 = asyncio.create_task(
                    validate_analysis_with_debate(
                        topic="인터뷰 분석 검증",
                        validation_focus=(
                            "- 모순도/깊이/신뢰도 점수가 인터뷰 로그 내용과 일치하는지\n"
                            "- positive/negative aspects가 구체적이고 균형있는지\n"
                            "- final_comment가 근거 기반인지"
                        ),
                        analysis_result=state["interview_analysis"],
                        data_summary=data_summary,
                    )
                )
                tasks.append(("interview_analysis", task1))

            # 2. 역량 평가 검증
            if state.get("competency_eval"):
                task2 = asyncio.create_task(
                    validate_analysis_with_debate(
                        topic="역량 평가 검증",
                        validation_focus=(
                            "- 점수가 과도하게 높거나 낮지 않은지 (근거 부족 시 낮은 점수)\n"
                            "- 가중치 합계가 100인지\n"
                            "- headline이 점수와 논리적으로 일치하는지\n"
                            "- 역량 간 차별화가 명확한지 (최소 20점 편차)"
                        ),
                        analysis_result=state["competency_eval"],
                        data_summary=data_summary,
                    )
                )
                tasks.append(("competency_eval", task2))

            # 3. 증거 매핑 검증
            if state.get("evidence_mapping"):
                task3 = asyncio.create_task(
                    validate_analysis_with_debate(
                        topic="증거 매핑 검증",
                        validation_focus=(
                            "- 모든 역량이 최소 1개 이상의 EID를 가지는지\n"
                            "- 증거가 해당 역량에 실제로 관련있는지 (단순 키워드 매칭 금지)\n"
                            "- 충족도 평가가 증거 강도와 일치하는지"
                        ),
                        analysis_result=state["evidence_mapping"],
                        data_summary=data_summary,
                    )
                )
                tasks.append(("evidence_mapping", task3))

            logger.debug(f"[debate_validation_node] {len(tasks)}개 검증 작업 병렬 실행")

            # 병렬 실행
            results = await asyncio.wait_for(
                asyncio.gather(*[t[1] for t in tasks]),
                timeout=DEBATE_VALIDATION_TIMEOUT_SEC,
            )

            # 결과 저장
            for idx, (name, _) in enumerate(tasks):
                result = results[idx]
                validation_results.append(
                    {
                        "topic": result.debate_log.topic,
                        "is_valid": result.is_valid,
                        "quality_score": result.quality_score,
                        "issues": result.issues,
                        "suggestions": result.suggestions,
                        "debate_log": result.debate_log.model_dump(),
                    }
                )

                # 로그 추가
                state["agent_logs"].append(
                    {
                        "agent": f"디베이트 검증 - {name}",
                        "message": (
                            f"{' 통과' if result.is_valid else '⚠️ 개선 필요'} "
                            f"(품질: {result.quality_score:.2f})\n"
                            f"   이슈: {len(result.issues)}개, 제안: {len(result.suggestions)}개"
                        ),
                    }
                )

                # 디베이트 로그를 스트리밍용으로 추가
                state["agent_logs"].append(
                    {
                        "agent": f"디베이트 로그 - {result.debate_log.topic}",
                        "message": f"디베이트 {len(result.debate_log.turns)}턴 완료",
                        "debate_turns": result.debate_log.turns,  # 프론트엔드에서 사용
                    }
                )

            state["debate_validation_results"] = validation_results

            # 전체 검증 결과 요약
            all_valid = all(r["is_valid"] for r in validation_results)
            avg_quality = sum(r["quality_score"] for r in validation_results) / len(validation_results)

            state["agent_logs"].append(
                {
                    "agent": "디베이트 검증 에이전트",
                    "message": (
                        f"{' 전체 검증 통과' if all_valid else '⚠️ 일부 개선 필요'}\n"
                        f"   평균 품질: {avg_quality:.2f}\n"
                        f"   검증 항목: {len(validation_results)}개"
                    ),
                }
            )

        except asyncio.TimeoutError:
            logger.warning("[debate_validation_node] 타임아웃 발생")
            for _, task in tasks:
                if not task.done():
                    task.cancel()

            state["error"] = f"디베이트 검증 타임아웃: {DEBATE_VALIDATION_TIMEOUT_SEC}초"
            state["agent_logs"].append(
                {
                    "agent": "디베이트 검증 에이전트",
                    "message": f"❌ 오류: {state['error']}",
                }
            )
        except Exception as e:
            logger.warning(f"[debate_validation_node] 예외 발생: {e}")
            state["error"] = f"디베이트 검증 오류: {str(e)}"
            state["agent_logs"].append(
                {
                    "agent": "디베이트 검증 에이전트",
                    "message": f"❌ 오류: {state['error']}",
                }
            )

        return state

    # 동기 래퍼
    try:
        loop = asyncio.get_running_loop()
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(lambda: asyncio.run(_run_debate_validation()))
            return future.result()
    except RuntimeError:
        try:
            loop = asyncio.get_event_loop()
            if loop.is_closed():
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        return loop.run_until_complete(_run_debate_validation())


# ==================== LangGraph State 정의 ====================
class ReportState(TypedDict):
    """리포트 생성 워크플로우 상태 (포트폴리오 + 디베이트 검증)"""

    # 입력
    session_id: str
    candidate_name: str
    axes_keys: List[str]
    user_prompt: Optional[str]
    has_portfolio: bool

    # 프롬프트 최적화
    optimized_prompt: Optional[Dict[str, Any]]

    # 질의 설계
    query_plan: Optional[Dict[str, Any]]
    query_hint: Optional[str]
    retry_count: int

    # 리트리버 결과
    jd_ctx: Optional[str]
    resume_ctx: Optional[str]
    portfolio_ctx: Optional[str]
    log_ctx: Optional[str]
    comp_id_list: Optional[List[str]]
    eid_list: Optional[List[str]]
    qa_pairs: Optional[List[Dict]]

    # 분석 결과
    interview_analysis: Optional[Dict[str, Any]]
    competency_eval: Optional[Dict[str, Any]]
    evidence_mapping: Optional[Dict[str, Any]]
    interview_summary: Optional[Dict[str, Any]]
    final_recommendation: Optional[Dict[str, Any]]

    # 디베이트 검증 결과
    debate_validation_results: Optional[List[Dict[str, Any]]]

    # 디베이트 재시도 관련 (추가)
    debate_retry_count: int
    retry_targets: Optional[List[str]]  # 재실행 대상: ["interview_analysis", "competency_eval", "evidence_mapping"]
    retry_feedbacks: Optional[Dict[str, List[str]]]  # 각 에이전트별 피드백

    # 검증 결과
    validation_result: Optional[Dict[str, Any]]

    # 최종 리포트
    report: Optional[Dict[str, Any]]

    # 에이전트 로그
    agent_logs: List[Dict[str, str]]

    # 에러
    error: Optional[str]


# ==================== LangGraph 노드 구현 ====================
def prompt_optimizer_node(state: ReportState) -> ReportState:
    """노드 0: 사용자 프롬프트 최적화"""
    state["agent_logs"].append(
        {
            "agent": "프롬프트 최적화",
            "message": "사용자 프롬프트를 렌즈 관점으로 변환 중...",
        }
    )

    try:
        optimized = optimize_user_prompt(state.get("user_prompt") or "", state["axes_keys"])
        state["optimized_prompt"] = optimized.model_dump()

        state["agent_logs"].append(
            {
                "agent": "프롬프트 최적화",
                "message": (
                    f" 렌즈 관점 변환 완료\n"
                    f"   원본: {optimized.original_prompt[:50]}...\n"
                    f"   변환: {optimized.lens_perspective[:80]}...\n"
                    f"   초점 영역: {', '.join(optimized.key_focus_areas)}"
                ),
            }
        )
    except Exception as e:
        state["error"] = f"프롬프트 최적화 오류: {str(e)}"
        state["agent_logs"].append(
            {
                "agent": "프롬프트 최적화",
                "message": f"❌ 오류: {state['error']}",
            }
        )

    return state


def query_planner_node(state: ReportState) -> ReportState:
    """노드 1: 질의 설계 (포트폴리오 포함)"""
    state["agent_logs"].append(
        {
            "agent": "질의설계",
            "message": f"벡터 검색 질의 설계 중... (재시도: {state['retry_count']})",
        }
    )

    try:
        optimized = state.get("optimized_prompt") or {}
        lens_perspective = optimized.get("lens_perspective", "일반 평가")
        key_focus_areas = optimized.get("key_focus_areas", state["axes_keys"])

        query_plan = design_queries(
            state["axes_keys"],
            lens_perspective,
            key_focus_areas,
            state.get("has_portfolio", False),
            state.get("query_hint"),
        )

        state["query_plan"] = query_plan.model_dump()
        state["agent_logs"].append(
            {
                "agent": "질의설계",
                "message": (
                    " 질의 설계 완료\n"
                    f"   이력서: {query_plan.resume_query[:50]}...\n"
                    f"   JD: {query_plan.jd_query[:50]}...\n"
                    f"   포트폴리오: {query_plan.portfolio_query[:50] if query_plan.portfolio_query else '없음'}..."
                ),
            }
        )
    except Exception as e:
        state["error"] = f"질의 설계 오류: {str(e)}"
        state["agent_logs"].append(
            {
                "agent": "질의설계",
                "message": f"❌ 오류: {state['error']}",
            }
        )

    return state


def retriever_node(state: ReportState) -> ReportState:
    """노드 2: 리트리버 (포트폴리오 포함)"""
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
            portfolio_ctx,
            log_ctx,
            comp_id_list,
            eid_list,
            qa_pairs,
        ) = retrieve_contexts(
            state["session_id"],
            query_plan,
            state["axes_keys"],
            state.get("has_portfolio", False),
        )

        state["jd_ctx"] = jd_ctx
        state["resume_ctx"] = resume_ctx
        state["portfolio_ctx"] = portfolio_ctx
        state["log_ctx"] = log_ctx
        state["comp_id_list"] = comp_id_list
        state["eid_list"] = eid_list
        state["qa_pairs"] = qa_pairs

        state["agent_logs"].append(
            {
                "agent": "리트리버",
                "message": (
                    " 검색 완료 - "
                    f"이력서 {len(resume_ctx)}자, JD {len(jd_ctx)}자, "
                    f"포트폴리오 {len(portfolio_ctx)}자, 로그 {len(log_ctx)}자"
                ),
            }
        )
    except Exception as e:
        state["error"] = f"리트리버 오류: {str(e)}"
        state["agent_logs"].append(
            {
                "agent": "리트리버",
                "message": f"❌ 오류: {str(e)}",
            }
        )

    return state


def analysis_parallel_node(state: ReportState) -> ReportState:
    """노드 3: 병렬 분석 (포트폴리오 포함, 선택적 재실행 지원)"""
    # 재실행 여부 확인
    retry_targets = state.get("retry_targets") or []
    is_retry = len(retry_targets) > 0

    if is_retry:
        state["agent_logs"].append(
            {
                "agent": "재실행 에이전트",
                "message": f"품질 개선을 위한 선택적 재실행: {', '.join(retry_targets)}",
            }
        )
    else:
        state["agent_logs"].append(
            {
                "agent": "리포트 분석 에이전트",
                "message": "인터뷰 분석/역량 평가/증거 매핑/요약 병렬 실행 중...",
            }
        )

    async def _run_parallel_analysis():
        tasks = []
        task_names = []

        try:
            query_plan = QueryPlan(**state["query_plan"]) if state.get("query_plan") else None
            interview_query = query_plan.interview_query if query_plan else ""

            optimized = state.get("optimized_prompt") or {}
            lens_perspective = optimized.get("lens_perspective", "일반 평가")

            log_ctx = state.get("log_ctx") or ""
            resume_ctx = state.get("resume_ctx") or ""
            portfolio_ctx = state.get("portfolio_ctx") or ""
            axes_keys = state.get("axes_keys") or []
            eid_list = state.get("eid_list") or []
            qa_pairs = state.get("qa_pairs") or []

            logger.debug(f"[analysis_parallel_node] 병렬 분석 시작 (재실행: {is_retry}, 대상: {retry_targets})")

            # 피드백 준비 (재실행 시에만 존재)
            retry_feedbacks = state.get("retry_feedbacks") or {}

            # 선택적 실행: retry_targets가 있으면 해당 에이전트만, 없으면 모두 실행
            if not is_retry or "interview_analysis" in retry_targets:
                feedback = retry_feedbacks.get("interview_analysis")
                if feedback:
                    state["agent_logs"].append({
                        "agent": "인터뷰 분석 재실행",
                        "message": f"피드백 반영: {', '.join(feedback[:2])}..."
                    })
                task1 = asyncio.create_task(
                    analyze_interview(log_ctx, portfolio_ctx, interview_query, feedback)
                )
                tasks.append(task1)
                task_names.append("interview_analysis")

            if not is_retry or "competency_eval" in retry_targets:
                feedback = retry_feedbacks.get("competency_eval")
                if feedback:
                    state["agent_logs"].append({
                        "agent": "역량 평가 재실행",
                        "message": f"피드백 반영: {', '.join(feedback[:2])}..."
                    })
                task2 = asyncio.create_task(
                    evaluate_competency(resume_ctx, portfolio_ctx, log_ctx, axes_keys, lens_perspective, feedback)
                )
                tasks.append(task2)
                task_names.append("competency_eval")

            if not is_retry or "evidence_mapping" in retry_targets:
                feedback = retry_feedbacks.get("evidence_mapping")
                if feedback:
                    state["agent_logs"].append({
                        "agent": "증거 매핑 재실행",
                        "message": f"피드백 반영: {', '.join(feedback[:2])}..."
                    })
                task3 = asyncio.create_task(
                    map_competency_evidence(axes_keys, resume_ctx, portfolio_ctx, log_ctx, eid_list, feedback)
                )
                tasks.append(task3)
                task_names.append("evidence_mapping")

            # 인터뷰 요약은 재실행하지 않음 (통계 데이터)
            if not is_retry:
                task4 = asyncio.create_task(summarize_interview(log_ctx, qa_pairs))
                tasks.append(task4)
                task_names.append("interview_summary")

            results = await asyncio.wait_for(
                asyncio.gather(*tasks),
                timeout=PARALLEL_ANALYSIS_TIMEOUT_SEC,
            )

            logger.debug(f"[analysis_parallel_node] 병렬 분석 완료 (실행된 에이전트: {task_names})")

            # 결과를 State에 저장
            for name, result in zip(task_names, results):
                # 인터뷰 분석 결과
                if name == "interview_analysis":
                    state[name] = result.model_dump()
                    state["agent_logs"].append(
                        {
                            "agent": "인터뷰 분석 에이전트" if not is_retry else "인터뷰 분석 재실행",
                            "message": (
                                f" {'재실행 ' if is_retry else ''}완료 - "
                                f"모순도 {result.contradiction_score}%, "
                                f"깊이 {result.depth_score}%, "
                                f"신뢰도 {result.reliability_score}%"
                            ),
                        }
                    )

                # 핵심역량 평가 결과 (axes_keys와 키 정규화)
                elif name == "competency_eval":
                    # LLM이 반환한 scores/weights를 axes_keys 순서와 키에 맞게 정규화
                    try:
                        raw_scores = list(result.scores or [])
                        raw_weights = list(result.weights or [])
                        score_map = {s.key: s.value for s in raw_scores}
                        weight_map = {w.key: w.value for w in raw_weights}
                        raw_keys = list(score_map.keys())

                        def _match_axis_key(axis_key: str) -> str | None:
                            # 1) 완전 일치
                            if axis_key in score_map:
                                return axis_key
                            # 2) 부분 일치 (예: "문제해결" ↔ "문제해결능력")
                            for rk in raw_keys:
                                if axis_key and (axis_key in rk or rk in axis_key):
                                    return rk
                            return None

                        normalized_scores: list[ScoreItem] = []
                        normalized_weights: list[WeightItem] = []

                        for axis_key in axes_keys:
                            raw_key = _match_axis_key(axis_key)
                            val = score_map.get(raw_key, 0)
                            w_val = weight_map.get(raw_key, 0)
                            normalized_scores.append(ScoreItem(key=axis_key, value=val))
                            normalized_weights.append(WeightItem(key=axis_key, value=w_val))

                        # 정규화된 결과로 교체
                        result.scores = normalized_scores
                        result.weights = normalized_weights
                    except Exception:
                        # 문제가 생겨도 전체 파이프라인이 죽지 않도록 원본 결과를 그대로 사용
                        pass

                    state[name] = result.model_dump()
                    scores_str = ", ".join([f"{s.key}: {s.value}점" for s in result.scores])
                    state["agent_logs"].append(
                        {
                            "agent": "핵심역량평가 에이전트" if not is_retry else "역량 평가 재실행",
                            "message": f" {'재실행 ' if is_retry else ''}완료 - {scores_str}",
                        }
                    )

                # 증거 매핑 결과
                elif name == "evidence_mapping":
                    state[name] = result.model_dump()
                    state["agent_logs"].append(
                        {
                            "agent": "증거매핑 에이전트" if not is_retry else "증거 매핑 재실행",
                            "message": (
                                f" {'재실행 ' if is_retry else ''}완료 - "
                                f"역량 {len(result.competencyCoverage)}개, "
                                f"증거 {len(result.evidence)}개"
                            ),
                        }
                    )

                # 인터뷰 요약 결과
                elif name == "interview_summary":
                    state[name] = result.model_dump()
                    state["agent_logs"].append(
                        {
                            "agent": "요약생성 에이전트",
                            "message": f" 요약 완료 - QA {len(result.qa_summaries)}개 요약",
                        }
                    )

        except asyncio.TimeoutError:
            logger.warning("[analysis_parallel_node] 타임아웃 발생")
            for task in tasks:
                if not task.done():
                    task.cancel()
            try:
                await asyncio.wait_for(
                    asyncio.gather(*tasks, return_exceptions=True), timeout=5.0
                )
            except asyncio.TimeoutError:
                logger.error("[analysis_parallel_node] Task 정리 타임아웃")

            state["error"] = f"병렬 분석 타임아웃: {PARALLEL_ANALYSIS_TIMEOUT_SEC}초"
            state["agent_logs"].append(
                {
                    "agent": "병렬 분석 에이전트",
                    "message": f"❌ 오류: {state['error']}",
                }
            )
        except Exception as e:
            logger.warning(f"[analysis_parallel_node] 예외 발생: {e}")
            for task in tasks:
                if not task.done():
                    task.cancel()
            state["error"] = f"병렬 분석 노드 오류: {str(e)}"
            state["agent_logs"].append(
                {
                    "agent": "병렬 분석 에이전트",
                    "message": f"❌ 오류: {state['error']}",
                }
            )

        return state

    # 동기 래퍼
    try:
        loop = asyncio.get_running_loop()
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(lambda: asyncio.run(_run_parallel_analysis()))
            return future.result()
    except RuntimeError:
        try:
            loop = asyncio.get_event_loop()
            if loop.is_closed():
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        return loop.run_until_complete(_run_parallel_analysis())


def final_recommendation_node(state: ReportState) -> ReportState:
    """노드 4: 최종 권고 생성"""
    state["agent_logs"].append(
        {
            "agent": "최종 권고 생성",
            "message": "채용 의사결정 권고 생성 중...",
        }
    )

    async def _run():
        try:
            interview_analysis = state.get("interview_analysis") or {}
            competency_eval = state.get("competency_eval") or {}
            interview_summary = state.get("interview_summary") or {}

            recommendation = await generate_final_recommendation(
                contradiction=interview_analysis.get("contradiction_score", 50),
                depth=interview_analysis.get("depth_score", 50),
                reliability=interview_analysis.get("reliability_score", 50),
                scores=[ScoreItem(**s) for s in competency_eval.get("scores", [])],
                positive=interview_summary.get("positive", []),
                negative=interview_summary.get("negative", []),
            )

            state["final_recommendation"] = recommendation.model_dump()

            state["agent_logs"].append(
                {
                    "agent": "최종 권고 생성",
                    "message": f" 권고 완료 - {recommendation.hiring_decision}",
                }
            )
        except Exception as e:
            state["error"] = f"최종 권고 생성 오류: {str(e)}"
            state["agent_logs"].append(
                {
                    "agent": "최종 권고 생성",
                    "message": f"❌ 오류: {state['error']}",
                }
            )

        return state

    # 동기 래퍼
    try:
        loop = asyncio.get_running_loop()
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(lambda: asyncio.run(_run()))
            return future.result()
    except RuntimeError:
        try:
            loop = asyncio.get_event_loop()
            if loop.is_closed():
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        return loop.run_until_complete(_run())


def validation_node(state: ReportState) -> ReportState:
    """노드 5: 최종 리포트 조립 및 검증"""
    state["agent_logs"].append(
        {
            "agent": "검증 에이전트",
            "message": "리포트 조립 중...",
        }
    )

    try:
        comp_eval = state["competency_eval"] or {}
        evidence_map = state["evidence_mapping"] or {}
        interview_sum = state["interview_summary"] or {}
        interview_analysis = state["interview_analysis"] or {}
        final_rec = state["final_recommendation"] or {}

        # talkSummary 조립
        def _to_text(x):
            if isinstance(x, str):
                return x
            if isinstance(x, list):
                return "\n".join(str(i) for i in x)
            return str(x)

        talk_items: List[Dict[str, str]] = []

        overall = _to_text(interview_sum.get("overall") or "")
        if overall.strip():
            talk_items.append({"주제": "인터뷰 요약", "발언요약": overall})

        for qa in interview_sum.get("qa_summaries", []) or []:
            q = (qa.get("question_short") or "질문").strip() or "질문"
            a = _to_text(qa.get("answer_summary") or "답변")
            talk_items.append({"주제": q, "발언요약": a})

        positive_text = _to_text(interview_sum.get("positive") or [])
        if positive_text.strip():
            talk_items.append({"주제": "긍정 의견", "발언요약": positive_text})

        negative_text = _to_text(interview_sum.get("negative") or [])
        if negative_text.strip():
            talk_items.append({"주제": "부정 의견", "발언요약": negative_text})

        if not talk_items:
            talk_items.append(
                {"주제": "요약 없음", "발언요약": "요약 데이터를 생성하지 못했습니다."}
            )

        # 메타데이터 생성
        position = extract_position_from_jd(state.get("jd_ctx") or "")
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        metadata = ReportMetadata(
            candidate_name=state.get("candidate_name", "지원자"),
            position_applied=position,
            interview_date=today,
            report_date=today,
            session_id=state["session_id"],
            interviewer_name="AI 평가 시스템",
        )

        # 리포트 조립
        axes_keys = state["axes_keys"] or []

        # headline 병합: comp_eval과 interview_analysis에서 가져옴
        headline_data = comp_eval.get("headline") or {}
        headline_merged = {
            **headline_data,
            "contradiction_score": interview_analysis.get("contradiction_score", headline_data.get("contradiction_score")),
            "contradiction_reason": interview_analysis.get("contradiction_reason", headline_data.get("contradiction_reason", "")),
            "depth_score": interview_analysis.get("depth_score", headline_data.get("depth_score")),
            "depth_reason": interview_analysis.get("depth_reason", headline_data.get("depth_reason", "")),
            "reliability_score": interview_analysis.get("reliability_score", headline_data.get("reliability_score")),
            "reliability_reason": interview_analysis.get("reliability_reason", headline_data.get("reliability_reason", "")),
        }

        report: Dict[str, Any] = {
            "metadata": metadata.model_dump(),
            "axes": [
                {"key": k, "label": k.replace("_", " ").title()} for k in axes_keys
            ],
            "scores": comp_eval.get("scores", []),
            "weights": comp_eval.get("weights", []),
            "headline": headline_merged,
            "talkSummary": {"items": talk_items},
            "convStats": [
                {"k": str(k), "v": str(v)}
                for k, v in (interview_sum.get("stats") or {}).items()
            ],
            "jdCoverage": evidence_map.get("competencyCoverage", []),
            "evidence": evidence_map.get("evidence", []),
            "competency_comments": comp_eval.get("competency_comments", []),
            "interview_stats": interview_sum.get("interview_stats"),
            "recommendation": final_rec,
        }

        # 스키마 검증
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

        # 메타데이터 추가
        report.update(
            {
                "status": "ok",
                "report_id": uuid4().hex,
                "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            }
        )

        # 디베이트 로그 추가 (스키마 외 필드)
        debate_log = interview_analysis.get("debate_log") or []
        report["debate_log"] = debate_log

        state["report"] = report
        state["agent_logs"].append(
            {
                "agent": "검증 에이전트",
                "message": " 리포트 생성 완료!",
            }
        )

    except Exception as e:
        state["error"] = f"검증 오류: {str(e)}"
        state["agent_logs"].append(
            {
                "agent": "검증 에이전트",
                "message": f"❌ 오류: {state['error']}",
            }
        )

    return state


def debate_router_node(state: ReportState) -> str:
    """노드 3.7: 디베이트 검증 후 라우팅 (재실행 또는 진행)"""

    # 재시도 횟수 확인 (최대 2회)
    debate_retry_count = state.get("debate_retry_count", 0)
    if debate_retry_count >= 2:
        logger.debug("[debate_router] 최대 재시도 횟수 도달 (2회) → 진행")
        state["agent_logs"].append({
            "agent": "디베이트 라우터",
            "message": "최대 재시도 횟수 도달 (2회) → 최종 권고 생성으로 진행"
        })
        return "final_recommendation"

    # 검증 결과 확인
    validation_results = state.get("debate_validation_results") or []
    if not validation_results:
        logger.debug("[debate_router] 검증 결과 없음 → 진행")
        return "final_recommendation"

    # 실패한 항목 필터링
    failed_validations = [v for v in validation_results if not v.get("is_valid", True)]

    if not failed_validations:
        logger.debug("[debate_router] 모든 검증 통과 → 진행")
        state["agent_logs"].append({
            "agent": "디베이트 라우터",
            "message": " 모든 검증 통과 → 최종 권고 생성으로 진행"
        })
        return "final_recommendation"

    # 재실행 대상 및 피드백 수집
    retry_targets = []
    retry_feedbacks = {}

    topic_to_target = {
        "인터뷰 분석 검증": "interview_analysis",
        "역량 평가 검증": "competency_eval",
        "증거 매핑 검증": "evidence_mapping",
    }

    for validation in failed_validations:
        topic = validation.get("topic", "")
        target = topic_to_target.get(topic)
        if target:
            retry_targets.append(target)
            retry_feedbacks[target] = validation.get("suggestions", [])

    if not retry_targets:
        logger.debug("[debate_router] 재실행 대상 없음 → 진행")
        return "final_recommendation"

    # State 업데이트
    state["retry_targets"] = retry_targets
    state["retry_feedbacks"] = retry_feedbacks
    state["debate_retry_count"] = debate_retry_count + 1

    logger.debug(f"[debate_router] 재실행 대상: {retry_targets} (재시도 {state['debate_retry_count']}/2)")
    state["agent_logs"].append({
        "agent": "디베이트 라우터",
        "message": (
            f"⚠️ 품질 개선 필요 → 재실행 ({state['debate_retry_count']}/2)\n"
            f"   대상: {', '.join(retry_targets)}"
        )
    })

    return "analysis"


def retry_router_node(state: ReportState) -> str:
    """노드 6: 최종 검증 후 재시도 라우터 (현재는 단순 종료)"""
    return "end"


# ==================== LangGraph 워크플로우 구성 ====================
def build_report_graph() -> StateGraph:
    """리포트 생성 LangGraph 구성 (디베이트 검증 + 선택적 재실행)"""
    logger.debug("[build_report_graph] LangGraph 워크플로우 컴파일 시작")
    workflow = StateGraph(ReportState)

    # 노드 추가
    workflow.add_node("prompt_optimizer", prompt_optimizer_node)
    workflow.add_node("query_planner", query_planner_node)
    workflow.add_node("retriever", retriever_node)
    workflow.add_node("analysis", analysis_parallel_node)  # 초기 실행 + 선택적 재실행 통합
    workflow.add_node("debate_validation", debate_validation_node)
    workflow.add_node("final_recommendation", final_recommendation_node)
    workflow.add_node("validation", validation_node)

    # 엣지 정의
    workflow.set_entry_point("prompt_optimizer")
    workflow.add_edge("prompt_optimizer", "query_planner")
    workflow.add_edge("query_planner", "retriever")
    workflow.add_edge("retriever", "analysis")
    workflow.add_edge("analysis", "debate_validation")

    # 디베이트 검증 후 조건부 라우팅 (재실행 OR 진행)
    workflow.add_conditional_edges(
        "debate_validation",
        debate_router_node,
        {
            "analysis": "analysis",  # 품질 부족 → 재실행 (analysis 노드로 돌아감)
            "final_recommendation": "final_recommendation",  # 통과 → 진행
        },
    )

    # 최종 권고 → 검증
    workflow.add_edge("final_recommendation", "validation")

    # 최종 검증 후 라우팅
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
    session_id: str,
    candidate_name: str,
    axes_keys: List[str],
    user_prompt: Optional[str] = None,
    has_portfolio: bool = False,
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    리포트 생성 - LangGraph 기반 (비동기 스트리밍)

    Yields:
        - type: "log"    -> 에이전트 진행 로그
        - type: "debate" -> 디베이트 로그
        - type: "error"  -> 에러 메시지
        - type: "report" -> 최종 리포트
    """
    logger.debug("[create_report_async] 시작")
    graph = build_report_graph()

    initial_state: ReportState = {
        "session_id": session_id,
        "candidate_name": candidate_name,
        "axes_keys": axes_keys,
        "user_prompt": user_prompt,
        "has_portfolio": has_portfolio,
        "optimized_prompt": None,
        "query_plan": None,
        "query_hint": None,
        "retry_count": 0,
        "jd_ctx": None,
        "resume_ctx": None,
        "portfolio_ctx": None,
        "log_ctx": None,
        "comp_id_list": None,
        "eid_list": None,
        "qa_pairs": None,
        "interview_analysis": None,
        "competency_eval": None,
        "evidence_mapping": None,
        "interview_summary": None,
        "final_recommendation": None,
        "debate_validation_results": None,
        "debate_retry_count": 0,  # 디베이트 재시도 횟수
        "retry_targets": None,  # 재실행 대상
        "retry_feedbacks": None,  # 재실행 피드백
        "validation_result": None,
        "report": None,
        "agent_logs": [],
        "error": None,
    }

    debate_sent = False
    debate_validation_sent = set()  # 이미 전송한 디베이트 검증 로그 추적

    async for event in graph.astream(initial_state):
        for _, state in event.items():
            # 로그 스트리밍
            if "agent_logs" in state and state["agent_logs"]:
                new_logs = state["agent_logs"][-1:]
                for log in new_logs:
                    # 디베이트 검증 로그는 별도 처리
                    if "debate_turns" in log:
                        # 디베이트 검증 로그를 별도 이벤트로 스트리밍
                        topic = log["agent"].replace("디베이트 로그 - ", "")
                        if topic not in debate_validation_sent:
                            debate_validation_sent.add(topic)
                            yield {
                                "type": "debate_validation",
                                "agent": topic,
                                "turns": log["debate_turns"],
                            }
                    else:
                        # 일반 로그
                        yield {
                            "type": "log",
                            "agent": log["agent"],
                            "message": log["message"],
                        }

            # 인터뷰 분석 디베이트 로그 스트리밍
            if (
                not debate_sent
                and state.get("interview_analysis")
                and isinstance(state["interview_analysis"], dict)
            ):
                debate = state["interview_analysis"].get("debate_log") or []
                if debate:
                    debate_sent = True
                    yield {
                        "type": "debate",
                        "agent": "인터뷰분석",
                        "turns": debate,
                    }

            # 에러 발생 시 종료
            if state.get("error"):
                yield {
                    "type": "error",
                    "message": state["error"],
                }
                return

            # 최종 리포트
            if state.get("report"):
                # 디베이트 검증 결과도 리포트에 포함
                if state.get("debate_validation_results"):
                    state["report"]["debate_validation_results"] = state["debate_validation_results"]

                yield {
                    "type": "report",
                    "data": state["report"],
                }


# ==================== 동기 래퍼 ====================
def create_report(
    session_id: str,
    candidate_name: str,
    axes_keys: List[str],
    user_prompt: Optional[str] = None,
    has_portfolio: bool = False,
) -> Dict[str, Any]:
    """
    리포트 생성 - 동기 버전

    Returns:
        리포트 결과 딕셔너리
    """
    logger.debug("[create_report] 시작")
    import asyncio as _asyncio

    async def _run():
        final_report = None
        async for event in create_report_async(
            session_id, candidate_name, axes_keys, user_prompt, has_portfolio
        ):
            if event["type"] == "error":
                return _error("AGENT_ERROR", event["message"])
            elif event["type"] == "report":
                final_report = event["data"]

        return final_report or _error("NO_REPORT", "리포트 생성 실패")

    try:
        loop = _asyncio.get_event_loop()
    except RuntimeError:
        loop = _asyncio.new_event_loop()
        _asyncio.set_event_loop(loop)

    return loop.run_until_complete(_run())
