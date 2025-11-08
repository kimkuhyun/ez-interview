from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from annotated_types import Ge, Le
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from pydantic import BaseModel, Field, StringConstraints
from typing_extensions import Annotated

"""
리포트 에이전트 공용 구성요소
- LLM/임베딩 클라이언트 팩토리
- Pydantic 스키마와 에러 타입 정의
- 임시 디렉터리 및 기본 파라미터 상수
"""

__all__ = [
    "ReportError",
    "get_llm",
    "get_embeddings",
    "OPENAI_MODEL",
    "EMBED_MODEL",
    "TMP_DIR",
    "DEFAULT_TOKEN_BUDGET",
    "TOKEN_TO_CHARS",
    "SAFETY_RATIO",
    "Competency",
    "ScoreItem",
    "WeightItem",
    "Headline",
    "TalkSummaryItem",
    "TalkSummary",
    "JDCoverRow",
    "EvidenceRow",
    "ConvKV",
    "ReportOut",
    "VerifyCoverage",
    "AxisRescore",
    "VerifyLLMOut",
]

OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")
EMBED_MODEL = os.getenv("OPENAI_EMBED_MODEL", "text-embedding-3-small")
TMP_DIR = Path(os.getenv("RAG_TMP_DIR", "app/.tmp"))
TMP_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_TOKEN_BUDGET = 32_000
TOKEN_TO_CHARS = 4
SAFETY_RATIO = 0.7


def get_llm(**overrides: Any) -> ChatOpenAI:
    """
    Returns a ChatOpenAI client with sane defaults and optional overrides.
    """
    params: Dict[str, Any] = {
        "model_name": overrides.pop("model_name", OPENAI_MODEL),
        "temperature": overrides.pop("temperature", 0.1),
        "timeout": overrides.pop("timeout", 60),
    }
    params.update(overrides)
    return ChatOpenAI(**params)


def get_embeddings(**overrides: Any) -> OpenAIEmbeddings:
    """
    Returns an embeddings client that mirrors the legacy configuration.
    """
    params: Dict[str, Any] = {
        "model": overrides.pop("model", EMBED_MODEL),
    }
    params.update(overrides)
    return OpenAIEmbeddings(**params)


class ReportError(Exception):
    """
    Domain specific error mirroring the legacy contract.
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        http: int = 400,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http = http
        self.details: Dict[str, Any] = details or {}


KeyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=48)]
NonEmpty = Annotated[str, StringConstraints(min_length=1)]
E_ID = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^E\d{2,3}$")]
J_ID = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^J\d{2,3}$")]
Score = Annotated[int, Ge(0), Le(100)]
Weight = Annotated[int, Ge(0), Le(100)]


class Competency(BaseModel):
    key: KeyStr
    label: NonEmpty


class ScoreItem(BaseModel):
    key: KeyStr
    value: Score


class WeightItem(BaseModel):
    key: KeyStr
    value: Weight


class Headline(BaseModel):
    summary: NonEmpty
    tag: NonEmpty


class TalkSummaryItem(BaseModel):
    주제: NonEmpty
    발언요약: NonEmpty


class TalkSummary(BaseModel):
    items: List[TalkSummaryItem] = Field(min_length=1)


class JDCoverRow(BaseModel):
    jid: J_ID
    요구사항: NonEmpty
    기준: NonEmpty
    충족도: NonEmpty
    근거: List[E_ID] = Field(default_factory=list)  # 임시적으로 빈리스트허용 min_length=1


class EvidenceRow(BaseModel):
    eid: E_ID
    출처: NonEmpty
    내용: NonEmpty
    jid: J_ID


class ConvKV(BaseModel):
    k: NonEmpty
    v: NonEmpty


class ReportOut(BaseModel):
    axes: List[Competency] = Field(min_length=5, max_length=5)
    scores: List[ScoreItem] = Field(min_length=5, max_length=5)
    weights: List[WeightItem] = Field(min_length=5, max_length=5)
    headline: Headline
    talkSummary: TalkSummary
    convStats: List[ConvKV] = Field(default_factory=list)
    jdCoverage: List[JDCoverRow] = Field(min_length=1)
    evidence: List[EvidenceRow] = Field(min_length=1)


class VerifyCoverage(BaseModel):
    jid: J_ID
    p_satisfy: float = Field(ge=0.0, le=1.0)
    p_partial: float = Field(ge=0.0, le=1.0)
    p_unsatisfied: float = Field(ge=0.0, le=1.0)
    verdict: NonEmpty
    valid_eids: List[E_ID] = Field(default_factory=list)
    reasons: List[NonEmpty] = Field(default_factory=list)


class AxisRescore(BaseModel):
    key: KeyStr
    score: Score
    confidence: float = Field(ge=0.0, le=1.0)


class VerifyLLMOut(BaseModel):
    coverage: List[VerifyCoverage]
    axis: List[AxisRescore]
    headline: Optional[Headline] = None
    positives: List[NonEmpty] = Field(default_factory=list)
    negatives: List[NonEmpty] = Field(default_factory=list)
