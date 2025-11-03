"""RAG 관련 라우트 모음 (서브 라우터를 포함합니다)."""

from fastapi import APIRouter

from app.routes.rag_uploads import uploads_router
from app.routes.rag_interview import interview_router


rag_router = APIRouter()


# 하위 라우터 포함 (`app.main`에서 이 라우터를 prefix="/rag"로 포함)
rag_router.include_router(uploads_router, prefix="")
rag_router.include_router(interview_router, prefix="")
