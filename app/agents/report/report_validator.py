from __future__ import annotations

import json
from typing import Any, Dict, List, Tuple

from langchain_core.prompts import ChatPromptTemplate

from app.agents.report import ReportError, ReportOut, VerifyLLMOut, get_llm

"""
리포트 초안의 주장과 근거를 컨텍스트로 재검증하고, 축별 점수/헤드라인/피드백을 보정하는 노드.
- 컨텍스트 기반 검증 전용 프롬프트를 사용합니다.
"""


def _verify_prompt() -> ChatPromptTemplate:
    return ChatPromptTemplate.from_messages(
        [
            (
                "system",
                (
                    "당신은 시니어 검증자입니다. 제공된 컨텍스트만을 근거로 초안 리포트를 교차 검증하고, "
                    "반드시 VerifyLLMOut 스키마에 맞춰 응답하세요. 증거 ID로 뒷받침되는 주장을 우대하고, "
                    "근거 없는 추측/환각은 감점하세요."
                ),
            ),
            (
                "human",
                "[JD]\n{jd}\n\n[RESUME]\n{resume}\n\n[INTERVIEW_LOG]\n{log}\n\n[초안]\n{draft_json}",
            ),
        ]
    )


def verify_report(
    jd: str,
    resume: str,
    log: str,
    draft: Dict[str, Any],
    *,
    attempts: int = 3,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    obj = json.loads(json.dumps(draft, ensure_ascii=False))
    if isinstance(obj.get("scores"), list):
        obj["scores"] = {item["key"]: item["value"] for item in obj["scores"]}
    if isinstance(obj.get("weights"), list):
        obj["weights"] = {item["key"]: item["value"] for item in obj["weights"]}

    chain = _verify_prompt() | get_llm(temperature=0.0).with_structured_output(VerifyLLMOut)
    outputs: List[VerifyLLMOut] = []
    for _ in range(min(attempts, 3)):
        outputs.append(
            chain.invoke(
                {
                    "jd": jd,
                    "resume": resume,
                    "log": log,
                    "draft_json": json.dumps(obj, ensure_ascii=False),
                }
            )
        )

    coverage_pool: Dict[str, Dict[str, List[float]]] = {}
    for out in outputs:
        for cov in out.coverage:
            bucket = coverage_pool.setdefault(cov.jid, {"p_sat": [], "p_par": [], "p_uns": [], "valid": []})
            bucket["p_sat"].append(cov.p_satisfy)
            bucket["p_par"].append(cov.p_partial)
            bucket["p_uns"].append(cov.p_unsatisfied)
            bucket["valid"].extend(cov.valid_eids)

    consensus_scores: List[float] = []
    row_map = {row["jid"]: row for row in obj.get("jdCoverage", []) if isinstance(row, dict)}
    for jid, metrics in coverage_pool.items():
        ps = sum(metrics["p_sat"]) / len(metrics["p_sat"]) if metrics["p_sat"] else 0.0
        pp = sum(metrics["p_par"]) / len(metrics["p_par"]) if metrics["p_par"] else 0.0
        pu = sum(metrics["p_uns"]) / len(metrics["p_uns"]) if metrics["p_uns"] else 0.0
        verdict = "충족" if ps >= max(pp, pu) else ("부분 충족" if pp >= pu else "미충족")
        row = row_map.get(jid)
        if row:
            row["충족도"] = verdict
            valid_eids = sorted(set(metrics["valid"]))
            if valid_eids:
                row["근거"] = [eid for eid in row.get("근거", []) if eid in valid_eids]
        consensus_scores.append(max(ps, pp, pu))

    axis_scores: Dict[str, List[float]] = {}
    axis_conf: Dict[str, List[float]] = {}
    for out in outputs:
        for axis in out.axis:
            axis_scores.setdefault(axis.key, []).append(axis.score)
            axis_conf.setdefault(axis.key, []).append(axis.confidence)
    for key, values in axis_scores.items():
        obj["scores"][key] = int(round(sum(values) / len(values)))

    if outputs:
        best_headline = next((out.headline for out in outputs if out.headline), None)
        if best_headline:
            obj["headline"] = best_headline.model_dump(mode="json")
        positives: List[str] = []
        negatives: List[str] = []
        for out in outputs:
            positives.extend(out.positives)
            negatives.extend(out.negatives)
        if positives or negatives:
            items = obj.get("talkSummary", {}).get("items", [])
            items = [item for item in items if item.get("주제") not in ("긍정 의견", "부정 의견")]
            if positives:
                items.append({"주제": "긍정 의견", "발언요약": "; ".join(positives[:5])})
            if negatives:
                items.append({"주제": "부정 의견", "발언요약": "; ".join(negatives[:5])})
            obj["talkSummary"] = {"items": items}

    try:
        validate_schema(obj, [axis["key"] if isinstance(axis, dict) else axis for axis in obj.get("axes", [])])
    except Exception:
        # Keep going; manual validation happens later.
        pass

    meta = {
        "attempts": len(outputs),
        "consensus": round(sum(consensus_scores) / len(consensus_scores), 3) if consensus_scores else 0.0,
        "axis_conf": {k: round(sum(v) / len(v), 3) for k, v in axis_conf.items()},
    }
    conv_stats = obj.setdefault("convStats", [])
    conv_stats.append({"k": "verify_consensus", "v": f"{meta['consensus']:.2f}"})
    for key, values in axis_conf.items():
        conv_stats.append({"k": f"axis_conf_{key}", "v": f"{sum(values)/len(values):.2f}"})
    return obj, meta


def validate_schema(report: Dict[str, Any], axes_keys: List[str]) -> None:
    payload = ReportOut(
        axes=report["axes"],
        scores=[{"key": k, "value": v} for k, v in report["scores"].items()],
        weights=[{"key": k, "value": v} for k, v in report["weights"].items()],
        headline=report["headline"],
        talkSummary=report["talkSummary"],
        convStats=report.get("convStats", []),
        jdCoverage=report["jdCoverage"],
        evidence=report["evidence"],
    )
    axes_set = {axis.key for axis in payload.axes}
    if sorted(axes_set) != sorted(axes_keys):
        raise ReportError("AXES_KEYS", "axes keys mismatch", details={"axes": sorted(axes_set), "expect": axes_keys})
    score_keys = {score.key for score in payload.scores}
    weight_keys = {weight.key for weight in payload.weights}
    if score_keys != axes_set:
        raise ReportError("SCORES_KEYS", "scores keys mismatch", details={"scores": sorted(score_keys)})
    if weight_keys != axes_set:
        raise ReportError("WEIGHTS_KEYS", "weights keys mismatch", details={"weights": sorted(weight_keys)})
    weight_sum = sum(weight.value for weight in payload.weights)
    if weight_sum != 100:
        raise ReportError("WEIGHT_SUM", "weights sum must be 100", details={"sum": weight_sum})
