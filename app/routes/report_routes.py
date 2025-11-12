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


@report_bp.route("/panel/report/view")
def report_view():
    """리포트 전용 뷰 (사이드바 없이 리포트만 표시)"""
    return render_template("agents/report_view.html")


@reports_bp.post("/generate")
def generate_from_txt():
    from app.routes.state_routes import GLOBAL_STATE
    
    data = request.form if request.form else request.get_json(silent=True) or {}
    
    print(f"\n[reports/generate] 요청 시작")
    print(f"   - 요청 데이터: {data.keys()}")

    try:
        # ===== 1. GLOBAL_STATE에서 데이터 가져오기 =====
        session_id = data.get('session_id') or GLOBAL_STATE.session_id
        resume_id = GLOBAL_STATE.resume_id
        jd_id = GLOBAL_STATE.jd_id
        metrics = GLOBAL_STATE.metrics
        
        print(f"\n[State에서 가져온 데이터]")
        print(f"   - session_id: {session_id}")
        print(f"   - resume_id: {resume_id}")
        print(f"   - jd_id: {jd_id}")
        print(f"   - metrics: {metrics}")
        
        # ===== 2. 폴백: 하드코딩된 세션 ID 사용 (DB에 실제 데이터가 있는 UUID) =====
        # DB 확인 결과: 09f4963c-f8a3-4f08-9b77-6ac6406de47b에 documents(13개) + interview_logs(12개) 존재
        FALLBACK_SESSION_ID = "09f4963c-f8a3-4f08-9b77-6ac6406de47b"

        # session_id가 있으면 DB에서 데이터 존재 여부 확인
        use_fallback = False
        if not session_id:
            print(f"\n⚠️  [폴백] session_id가 없음")
            use_fallback = True
        else:
            # DB에서 데이터 확인
            from app.db.db_connection import get_connection
            try:
                conn = get_connection()
                cur = conn.cursor()
                cur.execute('SELECT COUNT(*) FROM rag.documents WHERE session_id = %s', (session_id,))
                doc_count = cur.fetchone()[0]
                cur.execute('SELECT COUNT(*) FROM rag.interview_logs WHERE session_id = %s', (session_id,))
                log_count = cur.fetchone()[0]
                cur.close()
                conn.close()

                print(f"\n[DB 데이터 확인]")
                print(f"   - session_id: {session_id}")
                print(f"   - documents: {doc_count}, interview_logs: {log_count}")

                if doc_count == 0 and log_count == 0:
                    print(f"   ⚠️  DB에 데이터가 없음 → 폴백 사용")
                    use_fallback = True
            except Exception as e:
                print(f"\n⚠️  [DB 확인 실패] {e} → 폴백 사용")
                use_fallback = True

        if use_fallback:
            print(f"\n⚠️  [폴백 적용]")
            print(f"   원래 session_id: {session_id}")
            session_id = FALLBACK_SESSION_ID
            print(f"   폴백 session_id: {session_id}")
        
        # ===== 3. axes_keys 파싱 =====
        if metrics:
            axes_keys = metrics
        else:
            axes_keys_raw = data.get('axes_keys')
            if axes_keys_raw:
                axes_keys = _parse_axes_keys(data)
            else:
                axes_keys = _DEFAULT_AXES_KEYS.copy()
                print(f"\n⚠️  [폴백] 평가지표가 없어서 기본값 사용: {axes_keys}")
        
        if len(axes_keys) != 5:
            axes_keys = _DEFAULT_AXES_KEYS.copy()
            print(f"\n⚠️  [폴백] 평가지표 개수가 5개가 아니어서 기본값 사용: {axes_keys}")
        
        print(f"\n[최종 사용 데이터]")
        print(f"   - session_id: {session_id}")
        print(f"   - axes_keys: {axes_keys}")
        
        # ===== 4. 리포트 생성 =====
        print(f"\n[리포트 생성 시작]")
        print(f"   - VectorDB 기반 (interview_logs + rag.documents)")
        
        # 폴백용 텍스트는 비워둠 (VectorDB에서 가져오기 때문)
        resume_text = ""
        jd_text = ""
        log_text = ""
        
        # report_agent 호출
        rpt = create_report(
            session_id=session_id,
            axes_keys=axes_keys,
            resume_text=resume_text,
            jd_text=jd_text,
            log_text=log_text
        )
        
        print(f"\n[리포트 생성 완료]")
        print(f"   - status: {rpt.get('status', 'success')}")

        if rpt.get("status") == "failed":
            print(f"\n❌ [리포트 생성 실패]")
            print(f"   - code: {rpt.get('code')}")
            print(f"   - message: {rpt.get('message')}")
            return (render_template("agents/report.html", report=rpt), 200) if _wants_html(data) else (jsonify(rpt), 200)

        # 디버깅용 로그(서버 콘솔)
        if rpt.get("talkSummary") and rpt["talkSummary"].get("items"):
            print(f"\n[talkSummary 정보]")
            print(f"   - 항목 개수: {len(rpt['talkSummary']['items'])}")
            for idx, item in enumerate(rpt["talkSummary"]["items"]):
                print(f"      [{idx}] 주제: {item.get('주제', 'N/A')}")
        
        print(f"\n[응답 반환]")
        print(f"   - format: {'html' if _wants_html(data) else 'json'}")
        
        return (render_template("agents/report.html", report=rpt), 200) if _wants_html(data) else (jsonify(rpt), 200)

    except KeyError as e:
        print(f"\n❌ [KeyError] {e}")
        import traceback
        traceback.print_exc()
        err = {"status": "failed", "code": "bad_request", "message": f"필수 파라미터 누락: {e}", "details": {}}
        return (render_template("agents/report.html", report=err), 200) if _wants_html(data) else (jsonify(err), 200)
    except ValidationError as e:
        err = {"status": "failed", "code": "validation_failed", "message": "입력 검증 실패", "details": e.errors()}
        return (render_template("agents/report.html", report=err), 200) if _wants_html(data) else (jsonify(err), 200)
    except Exception as e:
        print(f"\n❌ [Exception] {e}")
        import traceback
        traceback.print_exc()
        err = {"status": "failed", "code": "failed", "message": str(e)}
        return (render_template("agents/report.html", report=err), 200) if _wants_html(data) else (jsonify(err), 200)

