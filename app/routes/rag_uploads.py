from typing import List, Optional, Any, Dict, Union
from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Request
from fastapi.responses import HTMLResponse

from app.services.text_extract import extract_text_any
from app.services.rag import get_retriever, index_documents
from app.services.uploads_feature import build_preview_payload, process_bytes
from app.routes.shared import _render_first, _renumber_questions
from app.state import _state

uploads_router = APIRouter()


@uploads_router.post("/upload")
async def upload(
    kind: str = Form(...),
    file: UploadFile = File(...),
) -> Dict[str, Union[str, int]]:
    if kind not in {"resume", "jd"}:
        raise HTTPException(status_code=400, detail="파일 종류는 'resume' 또는 'jd'여야 합니다")

    try:
        raw = await file.read()
        text, preview = process_bytes(file.filename or "", raw)

        setattr(_state, f"{kind}_text", text)
        setattr(_state, f"{kind}_pdf_images", preview.get("pdf_images"))
        setattr(_state, f"{kind}_html_preview", preview.get("html_preview"))
        setattr(_state, f"{kind}_docx_url", preview.get("docx_url"))

        return {"kind": kind, "chars": len(text)}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"파일 처리 실패: {str(e)}")


@uploads_router.post("/upload_many")
async def upload_many(
    resume: Optional[UploadFile] = File(None),
    jd: Optional[UploadFile] = File(None),
) -> Dict[str, int]:
    if not resume and not jd:
        raise HTTPException(status_code=400, detail="최소 하나의 파일이 필요합니다")

    async def process_file(file: UploadFile, kind: str) -> None:
        raw = await file.read()
        text = extract_text_any(file.filename or "", raw)
        preview = build_preview_payload(file.filename or "", raw)

        setattr(_state, f"{kind}_text", text)
        setattr(_state, f"{kind}_pdf_images", preview.get("pdf_images"))
        setattr(_state, f"{kind}_html_preview", preview.get("html_preview"))
        setattr(_state, f"{kind}_docx_url", preview.get("docx_url"))

    try:
        if resume:
            await process_file(resume, "resume")
        if jd:
            await process_file(jd, "jd")

        return {"resume_chars": len(_state.resume_text), "jd_chars": len(_state.jd_text)}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"파일 처리 실패: {str(e)}")


@uploads_router.post("/index")
def index() -> Dict[str, List[str]]:
    try:
        stores = index_documents(_state.resume_text, _state.jd_text)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    _state.stores = stores
    _state.store = stores.get("combined") or stores.get("resume") or stores.get("jd")

    return {"indexed": [name for name, store in stores.items() if store is not None]}


@uploads_router.post("/search")
def search(q: str, target: Optional[str] = "combined", k: int = 5):
    stores = getattr(_state, "stores", {}) or {}
    store = stores.get(target or "combined")
    if not store:
        raise HTTPException(400, f"store '{target}' not ready")
    retriever = get_retriever(store, k=k)
    docs = retriever.invoke(q)
    return {"q": q, "target": target, "k": k, "hits": [{"text": d.page_content} for d in docs]}


@uploads_router.post("/pipeline", response_class=HTMLResponse)
async def pipeline(
    request: Request,
    files: List[UploadFile] = File(...),
    auto_generate: bool = Form(False),
) -> HTMLResponse:
    if not files:
        return HTMLResponse("<div class='text-red-600 p-2'>파일이 없습니다.</div>", status_code=400)

    for i, file in enumerate(files[:2]):
        raw = await file.read()
        text, preview = process_bytes(file.filename or "", raw)
        setattr(_state, f"{'resume' if i == 0 else 'jd'}_text", text)
        setattr(_state, f"{'resume' if i == 0 else 'jd'}_pdf_images", preview.get("pdf_images"))
        setattr(_state, f"{'resume' if i == 0 else 'jd'}_html_preview", preview.get("html_preview"))
        setattr(_state, f"{'resume' if i == 0 else 'jd'}_docx_url", preview.get("docx_url"))

    texts = [t for t in (_state.resume_text, _state.jd_text) if t]
    combined = "\n\n".join(texts)
    if not combined:
        return HTMLResponse("<div class='text-red-600 p-2'>추출된 텍스트가 없습니다.</div>", status_code=400)

    try:
        stores = index_documents(_state.resume_text, _state.jd_text)
    except ValueError:
        return HTMLResponse("<div class='text-red-600 p-2'>추출된 텍스트가 없습니다.</div>", status_code=400)

    _state.stores = stores
    _state.store = stores.get("combined") or stores.get("resume") or stores.get("jd")
    vs = _state.store
    _state.questions = []
    _state.transcripts = []

    if auto_generate and texts:
        try:
            topic = "이력서와 직무 요구사항 기반 질문"
            k = 6
            docs = get_retriever(vs, k=k).invoke(topic)
            ctx = "\n\n".join(d.page_content for d in docs)
            _state.questions = []  # will be filled by caller (generate endpoint)
            _renumber_questions()
        except Exception as e:
            print(f"자동 질문 생성 실패: {str(e)}")

    ctx = {"request": request, "state": _state}
    right = _render_first(["partials/right_prepare.html", "right_prepare.html"], ctx)
    left = _render_first(["partials/left_preview_tabs_oob.html", "left_preview_tabs_oob.html"], {"state": _state})
    # also remove the upload panel (leftUpload) out-of-band so it hides after successful upload
    left_upload_hidden = "<div id=\"leftUpload\" hx-swap-oob=\"outerHTML\"></div>"
    return HTMLResponse(right.body.decode() + left.body.decode() + left_upload_hidden)
