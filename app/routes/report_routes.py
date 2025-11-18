from __future__ import annotations

import re
import json
import asyncio
from typing import AsyncGenerator

from flask import Blueprint, render_template, request, jsonify, Response, stream_with_context
from pydantic import ValidationError

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

_AXES_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,32}$")
_DEFAULT_AXES_KEYS = [
    "문제해결",
    "커뮤니케이션",
    "학습능력",
    "협업능력",
    "전문성",
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


@reports_bp.route("/pdf", methods=["GET", "POST"])
def report_pdf():
    """
    리포트 PDF 다운로드용 엔드포인트 (Playwright 사용)
    - POST(JSON): {"report": <ReportOut 딕셔너리>} → 재생성 없이 바로 PDF 변환
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
    if request.method == "POST" and request.is_json:
        data = request.get_json(silent=True) or {}
        report = data.get("report") or None

    # 2) JSON이 없으면 기존처럼 에이전트를 호출해 생성
    if report is None:
        from app.routes.state_routes import GLOBAL_STATE

        session_id = request.args.get("session_id") or GLOBAL_STATE.session_id
        metrics = GLOBAL_STATE.metrics
        user_prompt = request.args.get("user_prompt", "")
        candidate_name = GLOBAL_STATE.candidate_name or "지원자"

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
            report = create_report(
                session_id=session_id,
                candidate_name=candidate_name,
                axes_keys=axes_keys,
                user_prompt=user_prompt,
                has_portfolio=bool(getattr(GLOBAL_STATE, "portfolio_len", 0)),
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

    # 파일명용 세션 ID는 report 메타데이터에서 우선 사용
    meta = report.get("metadata", {}) if isinstance(report, dict) else {}
    filename_session = meta.get("session_id")
    if not filename_session:
        # GET 방식 호출에서만 session_id가 정의되어 있음
        filename_session = locals().get("session_id", "report")

    filename = f"interview_report_{filename_session}.pdf"
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

    # GET 요청이므로 query parameter에서 가져옴
    print(f"\n[reports/generate/stream] 스트리밍 요청 시작")
    print(f"   - Query 파라미터: {dict(request.args)}")

    # session_id, axes_keys 파싱
    session_id = request.args.get('session_id') or GLOBAL_STATE.session_id
    metrics = GLOBAL_STATE.metrics
    user_prompt = request.args.get('user_prompt', '')
    candidate_name = GLOBAL_STATE.candidate_name or "지원자"

    # 폴백 session_id 처리
    FALLBACK_SESSION_ID = "7ac7d019-0c29-4af8-abd3-8bf18f4544bf"
    if not session_id:
        print(f"⚠️  session_id가 없음 → 폴백 사용: {FALLBACK_SESSION_ID}")
        session_id = FALLBACK_SESSION_ID
    else:
        # DB에서 데이터 확인
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
                print(f"⚠️  DB에 데이터가 없음 → 폴백 사용: {FALLBACK_SESSION_ID}")
                session_id = FALLBACK_SESSION_ID
        except Exception as e:
            print(f"⚠️  DB 확인 실패: {e} → 폴백 사용: {FALLBACK_SESSION_ID}")
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

    print(f"\n[스트리밍 파라미터]")
    print(f"   - session_id: {session_id}")
    print(f"   - candidate_name: {candidate_name}")
    print(f"   - axes_keys: {axes_keys}")
    print(f"   - user_prompt: {user_prompt[:100] if user_prompt else '(없음)'}")

    async def async_generate():
        """비동기 SSE 이벤트 생성기"""
        try:
            print(f"\n[SSE 스트림 시작]")
            event_count = 0

            async for event in create_report_async(
                session_id=session_id,
                candidate_name=candidate_name,
                axes_keys=axes_keys,
                user_prompt=user_prompt,
                has_portfolio=bool(getattr(GLOBAL_STATE, "portfolio_len", 0)),
            ):
                event_count += 1
                print(
                    f"[SSE] 이벤트 #{event_count} 전송 - "
                    f"type: {event.get('type')}, agent: {event.get('agent', 'N/A')}"
                )
                event_count += 1
                print(f"[SSE] 이벤트 #{event_count} 전송 - type: {event.get('type')}, agent: {event.get('agent', 'N/A')}")

                if event["type"] == "debate":
                    yield f"event: debate\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                elif event["type"] == "error":
                    print(f"[SSE] 에러 이벤트 전송 - {event.get('message', '')[:100]}")
                    yield f"event: error\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                elif event["type"] == "report":
                    print(f"[SSE] 최종 리포트 전송 중...")
                    yield f"event: report\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                elif event["type"] == "done":
                    print(f"[SSE] 완료 이벤트 전송")
                    yield f"event: done\ndata: {json.dumps({'status': 'completed'}, ensure_ascii=False)}\n\n"
                    return

        except Exception as e:
            print(f"\n❌ [스트리밍 오류] {e}")
            import traceback
            traceback.print_exc()
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
                    # 제너레이터 정상 종료
                    break

        except Exception as e:
            print(f"\n❌ [동기 래퍼 오류] {e}")
            import traceback
            traceback.print_exc()
            error_event = {
                "type": "error",
                "message": f"래퍼 오류: {str(e)}"
            }
            yield f"event: error\ndata: {json.dumps(error_event, ensure_ascii=False)}\n\n"
        finally:
            # async generator 정리 → 내부 pending task 제거
            if async_gen is not None:
                try:
                    loop.run_until_complete(async_gen.aclose())
                except Exception:
                    pass
            if loop and not loop.is_closed():
                loop.close()
                print(f"[SSE] 이벤트 루프 종료")

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no'
        }
    )


