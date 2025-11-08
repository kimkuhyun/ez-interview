from __future__ import annotations

from typing import Dict, List

from app.agents.report import ReportError

"""
JD-증거(J/E ID) 참조의 유효성을 검사하는 가드 노드.
검색 컨텍스트에 존재하는 ID만 사용했는지 확인합니다.
"""


def guard_grounding(report: Dict, allowed_eids: List[str], allowed_jids: List[str]) -> Dict:
    """
    검색 컨텍스트 내에서 jdCoverage/evidence의 참조 일관성을 검사합니다.
    """
    eids = set(allowed_eids)
    jids = set(allowed_jids)

    for row in report.get("jdCoverage", []):
        jid = row.get("jid")
        if jid not in jids:
            raise ReportError("JID_UNKNOWN", f"알 수 없는 JID {jid}")
        for evidence in row.get("근거", []):
            if evidence not in eids:
                raise ReportError("EID_UNKNOWN", f"알 수 없는 EID {evidence}")

    for ev in report.get("evidence", []):
        if ev.get("jid") not in jids:
            raise ReportError("JID_UNKNOWN", f"알 수 없는 JID {ev.get('jid')}")
        if ev.get("eid") not in eids:
            raise ReportError("EID_UNKNOWN", f"알 수 없는 EID {ev.get('eid')}")

    return report
