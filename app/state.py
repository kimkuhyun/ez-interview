from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class InterviewState:
    # 원문
    resume_text: str = ""
    jd_text: str = ""

    # 질의/대화
    questions: List[str] = field(default_factory=list)
    transcripts: List[List[Dict[str, str]]] = field(default_factory=list)
    stt: bool = False

    # 벡터 스토어
    stores: Dict[str, Any] = field(default_factory=lambda: {})
    store: Optional[Any] = None

    # 미리보기 (이력서)
    resume_pdf_images: Optional[List[str]] = None
    resume_html_preview: Optional[str] = None
    resume_docx_url: Optional[str] = None

    # 미리보기 (직무기술서)
    jd_pdf_images: Optional[List[str]] = None
    jd_html_preview: Optional[str] = None
    jd_docx_url: Optional[str] = None

    # 의사결정 (추천/보류/비추천)
    recommendation: Optional[str] = None

    def reset_questions(self) -> None:
        self.questions = []
        self.transcripts = []


# 전역 상태
_state = InterviewState()
