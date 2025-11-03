from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, TypedDict

class TranscriptTurn(TypedDict):
    role: str
    text: str

# STT 활성화 상태
_STT_FLAG = False

def set_stt_flag(flag: bool) -> bool:
    """STT 활성화 플래그 설정."""
    global _STT_FLAG
    _STT_FLAG = bool(flag)
    return _STT_FLAG

def get_stt_flag() -> bool:
    """현재 STT 활성화 상태를 반환."""
    return _STT_FLAG

def run_stream_chain(
    transcripts: List[List[TranscriptTurn]]
) -> List[List[TranscriptTurn]]:
    """스트림 처리 스텁(현재 입력을 그대로 반환)."""
    return transcripts
