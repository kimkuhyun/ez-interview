from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple, TypedDict
from uuid import uuid4

from langgraph.graph import StateGraph, END

from app.agents.report import ReportError, ReportOut
from app.agents.report.TEMP_report_db import apply_feedback as db_apply_feedback
from app.agents.report.TEMP_report_db import get as db_get_report
from app.agents.report.TEMP_report_db import save as db_save
from app.agents.report.report_competency import (
    RetrievalResult,
    build_retrieval_context,
    generate_report_draft,
)
from app.agents.report.report_jd_evidence import guard_grounding
from app.agents.report.report_summary import ensure_talk_tracks
from app.agents.report.report_validator import validate_schema, verify_report
from app.agents.report.report_writer import finalize_report, normalize_score_payload
from app.agents.report.TEMP_report_db import read_texts as db_read_texts

ROOT = Path(__file__).resolve().parents[1]

"""
LangGraph 기반 보고서 파이프라인 에이전트.
- 입력 로딩 → 검색 → 초안 생성 → 요약 검증 → JD/증거 검증 → 재검증 → 최종화 순.
"""


class ReportState(TypedDict, total=False):
    resume_path: str
    jd_path: str
    log_path: str
    axes_keys: List[str]
    resume_text: str
    jd_text: str
    log_text: str
    context: Tuple[str, str, str]
    evidence_ids: List[str]
    jd_ids: List[str]
    rag_stats: Dict[str, Any]
    draft: Dict[str, Any]
    report: Dict[str, Any]
    verify_meta: Dict[str, Any]


def _resolve_path(value: str) -> Path:
    candidate = (value or "").strip().replace("\\", "/")
    for option in [
        Path(candidate),
        ROOT / candidate,
        ROOT / "data" / Path(candidate).name,
        ROOT / "app" / "data" / Path(candidate).name,
    ]:
        if option.exists():
            return option
    return Path(candidate)

def _format_error(code: str, message: str, *, http: int = 400, details: Dict[str, Any] | None = None) -> Dict[str, Any]:
    return {
        "status": "failed",
        "code": code,
        "message": message,
        "http": http,
        "trace_id": uuid4().hex,
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "details": details or {},
    }


def _load_inputs(state: ReportState) -> ReportState:
    resume, jd, log = db_read_texts(state["resume_path"], state["jd_path"], state.get("log_path", "") or "")
    return {
        **state,
        "resume_text": resume,
        "jd_text": jd,
        "log_text": log,
    }


def _retrieve_context(state: ReportState) -> ReportState:
    result: RetrievalResult = build_retrieval_context(
        state["resume_text"],
        state["jd_text"],
        state.get("log_text", ""),
        state["axes_keys"],
    )
    return {
        **state,
        "context": result.context,
        "evidence_ids": result.evidence_ids,
        "jd_ids": result.jd_ids,
        "rag_stats": result.stats,
    }


def _draft_report(state: ReportState) -> ReportState:
    draft = generate_report_draft(state["context"], state["axes_keys"])
    return {**state, "draft": draft, "report": draft}


def _summary_node(state: ReportState) -> ReportState:
    report = ensure_talk_tracks(dict(state["report"]))
    return {**state, "report": report}


def _grounding_node(state: ReportState) -> ReportState:
    report = guard_grounding(dict(state["report"]), state.get("evidence_ids", []), state.get("jd_ids", []))
    return {**state, "report": report}


def _verify_node(state: ReportState) -> ReportState:
    jd_ctx, resume_ctx, log_ctx = state["context"]
    refined, meta = verify_report(jd_ctx, resume_ctx, log_ctx, dict(state["report"]))
    return {**state, "report": refined, "verify_meta": meta}


def _finalize_node(state: ReportState) -> ReportState:
    report = dict(state["report"])
    report = normalize_score_payload(report)
    validate_schema(report, state["axes_keys"])
    final = finalize_report(report, rag_stats=state.get("rag_stats", {}), verify_meta=state.get("verify_meta", {}))
    db_save(final)
    return {**state, "report": final}


def _build_graph() -> StateGraph:
    graph = StateGraph(ReportState)
    graph.add_node("load_inputs", _load_inputs)
    graph.add_node("retrieve_context", _retrieve_context)
    graph.add_node("draft_report", _draft_report)
    graph.add_node("summary", _summary_node)
    graph.add_node("grounding", _grounding_node)
    graph.add_node("verify", _verify_node)
    graph.add_node("finalize", _finalize_node)
    graph.set_entry_point("load_inputs")
    graph.add_edge("load_inputs", "retrieve_context")
    graph.add_edge("retrieve_context", "draft_report")
    graph.add_edge("draft_report", "summary")
    graph.add_edge("summary", "grounding")
    graph.add_edge("grounding", "verify")
    graph.add_edge("verify", "finalize")
    graph.add_edge("finalize", END)
    return graph


GRAPH = _build_graph().compile()


def create_report_from_files(resume_path: str, jd_path: str, log_path: str, axes_keys: List[str]) -> Dict[str, Any]:
    try:
        state = GRAPH.invoke(
            {
                "resume_path": resume_path,
                "jd_path": jd_path,
                "log_path": log_path,
                "axes_keys": axes_keys,
            }
        )
        return state["report"]
    except ReportError as exc:
        return _format_error(exc.code, exc.message, http=exc.http, details=exc.details)
    except Exception as exc:  # pragma: no cover - defensive
        return _format_error("INTERNAL", f"내부 오류: {type(exc).__name__}: {exc}", http=500)


def validate_and_save(report_json: Dict[str, Any]) -> Dict[str, Any]:
    normalized = normalize_score_payload(dict(report_json))
    payload = ReportOut(
        axes=normalized["axes"],
        scores=[{"key": k, "value": v} for k, v in normalized["scores"].items()],
        weights=[{"key": k, "value": v} for k, v in normalized["weights"].items()],
        headline=normalized["headline"],
        talkSummary=normalized["talkSummary"],
        convStats=normalized.get("convStats", []),
        jdCoverage=normalized["jdCoverage"],
        evidence=normalized["evidence"],
    ).model_dump(mode="json")
    payload["scores"] = normalized["scores"]
    payload["weights"] = normalized["weights"]
    final = finalize_report(payload, rag_stats={"max_sim": 0.0, "selected": 0}, verify_meta={"attempts": 0})
    return db_save(final)


def get_report(report_id: str) -> Dict[str, Any] | None:
    return db_get_report(report_id)


def apply_feedback(report_id: str, patch: Dict[str, Any]) -> Dict[str, Any] | None:
    return db_apply_feedback(report_id, patch)
