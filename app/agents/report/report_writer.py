from __future__ import annotations

from datetime import datetime
from typing import Any, Dict
from uuid import uuid4

"""
리포트 최종화 유틸리티
- 점수/가중치 포맷 정규화 및 메타데이터 부착을 담당합니다.
"""


def normalize_score_payload(report: Dict[str, Any]) -> Dict[str, Any]:
    """
    점수/가중치가 딕셔너리 형태인지 정규화합니다.
    (후속 검증/가드 로직이 일관된 형태를 기대함)
    """
    scores = report.get("scores")
    if isinstance(scores, list):
        report["scores"] = {item["key"]: item["value"] for item in scores if isinstance(item, dict)}
    weights = report.get("weights")
    if isinstance(weights, list):
        report["weights"] = {item["key"]: item["value"] for item in weights if isinstance(item, dict)}
    return report


def finalize_report(report: Dict[str, Any], *, rag_stats: Dict[str, Any], verify_meta: Dict[str, Any]) -> Dict[str, Any]:
    """
    레거시 API와 동일한 형태가 되도록 식별자/메타데이터를 부착합니다.
    """
    report = normalize_score_payload(report)
    conv_stats = report.setdefault("convStats", [])
    conv_stats.append({"k": "rag_max_sim", "v": f"{rag_stats.get('max_sim', 0.0):.2f}"})
    conv_stats.append({"k": "rag_selected", "v": str(rag_stats.get("selected", 0))})
    report["status"] = "ok"
    report["report_id"] = uuid4().hex
    report["created_at"] = datetime.utcnow().isoformat() + "Z"
    report["meta"] = {"rag": rag_stats, "verify": verify_meta}
    return report
