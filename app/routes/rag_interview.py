from typing import List
from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

from app.routes.shared import _render_first, _renumber_questions, _ensure_transcripts_len
from app.state import _state, InterviewState
from app.services.rag import get_retriever
from app.agents.document_agent import generate_questions_from_text
from app.agents.report_agent import generate_report

interview_router = APIRouter()


@interview_router.post("/generate_questions", response_class=HTMLResponse)
def generate_questions(request: Request, topic: str = Form("초기 면접 질문"), n: int = Form(5)) -> HTMLResponse:
    if not getattr(_state, "store", None):
        return HTMLResponse("<div class='text-red-600'>인덱싱이 필요합니다. 파일을 먼저 불러오세요.</div>", status_code=400)
    k = max(6, int(n) + 2)
    docs = get_retriever(_state.store, k=k).invoke(topic)
    ctx = "\n\n".join(d.page_content for d in docs)
    new = generate_questions_from_text(topic, ctx, n=int(n))
    _state.questions.extend(new)
    _renumber_questions()
    return _render_first(["partials/right_prepare.html", "right_prepare.html"], {"request": request, "state": _state})



@interview_router.post("/reset", response_class=HTMLResponse)
def reset(request: Request) -> HTMLResponse:
    """앱 상태를 초기화하고 초기 우측 패널을 렌더링합니다."""
    # 재설정: _state의 속성들을 기본값으로 덮어씌움
    new = InterviewState()
    for k, v in vars(new).items():
        setattr(_state, k, v)

    # Render right initial panel
    right = _render_first(["partials/right_initial.html", "right_initial.html"], {"request": request, "state": _state})
    # Also restore the left upload panel out-of-band so the user can upload again
    left = _render_first(["partials/left_upload_oob.html", "left_upload_oob.html"], {"state": _state})

    # Return both: main right HTML (will be swapped into #right) and the left OOB fragment
    return HTMLResponse(right.body.decode() + left.body.decode())


@interview_router.post("/add_question", response_class=HTMLResponse)
def add_question(request: Request, q_text: str = Form(...)) -> HTMLResponse:
    txt = (q_text or "").strip()
    if not txt:
        return HTMLResponse("<div class='p-2 text-red-600'>질문 내용을 입력하세요.</div>", status_code=400)
    _state.questions.append(txt)
    _renumber_questions()
    return _render_first(["partials/right_prepare.html", "right_prepare.html"], {"request": request, "state": _state})


@interview_router.post("/start_interview", response_class=HTMLResponse)
def start_interview(request: Request) -> HTMLResponse:
    if not getattr(_state, "questions", None):
        return HTMLResponse("<div class='p-2 text-red-600'>질문이 없습니다.</div>", status_code=400)
    _ensure_transcripts_len()
    return _render_first(["partials/right_interview.html", "right_interview.html"], {"request": request, "state": _state, "q_index": 0})


@interview_router.post("/goto", response_class=HTMLResponse)
def goto(request: Request, q_index: int = Form(0)) -> HTMLResponse:
    total = len(getattr(_state, "questions", []))
    if total == 0:
        return HTMLResponse("<div class='p-2 text-red-600'>질문이 없습니다.</div>", status_code=400)
    qi = max(0, min(int(q_index), total - 1))
    return _render_first(["partials/right_interview.html", "right_interview.html"], {"request": request, "state": _state, "q_index": qi})


@interview_router.post("/stt/start", response_class=HTMLResponse)
def stt_start(request: Request, q_index: int = Form(0)) -> HTMLResponse:
    _state.stt = True
    return goto(request, q_index)


@interview_router.post("/stt/stop", response_class=HTMLResponse)
def stt_stop(request: Request, q_index: int = Form(0)) -> HTMLResponse:
    _state.stt = False
    return goto(request, q_index)


@interview_router.post("/turn/add_interviewer", response_class=HTMLResponse)
def turn_add_interviewer(request: Request, q_index: int = Form(...), text: str = Form(...)) -> HTMLResponse:
    _ensure_transcripts_len()
    if not getattr(_state, "questions", None):
        return HTMLResponse("<div class='p-2 text-red-600'>질문이 없습니다.</div>", status_code=400)
    qi = max(0, min(int(q_index), len(getattr(_state, "questions", [])) - 1))
    msg = (text or "").strip()
    if msg:
        _state.transcripts[qi].append({"role": "interviewer", "text": msg})
    return _render_first(["partials/right_interview.html", "right_interview.html"], {"request": request, "state": _state, "q_index": qi})


@interview_router.post("/turn/delete_last_interviewer", response_class=HTMLResponse)
def turn_delete_last_interviewer(request: Request, q_index: int = Form(...)) -> HTMLResponse:
    _ensure_transcripts_len()
    if not getattr(_state, "questions", None):
        return HTMLResponse("<div class='p-2 text-red-600'>질문이 없습니다.</div>", status_code=400)
    qi = max(0, min(int(q_index), len(getattr(_state, "questions", [])) - 1))
    buf = _state.transcripts[qi]
    for i in range(len(buf) - 1, -1, -1):
        if buf[i].get("role") == "interviewer":
            buf.pop(i)
            break
    return _render_first(["partials/right_interview.html", "right_interview.html"], {"request": request, "state": _state, "q_index": qi})


@interview_router.post("/finish", response_class=HTMLResponse)
def finish(request: Request, use_dummy: bool = Form(False)) -> HTMLResponse:
    qs = getattr(_state, "questions", []) or []
    trs = getattr(_state, "transcripts", []) or []
    
    # 문서 정보 수집 (선택)
    documents = {
        "resume": getattr(_state, "resume_text", None),
        "jd": getattr(_state, "jd_text", None),
    }
    
    # 리포트 에이전트 호출 (더미 모드 지원)
    report = generate_report(
        questions=qs,
        transcripts=trs,
        documents=documents,
        use_dummy=use_dummy
    )

    ctx = {"request": request, "state": _state, "report": report}
    html = _render_first(["partials/right_summary.html"], ctx)
    return html


@interview_router.post("/recommendation/set", response_class=HTMLResponse)
def recommendation_set(request: Request, value: str = Form(...)) -> HTMLResponse:
    """추천/보류/비추천 선택 상태를 저장하고 버튼 영역만 부분 갱신합니다."""
    allowed = {"recommend", "hold", "reject"}
    v = (value or "").strip()
    if v not in allowed:
        return HTMLResponse("<div class='text-red-600 text-sm'>잘못된 선택입니다.</div>", status_code=400)
    _state.recommendation = v
    return _render_first(["partials/_recommend_buttons.html"], {"request": request, "state": _state})
