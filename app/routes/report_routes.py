from __future__ import annotations

import re

from flask import Blueprint, render_template, request, jsonify
from pydantic import ValidationError

# report_agent_test에서 create_report_from_files만 가져오기
from app.agents.report_agent import create_report

# 나머지는 기존 report_agent에서 가져오기
"""
from app.agents.report_agent import (
    validate_and_save,
    get_report,
    apply_feedback,
)

"""

"""
리포트 생성/검증 HTTP 라우트. LangGraph 기반 report_agent와 연동
- report_bp: HTML 패널
- reports_bp: JSON API (url_prefix=/reports)
"""

report_bp = Blueprint("report", __name__)
reports_bp = Blueprint("reports", __name__)

_AXES_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,32}$")
_DEFAULT_AXES_KEYS = [
    "problem_solving",
    "communication",
    "self_driven_initiative",
    "collaboration",
    "professional_expertise",
]


def _wants_html(payload: dict) -> bool:
    return (payload.get("format") == "html")


def _parse_axes_keys(payload: dict) -> list[str]:
    raw = payload.get("axes_keys")
    if isinstance(raw, list):
        candidates = raw
    else:
        axes_keys_str = str(raw or "")
        candidates = axes_keys_str.split(",")

    normalized: list[str] = []
    seen: set[str] = set()
    for cand in candidates:
        key = str(cand or "").strip()
        if not key:
            continue
        key = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", key)
        key = re.sub(r"[^a-z0-9]+", "_", key, flags=re.IGNORECASE)
        key = re.sub(r"_+", "_", key).strip("_").lower()
        if not key:
            continue
        if not _AXES_KEY_PATTERN.fullmatch(key):
            continue
        if key in seen:
            continue
        normalized.append(key)
        seen.add(key)
        if len(normalized) == 5:
            break

    return normalized


@report_bp.route("/panel/report")
def report_panel():
    return render_template("agents/report.html", report={})


@reports_bp.post("/generate")
def generate_from_txt():
    data = request.form if request.form else request.get_json(silent=True) or {}
    
    print(f"[reports/generate] 요청 시작 - resume_path: {data.get('resume_path')}, jd_path: {data.get('jd_path')}, log_path: {data.get('log_path')}")

    try:
        axes_keys = _parse_axes_keys(data)
        # axes_keys가 없거나 5개가 아니면 테스트용 하드코딩 값으로 세팅
        if len(axes_keys) != 5:
            axes_keys = _DEFAULT_AXES_KEYS.copy()
        
        print(f"[reports/generate] axes_keys: {axes_keys}")
        print(f"[reports/generate] 보고서 생성 시작 (V2 with validation)...")

        # 파일 읽기
        from pathlib import Path
        resume_path = Path(data["resume_path"])
        jd_path = Path(data["jd_path"])
        log_path = Path(data.get("log_path", ""))
        
        resume_text = resume_path.read_text(encoding="utf-8") if resume_path.exists() else ""
        jd_text = jd_path.read_text(encoding="utf-8") if jd_path.exists() else ""
        log_text = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
        
        print(f"[reports/generate] 파일 읽기 완료 - resume: {len(resume_text)} chars, jd: {len(jd_text)} chars, log: {len(log_text)} chars")

        rpt = create_report(
            resume_text=resume_text,
            jd_text=jd_text,
            log_text=log_text,
            axes_keys=axes_keys,
        )
        
        print(f"[reports/generate] 보고서 생성 완료 - status: {rpt.get('status', 'success')}")

        if rpt.get("status") == "failed":
            # 실패인 경우에도 HTML은 200으로 화면 출력, JSON은 200 유지(기존 동작과 동일)
            print(f"[reports/generate] 생성 실패 - code: {rpt.get('code')}, message: {rpt.get('message')}")
            return (render_template("agents/report.html", report=rpt), 200) if _wants_html(data) else (jsonify(rpt), 200)

        # 디버깅용 로그(서버 콘솔)
        if rpt.get("talkSummary") and rpt["talkSummary"].get("items"):
            print(f"[reports] talkSummary.items: {len(rpt['talkSummary']['items'])}")
            for idx, item in enumerate(rpt["talkSummary"]["items"]):
                print(f"  [{idx}] 주제: {item.get('주제', 'N/A')}")
        
        print(f"[reports/generate] 응답 반환 - format: {'html' if _wants_html(data) else 'json'}")
        return (render_template("agents/report.html", report=rpt), 200) if _wants_html(data) else (jsonify(rpt), 200)

    except KeyError as e:
        print(f"[reports/generate] KeyError 발생: {e}")
        import traceback
        traceback.print_exc()
        err = {"status": "failed", "code": "bad_request", "message": f"필수 파라미터 누락: {e}", "details": {}}
        return (render_template("agents/report.html", report=err), 200) if _wants_html(data) else (jsonify(err), 200)
    except ValidationError as e:
        err = {"status": "failed", "code": "validation_failed", "message": "입력 검증 실패", "details": e.errors()}
        return (render_template("agents/report.html", report=err), 200) if _wants_html(data) else (jsonify(err), 200)
    except Exception as e:
        import traceback
        traceback.print_exc()
        err = {"status": "failed", "code": "failed", "message": str(e)}
        return (render_template("agents/report.html", report=err), 200) if _wants_html(data) else (jsonify(err), 200)


@reports_bp.post("/validate-and-save")
def validate_then_save():
    data = request.get_json(silent=True) or {}
    try:
        rpt = validate_and_save(data)
        return jsonify(rpt), 201
    except ValidationError as e:
        return jsonify({"error": "validation_failed", "detail": e.errors()}), 422


@reports_bp.get("/<rid>")
def read_report(rid: str):
    rpt = get_report(rid)
    if not rpt:
        return jsonify({"error": "not_found"}), 404
    return jsonify(rpt), 200


@reports_bp.post("/<rid>/feedback")
def patch_report(rid: str):
    patch = request.get_json(silent=True) or {}
    try:
        rpt = apply_feedback(rid, patch)
        if not rpt:
            return jsonify({"error": "not_found"}), 404
        return jsonify(rpt), 200
    except ValidationError as e:
        return jsonify({"error": "validation_failed", "detail": e.errors()}), 422
