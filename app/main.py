from __future__ import annotations

from pathlib import Path
import os
import logging

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.templating import templates
from app.routes_rag import rag_router
from app.state import _state as rag_state

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI()

app.mount(
    "/static",
    StaticFiles(directory=BASE_DIR.parent / "backup" / "app" / "static"),
    name="static",
)

@app.get("/", response_class=HTMLResponse)
def load_interview(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("interview.html", {"request": request, "state": rag_state})


# RAG 라우터는 '/rag' 프리픽스를 사용하도록 등록합니다.
app.include_router(rag_router, prefix="/rag")