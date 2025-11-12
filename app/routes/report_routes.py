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
    
    print(f"[reports/generate] 요청 시작")
    print(f"   - session_id: {data.get('session_id')}")
    print(f"   - axes_keys: {data.get('axes_keys')}")

    try:
        # 1. session_id 필수 체크
        session_id = data.get('session_id')
        if not session_id:
            return jsonify({
                "status": "failed",
                "code": "missing_session_id",
                "message": "session_id가 필요합니다"
            }), 400
        
        # 2. axes_keys 파싱 (state.metrics에서 전달된 값)
        axes_keys = _parse_axes_keys(data)
        if len(axes_keys) != 5:
            axes_keys = _DEFAULT_AXES_KEYS.copy()
        
        print(f"[reports/generate] axes_keys: {axes_keys}")
        print(f"[reports/generate] 보고서 생성 시작 (VectorDB + interview_store 기반)...")

        # 3. 폴백용 텍스트 (선택적)
        from pathlib import Path
        resume_text = ""
        jd_text = ""
        log_text = ""
        
        # 폴백 경로가 제공된 경우에만 읽기
        if data.get("resume_path"):
            resume_path = Path(data["resume_path"])
            resume_text = resume_path.read_text(encoding="utf-8") if resume_path.exists() else ""
        
        if data.get("jd_path"):
            jd_path = Path(data["jd_path"])
            jd_text = jd_path.read_text(encoding="utf-8") if jd_path.exists() else ""
        
        if data.get("log_path"):
            log_path = Path(data["log_path"])
            log_text = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
        
        print(f"[reports/generate] 폴백 텍스트 - resume: {len(resume_text)} chars, jd: {len(jd_text)} chars, log: {len(log_text)} chars")

        # 4. report_agent 호출 (VectorDB + interview_store 기반)
        rpt = create_report(
            session_id=session_id,
            axes_keys=axes_keys,
            resume_text=resume_text,
            jd_text=jd_text,
            log_text=log_text
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

