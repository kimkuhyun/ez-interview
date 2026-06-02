"""
report_agent_v5.py — 멀티에이전트 평가 리포트 그래프 (v5 · 실제 디베이트)

v4o 대비 핵심 개선 (피드백: "랭그래프가 너무 단순하다" 대응)
------------------------------------------------------------------
v4o 의 한계:
  - 각 분석 에이전트가 자기 결과의 quality_score 를 "스스로" 매기는 self-scoring 구조였다.
    (debate_topic / debate_log 필드만 있고 실제 토론/검증 주체가 없음 → 가짜 디베이트)

v5 의 변화:
  1) [신규] 증거 교차검증 노드(node_evidence_crosscheck)
       - 역량 근거(EID)가 실제 면접 로그·JD 에 트레이스되는지 교차 확인 → 환각/약한근거 차단
  2) [신규] 적대적 디베이트 검증 노드(node_debate_critic)
       - 독립된 3개 Critic 에이전트가 "반박(refute)" 관점으로 분석 결과를 공격
       - 각 Critic 은 서로 다른 렌즈(점수타당성 / 증거-발언일치 / 모순·환각)를 가짐
       - self-scoring 이 아니라 제3자 적대적 검증으로 품질을 판정
  3) [신규] 디베이트 라우터(node_debate_router)
       - Critic verdict 기준으로 "실패한 분석만" 선택적 재실행 (피드백 주입)
       - 데이터 부족 → 재검색(retrieve), 품질 부족 → 선택적 재분석(parallel)
       - 최대 2 라운드 후 강제 통과(무한 루프 방지)

그래프 토폴로지
------------------------------------------------------------------
START → optimize → query_plan → retrieve ◄────────────┐(데이터 재검색)
        → parallel(역량·요약·품질) ◄──────────────┐    │(선택적 재실행)
        → evidence_crosscheck                     │    │
        → debate_critic (Critic A/B/C 병렬, 반박)  │    │
        → debate_router ──────────────────────────┴────┘
            └─(통과/최대라운드) → final_consensus → assemble → END
"""
from __future__ import annotations

import asyncio
from typing import Any, AsyncGenerator, Dict, List, Optional

from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import PydanticOutputParser
from pydantic import BaseModel, Field
from typing_extensions import TypedDict, Literal
from langgraph.graph import StateGraph, START, END

# v4o 의 검증된 구성요소 재사용 (LLM 팩토리 / 스키마 / 프롬프트 빌더 / 노드)
from app.agents.report_agent_v4o import (
    ReportState,
    CompAgentOut, SummaryAgentOut, QualityAgentOut,
    build_system_prompt,
    _llm_solar_reasoning, _llm_gpt,
    node_optimize_prompt, node_query_plan, node_retrieve,
    node_final_agent, node_assemble_report,
)

# 디베이트 최대 재검증 라운드
MAX_DEBATE_ROUNDS = 2
# Critic 통과 임계값(품질 0~1)
DEBATE_PASS_THRESHOLD = 0.75


# ==================== v5 확장 스키마 ====================
class EvidenceCheckRow(BaseModel):
    eid: str = Field(default="", description="검증 대상 근거 ID")
    traceable: bool = Field(default=True, description="면접 로그/문서에 실제로 추적 가능한가")
    link_strength: float = Field(default=1.0, description="근거-발언 연결 강도 (0~1)")
    note: str = Field(default="", description="검증 코멘트")


class EvidenceCrossCheckOut(BaseModel):
    """증거 교차검증 결과 — 환각/약한근거 탐지"""
    rows: List[EvidenceCheckRow] = Field(default_factory=list)
    hallucination_flags: List[str] = Field(default_factory=list, description="컨텍스트 근거가 없는 주장 EID 목록")
    weak_links: List[str] = Field(default_factory=list, description="연결강도가 약한 EID 목록")
    quality: float = Field(default=1.0, description="교차검증 종합 품질 (0~1)")
    summary: str = Field(default="", description="한 줄 요약")


class CriticVerdict(BaseModel):
    """단일 Critic 의 적대적 판정"""
    topic: str = Field(default="", description="검증 주제")
    target: Literal["competency", "summary", "quality", "evidence"] = Field(
        default="competency", description="재실행이 필요할 경우의 대상 노드"
    )
    is_valid: bool = Field(default=True, description="반박을 견뎌냈는가(유효한가)")
    quality: float = Field(default=1.0, description="검증 품질 점수 (0~1)")
    refutation: str = Field(default="", description="반박 시도 내용/결과")
    suggestions: List[str] = Field(default_factory=list, description="재실행 시 반영할 개선 제안")


class DebateOut(BaseModel):
    """디베이트 종합 결과"""
    verdicts: List[CriticVerdict] = Field(default_factory=list)
    avg_quality: float = Field(default=1.0)
    passed: bool = Field(default=True, description="모든 Critic 통과 여부")


class ReportStateV5(ReportState):
    """v4o ReportState 확장 — 디베이트/교차검증/선택적 재실행 필드 추가"""
    crosscheck_out: Optional[EvidenceCrossCheckOut]
    debate_out: Optional[DebateOut]
    debate_round: int
    retry_targets: Optional[List[str]]
    retry_feedbacks: Optional[Dict[str, List[str]]]


# ==================== 분석 실행 헬퍼 (초기/재실행 공용) ====================
def _comp_chain(feedback: Optional[List[str]] = None):
    extra = _feedback_block(feedback)
    prompt = ChatPromptTemplate.from_messages([
        ("system", build_system_prompt("comp_agent") + extra),
        ("user", "has_interview: {has_interview}\nresume: {resume}\nportfolio: {portfolio}\n"
                 "interview: {interview}\naxes: {axes}\njd_text: {jd}\n\n{format_instructions}")
    ])
    parser = PydanticOutputParser(pydantic_object=CompAgentOut)
    return prompt | _llm_solar_reasoning() | parser, parser


def _summary_chain(feedback: Optional[List[str]] = None):
    extra = _feedback_block(feedback)
    prompt = ChatPromptTemplate.from_messages([
        ("system", build_system_prompt("summary_agent") + extra),
        ("user", "interview: {interview}\n\n{format_instructions}")
    ])
    parser = PydanticOutputParser(pydantic_object=SummaryAgentOut)
    return prompt | _llm_solar_reasoning() | parser, parser


def _quality_chain(feedback: Optional[List[str]] = None):
    extra = _feedback_block(feedback)
    prompt = ChatPromptTemplate.from_messages([
        ("system", build_system_prompt("quality_agent") + extra),
        ("user", "resume: {resume}\nportfolio: {portfolio}\ninterview: {interview}\njd_text: {jd}\n\n{format_instructions}")
    ])
    parser = PydanticOutputParser(pydantic_object=QualityAgentOut)
    return prompt | _llm_gpt() | parser, parser


def _feedback_block(feedback: Optional[List[str]]) -> str:
    if not feedback:
        return ""
    body = "\n".join(f"- {fb}" for fb in feedback)
    return ("\n\n[디베이트 피드백 — 반드시 반영]\n이전 결과가 적대적 검증을 통과하지 못했습니다. "
            f"아래 지적을 해결하세요:\n{body}\n")


# ==================== 노드: 병렬 분석 (선택적 재실행 지원) ====================
async def node_parallel_analysis_v5(state: ReportStateV5) -> Dict[str, Any]:
    """초기 실행 + 디베이트 피드백 기반 선택적 재실행을 단일 노드로 처리."""
    resume = state.get("resume_ctx") or state.get("resume_text") or ""
    portfolio = state.get("portfolio_ctx") or state.get("portfolio_text") or ""
    jd = state.get("jd_ctx") or state.get("jd_text") or ""
    interview = state.get("interview_ctx") or state.get("interview_logs") or ""
    axes = state["axes"]
    has_interview = bool(interview and interview.strip())

    retry_targets = state.get("retry_targets") or []
    is_retry = len(retry_targets) > 0
    feedbacks = state.get("retry_feedbacks") or {}

    print(f"\n[병렬 분석 v5] {'선택적 재실행 ' + str(retry_targets) if is_retry else '초기 실행'} "
          f"/ 인터뷰: {'있음' if has_interview else '없음'}")

    common = {"resume": resume, "portfolio": portfolio, "interview": interview, "jd": jd}
    tasks, names = [], []

    # 인터뷰가 없으면 역량만 (v4o 동작 유지)
    if not has_interview:
        chain, parser = _comp_chain()
        comp_out = await chain.ainvoke({**common, "has_interview": has_interview, "axes": axes,
                                        "format_instructions": parser.get_format_instructions()})
        return {
            "comp_out": comp_out,
            "summary_out": _empty_summary(),
            "quality_out": _empty_quality(),
            "retry_targets": [],   # 인터뷰 없음: 재실행 대상 없음(빈 결과 반복 방지)
        }

    if not is_retry or "competency" in retry_targets:
        chain, parser = _comp_chain(feedbacks.get("competency"))
        tasks.append(chain.ainvoke({**common, "has_interview": has_interview, "axes": axes,
                                    "format_instructions": parser.get_format_instructions()}))
        names.append("comp_out")

    if not is_retry or "summary" in retry_targets:
        chain, parser = _summary_chain(feedbacks.get("summary"))
        tasks.append(chain.ainvoke({"interview": interview,
                                    "format_instructions": parser.get_format_instructions()}))
        names.append("summary_out")

    if not is_retry or "quality" in retry_targets:
        chain, parser = _quality_chain(feedbacks.get("quality"))
        tasks.append(chain.ainvoke({**common, "format_instructions": parser.get_format_instructions()}))
        names.append("quality_out")

    results = await asyncio.gather(*tasks)
    updates = dict(zip(names, results))
    # 재실행 후 타겟 초기화 (다음 라운드 오염 방지)
    updates["retry_targets"] = []
    return updates


# ==================== 노드: 증거 교차검증 (신규) ====================
async def node_evidence_crosscheck(state: ReportStateV5) -> Dict[str, Any]:
    """역량 근거(EID)가 실제 면접 로그/JD 에 트레이스되는지 교차 확인."""
    comp = state.get("comp_out")
    interview = state.get("interview_ctx") or state.get("interview_logs") or ""
    jd = state.get("jd_ctx") or state.get("jd_text") or ""

    if not comp or not interview.strip():
        return {"crosscheck_out": EvidenceCrossCheckOut(quality=0.0, summary="교차검증 대상 데이터 없음")}

    evidences_json = "\n".join(
        f"{e.eid} | {e.competency} | {e.evidence}" for e in (comp.evidences or [])
    )
    parser = PydanticOutputParser(pydantic_object=EvidenceCrossCheckOut)
    prompt = ChatPromptTemplate.from_messages([
        ("system", build_system_prompt("crosscheck_agent")),
        ("user", "다음 근거 목록의 각 EID 가 면접 로그(interview)에서 실제로 확인되는지, "
                 "그리고 JD 요구역량과 연결되는지 교차검증하라. 로그에 근거가 없으면 hallucination_flags 에, "
                 "연결이 약하면 weak_links 에 EID 를 넣어라.\n\n"
                 "[근거 목록]\n{evidences}\n\n[면접 로그]\n{interview}\n\n[JD]\n{jd}\n\n{format_instructions}")
    ])
    chain = prompt | _llm_gpt() | parser
    result = await chain.ainvoke({
        "evidences": evidences_json, "interview": interview, "jd": jd,
        "format_instructions": parser.get_format_instructions(),
    })
    print(f"[증거 교차검증] 품질 {result.quality:.2f} · 환각 {len(result.hallucination_flags)}건 "
          f"· 약한근거 {len(result.weak_links)}건")
    return {"crosscheck_out": result}


# ==================== 노드: 적대적 디베이트 검증 (신규) ====================
# 3개 Critic 렌즈 정의 (서로 다른 관점으로 분석 결과를 "반박")
_CRITIC_LENSES = [
    {
        "topic": "역량 점수 타당성", "target": "competency",
        "lens": "각 역량 점수가 근거로 정당화되는지, 점수 간 변별(최소 15점 편차)이 있는지, "
                "과대/과소 평가가 없는지를 '반박' 관점에서 공격하라. 근거가 빈약하면 is_valid=false.",
    },
    {
        "topic": "증거-발언 일치", "target": "evidence",
        "lens": "각 근거(EID)가 실제 면접 발언에 단단히 연결되는지, 단순 키워드 매칭이 아닌지를 '반박'하라. "
                "교차검증의 weak_links/hallucination 을 반영해 연결이 약하면 is_valid=false.",
    },
    {
        "topic": "모순·환각 탐지", "target": "quality",
        "lens": "리포트의 주장/요약/품질점수가 면접 컨텍스트와 모순되거나 과장됐는지를 '반박'하라. "
                "컨텍스트 밖 주장이 있으면 is_valid=false.",
    },
]


async def _run_critic(lens: Dict[str, str], payload: Dict[str, str]) -> CriticVerdict:
    parser = PydanticOutputParser(pydantic_object=CriticVerdict)
    prompt = ChatPromptTemplate.from_messages([
        ("system", build_system_prompt("critic_agent") +
         f"\n\n[이번 Critic 의 검증 렌즈]\n주제: {lens['topic']}\n{lens['lens']}\n"
         "너는 분석 결과를 옹호하지 말고 '틀렸다'를 입증하려 시도하라. 반박에 실패하면 is_valid=true."),
        ("user", "[역량 분석]\n{comp}\n\n[면접 요약]\n{summary}\n\n[품질 평가]\n{quality}\n\n"
                 "[증거 교차검증]\n{crosscheck}\n\n{format_instructions}")
    ])
    chain = prompt | _llm_gpt() | parser
    verdict = await chain.ainvoke({**payload, "format_instructions": parser.get_format_instructions()})
    # 렌즈의 기본 target/topic 보정
    verdict.topic = verdict.topic or lens["topic"]
    if verdict.target not in ("competency", "summary", "quality", "evidence"):
        verdict.target = lens["target"]
    return verdict


async def node_debate_critic(state: ReportStateV5) -> Dict[str, Any]:
    """독립 Critic 3개가 병렬로 분석 결과를 적대적으로 검증."""
    comp = state.get("comp_out")
    summary = state.get("summary_out")
    quality = state.get("quality_out")
    crosscheck = state.get("crosscheck_out")

    # 인터뷰 로그가 없으면 적대적 디베이트는 의미가 없음 → 통과 처리
    # (빈 요약/품질로 Critic 호출·재실행 라운드를 낭비하지 않음)
    interview = state.get("interview_ctx") or state.get("interview_logs") or ""
    if not (interview and interview.strip()):
        print("[디베이트] 인터뷰 로그 없음 → 검증 생략, 통과 처리")
        return {"debate_out": DebateOut(verdicts=[], avg_quality=0.0, passed=True)}

    payload = {
        "comp": comp.model_dump_json() if comp else "{}",
        "summary": summary.model_dump_json() if summary else "{}",
        "quality": quality.model_dump_json() if quality else "{}",
        "crosscheck": crosscheck.model_dump_json() if crosscheck else "{}",
    }

    print(f"\n[디베이트] Critic 3종 적대적 검증 시작 (round {state.get('debate_round', 0) + 1})")
    verdicts = await asyncio.gather(*[_run_critic(lens, payload) for lens in _CRITIC_LENSES])

    avg = sum(v.quality for v in verdicts) / max(len(verdicts), 1)
    passed = all(v.is_valid and v.quality >= DEBATE_PASS_THRESHOLD for v in verdicts)
    for v in verdicts:
        mark = "✓ 통과" if (v.is_valid and v.quality >= DEBATE_PASS_THRESHOLD) else "⚠ 개선"
        print(f"  - Critic[{v.topic}] {mark} ({v.quality:.2f})")

    return {"debate_out": DebateOut(verdicts=verdicts, avg_quality=avg, passed=passed)}


# ==================== 노드/라우터: 디베이트 결과 기반 선택적 재실행 ====================
def node_debate_router(state: ReportStateV5) -> Dict[str, Any]:
    """Critic verdict 로 재실행 대상을 정하고 라운드를 증가."""
    debate = state.get("debate_out")
    crosscheck = state.get("crosscheck_out")
    rnd = state.get("debate_round", 0)

    # 최대 라운드 → 강제 통과
    if not debate or debate.passed or rnd >= MAX_DEBATE_ROUNDS:
        if debate and not debate.passed and rnd >= MAX_DEBATE_ROUNDS:
            print(f"[라우터] 최대 라운드({MAX_DEBATE_ROUNDS}) 도달 → 강제 통과")
        return {"retry_targets": [], "retry_feedbacks": {}}

    # 실패한 Critic → 재실행 대상 + 피드백 수집
    target_map = {"evidence": "competency"}  # 증거 문제는 역량 분석 재실행으로 교정
    targets: List[str] = []
    feedbacks: Dict[str, List[str]] = {}
    for v in debate.verdicts:
        if v.is_valid and v.quality >= DEBATE_PASS_THRESHOLD:
            continue
        tgt = target_map.get(v.target, v.target)
        if tgt not in targets:
            targets.append(tgt)
        feedbacks.setdefault(tgt, []).extend(v.suggestions or ([v.refutation] if v.refutation else []))

    # 교차검증의 약한근거/환각도 역량 재실행 피드백에 합류
    if crosscheck and (crosscheck.weak_links or crosscheck.hallucination_flags):
        feedbacks.setdefault("competency", [])
        if crosscheck.weak_links:
            feedbacks["competency"].append(f"약한 근거 보강 필요: {', '.join(crosscheck.weak_links)}")
        if crosscheck.hallucination_flags:
            feedbacks["competency"].append(f"환각 근거 제거 필요: {', '.join(crosscheck.hallucination_flags)}")
        if "competency" not in targets:
            targets.append("competency")

    print(f"[라우터] 재실행 대상: {targets} (round {rnd + 1}/{MAX_DEBATE_ROUNDS})")
    updates: Dict[str, Any] = {"retry_targets": targets, "retry_feedbacks": feedbacks, "debate_round": rnd + 1}

    # 데이터 부족(환각 다수)으로 재검색하는 경우, 동일 쿼리 반복을 막기 위해 검색 질의를 강화
    if crosscheck and len(crosscheck.hallucination_flags) >= 3:
        qp = state.get("query_plan")
        if qp is not None:
            axes_hint = ", ".join(state.get("axes", [])[:3])
            boost = f"; 구체적 사례, 프로젝트 경험, 성과, {axes_hint}"
            updates["query_plan"] = qp.model_copy(update={
                "resume_query": (qp.resume_query or "") + boost,
                "competency_query": (qp.competency_query or "") + boost,
                "portfolio_query": ((qp.portfolio_query or "") + boost) if state.get("has_portfolio") else (qp.portfolio_query or ""),
            })
    return updates


def route_after_debate(state: ReportStateV5) -> str:
    """라우팅 분기: 재검색 / 선택적 재분석 / 최종."""
    targets = state.get("retry_targets") or []
    crosscheck = state.get("crosscheck_out")
    if not targets:
        return "final"
    # 환각이 다수면 데이터 자체가 부족 → 재검색 우선
    if crosscheck and len(crosscheck.hallucination_flags) >= 3:
        return "retry_data"
    return "retry_selective"


# ==================== 그래프 빌더 ====================
def build_report_graph_v5() -> StateGraph:
    g = StateGraph(ReportStateV5)

    g.add_node("optimize", node_optimize_prompt)
    g.add_node("query_plan", node_query_plan)
    g.add_node("retrieve", node_retrieve)
    g.add_node("parallel", node_parallel_analysis_v5)
    g.add_node("crosscheck", node_evidence_crosscheck)
    g.add_node("debate", node_debate_critic)
    g.add_node("router", node_debate_router)
    g.add_node("final_agent", node_final_agent)
    g.add_node("assemble", node_assemble_report)

    g.add_edge(START, "optimize")
    g.add_edge("optimize", "query_plan")
    g.add_edge("query_plan", "retrieve")
    g.add_edge("retrieve", "parallel")
    g.add_edge("parallel", "crosscheck")
    g.add_edge("crosscheck", "debate")
    g.add_edge("debate", "router")

    g.add_conditional_edges(
        "router",
        route_after_debate,
        {
            "retry_data": "retrieve",       # 데이터 부족 → 재검색
            "retry_selective": "parallel",  # 품질 부족 → 실패 분석만 재실행
            "final": "final_agent",
        },
    )

    g.add_edge("final_agent", "assemble")
    g.add_edge("assemble", END)
    return g.compile()


# ==================== 기본값 헬퍼 ====================
def _empty_summary() -> SummaryAgentOut:
    from app.agents.report_agent_v4o import Headline, InterviewSummary, InterviewStat
    return SummaryAgentOut(
        headline=Headline(oneline_summary="인터뷰 미진행", tag=["데이터 부족"]),
        interview_summary=InterviewSummary(interview_summary="인터뷰가 진행되지 않았습니다.", items=[]),
        interview_stat=InterviewStat(),
        quality_score=0.0, needs_retry=True, hints=["인터뷰 로그가 필요합니다"],
    )


def _empty_quality() -> QualityAgentOut:
    return QualityAgentOut(
        contradiction_score=0, depth_score=0, reliability_score=0,
        contradiction_reason="인터뷰 미진행", depth_reason="인터뷰 미진행", reliability_reason="인터뷰 미진행",
        positive_comment="이력서 기반 평가만 가능합니다.", improvement_comment="인터뷰 검증이 필요합니다.",
        quality_score=0.0, needs_retry=True, hints=["인터뷰 로그가 필요합니다"],
    )


# ==================== 공개 API (v5) ====================
def _initial_state_v5(session_id, candidate_name, position_applied, axes_keys,
                      user_prompt, resume_text, jd_text, portfolio_text,
                      interview_logs, has_portfolio, jd_id) -> ReportStateV5:
    return {
        "session_id": session_id, "candidate_name": candidate_name,
        "position_applied": position_applied, "jd_id": jd_id,
        "user_prompt": user_prompt or "표준 평가 기준으로 진행", "axes": axes_keys,
        "resume_text": resume_text, "jd_text": jd_text,
        "portfolio_text": portfolio_text, "interview_logs": interview_logs,
        "resume_ctx": None, "jd_ctx": None, "portfolio_ctx": None, "interview_ctx": None,
        "optimized_prompt": None, "query_plan": None,
        "comp_out": None, "summary_out": None, "quality_out": None,
        "final_out": None, "report": None,
        "retry_count": 0, "retry_mode": None, "has_portfolio": has_portfolio,
        # v5 전용
        "crosscheck_out": None, "debate_out": None, "debate_round": 0,
        "retry_targets": [], "retry_feedbacks": {},
    }


def create_report(session_id: str, candidate_name: str, position_applied: str,
                  axes_keys: List[str], user_prompt: Optional[str] = None,
                  resume_text: Optional[str] = None, jd_text: Optional[str] = None,
                  portfolio_text: Optional[str] = None, interview_logs: Optional[str] = None,
                  has_portfolio: bool = False, jd_id: Optional[str] = None) -> Dict[str, Any]:
    """리포트 생성 (Sync · v5 적대적 디베이트 그래프)"""
    try:
        if not session_id or not candidate_name or not axes_keys:
            return {"status": "failed", "error": "필수 파라미터 누락 (session_id, candidate_name, axes_keys)"}
        if len(axes_keys) != 5:
            return {"status": "failed", "error": f"axes_keys는 5개여야 합니다 (현재: {len(axes_keys)}개)"}
        graph = build_report_graph_v5()
        state = _initial_state_v5(session_id, candidate_name, position_applied, axes_keys,
                                  user_prompt, resume_text, jd_text, portfolio_text,
                                  interview_logs, has_portfolio, jd_id)
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            final_state = loop.run_until_complete(graph.ainvoke(state))
        finally:
            loop.close()
        report = final_state.get("report")
        if not report:
            return {"status": "failed", "error": "리포트 생성 실패 - 결과 없음"}
        return report.model_dump()
    except Exception as e:
        return {"status": "failed", "error": f"리포트 생성 중 오류: {str(e)}"}


async def create_report_async(session_id: str, candidate_name: str, position_applied: str,
                              axes_keys: List[str], user_prompt: Optional[str] = None,
                              resume_text: Optional[str] = None, jd_text: Optional[str] = None,
                              portfolio_text: Optional[str] = None, interview_logs: Optional[str] = None,
                              has_portfolio: bool = False, jd_id: Optional[str] = None
                              ) -> AsyncGenerator[Dict[str, Any], None]:
    """스트리밍 버전 (v5) — 노드별 debate 이벤트 + 증거 교차검증 + 적대적 Critic 결과를 yield"""
    try:
        if not session_id or not candidate_name or not axes_keys:
            yield {"type": "error", "message": "필수 파라미터 누락"}; return
        if len(axes_keys) != 5:
            yield {"type": "error", "message": f"axes_keys는 5개여야 합니다 (현재: {len(axes_keys)}개)"}; return

        print("\n" + "=" * 70 + "\n📊 [REPORT AGENT v5] 적대적 디베이트 그래프 시작\n" + "=" * 70)
        graph = build_report_graph_v5()
        state = _initial_state_v5(session_id, candidate_name, position_applied, axes_keys,
                                  user_prompt, resume_text, jd_text, portfolio_text,
                                  interview_logs, has_portfolio, jd_id)

        _AGENT_LABELS = [("optimized_prompt", "프롬프트 최적화"), ("query_plan", "쿼리 플래닝"),
                         ("comp_out", "역량 분석"), ("summary_out", "면접 요약"),
                         ("quality_out", "품질 평가"), ("final_out", "최종 평가")]

        async for event in graph.astream(state):
            for _node, out in event.items():
                if not isinstance(out, dict):
                    continue
                # 분석 에이전트 self-comment (v4o 호환 debate 이벤트)
                for key, label in _AGENT_LABELS:
                    obj = out.get(key)
                    if obj is not None and hasattr(obj, "debate_topic"):
                        yield {"type": "debate", "agent": "",
                               "message": getattr(obj, "self_comment", "") or f"{label} 완료",
                               "debate_topic": getattr(obj, "debate_topic", label),
                               "debate_log": list(getattr(obj, "debate_log", []) or [])}
                # 증거 교차검증 (신규)
                cc = out.get("crosscheck_out")
                if cc is not None:
                    yield {"type": "debate", "agent": "",
                           "message": cc.summary or "증거 교차검증 완료",
                           "debate_topic": "증거 교차검증",
                           "debate_log": [f"환각 {len(cc.hallucination_flags)}건 · 약한근거 {len(cc.weak_links)}건 · 품질 {cc.quality:.2f}"]}
                # 적대적 디베이트 (신규) — Critic별 verdict
                dbt = out.get("debate_out")
                if dbt is not None:
                    for v in dbt.verdicts:
                        ok = v.is_valid and v.quality >= DEBATE_PASS_THRESHOLD
                        yield {"type": "debate", "agent": "",
                               "message": f"[{v.topic}] {'✓ 통과' if ok else '⚠ 개선 필요'} ({v.quality:.2f})",
                               "debate_topic": "적대적 디베이트 검증",
                               "debate_log": ([v.refutation] if v.refutation else []) + list(v.suggestions or [])}
                # 최종 리포트
                rep = out.get("report")
                if rep is not None:
                    yield {"type": "report", "data": rep.model_dump()}

        yield {"type": "done", "status": "completed"}
    except Exception as e:
        yield {"type": "error", "message": f"리포트 생성 중 오류: {str(e)}"}
