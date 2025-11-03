from typing import List, Dict
from fastapi.responses import HTMLResponse

from app.templating import templates
from app.state import _state
import re


_QPAT = re.compile(r"^Q\d+\s*:\s*", re.I)


def _render_first(candidates: List[str], ctx: dict) -> HTMLResponse:
    for name in candidates:
        try:
            html = templates.env.get_template(name).render(ctx)
            return HTMLResponse(html)
        except Exception:
            continue
    return HTMLResponse(
        "<div class='p-2 text-red-600'>템플릿을 찾을 수 없습니다.</div>",
        status_code=500,
    )


def _renumber_questions() -> None:
    qs = _state.questions
    _state.questions = [
        f"Q{i+1}: {_QPAT.sub('', q).strip()}"
        for i, q in enumerate(qs)
    ]

    need = len(_state.questions)
    cur = len(_state.transcripts)

    if cur < need:
        _state.transcripts.extend([[] for _ in range(need - cur)])
    elif cur > need:
        _state.transcripts = _state.transcripts[:need]


def _ensure_transcripts_len() -> None:
    if len(_state.transcripts) != len(_state.questions):
        _state.transcripts = [[] for _ in _state.questions]
