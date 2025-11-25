from __future__ import annotations

import re
import json
import asyncio
from typing import AsyncGenerator

from flask import Blueprint, render_template, request, jsonify, Response, stream_with_context
from pydantic import ValidationError
from app.db.db_connection import get_connection

# PDF 생성을 위한 Playwright (별도 설치 필요)
try:
    from playwright.sync_api import sync_playwright
except Exception:  # pragma: no cover - 런타임 환경에 따라 없을 수 있음
    sync_playwright = None

# report_agent에서 create_report, create_report_async 가져오기
from app.agents.report_agent_v4o import create_report, create_report_async

"""
리포트 생성/검증 HTTP 라우트. LangGraph 기반 report_agent와 연동
- report_bp: HTML 패널
- reports_bp: JSON API (url_prefix=/reports)
"""

report_bp = Blueprint("report", __name__)
reports_bp = Blueprint("reports", __name__)

_DEFAULT_AXES_KEYS = [
    "문제해결",
    "커뮤니케이션",
    "학습능력",
    "협업능력",
    "전문성",
]


def _detect_has_portfolio(session_id: str, fallback: bool) -> bool:
    """DB에 portfolio 문서가 있는지 확인하고, 없으면 fallback 사용"""
    if not session_id:
        return fallback
    conn = None
    cur = None
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM rag.documents WHERE session_id = %s AND doc_type = 'portfolio'",
            (session_id,),
        )
        count = cur.fetchone()[0]
        return count > 0 or fallback
    except Exception as e:
        print(f"⚠️  포트폴리오 확인 실패(session_id={session_id}): {e}")
        return fallback
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


def _normalize_axes_keys(raw_axes) -> list[str]:
    """axes_keys 입력을 5개로 정규화"""
    if not raw_axes:
        return _DEFAULT_AXES_KEYS.copy()
    axes = []
    if isinstance(raw_axes, str):
        axes = [a.strip() for a in raw_axes.split(",") if a and a.strip()]
    elif isinstance(raw_axes, (list, tuple)):
        axes = [str(a).strip() for a in raw_axes if str(a).strip()]
    axes = axes[:5]
    while len(axes) < 5:
        axes.append(_DEFAULT_AXES_KEYS[len(axes)])
    return axes



@report_bp.route("/panel/report")
def report_panel():
    return render_template("agents/report.html", report={})


@report_bp.route("/panel/report/view")
def report_view():
    """리포트 전용 뷰 (사이드바 없이 리포트만 표시)"""
    return render_template("agents/report_view.html")

@report_bp.route("/panel/report/lab")
def report_lab():
    """독립적인 리포트 생성 테스트 페이지"""
    return render_template("agents/report_lab.html")


@report_bp.route("/reports/pdf", methods=["GET", "POST"])
def report_pdf():
    """
    리포트 PDF 다운로드용 엔드포인트 (Playwright 사용)
    - POST(JSON): {"report": <ReportOut 딕셔너리>, "session_id": "...", "save_to_server": true} → 서버에 저장 및 DB 업데이트
    - GET: session_id, axes_keys, user_prompt 로 에이전트를 다시 호출해 PDF 생성
    """
    if sync_playwright is None:
        err = {
            "status": "failed",
            "code": "PLAYWRIGHT_MISSING",
            "message": "Playwright가 설치되어 있지 않아 PDF를 생성할 수 없습니다. (pip install playwright 후 playwright install 필요)",
        }
        return jsonify(err), 500

    # 1) report JSON이 직접 넘어온 경우: 재생성 없이 그대로 사용
    report = None
    session_id_from_request = None
    save_to_server = False
    
    if request.method == "POST" and request.is_json:
        data = request.get_json(silent=True) or {}
        report = data.get("report") or None
        session_id_from_request = data.get("session_id")
        save_to_server = data.get("save_to_server", False)

    # 2) JSON이 없으면 기존처럼 에이전트를 호출해 생성
    if report is None:
        from app.routes.state_routes import GLOBAL_STATE

        session_id = request.args.get("session_id") or GLOBAL_STATE.session_id
        metrics = GLOBAL_STATE.metrics
        user_prompt = request.args.get("user_prompt", "")
        
        # URL 파라미터에서 우선 가져오고, 없으면 GLOBAL_STATE에서
        candidate_name = request.args.get("name") or GLOBAL_STATE.candidate_name or "지원자"
        position = request.args.get("position") or GLOBAL_STATE.position or "-"

        FALLBACK_SESSION_ID = "7ac7d019-0c29-4af8-abd3-8bf18f4544bf"
        if not session_id:
            session_id = FALLBACK_SESSION_ID
        else:
            try:
                from app.db.db_connection import get_connection

                conn = get_connection()
                cur = conn.cursor()
                cur.execute(
                    "SELECT COUNT(*) FROM rag.documents WHERE session_id = %s",
                    (session_id,),
                )
                doc_count = cur.fetchone()[0]
                cur.execute(
                    "SELECT COUNT(*) FROM rag.interview_logs WHERE session_id = %s",
                    (session_id,),
                )
                log_count = cur.fetchone()[0]
                cur.close()
                conn.close()

                if doc_count == 0 and log_count == 0:
                    session_id = FALLBACK_SESSION_ID
            except Exception:
                session_id = FALLBACK_SESSION_ID

        if metrics:
            axes_keys = metrics
        else:
            axes_keys_raw = request.args.get("axes_keys")
            if axes_keys_raw:
                axes_keys = [k.strip() for k in axes_keys_raw.split(",") if k.strip()]
            else:
                axes_keys = _DEFAULT_AXES_KEYS.copy()

        if not axes_keys or len(axes_keys) != 5:
            axes_keys = _DEFAULT_AXES_KEYS.copy()

        try:
            has_portfolio = _detect_has_portfolio(
                session_id=session_id,
                fallback=bool(getattr(GLOBAL_STATE, "portfolio_len", 0)),
            )
            report = create_report(
                session_id=session_id,
                candidate_name=candidate_name,
                position_applied=position,
                axes_keys=axes_keys,
                user_prompt=user_prompt,
                has_portfolio=has_portfolio,
                jd_id=getattr(GLOBAL_STATE, "jd_id", None), 
            )
        except Exception as e:
            err = {
                "status": "failed",
                "code": "AGENT_ERROR",
                "message": f"리포트 생성 실패: {str(e)}",
            }
            return jsonify(err), 500

        if report.get("status") == "failed":
            return jsonify(report), 500

    # 3) PDF 템플릿 렌더링
    html = render_template("agents/report_view_pdf.html", report=report)

    # 4) Playwright로 HTML → PDF 변환
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.set_content(html, wait_until="load")
            pdf_bytes = page.pdf(format="A4", print_background=True)
            browser.close()
    except Exception as e:
        err = {
            "status": "failed",
            "code": "PDF_RENDER_ERROR",
            "message": f"PDF 생성 실패: {str(e)}",
        }
        return jsonify(err), 500

    # 파일명용 세션 ID 결정
    meta = report.get("metadata", {}) if isinstance(report, dict) else {}
    filename_session = session_id_from_request or meta.get("session_id") or locals().get("session_id", "report")
    
    filename = f"{filename_session}_report.pdf"
    
    # 서버에 저장 요청이 있는 경우
    if save_to_server and filename_session:
        import os
        from pathlib import Path
        import psycopg2
        from psycopg2.extras import RealDictCursor
        
        # uploads/reports 디렉토리 생성
        reports_dir = Path("uploads/reports")
        reports_dir.mkdir(parents=True, exist_ok=True)
        
        # PDF 파일 저장
        pdf_path = reports_dir / filename
        with open(pdf_path, "wb") as f:
            f.write(pdf_bytes)
        
        print(f"✅ PDF 저장 완료: {pdf_path}")
        
        # DB에 report_path 업데이트
        try:
            conn = psycopg2.connect(
                host=os.getenv("DB_HOST", "localhost"),
                port=os.getenv("DB_PORT", "5432"),
                database=os.getenv("DB_NAME", "postgres"),
                user=os.getenv("DB_USER", "postgres"),
                password=os.getenv("DB_PASSWORD", "")
            )
            cur = conn.cursor(cursor_factory=RealDictCursor)
            
            relative_path = f"reports/{filename}"
            cur.execute("""
                UPDATE interview.candidates
                SET report_path = %s
                WHERE session_id = %s
            """, (relative_path, filename_session))
            
            conn.commit()
            cur.close()
            conn.close()
            
            print(f"✅ DB 업데이트 완료: report_path = {relative_path}")
            
            # JSON 응답 반환 (다운로드 URL 포함)
            return jsonify({
                "success": True,
                "message": "PDF가 서버에 저장되었습니다.",
                "file_path": str(pdf_path),
                "download_url": f"/api/files/{relative_path}"
            }), 200
            
        except Exception as e:
            print(f"❌ DB 업데이트 실패: {e}")
            import traceback
            traceback.print_exc()
            return jsonify({
                "success": False,
                "error": f"DB 업데이트 실패: {str(e)}"
            }), 500
    
    # 일반 다운로드 (save_to_server=false인 경우)
    resp = Response(pdf_bytes, mimetype="application/pdf")
    resp.headers["Content-Disposition"] = f"attachment; filename={filename}"
    return resp


@reports_bp.route("/generate/stream", methods=["GET"])
def generate_stream():
    """
    리포트 생성 - 스트리밍 버전 (Server-Sent Events)
    에이전트 대화를 실시간으로 전송
    """
    from app.routes.state_routes import GLOBAL_STATE

    # session_id, axes_keys 파싱
    session_id = request.args.get('session_id') or GLOBAL_STATE.session_id
    metrics = GLOBAL_STATE.metrics
    user_prompt = request.args.get('user_prompt', '')
    
    # URL 파라미터에서 우선 가져오고, 없으면 GLOBAL_STATE에서
    candidate_name = request.args.get('name') or GLOBAL_STATE.candidate_name or "지원자"
    position = request.args.get('position') or GLOBAL_STATE.position or "-"

    # 폴백 session_id 처리
    FALLBACK_SESSION_ID = "7ac7d019-0c29-4af8-abd3-8bf18f4544bf"
    if not session_id:
        session_id = FALLBACK_SESSION_ID
    else:
        try:
            from app.db.db_connection import get_connection
            conn = get_connection()
            cur = conn.cursor()
            cur.execute('SELECT COUNT(*) FROM rag.documents WHERE session_id = %s', (session_id,))
            doc_count = cur.fetchone()[0]
            cur.execute('SELECT COUNT(*) FROM rag.interview_logs WHERE session_id = %s', (session_id,))
            log_count = cur.fetchone()[0]
            cur.close()
            conn.close()

            if doc_count == 0 and log_count == 0:
                session_id = FALLBACK_SESSION_ID
        except Exception:
            session_id = FALLBACK_SESSION_ID

    if metrics:
        axes_keys = metrics
    else:
        axes_keys_raw = request.args.get('axes_keys')
        if axes_keys_raw:
            axes_keys = [k.strip() for k in axes_keys_raw.split(',') if k.strip()]
        else:
            axes_keys = _DEFAULT_AXES_KEYS.copy()

    if not axes_keys or len(axes_keys) != 5:
        axes_keys = _DEFAULT_AXES_KEYS.copy()
    
    jd_id = getattr(GLOBAL_STATE, 'jd_id', None)
    has_portfolio = _detect_has_portfolio(
        session_id=session_id,
        fallback=bool(getattr(GLOBAL_STATE, "portfolio_len", 0)),
    )

    async def async_generate():
        """비동기 SSE 이벤트 생성기"""
        try:
            async for event in create_report_async(
                session_id=session_id,
                candidate_name=candidate_name,
                position_applied=position,
                axes_keys=axes_keys,
                user_prompt=user_prompt,
                has_portfolio=has_portfolio,
                jd_id=jd_id,
            ):
                if event["type"] == "debate":
                    yield f"event: debate\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                elif event["type"] == "error":
                    yield f"event: error\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                elif event["type"] == "report":
                    yield f"event: report\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                elif event["type"] == "done":
                    yield f"event: done\ndata: {json.dumps({'status': 'completed'}, ensure_ascii=False)}\n\n"
                    return

        except Exception as e:
            error_event = {
                "type": "error",
                "message": f"스트리밍 오류: {str(e)}"
            }
            yield f"event: error\ndata: {json.dumps(error_event, ensure_ascii=False)}\n\n"

    def generate():
        """동기 래퍼 - 비동기 제너레이터를 동기로 변환"""
        loop = None
        async_gen = None
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            async_gen = async_generate()

            while True:
                try:
                    # async_generate에서 한 이벤트 받아오기
                    event_data = loop.run_until_complete(async_gen.__anext__())
                    yield event_data
                except StopAsyncIteration:
                    break

        except Exception as e:
            error_event = {
                "type": "error",
                "message": f"래퍼 오류: {str(e)}"
            }
            yield f"event: error\ndata: {json.dumps(error_event, ensure_ascii=False)}\n\n"
        finally:
            if async_gen is not None:
                try:
                    loop.run_until_complete(async_gen.aclose())
                except Exception:
                    pass
            if loop and not loop.is_closed():
                loop.close()

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no'
        }
    )


@reports_bp.route("/lab/generate", methods=["POST"])
def lab_generate():
    """독립 테스트용 리포트 생성 엔드포인트"""
    data = request.get_json(silent=True) or {}
    session_id = data.get("session_id") or ""
    candidate_name = data.get("candidate_name") or "지원자"
    position = data.get("position") or "-"
    user_prompt = data.get("user_prompt") or ""
    jd_id = data.get("jd_id")
    axes_keys = _normalize_axes_keys(data.get("axes_keys"))
    has_portfolio = bool(data.get("has_portfolio", False))

    try:
        report = create_report(
            session_id=session_id,
            candidate_name=candidate_name,
            position_applied=position,
            axes_keys=axes_keys,
            user_prompt=user_prompt,
            has_portfolio=has_portfolio,
            jd_id=jd_id,
        )
    except Exception as e:
        return jsonify({"status": "failed", "message": str(e)}), 500

    if isinstance(report, dict) and report.get("status") == "failed":
        return jsonify(report), 400
    return jsonify(report)


@reports_bp.route("/lab/render", methods=["POST"])
def lab_render():
    """클라이언트가 전달한 report JSON을 바로 HTML로 렌더링"""
    data = request.get_json(silent=True) or {}
    report = data.get("report")
    if not report:
        return jsonify({"status": "failed", "message": "report payload가 없습니다"}), 400
    html = render_template("agents/report_view_pdf.html", report=report)
    return Response(html, mimetype="text/html")


