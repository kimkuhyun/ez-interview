from __future__ import annotations

from typing import Any, Dict, List

from app.agents.report import ReportError

"""
talkSummary 필수 항목(인터뷰요약/긍정 의견/부정 의견) 점검 노드.
필수 토픽이 모두 포함돼 있는지 간단히 확인
"""

REQUIRED_TOPICS = {"인터뷰요약", "긍정 의견", "부정 의견"}


def _topic_index(items: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {item.get("주제", ""): item for item in items if isinstance(item, dict)}


def ensure_talk_tracks(report: Dict[str, Any]) -> Dict[str, Any]:
    """0
    UI에서 기대하는 필수 항목이 talkSummary에 포함되었는지 확인합니다.
    """
    talk = report.get("talkSummary") or {}
    items = talk.get("items") or []
    if not isinstance(items, list):
        raise ReportError("STRUCT_TALKS", "talkSummary.items는 리스트여야 합니다")
    index = _topic_index(items)
    missing = [topic for topic in REQUIRED_TOPICS if topic not in index]
    if missing:
        raise ReportError("STRUCT_MISSING", f"talkSummary 필수 항목 누락: {', '.join(missing)}")
    report["talkSummary"] = {"items": items}
    return report

