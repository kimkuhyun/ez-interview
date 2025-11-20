
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, AsyncGenerator
import asyncio

from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import PydanticOutputParser
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field, ConfigDict
from typing_extensions import TypedDict, Literal
from langgraph.graph import StateGraph, START, END



from app.utils.rag_retriever import search_similar_chunks
from app.utils.interview_store import retrieve_interview_context

from pathlib import Path
POLICY_DIR = Path("report_prompt")
def load_policy_prompt(file_name: str) -> str:
    path = POLICY_DIR / f"{file_name}.md"
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()
def build_system_prompt(agent_policy_name: str) -> str:
    global_rules = load_policy_prompt("global_rules")
    agent_rules = load_policy_prompt(agent_policy_name)
    parts = []
    if global_rules:
        parts.append(global_rules)
    if agent_rules:
        parts.append(agent_rules)
    # 룰이 하나도 없으면 빈 문자열 반환
    return "\n\n".join(parts)
#==================== LLM 설정 ====================

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
SOLAR_API_KEY = os.getenv("SOLAR_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.1")
OPENAI_MODEL_CHAT = os.getenv("OPENAI_MODEL_CHAT", "gpt-4o-mini")
# Upstage Solar Pro 2 설정
SOLAR_MODEL = os.getenv("SOLAR_MODEL", "solar-pro2")
SOLAR_BASE_URL = "https://api.upstage.ai/v1"

def _llm_gpt() -> ChatOpenAI:
    """o3-mini 모델"""
    return ChatOpenAI(
        model=OPENAI_MODEL,
        timeout=90,
        api_key=OPENAI_API_KEY,
    )


def _llm_gpt_chat() -> ChatOpenAI:
    """GPT-4o 모델"""
    return ChatOpenAI(
        model=OPENAI_MODEL_CHAT,
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


# ==================== 핵심 역량/헤드라인 ====================
class candiMeta(BaseModel):
    candidate_name: str = Field(default="", description="후보자 이름")
    position_applied: str = Field(default="", description="지원 포지션")
    interview_date: str = Field(default="", description="면접 일자 (예: 2024-01-15)")
    report_date: str = Field(default="", description="보고서 작성 일자 (예: 2024-01-20)")
    session_id: str = Field(default="", description="면접 세션 ID")
    interviewer_name: str = Field(default="AI 평가 시스템", description="면접관 이름")


class Headline(BaseModel):
    oneline_summary: str = Field(default="", description="후보자에 대해 한 줄 요약 (40~60자)")
    tag: List[str] = Field(default_factory=list, description="후보자에 대해 대표 키워드 3~5개")

class InterviewSummaryItem(BaseModel):
    questions: str = Field(default="", description="면접 질문")
    answers: str = Field(default="", description="후보자 답변 요약")

class InterviewSummary(BaseModel):
    interview_summary: str = Field(default="", description="면접 요약문 (200~300자), PDF전용")
    items: List[InterviewSummaryItem] = Field(default_factory=list, description="면접 Q&A 요약 목록")

class ReportQuality(BaseModel):
    contradiction_score: int = Field(default=0, description="모순 정도 (0~100)")
    depth_score: int = Field(default=0, description="대화 깊이 (0~100)")
    reliabality_score: int = Field(default=0, description="신뢰도 (0~100)")
    contradiction_reason: str = Field(default="", description="모순도 판단 근거")
    depth_reason: str = Field(default="", description="대화 깊이 판단 근거")
    reliability_reason: str = Field(default="", description="신뢰도 판단 근거")

class InterviewComentary(BaseModel):
    positive_comment: str = Field(default="", description="후보자의 강점 (200~300자)")
    improvement_comment: str = Field(default="", description="후보자의 약점 (200~300자)")

class Compaxes(BaseModel):
    competency: str = Field(default="", description="핵심역량")
    score: int = Field(default=0, description="역량 점수 (0~100점)")

class CompCoverRow(BaseModel):
    competency: str = Field(default="", description="핵심역량 이름")
    expectation: str = Field(default="", description="핵심역량의 기대수준(기준)")
    fulfillment: int = Field(default=0, description="후보자의 역량 충족도 (0~100점)")
    comment: str = Field(default="", description="역량별 점수 이유,PDF전용")
    evidence_ids: List[str] = Field(default_factory=list, description="이 역량과 연결된 EID 목록 (E01, E02 등)")

class EvidenceRow(BaseModel):
    eid: str = Field(default="", description="행동면접 질문 ID (E01~E99)")
    evidence: str = Field(default="", description="핵심역량 관련 구체적 행동 사례")
    competency: str = Field(default="", description="관련 핵심역량")


class QuestionTypeRatio(BaseModel):
    """질문 유형별 비율"""
    type: str = Field(default="", description="질문 유형 (예: 사실관계파악, 기술질문, 커뮤니케이션질문)")
    ratio: str = Field(default="", description="비율 (예: 35%)")

class InterviewStat(BaseModel):
    duration: str = Field(default="정보 없음", description="면접 소요 시간 (예: 25분)")
    follow_up_avg: str = Field(default="정보 없음", description="후속 질문 평균 개수 (예: 2.3개)")
    question_type_ratio: List[QuestionTypeRatio] = Field(
        default_factory=list,
        description="질문 유형별 비율"
    )

class FinalRecommendation(BaseModel):
    final_comment: str = Field(default="", description="최종 코멘트(300~400자)")
    hiring_decision: Literal["추천", "비추천", "보류"] = Field(default="보류", description="최종 채용 결정 (추천/비추천/보류 중 하나만 선택)")

class ReportOut(BaseModel):
    metadata: candiMeta
    headline: Headline
    quality: ReportQuality
    axes: List[Compaxes] = Field(default_factory=list, description="핵심역량 축 목록 5개 (라벨 + 점수),그래프용")
    interview_summary: InterviewSummary
    interview_commentary: InterviewComentary
    compCoverage: List[CompCoverRow] = Field(default_factory=list, description="핵심역량 5개 별 기대수준, 점수, 매핑된 EID")
    evidences: List[EvidenceRow] = Field(default_factory=list, description="핵심역량 별 근거 목록 최소 5개")
    interview_stat: InterviewStat
    recommendation: FinalRecommendation
    
    
# ==================== 에이전트 스키마 ====================


class OptimizedPrompt(BaseModel):
    original_prompt: str = Field(
        default="",
        description="원본 사용자 프롬프트"
    )
    lens_perspective: str = Field(
        default="표준 평가 기준 적용",
        description="사용자 프롬프트를 해석한 핵심 평가 관점 요약",
    )
    key_focus_areas: List[str] = Field(
        default_factory=list,
        description="이번 평가에서 특히 중점적으로 볼 핵심 영역 키워드",
    )
    reasoning: str = Field(
        default="표준 평가 기준 적용",
        description="이 관점과 포커스를 선택한 이유 요약"
    )
    quality_score: float = Field(default=1.0, description="품질 점수")
    needs_retry: bool = Field(default=False, description="재시도 필요 여부")
    hints: List[str] = Field(default_factory=list, description="재시도 힌트")
    self_comment: str = Field(default="", description="한 줄 코멘트")
    debate_topic: str = Field(default="프롬프트 최적화", description="토론 주제")
    debate_log: List[str] = Field(default_factory=list, description="토론 로그")

class QueryPlan(BaseModel):
    resume_query: str = Field(
        default="후보자 경력 및 학력",
        description="이력서 벡터 검색 질의"
    )
    competency_query: str = Field(
        default="핵심역량 평가",
        description="핵심역량 관련 벡터 검색 질의"
    )
    interview_query: str = Field(
        default="면접 대화 내용",
        description="인터뷰 로그 벡터 검색 질의"
    )
    portfolio_query: str = Field(
        default="",
        description="포트폴리오 검색 질의 (없으면 빈 문자열)",
    )
    quality_score: float = Field(default=1.0, description="품질 점수")
    needs_retry: bool = Field(default=False, description="재시도 필요 여부")
    hints: List[str] = Field(default_factory=list, description="재시도 힌트")
    self_comment: str = Field(default="", description="한 줄 코멘트")
    debate_topic: str = Field(default="검색 쿼리 계획", description="토론 주제")
    debate_log: List[str] = Field(default_factory=list, description="토론 로그")
    reasoning: str = Field(
        default="표준 검색 전략 적용",
        description="각 질의를 이렇게 설계한 이유 요약"
    )

class CompAgentOut(BaseModel):
    axes: List[Compaxes] = Field(default_factory=list, description="핵심역량 축 목록 5개 (라벨 + 점수),그래프용")
    compCoverage: List[CompCoverRow] = Field(default_factory=list, description="핵심역량 5개 별 기대수준, 점수, 매핑된 EID,핵심역량별 코멘트 포함")
    evidences: List[EvidenceRow] = Field(default_factory=list, description="핵심역량 별 근거 목록 최소 5개")
    quality_score: float = Field(default=1.0, description="품질 점수")
    needs_retry: bool = Field(default=False, description="재시도 필요 여부")
    hints: List[str] = Field(default_factory=list, description="재시도 힌트")
    self_comment: str = Field(default="", description="한 줄 코멘트")
    debate_topic: str = Field(default="역량 분석", description="토론 주제")
    debate_log: List[str] = Field(default_factory=list, description="토론 로그")

class SummaryAgentOut(BaseModel):
    headline: Headline
    interview_summary: InterviewSummary
    interview_stat: InterviewStat
    quality_score: float = Field(default=1.0, description="품질 점수")
    needs_retry: bool = Field(default=False, description="재시도 필요 여부")
    hints: List[str] = Field(default_factory=list, description="재시도 힌트")
    self_comment: str = Field(default="", description="한 줄 코멘트")
    debate_topic: str = Field(default="요약 작성", description="토론 주제")
    debate_log: List[str] = Field(default_factory=list, description="토론 로그")

class QualityAgentOut(BaseModel):
    contradiction_score: int = Field(
        default=0,
        description="답변 모순도 (0=매우 일관적, 100=매우 모순적)"
    )
    depth_score: int = Field(
        default=0,
        description="대화 깊이 (0=매우 피상적, 100=매우 깊이 있음)"
    )
    reliability_score: int = Field(
        default=0,
        description="리포트 신뢰도 (0=신뢰 불가, 100=매우 신뢰 가능)"
    )

    contradiction_reason: str = Field(
        default="",
        description="모순도 판단 근거 (2~3문장)"
    )
    depth_reason: str = Field(
        default="",
        description="대화 깊이 판단 근거 (2~3문장)"
    )
    reliability_reason: str = Field(
        default="",
        description="신뢰도 판단 근거 (2~3문장)"
    )

    positive_comment: str = Field(
        default="",
        description="후보자의 강점 코멘트 (200~300자, ReportOut.interview_commentary.positive_comment 용)"
    )
    improvement_comment: str = Field(
        default="",
        description="후보자의 개선 필요 코멘트 (200~300자, ReportOut.interview_commentary.improvement_comment 용)"
    )
    quality_score: float = Field(default=1.0, description="품질 점수")
    needs_retry: bool = Field(default=False, description="재시도 필요 여부")
    hints: List[str] = Field(default_factory=list, description="재시도 힌트")
    self_comment: str = Field(default="", description="한 줄 코멘트")
    debate_topic: str = Field(default="품질 평가", description="토론 주제")
    debate_log: List[str] = Field(default_factory=list, description="토론 로그")

class FinalAgentOut(BaseModel):
    recommendation: FinalRecommendation
    quality_score: float = Field(default=1.0, description="품질 점수")
    needs_retry: bool = Field(default=False, description="재시도 필요 여부")
    hints: List[str] = Field(default_factory=list, description="재시도 힌트")
    self_comment: str = Field(default="", description="한 줄 코멘트")
    debate_topic: str = Field(default="최종 평가", description="토론 주제")
    debate_log: List[str] = Field(default_factory=list, description="토론 로그")

#========================== 랭그래프 ================================
class ReportState(TypedDict):
    session_id: str
    candidate_name: str
    position_applied: str
    user_prompt: str
    axes: List[str]
    jd_id: Optional[str]  # 🆕 JD ID 추가

    resume_text: Optional[str]
    jd_text: Optional[str]
    portfolio_text: Optional[str]
    interview_logs: Optional[str]

    resume_ctx: Optional[str]
    jd_ctx: Optional[str]
    portfolio_ctx: Optional[str]
    interview_ctx: Optional[str]

    optimized_prompt: Optional[OptimizedPrompt]
    query_plan: Optional[QueryPlan]
    comp_out: Optional[CompAgentOut]
    summary_out: Optional[SummaryAgentOut]
    quality_out: Optional[QualityAgentOut]
    final_out: Optional[FinalAgentOut]
    report: Optional[ReportOut]

    retry_count: int
    retry_mode: Optional[str]
#========================== 프롬프트 에이전트 노드 ===========================
def node_optimize_prompt(state: ReportState) -> Dict[str, Any]:
    parser = PydanticOutputParser(pydantic_object=OptimizedPrompt)
    prompt = ChatPromptTemplate.from_messages([
        ("system", build_system_prompt("optimized_agent")),
        ("user", "user_prompt: {user_prompt}\naxes: {axes}\njd_text: {jd_text}\n\n{format_instructions}")
    ])
    chain = prompt | _llm_solar_chat() | parser
    result = chain.invoke({
        "user_prompt": state["user_prompt"],
        "axes": state["axes"],
        "jd_text": state.get("jd_text") or "",
        "format_instructions": parser.get_format_instructions()
    })
    return {"optimized_prompt": result}
#========================== 쿼리플랜 에이전트 노드 ===========================
def node_query_plan(state: ReportState) -> Dict[str, Any]:
    parser = PydanticOutputParser(pydantic_object=QueryPlan)
    opt = state["optimized_prompt"]
    prompt = ChatPromptTemplate.from_messages([
        ("system", build_system_prompt("queryPlan_agent")),
        ("user", "OptimizedPrompt: {opt}\naxes: {axes}\njd_text: {jd_text}\n\n{format_instructions}")
    ])
    chain = prompt | _llm_solar_chat() | parser
    result = chain.invoke({
        "opt": opt.model_dump_json(),
        "axes": state["axes"],
        "jd_text": state.get("jd_text") or "",
        "format_instructions": parser.get_format_instructions()
    })
    return {"query_plan": result}
#========================== 리트리버 에이전트 노드 ===========================
def node_retrieve(state: ReportState) -> Dict[str, Any]:
    qp = state["query_plan"]
    session_id = state["session_id"]
    jd_id = state.get("jd_id")  # state에서 jd_id 가져오기
    
    print("\n" + "="*80)
    print("🔍 [RETRIEVE NODE] RAG 검색 시작")
    print("="*80)
    print(f"🎯 검색 파라미터:")
    print(f"   - session_id (resume/portfolio용): {session_id}")
    print(f"   - jd_id (JD용): {jd_id}")
    print(f"   - resume_query: {qp.resume_query[:100] if qp.resume_query else 'None'}...")
    print(f"   - competency_query (JD): {qp.competency_query[:100] if qp.competency_query else 'None'}...")
    print(f"   - portfolio_query: {qp.portfolio_query[:100] if qp.portfolio_query else 'None'}...")
    print("="*80)
    
    resume_ctx = search_similar_chunks(qp.resume_query, session_id=session_id, doc_type="resume", top_k=20) if qp.resume_query else ""
    print(f"✅ Resume 검색 완료: {len(str(resume_ctx))} 자")
    
    jd_ctx = search_similar_chunks(qp.competency_query, jd_id=jd_id, doc_type="jd", top_k=10) if qp.competency_query else ""
    print(f"✅ JD 검색 완료: {len(str(jd_ctx))} 자")
    
    interview_ctx = retrieve_interview_context(session_id) if session_id else ""
    print(f"✅ Interview 검색 완료: {len(str(interview_ctx))} 자")
    
    portfolio_ctx = search_similar_chunks(qp.portfolio_query, session_id=session_id, doc_type="portfolio", top_k=20) if qp.portfolio_query and qp.portfolio_query.strip() else None
    print(f"✅ Portfolio 검색 완료: {len(str(portfolio_ctx)) if portfolio_ctx else 0} 자")
    print("="*80 + "\n")
    
    return {
        "resume_ctx": resume_ctx,
        "jd_ctx": jd_ctx,
        "interview_ctx": interview_ctx,
        "portfolio_ctx": portfolio_ctx
    }
#========================== 병렬 분석 에이전트 노드 ===========================
async def node_parallel_analysis(state: ReportState) -> Dict[str, Any]:
    """인터뷰 로그가 있을 때만 실행되는 병렬 분석 노드"""
    resume = state.get("resume_ctx") or state.get("resume_text") or ""
    portfolio = state.get("portfolio_ctx") or state.get("portfolio_text") or ""
    interview = state.get("interview_ctx") or state.get("interview_logs") or ""
    jd = state.get("jd_ctx") or state.get("jd_text") or ""
    axes = state["axes"]
    
    # 인터뷰 로그 확인
    has_interview = bool(interview and interview.strip())
    
    print(f"\n🔍 [병렬 분석] 인터뷰 로그 존재 여부: {has_interview}")
    print(f"   - 인터뷰 로그 길이: {len(interview) if interview else 0}자")
    print(f"   - 이력서 데이터 길이: {len(resume) if resume else 0}자\n")
    
    comp_parser = PydanticOutputParser(pydantic_object=CompAgentOut)
    summary_parser = PydanticOutputParser(pydantic_object=SummaryAgentOut)
    quality_parser = PydanticOutputParser(pydantic_object=QualityAgentOut)
    
    comp_prompt = ChatPromptTemplate.from_messages([
        ("system", build_system_prompt("comp_agent")),
        ("user", "has_interview: {has_interview}\nresume: {resume}\nportfolio: {portfolio}\ninterview: {interview}\naxes: {axes}\njd_text: {jd}\n\n{format_instructions}")
    ])
    
    comp_chain = comp_prompt | _llm_solar_reasoning() | comp_parser
    tasks = []
    task_names = []
    
    # CompAgent는 항상 실행 (이력서 기반 역량 평가 가능)
    tasks.append(comp_chain.ainvoke({"has_interview": has_interview, "resume": resume, "portfolio": portfolio, "interview": interview, "axes": axes, "jd": jd, "format_instructions": comp_parser.get_format_instructions()}))
    task_names.append("comp")
    
    result = {"comp_out": None, "summary_out": None, "quality_out": None}
    
    # 인터뷰 로그가 있을 때만 SummaryAgent와 QualityAgent 실행
    if has_interview:
        summary_prompt = ChatPromptTemplate.from_messages([
            ("system", build_system_prompt("summary_agent")),
            ("user", "interview: {interview}\naxes: {axes}\njd_text: {jd}\n\n{format_instructions}")
        ])
        quality_prompt = ChatPromptTemplate.from_messages([
            ("system", build_system_prompt("quality_agent")),
            ("user", "resume: {resume}\nportfolio: {portfolio}\ninterview: {interview}\njd_text: {jd}\n\n{format_instructions}")
        ])
        
        summary_chain = summary_prompt | _llm_solar_chat() | summary_parser
        quality_chain = quality_prompt | _llm_solar_reasoning() | quality_parser
        
        tasks.append(summary_chain.ainvoke({"interview": interview, "axes": axes, "jd": jd, "format_instructions": summary_parser.get_format_instructions()}))
        task_names.append("summary")
        
        tasks.append(quality_chain.ainvoke({"resume": resume, "portfolio": portfolio, "interview": interview, "jd": jd, "format_instructions": quality_parser.get_format_instructions()}))
        task_names.append("quality")
        
        # 병렬 실행
        results = await asyncio.gather(*tasks)
        result["comp_out"] = results[0]
        result["summary_out"] = results[1]
        result["quality_out"] = results[2]
        
        print("✅ [병렬 분석] CompAgent, SummaryAgent, QualityAgent 완료")
    else:
        # 인터뷰 로그가 없으면 CompAgent만 실행
        results = await asyncio.gather(*tasks)
        result["comp_out"] = results[0]
        
        # 기본값 생성
        result["summary_out"] = SummaryAgentOut(
            headline=Headline(
                oneline_summary="인터뷰 미진행",
                tag=["데이터 부족"]
            ),
            interview_summary=InterviewSummary(
                interview_summary="인터뷰가 진행되지 않았습니다.",
                items=[]
            ),
            interview_stat=InterviewStat(),
            quality_score=0.0,
            needs_retry=True,
            hints=["인터뷰 로그가 필요합니다"],
            self_comment="인터뷰 데이터 없음"
        )
        
        result["quality_out"] = QualityAgentOut(
            contradiction_score=0,
            depth_score=0,
            reliability_score=0,
            contradiction_reason="인터뷰 미진행으로 평가 불가",
            depth_reason="인터뷰 미진행으로 평가 불가",
            reliability_reason="인터뷰 미진행으로 평가 불가",
            positive_comment="이력서 기반 평가만 가능합니다.",
            improvement_comment="인터뷰를 통한 검증이 필요합니다.",
            quality_score=0.0,
            needs_retry=True,
            hints=["인터뷰 로그가 필요합니다"]
        )
        
        print("⚠️  [병렬 분석] 인터뷰 로그 없음 - CompAgent만 실행, 기본값 반환")
    
    return result

#========================== 라우팅 및 재시도 노드 ===========================
def node_route(state: ReportState) -> Dict[str, Any]:
    """재시도 여부를 결정하는 라우팅 노드"""
    comp = state["comp_out"]
    summary = state["summary_out"]
    quality = state["quality_out"]
    retry = state["retry_count"]
    interview = state.get("interview_ctx") or state.get("interview_logs") or ""
    has_interview = bool(interview and interview.strip())
    
    print(f"\n🔀 [라우팅] 재시도 체크 (시도 횟수: {retry}/3)")
    print(f"   - 인터뷰 로그 존재: {has_interview}")
    print(f"   - Comp quality: {comp.quality_score:.2f}, needs_retry: {comp.needs_retry}")
    print(f"   - Summary quality: {summary.quality_score:.2f}, needs_retry: {summary.needs_retry}")
    print(f"   - Quality quality: {quality.quality_score:.2f}, needs_retry: {quality.needs_retry}\n")
    
    if retry >= 3:
        print("❌ [라우팅] 최대 재시도 횟수 초과 - 최종 단계로 진행\n")
        return {"retry_mode": "none"}
    
    # 인터뷰 로그가 없는 경우 데이터 부족으로 처리하지 않음 (정상 플로우)
    if not has_interview:
        print("ℹ️  [라우팅] 인터뷰 미진행 - 최종 단계로 진행\n")
        return {"retry_mode": "none"}
    
    # 1. 데이터 부족 우선 처리
    if comp.needs_retry or summary.needs_retry or quality.needs_retry:
        print("⚠️  [라우팅] 데이터 부족 감지 - 추가 데이터 검색\n")
        return {"retry_mode": "data"}
    
    # 2. 품질 점수 종합 평가
    avg_quality = (comp.quality_score + summary.quality_score + quality.quality_score) / 3
    
    # 3. 개별 품질 불량 체크
    if (comp.quality_score < 0.7 or 
        summary.quality_score < 0.7 or 
        quality.quality_score < 0.7 or 
        avg_quality < 0.75):
        print(f"⚠️  [라우팅] 품질 불량 감지 (평균: {avg_quality:.2f}) - 품질 개선 재시도\n")
        return {"retry_mode": "quality"}
    
    print(f"✅ [라우팅] 품질 기준 충족 (평균: {avg_quality:.2f}) - 최종 단계로 진행\n")
    return {"retry_mode": "none"}
#========================== 재시도 에이전트 노드 ===========================
def node_retry_retrieve(state: ReportState) -> Dict[str, Any]:
    session_id = state["session_id"]
    additional_ctx = retrieve_interview_context(session_id)
    current_ctx = state.get("interview_ctx") or ""
    
    return {
        "interview_ctx": current_ctx + "\n\n=== 추가 컨텍스트 ===\n" + additional_ctx,
        "retry_count": state["retry_count"] + 1
    }

async def node_retry_quality_full(state: ReportState) -> Dict[str, Any]:
    comp = state["comp_out"]
    summary = state["summary_out"]
    quality = state["quality_out"]
    
    interview = state.get("interview_ctx") or state.get("interview_logs") or ""
    resume = state.get("resume_ctx") or state.get("resume_text") or ""
    portfolio = state.get("portfolio_ctx") or state.get("portfolio_text") or ""
    jd = state.get("jd_ctx") or state.get("jd_text") or ""
    axes = state["axes"]
    
    all_hints = comp.hints + summary.hints + quality.hints
    hint_context = "\n\n[중요: 이전 시도의 문제점]\n" + \
               "\n".join(f"- {hint}" for hint in all_hints) + \
               "\n위 문제를 반드시 해결하세요."
    
    updates = {}
    tasks = []
    
    if comp.quality_score < 0.7:
        comp_parser = PydanticOutputParser(pydantic_object=CompAgentOut)
        comp_prompt = ChatPromptTemplate.from_messages([
            ("system", build_system_prompt("comp_agent") + hint_context),
            ("user", "resume: {resume}\nportfolio: {portfolio}\ninterview: {interview}\naxes: {axes}\njd_text: {jd}\n\n{format_instructions}")
        ])
        comp_chain = comp_prompt | _llm_solar_reasoning() | comp_parser
        tasks.append(("comp_out", comp_chain.ainvoke({"resume": resume, "portfolio": portfolio, "interview": interview, "axes": axes, "jd": jd, "format_instructions": comp_parser.get_format_instructions()})))
    
    if summary.quality_score < 0.7:
        summary_parser = PydanticOutputParser(pydantic_object=SummaryAgentOut)
        summary_prompt = ChatPromptTemplate.from_messages([
            ("system", build_system_prompt("summary_agent") + hint_context),
            ("user", "interview: {interview}\naxes: {axes}\njd_text: {jd}\n\n{format_instructions}")
        ])
        summary_chain = summary_prompt | _llm_solar_reasoning() | summary_parser
        tasks.append(("summary_out", summary_chain.ainvoke({"interview": interview, "axes": axes, "jd": jd, "format_instructions": summary_parser.get_format_instructions()})))
    
    if quality.quality_score < 0.7:
        quality_parser = PydanticOutputParser(pydantic_object=QualityAgentOut)
        quality_prompt = ChatPromptTemplate.from_messages([
            ("system", build_system_prompt("quality_agent") + hint_context),
            ("user", "resume: {resume}\nportfolio: {portfolio}\ninterview: {interview}\njd_text: {jd}\n\n{format_instructions}")
        ])
        quality_chain = quality_prompt | _llm_solar_reasoning() | quality_parser
        tasks.append(("quality_out", quality_chain.ainvoke({"resume": resume, "portfolio": portfolio, "interview": interview, "jd": jd, "format_instructions": quality_parser.get_format_instructions()})))
    
    if tasks:
        keys, coros = zip(*tasks)
        results = await asyncio.gather(*coros)
        updates = dict(zip(keys, results))

    updates["retry_count"] = state["retry_count"] + 1 
    return updates

#========================== 최종 에이전트 노드 ===========================

def node_final_agent(state: ReportState) -> Dict[str, Any]:
    comp = state["comp_out"]
    summary = state["summary_out"]
    opt = state["optimized_prompt"]
    jd = state.get("jd_text") or ""
    position = state.get("position_applied", "")
    interview = state.get("interview_ctx") or state.get("interview_logs") or ""
    has_interview = bool(interview and interview.strip())
    
    # 이력서 전체 텍스트
    resume = state.get("resume_ctx") or state.get("resume_text") or ""
    
    # 포트폴리오는 RAG로 관련 부분만 검색
    session_id = state["session_id"]
    portfolio_relevant = ""
    if state.get("has_portfolio"):
        # CompAgent의 evidence 기반으로 포트폴리오 관련 부분 검색
        evidence_keywords = " ".join([ev.evidence[:100] for ev in comp.evidences[:20]])  # 상위 5개 증거의 키워드
        search_query = f"JD 요구사항: {jd[:200]} 관련 경험: {evidence_keywords}"
        
        print(f"\n📂 [FinalAgent] 포트폴리오 관련 부분 검색")
        print(f"   - 검색 쿼리: {search_query[:150]}...")
        
        portfolio_chunks = search_similar_chunks(
            query=search_query,
            session_id=session_id,
            doc_type="portfolio",
            top_k=5
        )
        portfolio_relevant = "\n\n".join([chunk.get("content", "") for chunk in portfolio_chunks])
        print(f"   ✅ 포트폴리오 관련 부분 검색 완료: {len(portfolio_relevant)}자\n")

    final_prompt = ChatPromptTemplate.from_messages([
        ("system", build_system_prompt("final_agent")),
        (
            "user",
            "has_interview: {has_interview}\n"
            "position_applied: {position}\n"
            "jd: {jd}\n"
            "resume: {resume}\n"
            "portfolio_relevant: {portfolio_relevant}\n"
            "comp: {comp}\n"
            "summary: {summary}\n"
            "opt: {opt}"
        ),
    ])
    final_chain = final_prompt | _llm_gpt().with_structured_output(FinalAgentOut)
    final_out = final_chain.invoke(
        {
            "has_interview": has_interview,
            "position": position,
            "jd": jd,
            "resume": resume,
            "portfolio_relevant": portfolio_relevant,
            "comp": comp.model_dump_json(),
            "summary": summary.model_dump_json(),
            "opt": opt.model_dump_json(),
        }
    )

    return {"final_out": final_out}
#========================== 보고서 조립 노드 ===========================
def node_assemble_report(state: ReportState) -> Dict[str, Any]:
    comp = state["comp_out"]
    summary = state["summary_out"]
    quality = state["quality_out"]
    final = state["final_out"]
    
    print("\n" + "="*80)
    print("📋 [ASSEMBLE REPORT] 리포트 최종 조립")
    print("="*80)
    print("📊 메타데이터 확인:")
    print(f"   - candidate_name: {state['candidate_name']}")
    print(f"   - position_applied: {state['position_applied']}")
    print(f"   - session_id: {state['session_id']}")
    print(f"   - jd_id: {state.get('jd_id')}")
    print("="*80 + "\n")
    
    report = ReportOut(
        metadata=candiMeta(
            candidate_name=state["candidate_name"],
            position_applied=state["position_applied"],
            interview_date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            report_date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            session_id=state["session_id"]
        ),
        headline=summary.headline,
        quality=ReportQuality(
            contradiction_score=quality.contradiction_score,
            depth_score=quality.depth_score,
            reliabality_score=quality.reliability_score,
            contradiction_reason=quality.contradiction_reason,
            depth_reason=quality.depth_reason,
            reliability_reason=quality.reliability_reason
        ),
        axes=comp.axes,
        interview_summary=summary.interview_summary,
        interview_commentary=InterviewComentary(
            positive_comment=quality.positive_comment,
            improvement_comment=quality.improvement_comment
        ),
        compCoverage=comp.compCoverage,
        evidences=comp.evidences,
        interview_stat=summary.interview_stat,
        recommendation=final.recommendation
    )
    
    return {"report": report}

def route_decision(state: ReportState) -> str:
    mode = state.get("retry_mode")
    if mode == "data":
        return "retry_data"
    elif mode == "quality":
        return "retry_quality_full"
    return "final"

#========================== 랭그래프 빌더 ===========================
def build_report_graph() -> StateGraph:
    graph = StateGraph(ReportState)
    
    graph.add_node("optimize", node_optimize_prompt)
    graph.add_node("query_plan", node_query_plan)
    graph.add_node("retrieve", node_retrieve)
    graph.add_node("parallel", node_parallel_analysis)
    graph.add_node("route", node_route)
    graph.add_node("retry_data", node_retry_retrieve)
    graph.add_node("retry_quality_full", node_retry_quality_full)
    graph.add_node("final_agent", node_final_agent)
    graph.add_node("assemble", node_assemble_report)
    
    graph.add_edge(START, "optimize")
    graph.add_edge("optimize", "query_plan")
    graph.add_edge("query_plan", "retrieve")
    graph.add_edge("retrieve", "parallel")
    graph.add_edge("parallel", "route")
    
    graph.add_conditional_edges(
        "route",
        route_decision,
        {
            "retry_data": "retry_data",
            "retry_quality_full": "retry_quality_full",
            "final": "final_agent"
        }
    )

    graph.add_edge("retry_data", "retrieve")
    graph.add_edge("retry_quality_full", "final_agent")
    graph.add_edge("final_agent", "assemble")
    graph.add_edge("assemble", END)
    
    return graph.compile()
#========================== 공개 API ================================
def create_report(
    session_id: str,
    candidate_name: str,
    position_applied: str,
    axes_keys: List[str],
    user_prompt: Optional[str] = None,
    resume_text: Optional[str] = None,
    jd_text: Optional[str] = None,
    portfolio_text: Optional[str] = None,
    interview_logs: Optional[str] = None,
    has_portfolio: bool = False,
) -> Dict[str, Any]:
    from app.routes.state_routes import GLOBAL_STATE
    
    graph = build_report_graph()
    
    initial_state: ReportState = {
        "session_id": session_id,
        "candidate_name": candidate_name,
        "position_applied": position_applied,
        "jd_id": getattr(GLOBAL_STATE, "jd_id", None),  # 🆕 jd_id 추가
        "user_prompt": user_prompt or "표준 평가 기준으로 진행",
        "axes": axes_keys,
        "resume_text": resume_text,
        "jd_text": jd_text,
        "portfolio_text": portfolio_text,
        "interview_logs": interview_logs,
        "resume_ctx": None,
        "jd_ctx": None,
        "portfolio_ctx": None,
        "interview_ctx": None,
        "optimized_prompt": None,
        "query_plan": None,
        "comp_out": None,
        "summary_out": None,
        "quality_out": None,
        "final_out": None,
        "report": None,
        "retry_count": 0,
        "retry_mode": None
    }
    
    final_state = graph.invoke(initial_state)
    report = final_state["report"]
    
    return report.model_dump() if report else None

async def create_report_async(
    session_id: str,
    candidate_name: str,
    position_applied: str,
    axes_keys: List[str],
    user_prompt: Optional[str] = None,
    resume_text: Optional[str] = None,
    jd_text: Optional[str] = None,
    portfolio_text: Optional[str] = None,
    interview_logs: Optional[str] = None,
    has_portfolio: bool = False,
) -> AsyncGenerator[Dict[str, Any], None]:
    """스트리밍 버전 - 각 에이전트 실행 중 이벤트를 yield"""
    try:
        print("\n" + "="*80)
        print("📊 [REPORT AGENT] 리포트 생성 시작")
        print("="*80)
        print(f"👤 후보자 정보:")
        print(f"   - session_id: {session_id}")
        print(f"   - candidate_name: {candidate_name}")
        print(f"   - position_applied: {position_applied}")
        print(f"   - axes_keys: {axes_keys}")
        print(f"   - has_portfolio: {has_portfolio}")
        
        # GLOBAL_STATE에서 jd_id 가져오기
        from app.routes.state_routes import GLOBAL_STATE
        jd_id = getattr(GLOBAL_STATE, "jd_id", None)
        print(f"   - jd_id: {jd_id}")
        print("="*80 + "\n")
        
        graph = build_report_graph()
        
        initial_state: ReportState = {
            "session_id": session_id,
            "candidate_name": candidate_name,
            "position_applied": position_applied,
            "jd_id": jd_id,  # 🆕 jd_id 추가
            "user_prompt": user_prompt or "표준 평가 기준으로 진행",
            "axes": axes_keys,
            "resume_text": resume_text,
            "jd_text": jd_text,
            "portfolio_text": portfolio_text,
            "interview_logs": interview_logs,
            "resume_ctx": None,
            "jd_ctx": None,
            "portfolio_ctx": None,
            "interview_ctx": None,
            "optimized_prompt": None,
            "query_plan": None,
            "comp_out": None,
            "summary_out": None,
            "quality_out": None,
            "final_out": None,
            "report": None,
            "retry_count": 0,
            "retry_mode": None
        }
        
        # 그래프 스트리밍 실행
        async for event in graph.astream(initial_state):
            for node_name, node_output in event.items():
                # 각 노드 실행 후 debate 정보가 있으면 전송
                if isinstance(node_output, dict):
                    # OptimizedPrompt 에이전트
                    if "optimized_prompt" in node_output and node_output["optimized_prompt"]:
                        opt = node_output["optimized_prompt"]
                        yield {
                            "type": "debate",
                            "agent": "",
                            "message": opt.self_comment or "프롬프트 최적화 완료",
                            "debate_topic": opt.debate_topic,
                            "debate_log": opt.debate_log
                        }
                    
                    # QueryPlan 에이전트
                    if "query_plan" in node_output and node_output["query_plan"]:
                        qp = node_output["query_plan"]
                        yield {
                            "type": "debate",
                            "agent": "",
                            "message": qp.self_comment or "쿼리 계획 완료",
                            "debate_topic": qp.debate_topic,
                            "debate_log": qp.debate_log
                        }
                    
                    # CompAgentOut
                    if "comp_out" in node_output and node_output["comp_out"]:
                        comp = node_output["comp_out"]
                        yield {
                            "type": "debate",
                            "agent": "",
                            "message": comp.self_comment or "역량 분석 완료",
                            "debate_topic": comp.debate_topic,
                            "debate_log": comp.debate_log
                        }
                    
                    # SummaryAgentOut
                    if "summary_out" in node_output and node_output["summary_out"]:
                        summary = node_output["summary_out"]
                        yield {
                            "type": "debate",
                            "agent": "",
                            "message": summary.self_comment or "요약 완료",
                            "debate_topic": summary.debate_topic,
                            "debate_log": summary.debate_log
                        }
                    
                    # QualityAgentOut
                    if "quality_out" in node_output and node_output["quality_out"]:
                        quality = node_output["quality_out"]
                        yield {
                            "type": "debate",
                            "agent": "",
                            "message": quality.self_comment or "품질 평가 완료",
                            "debate_topic": quality.debate_topic,
                            "debate_log": quality.debate_log
                        }
                    
                    # FinalAgentOut
                    if "final_out" in node_output and node_output["final_out"]:
                        final = node_output["final_out"]
                        yield {
                            "type": "debate",
                            "agent": "",
                            "message": final.self_comment or "최종 평가 완료",
                            "debate_topic": final.debate_topic,
                            "debate_log": final.debate_log
                        }
                    
                    # 최종 리포트 전송
                    if "report" in node_output and node_output["report"]:
                        report = node_output["report"]
                        yield {
                            "type": "report",
                            "data": report.model_dump()
                        }
        
        # 완료 이벤트
        yield {"type": "done", "status": "completed"}
        
    except Exception as e:
        yield {
            "type": "error",
            "message": f"리포트 생성 중 오류: {str(e)}"
        }
