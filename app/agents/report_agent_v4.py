# report_agent_v4.py

from __future__ import annotations

import os
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, AsyncGenerator, TypeVar,Type
from uuid import uuid4

import operator
from annotated_types import Ge, Le
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field, StringConstraints, ValidationError, model_validator
from typing_extensions import Annotated, TypedDict
from langgraph.graph import StateGraph,START, END

from app.utils.rag_retriever import search_similar_chunks
from app.utils.interview_store import retrieve_interview_context


# ==================== 기본 설정 ====================

logger = logging.getLogger(__name__)
if not logger.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    logger.addHandler(_h)
logger.setLevel(logging.INFO)
# ===================================================

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
SOLAR_API_KEY = os.getenv("SOLAR_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "o3-mini")
OPENAI_MODEL_CHAT = os.getenv("OPENAI_MODEL_CHAT", "gpt-4o-mini")
# Upstage Solar Pro 2 설정
SOLAR_MODEL = os.getenv("SOLAR_MODEL", "solar-pro2")
SOLAR_BASE_URL = "https://api.upstage.ai/v1"


REPORT_MAX_RETRIEVAL_RETRY = int(os.getenv("REPORT_MAX_RETRIEVAL_RETRY", "2"))
REPORT_MAX_ANALYSIS_RETRY = int(os.getenv("REPORT_MAX_ANALYSIS_RETRY", "1"))
REPORT_QUALITY_THRESHOLD = float(os.getenv("REPORT_QUALITY_THRESHOLD", "0.7"))
SUMMARY_MAX_LOG_CHARS = int(os.getenv("REPORT_SUMMARY_MAX_LOG_CHARS", "4000"))


def _llm_gpt() -> ChatOpenAI:
    """GPT-4o 모델"""
    return ChatOpenAI(
        model=OPENAI_MODEL,
        timeout=90,
        api_key=OPENAI_API_KEY,
    )


def _llm_gpt_chat() -> ChatOpenAI:
    """GPT-4o 모델"""
    return ChatOpenAI(
        model=OPENAI_MODEL_CHAT,
        timeout=90,
        api_key=OPENAI_API_KEY,
    )



def _llm_solar_chat() -> ChatOpenAI:
    """Solar Pro 2 Chat 모드"""
    return ChatOpenAI(
        model=SOLAR_MODEL,
        temperature=0.1,
        timeout=90,
        api_key=SOLAR_API_KEY,
        base_url=SOLAR_BASE_URL,
    )


def _llm_solar_reasoning() -> ChatOpenAI:
    """Solar Pro 2 Reasoning 모드"""
    return ChatOpenAI(
        model=SOLAR_MODEL,
        temperature=0.1,
        timeout=60,
        api_key=SOLAR_API_KEY,
        base_url=SOLAR_BASE_URL,
        reasoning_effort="high"
    )




# ==================== 공통 스키마 ====================

KeyStr = Annotated[str, StringConstraints(strip_whitespace=True)]
NonEmpty = Annotated[str, StringConstraints(min_length=1)]
E_ID = Annotated[str, StringConstraints(strip_whitespace=True)]
COMP_ID = Annotated[str, StringConstraints(strip_whitespace=True)]
Score = Annotated[int, Ge(0), Le(100)]

class Competency(BaseModel):
    key: KeyStr
    label: NonEmpty

class ScoreItem(BaseModel):
    key: KeyStr
    value: Score

class Headline(BaseModel):
    summary: NonEmpty = Field(
        description="후보자 특징을 한 문장으로 요약 (40-60자)"
    )
    overall_summary: NonEmpty = Field(
        description="인터뷰 전반에 대한 평가 (350-400자)"
    )
    tag: List[str] = Field(min_length=1, max_length=5, description="키워드 태그 3-5개")
    contradiction_score: Optional[Score]= Field(
        default=None, description="답변 모순도 (0=매우일관적, 100=매우모순적)"
    )
    contradiction_reason: Optional[str] = Field(
        default=None, description="모순 정도 점수 이유 (1-2문장)"
    )
    depth_score: Optional[Score] = Field(
        default=None, description="답변 깊이 (0=매우피상적, 100=매우깊이있음)"
    )
    depth_reason: Optional[str] = Field(
        default=None, description="대화 깊이 점수 이유 (1-2문장)"
    )
    reliability_score: Optional[Score] = Field(
        default=None, description="신뢰도 (0=매우낮음, 100=매우높음)"
    )
    reliability_reason: Optional[str] = Field(
        default=None, description="리포트 신뢰도 점수 이유 (1-2문장)"
    )
    
    @model_validator(mode="before")
    @classmethod
    def _ensure_both_summaries(cls, data: Any):
        """summary와 overall_summary 둘 다 있는지 확인하고 없으면 서로 복사"""
        if not isinstance(data, dict):
            return data
        data = dict(data)
        
        # summary가 없으면 overall_summary를 복사
        if not data.get("summary") and data.get("overall_summary"):
            data["summary"] = data["overall_summary"][:60] + "..." if len(data["overall_summary"]) > 60 else data["overall_summary"]
        
        # overall_summary가 없으면 summary를 복사
        if not data.get("overall_summary") and data.get("summary"):
            data["overall_summary"] = data["summary"]
        
        # tag가 문자열이면 리스트로 변환
        if isinstance(data.get("tag"), str):
            data["tag"] = [t.strip() for t in data["tag"].split(",") if t.strip()]
        
        return data


class TalkSummaryItem(BaseModel):
    topic: NonEmpty
    talkSummary: NonEmpty

    @model_validator(mode="before")
    @classmethod
    def _normalize_summary_field(cls, data: Any):
        """
        LLM이 "summary" 키만 사용하는 케이스를 talkSummary 필드로 이관해
        스키마와 파이프라인 모두가 깨지지 않도록 보정한다.
        """
        if not isinstance(data, dict):
            return data
        data = dict(data)
        if "talkSummary" not in data:
            summary_text = (
                data.get("summary")
                or data.get("talk_summary")
                or data.get("talksummary")
            )
            if isinstance(summary_text, str):
                summary_text = summary_text.strip()
                if summary_text:
                    data["talkSummary"] = summary_text
        return data


class TalkSummary(BaseModel):
    items: List[TalkSummaryItem] = Field(min_length=1)


class CompCoverRow(BaseModel):
    cid: COMP_ID
    requirement: NonEmpty
    expectation: NonEmpty
    fulfillment: NonEmpty
    evidence: List[E_ID]

class EvidenceRow(BaseModel):
    eid: E_ID
    source: NonEmpty
    content: NonEmpty
    cid: Optional[COMP_ID] = None

    @model_validator(mode="before")
    @classmethod
    def _normalize_eid(cls, data: Any):
        if not isinstance(data, dict):
            return data
        data = dict(data)
        # eid가 없고, e01/e06/e123 같은 키가 있으면 그것을 eid로 사용
        if "eid" not in data:
            for k, v in list(data.items()):
                if isinstance(k, str) and k.lower().startswith("e") and k[1:].isdigit():
                    data["eid"] = v
                    break
        return data


class ConvKV(BaseModel):
    k: str
    v: str


class CompetencyComment(BaseModel):
    key: KeyStr
    comment: NonEmpty = Field(
        description="역량별 점수 이유 (2-3문장)"
    )


class QuestionTypeRatio(BaseModel):
    "PDF 질문 유형별 비율"
    type: NonEmpty
    ratio: str


class InterviewStats(BaseModel):
    """통계"""
    duration: Optional[str] = Field(
        default=None, description="면접 시간 (예: 45분)"
    )
    followup_ratio: Optional[str] = Field(
        default=None, description="질문당 후속 질문 비율 (예: 2.3회)"
    )
    question_type_ratios: List[QuestionTypeRatio] = Field(
        default_factory=list,
        description="질문 유형별 비율 (사실관계파악, 기술질문, 커뮤니케이션질문 등)"
    )
    depth_score: Optional[Annotated[float, Ge(0.0), Le(1.0)]] = Field(
        default=None, description="심층도 스코어 (0~1, 선택값)"
    )
    technical_coverage: Optional[Annotated[float, Ge(0.0), Le(1.0)]] = Field(
        default=None, description="기술 커버리지 스코어 (0~1, 선택값)"
    )
    communication_skill: Optional[Annotated[float, Ge(0.0), Le(1.0)]] = Field(
        default=None, description="커뮤니케이션 스코어 (0~1, 선택값)"
    )

    @model_validator(mode="before")
    @classmethod
    def _normalize_stats(cls, data: Any):
        if not isinstance(data, dict):
            return data
        data = dict(data)

        ratios = data.get("question_type_ratios")
        if isinstance(ratios, dict):
            data["question_type_ratios"] = [
                {"type": str(k), "ratio": str(v)}
                for k, v in ratios.items()
            ]
        elif isinstance(ratios, list):
            normalized = []
            for item in ratios:
                if isinstance(item, dict) and {"type", "ratio"} <= set(item):
                    normalized.append({
                        "type": str(item["type"]),
                        "ratio": str(item["ratio"]),
                    })
                elif isinstance(item, str) and ":" in item:
                    key, value = item.split(":", 1)
                    normalized.append({"type": key.strip(), "ratio": value.strip()})
            if normalized:
                data["question_type_ratios"] = normalized

        for key in ("duration", "followup_ratio"):
            value = data.get(key)
            if isinstance(value, (int, float)):
                data[key] = str(value)
        return data


class ReportMetadata(BaseModel):
    """PDF 템플릿용 메타데이터"""
    candidate_name: NonEmpty
    position_applied: NonEmpty
    interview_date: str
    report_date: str
    session_id: NonEmpty
    interviewer_name: Optional[str] = "AI 평가 시스템"


class FinalRecommendation(BaseModel):
    """최종 코멘트 및 채용 권고"""
    final_comment: NonEmpty = Field(
        description="최종 코멘트 (250-300자)"
    )
    hiring_decision: NonEmpty
    decision_reasons: List[str] = Field(min_length=1, max_length=5)


class ReportOut(BaseModel):
    """최종 리포트 출력 스키마 (PDF 템플릿 지원)"""
    model_config = {"populate_by_name": True}
    
    metadata: ReportMetadata # 기본 정보
    
    axes: List[Competency] = Field(min_length=1)
    scores: List[ScoreItem] = Field(min_length=1)
    
    headline: Headline
    talkSummary: TalkSummary
    
    convStats: List[ConvKV] = Field(default_factory=list)
    compCoverage: List[CompCoverRow] = Field(default_factory=list, alias="jdCoverage")
    evidence: List[EvidenceRow] = Field(default_factory=list)
    
    competency_comments: List[CompetencyComment] = Field(
        default_factory=list,
        description="각 역량별 점수 이유 (2-3문장)"
    )
    interview_stats: Optional[InterviewStats] = Field(
        default=None,
        description="면접 시간, 질문 비율 등 통계"
    )
    
    recommendation: FinalRecommendation


# ==================== 중간 출력 스키마 ====================

class OptimizedPrompt(BaseModel):
    """프롬프트 렌즈 최적화 에이전트 출력 """
    original_prompt: NonEmpty
    lens_perspective: Optional[NonEmpty] = Field(
        default=None,
        description="사용자 프롬프트를 해석한 핵심 평가 관점 요약"
    )
    key_focus_areas: List[str] = Field(
        min_length=1,
        max_length=7,
        description="이번 평가에서 특히 중점적으로 볼 영역"
    )
    reasoning: NonEmpty
    



class QueryPlan(BaseModel):
    """Query plan schema"""
    resume_query: NonEmpty
    comp_query: NonEmpty
    portfolio_query: Optional[NonEmpty] = Field(default=None)
    interview_query: NonEmpty
    focus_areas: List[str] = Field(default_factory=list)
    reasoning: NonEmpty

    @model_validator(mode="before")
    @classmethod
    def _normalize_query_fields(cls, data: Any):
        if not isinstance(data, dict):
            return data
        data = dict(data)
        for key in ("resume_query", "comp_query", "portfolio_query", "interview_query"):
            value = data.get(key)
            if isinstance(value, dict):
                query_text = next(
                    (
                        value.get(field)
                        for field in (
                            "query", "q", "description",
                            "prompt", "질의", "질문"
                        )
                        if isinstance(value.get(field), str) and value.get(field).strip()
                    ),
                    None,
                )
                parts: List[str] = []
                if query_text:
                    parts.append(query_text.strip())
                keywords = value.get("keywords") or value.get("keyword")
                if isinstance(keywords, str) and keywords.strip():
                    parts.append(f"Keywords: {keywords.strip()}")
                elif isinstance(keywords, list):
                    kw = ", ".join(str(k).strip() for k in keywords if str(k).strip())
                    if kw:
                        parts.append(f"Keywords: {kw}")
                filters = value.get("filters") or value.get("focus")
                if isinstance(filters, str) and filters.strip():
                    parts.append(filters.strip())
                fmt = value.get("format") or value.get("template") or value.get("포맷")
                if isinstance(fmt, str) and fmt.strip():
                    parts.append(fmt.strip())
                if parts:
                    data[key] = " | ".join(parts)
            elif isinstance(value, list):
                joined = " | ".join(str(v).strip() for v in value if str(v).strip())
                if joined:
                    data[key] = joined
        raw_focus = data.get("focus_areas")
        if isinstance(raw_focus, str):
            focus_items = [item.strip() for item in raw_focus.split(',') if item.strip()]
            data["focus_areas"] = focus_items
        return data


class EvidenceEvalOut(BaseModel):
    """증거 매핑 & 1차 커버리지 평가"""
    missing_axes: List[str] = Field(
        default_factory=list,
        description="증거가 부족한 역량 축 리스트",
    )
    # 최종적으로는 문자열 리스트로 사용
    missing_jd_items: List[str] = Field(
        default_factory=list,
        description="커버되지 않은 JD/역량 항목 설명 리스트",
    )
    quality_score: Annotated[float, Ge(0.0), Le(1.0)] = Field(
        description="증거 커버리지 품질 점수 (0~1)",
    )
    needs_retry: bool = Field(
        default=False,
        description="증거 부족으로 리트리버 루프를 다시 돌릴지 여부",
    )
    retry_hints: List[str] = Field(
        default_factory=list,
        description="재질의 시 고려할 힌트/키워드",
    )

    @model_validator(mode="before")
    @classmethod
    def _normalize_missing_items(cls, data: Any):
        """
        LLM이 다음 두 가지 케이스로 줄 수 있음:
        - missing_jd_items: ["문자열", ...]
        - missing_comp_items: [{"axis": "...", "details": "..."}, ...]
        이걸 모두 missing_jd_items 문자열 리스트로 정규화한다.
        """
        if not isinstance(data, dict):
            return data

        data = dict(data)

        # 1) 이미 missing_jd_items가 문자열 리스트로 온 경우 → 그대로 사용
        raw_jd = data.get("missing_jd_items")
        if isinstance(raw_jd, list) and all(isinstance(x, str) for x in raw_jd):
            return data

        # 2) missing_comp_items -> missing_jd_items로 변환
        raw_comp = data.get("missing_comp_items")
        if isinstance(raw_comp, list):
            normalized: List[str] = []
            for item in raw_comp:
                if isinstance(item, str):
                    normalized.append(item)
                elif isinstance(item, dict):
                    axis = item.get("axis")
                    details = item.get("details")
                    if axis and details:
                        normalized.append(f"{axis}: {details}")
                    elif axis:
                        normalized.append(axis)
                    elif details:
                        normalized.append(details)
            data["missing_jd_items"] = normalized

        return data


class CrossCheckOut(BaseModel):
    overall_consistency_score: Score
    issues: List[str] = Field(default_factory=list)
    notes: List[str] = Field(default_factory=list)
    consistency_quality_score: Annotated[float, Ge(0.0), Le(1.0)] = Field(default=1.0)

    @model_validator(mode="before")
    @classmethod
    def _normalize_issue_lists(cls, data: Any):
        if not isinstance(data, dict):
            return data
        data = dict(data)

        def _norm_list(raw: Any) -> Any:
            if not isinstance(raw, list):
                return raw
            out: List[str] = []
            for item in raw:
                if isinstance(item, str):
                    out.append(item)
                elif isinstance(item, dict):
                    t = item.get("type")
                    desc = item.get("description")
                    if t and desc:
                        out.append(f"{t}: {desc}")
                    elif desc:
                        out.append(desc)
                    elif t:
                        out.append(t)
            return out

        if "issues" in data:
            data["issues"] = _norm_list(data.get("issues"))
        if "notes" in data:
            data["notes"] = _norm_list(data.get("notes"))
        return data



class InterviewAnalysisOut(BaseModel):
    contradiction_score: Score
    contradiction_reason: NonEmpty
    depth_score: Score
    depth_reason: NonEmpty
    reliability_score: Score
    reliability_reason: NonEmpty
    positive_aspects: str
    negative_aspects: str
    final_comment: NonEmpty
    analysis_quality_score: Annotated[float, Ge(0.0), Le(1.0)] = Field(default=1.0)
    analysis_needs_retry: bool = Field(default=False)
    analysis_retry_hints: List[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _normalize_scores_and_lists(cls, data: Any):
        if not isinstance(data, dict):
            return data
        data = dict(data)

        # 점수: 0~1 또는 0~10 → 0~100 int
        for key in ("contradiction_score", "depth_score", "reliability_score"):
            value = data.get(key)
            if isinstance(value, (int, float)):
                v = float(value)
                if 0.0 <= v <= 1.0:
                    v *= 100.0
                elif 0.0 <= v <= 10.0:
                    v *= 10.0
                data[key] = int(round(v))

        # 리스트 → 문자열
        for key in ("positive_aspects", "negative_aspects"):
            value = data.get(key)
            if isinstance(value, list):
                parts = [str(x) for x in value if isinstance(x, (str, int, float))]
                data[key] = "\n".join(parts)

        def _join_list_str(val: Any) -> str | None:
            if isinstance(val, str):
                return val.strip()
            if isinstance(val, list):
                parts = [str(x).strip() for x in val if isinstance(x, (str, int, float))]
                joined = " ".join(part for part in parts if part)
                return joined or None
            return None

        for key in ("contradiction_reason", "depth_reason", "reliability_reason"):
            normalized = _join_list_str(data.get(key))
            if normalized:
                data[key] = normalized

        final_comment = _join_list_str(data.get("final_comment"))
        if final_comment:
            data["final_comment"] = final_comment

        if not data.get("reliability_reason"):
            alias = (
                data.get("reliability_score_reason")
                or data.get("reliabilityReason")
                or data.get("reliability_scoreReason")
            )
            normalized_alias = _join_list_str(alias)
            if normalized_alias:
                data["reliability_reason"] = normalized_alias

        return data


class CompetencyEvalOut(BaseModel):
    scores: List[ScoreItem]
    headline: Headline
    reasoning: List[str] = Field(default_factory=list)
    competency_comments: List[CompetencyComment] = Field(default_factory=list)
    scoring_quality_score: Annotated[float, Ge(0.0), Le(1.0)] = Field(default=1.0)
    scoring_needs_retry: bool = Field(default=False)
    scoring_retry_hints: List[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _normalize_scores_and_comments(cls, data: Any):
        if not isinstance(data, dict):
            return data
        data = dict(data)

        # scores dict → ScoreItem 리스트
        raw_scores = data.get("scores")
        if isinstance(raw_scores, dict):
            norm_scores: List[Dict[str, Any]] = []
            for key, val in raw_scores.items():
                if not isinstance(key, str) or not isinstance(val, (int, float)):
                    continue
                v = float(val)
                if 0.0 <= v <= 1.0:
                    v *= 100.0
                elif 0.0 <= v <= 10.0:
                    v *= 10.0
                norm_scores.append({"key": key, "value": int(round(v))})
            data["scores"] = norm_scores

        # headline 문자/딕트 응답 정규화
        raw_headline = data.get("headline")
        if isinstance(raw_headline, str):
            text = raw_headline.strip()
            if text:
                data["headline"] = {
                    "summary": text,
                    "overall_summary": text,
                    "tag": ["headline"],
                }
        elif isinstance(raw_headline, dict):
            # 예: {축: 한줄요약, ...} 형태를 하나의 Headline으로 합치기
            if not raw_headline.get("summary"):
                parts: List[str] = []
                tags: List[str] = []
                for k, v in raw_headline.items():
                    if not isinstance(k, str):
                        continue
                    tags.append(k)
                    if isinstance(v, str):
                        txt = v.strip()
                    else:
                        txt = str(v)
                    if txt:
                        parts.append(f"{k}: {txt}")
                if parts:
                    summary = " | ".join(parts)
                    raw_headline = {
                        "summary": summary,
                        "overall_summary": summary,
                        "tag": tags[:5] or ["headline"],
                    }
            data["headline"] = raw_headline

        # reasoning dict/str 를 리스트로
        raw_reasoning = data.get("reasoning")
        if isinstance(raw_reasoning, dict):
            parts: List[str] = []
            for k, v in raw_reasoning.items():
                if isinstance(v, str):
                    parts.append(f"{k}: {v}")
            data["reasoning"] = parts
        elif isinstance(raw_reasoning, str):
            data["reasoning"] = [raw_reasoning]

        # competency_comments dict → CompetencyComment 리스트
        raw_comments = data.get("competency_comments")
        if isinstance(raw_comments, dict):
            norm_comments: List[Dict[str, Any]] = []
            for key, val in raw_comments.items():
                txt = ""
                if isinstance(val, list):
                    txt = "\n".join(
                        str(x) for x in val if isinstance(x, (str, int, float))
                    ).strip()
                elif isinstance(val, (str, int, float)):
                    txt = str(val).strip()
                if txt:
                    norm_comments.append({"key": str(key), "comment": txt})
            data["competency_comments"] = norm_comments

        return data




class CompCoverageOut(BaseModel):
    """역량 기준 커버리지 & 증거 테이블 """
    compCoverage: List[CompCoverRow] = Field(
        default_factory=list,
        description="역량 기준 요구사항/기대/충족도 및 증거 매핑"
    )
    evidence: List[EvidenceRow] = Field(
        default_factory=list,
        description="증거 테이블 (E_ID 기반)"
    )

    coverage_quality_score: Annotated[float, Ge(0.0), Le(1.0)] = Field(
        default=1.0,
        description="커버리지 분석 결과의 품질 점수"
    )
    coverage_needs_retry: bool = Field(
        default=False,
        description="커버리지 분석을 재시도해야 하는지 여부"
    )
    coverage_retry_hints: List[str] = Field(
        default_factory=list,
        description="재분석 시 고려해야 할 요구사항/역량"
    )



class SummaryStatsOut(BaseModel):
    """인터뷰 요약 & 통계 (노드 10-D)"""
    talkSummary: TalkSummary
    convStats: List[ConvKV] = Field(
        default_factory=list,
        description="대화 요약용 K/V 통계"
    )
    interview_stats: Optional[InterviewStats] = Field(
        default=None,
        description="면접 시간, 질문 비율 등 상세 통계"
    )

    summary_quality_score: Annotated[float, Ge(0.0), Le(1.0)] = Field(
        default=1.0,
        description="요약/통계 결과의 품질 점수"
    )
    summary_needs_retry: bool = Field(
        default=False,
        description="요약/통계를 재생성해야 하는지 여부"
    )
    summary_retry_hints: List[str] = Field(
        default_factory=list,
        description="필요 시 보강해야 할 힌트"
    )

    @model_validator(mode="before")
    @classmethod
    def _normalize_summary(cls, data: Any):
        if not isinstance(data, dict):
            return data
        data = dict(data)

        talk_summary = data.get("talkSummary")
        if isinstance(talk_summary, list):
            data["talkSummary"] = {"items": talk_summary}

        conv_stats = data.get("convStats")
        if isinstance(conv_stats, dict):
            data["convStats"] = [
                {"k": str(k), "v": cls._format_conv_value(v)}
                for k, v in conv_stats.items()
                if v is not None
            ]
        elif isinstance(conv_stats, list):
            normalized_stats = []
            for item in conv_stats:
                if isinstance(item, dict):
                    key = (
                        item.get("k")
                        or item.get("key")
                        or item.get("label")
                        or item.get("name")
                    )
                    value = item.get("v")
                    if value is None:
                        value = item.get("value") or item.get("val")
                    if key is None and isinstance(item.get("text"), str) and ":" in item["text"]:
                        key, value = item["text"].split(":", 1)
                    if key is not None and value is not None:
                        normalized_stats.append(
                            {"k": str(key).strip(), "v": cls._format_conv_value(value)}
                        )
                elif isinstance(item, str) and ":" in item:
                    key, value = item.split(":", 1)
                    normalized_stats.append({"k": key.strip(), "v": value.strip()})
            if normalized_stats:
                data["convStats"] = normalized_stats

        hints = data.get("summary_retry_hints")
        if isinstance(hints, str):
            data["summary_retry_hints"] = [hints]
        elif isinstance(hints, list):
            normalized_hints = []
            for hint in hints:
                if isinstance(hint, str):
                    hint = hint.strip()
                    if hint:
                        normalized_hints.append(hint)
                elif hint is not None:
                    normalized_hints.append(str(hint))
            data["summary_retry_hints"] = normalized_hints
        else:
            data["summary_retry_hints"] = []

        return data

    @staticmethod
    def _format_conv_value(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, (int, float)):
            return str(value)
        if isinstance(value, list):
            return ", ".join(str(v) for v in value)
        if isinstance(value, dict):
            return ", ".join(f"{k}: {v}" for k, v in value.items())
        return str(value)




# ==================== 에러 유틸 ====================

class ReportError(Exception):
    def __init__(self, code: str, message: str, http: int = 400, details: Dict[str, Any] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.http = http
        self.details = details or {}


def _error(code: str, message: str, http: int = 400, details: Dict[str, Any] = None) -> Dict[str, Any]:
    return {
        "status": "failed",
        "code": code,
        "message": message,
        "http": http,
        "trace_id": uuid4().hex,
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "details": details or {},
    }


# ==================== 헬퍼 ====================

def _extract_qa_pairs(log_text: str) -> List[Dict[str, str]]:
    qa_pairs: List[Dict[str, str]] = []
    lines = log_text.split("\n")
    current_q: Optional[str] = None
    current_a: List[str] = []
    q_num = 0

    for line in lines:
        line = line.strip()
        if not line:
            continue

        if any(line.startswith(prefix) for prefix in ["Q:", "q:", "면접관:", "Question:", "Q.", "[면접관]"]):
            if current_q and current_a:
                q_num += 1
                qa_pairs.append(
                    {"q_num": str(q_num), "question": current_q, "answer": " ".join(current_a)}
                )
                current_a = []

            for prefix in ["Q:", "q:", "면접관:", "Question:", "Q.", "[면접관]"]:
                if line.startswith(prefix):
                    current_q = line[len(prefix):].strip()
                    break

        elif any(line.startswith(prefix) for prefix in ["A:", "a:", "지원자:", "Answer:", "A.", "[지원자]"]):
            for prefix in ["A:", "a:", "지원자:", "Answer:", "A.", "[지원자]"]:
                if line.startswith(prefix):
                    answer_text = line[len(prefix):].strip()
                    current_a.append(answer_text)
                    break

        elif current_q and current_a:
            current_a.append(line)

    if current_q and current_a:
        q_num += 1
        qa_pairs.append({"q_num": str(q_num), "question": current_q, "answer": " ".join(current_a)})

    return qa_pairs

def _solar_chat_text(system: str, user: str) -> str:
    """간단한 system/user 프롬프트로 Solar Chat 호출해서 문자열만 반환."""
    prompt = ChatPromptTemplate.from_messages(
        [("system", system), ("user", user)]
    )
    chain = prompt | _llm_solar_chat()
    try:
        result = chain.invoke({})
    except Exception as e:  # noqa: BLE001
        logger.warning("solar_chat_text invoke error: %s", e)
        return ""

    # ChatOpenAI는 보통 BaseMessage를 반환
    if isinstance(result, str):
        return result.strip()
    content = getattr(result, "content", None)
    if isinstance(content, str):
        return content.strip()
    return str(result).strip()
T = TypeVar("T", bound=BaseModel)


def _safe_structured_invoke(
    prompt: ChatPromptTemplate,
    schema: type[T],
    variables: dict[str, Any],
) -> Optional[T]:
    """
    동기 구조화 호출 (프롬프트 + Pydantic 스키마)
    실패 시 None 리턴.
    """
    for method in ("json_mode", "function_calling"):
        try:
            chain = prompt | _llm_gpt_chat().with_structured_output(
                schema, method=method
            )
            return chain.invoke(variables)
        except ValidationError as e:
            logger.warning(
                "Pydantic validation error for %s (method=%s): %s",
                schema.__name__,
                method,
                e,
            )
        except Exception as e:
            logger.warning(
                "structured invoke failed for %s (method=%s): %s",
                schema.__name__,
                method,
                e,
            )
    return None


async def _safe_structured_invoke_async(
    prompt: ChatPromptTemplate,
    schema: type[T],
    variables: dict[str, Any],
) -> Optional[T]:
    """
    비동기 구조화 호출 (프롬프트 + Pydantic 스키마)
    실패 시 None 리턴.
    """
    for method in ("json_mode", "function_calling"):
        try:
            chain = prompt | _llm_solar_chat().with_structured_output(
                schema, method=method
            )
            return await chain.ainvoke(variables)
        except ValidationError as e:
            logger.warning(
                "Pydantic validation error for %s (method=%s): %s",
                schema.__name__,
                method,
                e,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "structured async invoke failed for %s (method=%s): %s",
                schema.__name__,
                method,
                e,
            )
    return None


def _keep_first_value(old: Any | None, new: Any | None) -> Any | None:
    return old if old not in (None, {}, [], "") else new

# ==================== LangGraph State ====================

class ReportState(TypedDict, total=False):
    # 입력
    session_id: Annotated[str, _keep_first_value]
    candidate_name: Annotated[str, _keep_first_value]
    axes_keys: Annotated[List[str], _keep_first_value]
    user_prompt: Annotated[Optional[str], _keep_first_value]
    meta: Annotated[Dict[str, Any], _keep_first_value]

    # 공용 로그
    agent_logs: Annotated[List[Dict[str, str]], operator.add]
    error: Annotated[Optional[str], _keep_first_value]

    # 0~3. 인터뷰 로그/세그먼트
    raw_log: Annotated[str, _keep_first_value]
    segments: Annotated[List[Dict[str, str]], _keep_first_value]
    segment_summaries: Annotated[List[Dict[str, Any]], _keep_first_value]
    segment_emotions: Annotated[List[Dict[str, Any]], _keep_first_value]
    segment_risks: Annotated[List[Dict[str, Any]], _keep_first_value]
    segment_index: Annotated[Dict[str, int], _keep_first_value]
    segment_cache_keys: Annotated[List[str], _keep_first_value]
    interview_summary_base: Annotated[Dict[str, Any], _keep_first_value]

    # 4~5. 프롬프트 + 플래닝
    optimized_prompt: Annotated[Dict[str, Any], _keep_first_value]
    query_plan: Annotated[Dict[str, Any], _keep_first_value]

    # 6. 컨텍스트
    jd_ctx: Annotated[str, _keep_first_value]
    resume_ctx: Annotated[str, _keep_first_value]
    log_ctx: Annotated[str, _keep_first_value]
    qa_pairs: Annotated[List[Dict[str, str]], _keep_first_value]
    comp_id_list: Annotated[List[str], _keep_first_value]
    eid_list: Annotated[List[str], _keep_first_value]

    # 7~8. Evidence
    evidence_summary: Annotated[Dict[str, Any], _keep_first_value]
    evidence_needs_retry: Annotated[bool, _keep_first_value]
    evidence_retry_hints: Annotated[List[str], _keep_first_value]
    evidence_quality_score: Annotated[float, _keep_first_value]
    retrieval_retry_count: Annotated[int, _keep_first_value]

    # 9. Cross-check
    cross_check: Annotated[Dict[str, Any], _keep_first_value]

    # 10A~D. 점수/커버리지/분석/요약
    comp_eval: Annotated[Dict[str, Any], _keep_first_value]
    scoring_needs_retry: Annotated[bool, _keep_first_value]
    scoring_retry_hints: Annotated[List[str], _keep_first_value]
    scoring_quality_score: Annotated[float, _keep_first_value]

    comp_cover_result: Annotated[Dict[str, Any], _keep_first_value]
    coverage_needs_retry: Annotated[bool, _keep_first_value]
    coverage_retry_hints: Annotated[List[str], _keep_first_value]
    coverage_quality_score: Annotated[float, _keep_first_value]

    interview_analysis: Annotated[Dict[str, Any], _keep_first_value]
    analysis_needs_retry: Annotated[bool, _keep_first_value]
    analysis_retry_hints: Annotated[List[str], _keep_first_value]
    analysis_quality_score: Annotated[float, _keep_first_value]

    summary_stats: Annotated[Dict[str, Any], _keep_first_value]
    summary_needs_retry: Annotated[bool, _keep_first_value]
    summary_retry_hints: Annotated[List[str], _keep_first_value]
    summary_quality_score: Annotated[float, _keep_first_value]

    analysis_retry_count: Annotated[int, _keep_first_value]

    # 11~15. 최종 단계
    debate_result: Annotated[Dict[str, Any], _keep_first_value]
    final_recommendation: Annotated[Dict[str, Any], _keep_first_value]
    report: Annotated[Dict[str, Any], _keep_first_value]

# ==================== 0. Precheck ====================

def precheck_node(state: ReportState) -> ReportState:
    session_id = state["session_id"]
    meta = state.get("meta") or {}
    missing: List[str] = []

    try:
        log_ctx = retrieve_interview_context(session_id) or ""
    except Exception:
        log_ctx = ""
    if not log_ctx:
        missing.append("인터뷰 로그")

    state["raw_log"] = log_ctx
    state["agent_logs"].append(
        {
            "agent": "0. 프리체크",
            "message": f"세션 {session_id} 프리체크 완료. 누락: {', '.join(missing) if missing else '없음'}",
        }
    )
    if missing:
        logger.info("Precheck: missing=%s", missing)
    return state


# ==================== 1. SegPrep ====================

def seg_prep_node(state: ReportState) -> ReportState:
    raw_log = state.get("raw_log", "") or ""
    qa_pairs = _extract_qa_pairs(raw_log)
    
    segments: List[Dict[str, Any]] = []
    for idx, qa in enumerate(qa_pairs, start=1):
        q_id = qa.get("q_id") or qa.get("q_num") or f"Q{idx}"
        segments.append(
            {
                "q_id": q_id,
                "q_index": idx - 1,
                "question": qa.get("question", ""),
                "answer": qa.get("answer", ""),
                # speaker/meta 필요하면 여기 확장
            }
        )
        
    state["segments"] = segments
    state.setdefault("agent_logs", []).append(
        {
            "agent": "1. 세그먼트 전처리",
            "message": f"질문-답변 세그먼트 {len(segments)}개 추출",
        }
    )
    return state


# ==================== 2-A/B/C. Seg 분석 ====================

def seg_summary_node(state: ReportState) -> ReportState:
    segments = state.get("segments") or []
    summaries: List[Dict[str, Any]] = []

    if not segments:
        state["segment_summaries"] = []
        state.setdefault("agent_logs", []).append(
            {"agent": "2-A. 세그 요약", "message": "세그먼트가 없어 요약을 건너뜀"}
        )
        return state

    system = (
        "당신은 면접 로그를 요약하는 어시스턴트입니다. "
        "각 세그먼트(질문/답변)에 대해 한국어로 2~3문장 요약을 생성하세요. "
        "불필요한 인삿말·장식은 제외하고, 핵심 행동/경험/성과 위주로 정리하세요."
    )

    for seg in segments:
        q_id = seg["q_id"]
        question = seg.get("question", "")
        answer = seg.get("answer", "")

        user = f"""다음 면접 세그먼트를 1~3문장으로 요약하세요.

[질문]
{question}

[답변]
{answer}
"""
        summary = _solar_chat_text(system, user)
        summaries.append(
            {
                "q_id": q_id,
                "summary": summary,
            }
        )

    return {
        "segment_summaries": summaries,
        "agent_logs": [
            {
                "agent": "2-A. 세그 요약",
                "message": f"세그먼트 요약 {len(summaries)}개 생성",
            }
        ],
    }
    


def seg_emotion_node(state: ReportState) -> ReportState:
    segments = state.get("segments") or []
    emotions: List[Dict[str, Any]] = []

    if not segments:
        state["segment_emotions"] = []
        state.setdefault("agent_logs", []).append(
            {"agent": "2-B. 세그 감정", "message": "세그먼트가 없어 감정 분석을 건너뜀"}
        )
        return state

    system = (
        "당신은 면접 답변의 감정·톤을 분류하는 분석가입니다. "
        "각 답변에 대해 다음 라벨 중 하나만 선택해서 한 단어로 출력하세요:\n"
        "- '차분', '긴장', '자신감', '열정적', '방어적', '모호함'\n"
        "추가 설명 없이 라벨만 출력하세요."
    )

    for seg in segments:
        q_id = seg["q_id"]
        answer = seg.get("answer", "")
        user = f"다음 면접 답변의 주된 감정/톤을 한 단어로 분류하세요.\n\n[답변]\n{answer}"
        label = _solar_chat_text(system, user)
        emotions.append(
            {
                "q_id": q_id,
                "emotion": label,
            }
        )

    return {
        "segment_emotions": emotions,
        "agent_logs": [
            {
                "agent": "2-B. 세그 감정",
                "message": f"세그먼트 감정 레이블 {len(emotions)}개 생성",
            }
        ],
    }



def seg_risk_node(state: ReportState) -> ReportState:
    segments = state.get("segments") or []
    risks: List[Dict[str, Any]] = []

    if not segments:
        state["segment_risks"] = []
        state.setdefault("agent_logs", []).append(
            {"agent": "2-C. 세그 리스크", "message": "세그먼트가 없어 리스크 분석을 건너뜀"}
        )
        return state

    system = (
        "당신은 면접 답변에서 리스크 신호를 감지하는 분석가입니다. "
        "각 답변에 대해 다음 분류 중 하나를 선택해 한 단어로 출력하세요:\n"
        "- '정상', '과장가능성', '회피답변', '모순의심', '경고', '불명확'\n"
        "추가 설명 없이 라벨만 출력하세요."
    )

    for seg in segments:
        q_id = seg["q_id"]
        question = seg.get("question", "")
        answer = seg.get("answer", "")
        user = f"""다음 질문/답변에서 리스크 신호를 분류하세요.

[질문]
{question}

[답변]
{answer}
"""
        label = _solar_chat_text(system, user)
        risks.append(
            {
                "q_id": q_id,
                "risk": label,
            }
        )

    return {
        "segment_risks": risks,
        "agent_logs": [
            {
                "agent": "2-C. 세그 리스크",
                "message": f"세그먼트 리스크 레이블 {len(risks)}개 생성",
            }
        ],
    }



# ==================== 3. SegMerge ====================

def seg_merge_node(state: ReportState) -> ReportState:
    """세그먼트 요약/감정/리스크를 하나의 베이스 요약/인덱스로 통합."""
    segments = state.get("segments") or []
    seg_summaries = {s["q_id"]: s for s in (state.get("segment_summaries") or [])}
    seg_emotions = {e["q_id"]: e for e in (state.get("segment_emotions") or [])}
    seg_risks = {r["q_id"]: r for r in (state.get("segment_risks") or [])}

    # q_id -> index 맵
    segment_index: Dict[str, int] = {}
    segment_cache_keys: List[str] = []

    session_id = state.get("session_id", "unknown_session")

    # 인터뷰 전체 요약용 텍스트 구성
    lines: List[str] = []
    for idx, seg in enumerate(segments):
        q_id = seg["q_id"]
        segment_index[q_id] = idx
        segment_cache_keys.append(f"{session_id}:seg:{q_id}")

        summary = seg_summaries.get(q_id, {}).get("summary", "")
        emotion = seg_emotions.get(q_id, {}).get("emotion", "")
        risk = seg_risks.get(q_id, {}).get("risk", "")

        line = f"[{q_id}] 요약: {summary}\n - 감정: {emotion}, 리스크: {risk}"
        lines.append(line)

    long_text = "\n\n".join(lines) if lines else ""

    # 인터뷰 전체 요약 베이스 (LLM 한 번 호출)
    if long_text:
        system = (
            "당신은 전체 인터뷰 세그먼트 요약을 바탕으로, "
            "인터뷰 전반의 핵심 특징을 정리하는 분석가입니다. "
            "핵심 주제, 강점, 우려 포인트를 포함하여 4~6문장으로 정리하세요."
        )
        user = f"[세그먼트별 요약/감정/리스크]\n\n{long_text}"
        overall = _solar_chat_text(system, user)
    else:
        overall = ""

    interview_summary_base: Dict[str, Any] = {
        "overall_summary": overall,
        "segment_count": len(segments),
    }

    state["segment_index"] = segment_index
    state["segment_cache_keys"] = segment_cache_keys
    state["interview_summary_base"] = interview_summary_base

    state.setdefault("agent_logs", []).append(
        {
            "agent": "3. 세그 통합",
            "message": f"세그먼트 메타/캐시 구성 완료 (세그먼트 수: {len(segments)})",
        }
    )
    return state


# ==================== 4. PromptLens ====================

def _prompt_lens_template() -> ChatPromptTemplate:
    return ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "당신은 채용 평가용 프롬프트를 정리하는 전문가입니다.\n"
                "사용자 프롬프트를 평가 관점으로 정리하고, 핵심 초점 영역을 추려주세요.\n"
                "모든 출력은 한국어 JSON입니다.",
            ),
            (
                "human",
                "사용자 프롬프트:\n{user_prompt}\n\n"
                "평가 축(axes_keys): {axes_keys}\n\n"
                "위 정보를 바탕으로 다음 필드를 JSON으로 반환하세요.\n"
                "- original_prompt\n- lens_perspective (2-3문장)\n"
                "- key_focus_areas (최대 5개)\n- reasoning (간단한 근거)",
            ),
        ]
    )


def prompt_lens_node(state: ReportState) -> ReportState:
    axes_keys = state.get("axes_keys") or []
    user_prompt = (state.get("user_prompt") or "").strip()
    state.setdefault("agent_logs", [])

    default_focus = axes_keys[:5] or ["전반적인 직무 적합도"]

    # 사용자 프롬프트 없으면 LLM 호출 안 함
    if not user_prompt:
        opt = OptimizedPrompt(
            original_prompt="사용자 프롬프트 없음",
            lens_perspective=(
                f"제출된 이력서, JD, 인터뷰 로그를 바탕으로 "
                f"{', '.join(default_focus)} 관점에서 후보자의 역량을 평가합니다."
            ),
            key_focus_areas=default_focus,
            reasoning="사용자 프롬프트가 없어 기본 평가 렌즈를 사용했습니다.",
        )
    else:
        prompt = _prompt_lens_template()
        result = _safe_structured_invoke(
            prompt,
            OptimizedPrompt,
            {
                "user_prompt": user_prompt,
                "axes_keys": ", ".join(axes_keys),
            },
        )

        if result is None:
            opt = OptimizedPrompt(
                original_prompt=user_prompt,
                lens_perspective=(
                    f"{', '.join(default_focus)} 관점에서 후보자를 평가합니다."
                ),
                key_focus_areas=default_focus,
                reasoning="LLM 호출 실패로 기본 평가 렌즈를 사용했습니다.",
            )
        else:
            opt = result

    state["optimized_prompt"] = opt.model_dump()
    state["agent_logs"].append(
        {
            "agent": "4. 프롬프트 렌즈",
            "message": f"렌즈 관점 생성: {', '.join(opt.key_focus_areas)}",
        }
    )
    return state


# ==================== 5. QueryPlanner ====================

def _query_planner_template() -> ChatPromptTemplate:
    return ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "당신은 벡터 검색 질의 계획을 설계하는 에이전트입니다.\n"
                "이력서, JD, 인터뷰 로그에서 필요한 정보를 찾기 위한 질의를 설계하세요.\n"
                "모든 출력은 한국어 JSON 형식입니다.",
            ),
            (
                "human",
                "평가 축: {axes_keys}\n"
                "렌즈 관점: {lens_perspective}\n"
                "핵심 초점: {key_focus_areas}\n\n"
                "**[사용자 핵심 지시사항]**\n{user_prompt}\n\n"
                "위 **[사용자 핵심 지시사항]**을 최우선으로 고려하여 다음 필드를 JSON으로 출력하세요.\n"
                "- resume_query\n"
                "- comp_query\n"
                "- interview_query\n"
                "- focus_areas (문자열 리스트)\n"
                "- reasoning\n"
                "포트폴리오가 필요하면 portfolio_query 필드도 추가하세요."
            ),
        ]
    )




def query_planner_node(state: ReportState) -> ReportState:
    state.setdefault("agent_logs", [])

    opt_raw = state.get("optimized_prompt") or {}
    opt = OptimizedPrompt.model_validate(opt_raw)

    axes_keys = state.get("axes_keys") or []
    axes_str = ", ".join(axes_keys)
    lens = opt.lens_perspective or axes_str or "전반적인 직무 적합도"
    focus_str = ", ".join(opt.key_focus_areas)

    prompt = _query_planner_template()
    result = _safe_structured_invoke(
        prompt,
        QueryPlan,
        {
            "axes_keys": axes_str,
            "lens_perspective": lens,
            "key_focus_areas": focus_str,
            "user_prompt": state.get("user_prompt") or "없음",
        },
    )

    if result is None:
        # 스키마에 맞는 최소 폴백
        plan = QueryPlan(
            resume_query=f"{lens} 관점에서 후보자의 경력/성과를 확인할 수 있는 내용",
            comp_query=f"{lens} 관점에서 JD 핵심 요구사항과 매칭되는 경험",
            portfolio_query=None,
            interview_query=f"{lens} 관점에서 인터뷰 답변의 강점/약점이 드러나는 부분",
            focus_areas=opt.key_focus_areas,
            reasoning="LLM 호출 실패로 기본 질의 템플릿을 사용했습니다.",
        )
    else:
        plan = result

    state["query_plan"] = plan.model_dump()
    state["agent_logs"].append(
        {
            "agent": "5. 질의 설계",
            "message": "이력서/JD/인터뷰 로그 질의 설계 완료",
        }
    )
    return state


# ==================== 6. Retriever ====================

def _retrieve_contexts(session_id: str, query_plan: QueryPlan, axes_keys: List[str],raw_log: str | None = None) -> Dict[str, Any]:
    resume_results: List[Dict[str, Any]] = []
    jd_results: List[Dict[str, Any]] = []
    portfolio_results: List[Dict[str, Any]] = []
    
    def _search_or_empty(query: str | None, doc_type: str) -> tuple[str, List[Dict[str, Any]]]:
        if not query:
            return "", []
        try:
            results = search_similar_chunks(
                query,
                session_id=session_id,
                doc_type=doc_type,
                top_k=5,
            )
            ctx = "\n\n".join(
                r.get("content", "") for r in results if r.get("content")
            )
            return ctx, results
        except Exception as e:  # noqa: BLE001
            logger.warning("%s search failed: %s", doc_type, e)
            return "", []

    # resume / jd / portfolio
    resume_ctx, resume_results = _search_or_empty(query_plan.resume_query, "resume")
    jd_ctx, jd_results = _search_or_empty(query_plan.comp_query, "jd")
    portfolio_ctx, portfolio_results = _search_or_empty(
        getattr(query_plan, "portfolio_query", None),
        "portfolio",
    )

    # 인터뷰 로그: 있으면 state.raw_log 재사용, 없으면 조회
    if raw_log and raw_log.strip():
        log_ctx = raw_log
    else:
        try:
            log_ctx = retrieve_interview_context(session_id) or ""
        except Exception as e:  # noqa: BLE001
            logger.warning("log retrieve failed: %s", e)
            log_ctx = ""

    qa_pairs = _extract_qa_pairs(log_ctx)

    total_evidence = len(resume_results) + len(jd_results) + len(portfolio_results) + len(qa_pairs)
    # 최소 15개 이상 E-ID 확보
    eid_list = [f"E{i:02d}" for i in range(1, max(total_evidence + 1, 15))]
    comp_id_list = [f"C{i + 1:02d}" for i in range(len(axes_keys))]

    return {
        "resume_ctx": resume_ctx,
        "jd_ctx": jd_ctx,
        "portfolio_ctx": portfolio_ctx,
        "log_ctx": log_ctx,
        "qa_pairs": qa_pairs,
        "comp_id_list": comp_id_list,
        "eid_list": eid_list,
    }


def retriever_node(state: ReportState) -> ReportState:
    state.setdefault("agent_logs", [])

    qp_raw = state.get("query_plan") or {}
    try:
        qp = QueryPlan.model_validate(qp_raw)
    except Exception as e:  # noqa: BLE001
        state["error"] = f"질의 설계 결과 파싱 실패: {e}"
        state["agent_logs"].append(
            {
                "agent": "6. 리트리버",
                "message": "QueryPlan 파싱 실패로 리트리버 중단",
            }
        )
        return state

    ctx = _retrieve_contexts(
        session_id=state["session_id"],
        query_plan=qp,
        axes_keys=state.get("axes_keys") or [],
        raw_log=state.get("raw_log", ""),
    )

    state["resume_ctx"] = ctx["resume_ctx"]
    state["jd_ctx"] = ctx["jd_ctx"]
    state["log_ctx"] = ctx["log_ctx"]
    state["qa_pairs"] = ctx["qa_pairs"]
    state["comp_id_list"] = ctx["comp_id_list"]
    state["eid_list"] = ctx["eid_list"]
    # 포트폴리오 컨텍스트를 쓰려면 ReportState에 필드만 추가해두면 됨
    state["portfolio_ctx"] = ctx.get("portfolio_ctx", "")

    state["retrieval_retry_count"] = state.get("retrieval_retry_count", 0)

    state["agent_logs"].append(
        {
            "agent": "6. 리트리버",
            "message": (
                f"이력서/JD/포트폴리오/로그 검색 완료 "
                f"(QA {len(state['qa_pairs'])}개)"
            ),
        }
    )
    return state


# ==================== 7. EvidenceMap ====================


def _evidence_template() -> ChatPromptTemplate:
    return ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "당신은 채용 인터뷰 증거 매핑 에이전트입니다.\n"
                "이력서, JD, 인터뷰 로그를 보고 어떤 축/요구사항이 부족한지 평가합니다.\n"
                "모든 출력은 한국어 JSON입니다.",
            ),
            (
                "human",
                "[이력서]\n{resume}\n\n[JD]\n{jd}\n\n[인터뷰 로그 요약]\n{log}\n\n"
                "평가 축: {axes_keys}\n\n"
                "다음 필드를 JSON으로 출력하세요.\n"
                "- missing_axes: 증거가 부족한 평가 축 리스트 (문자열 리스트)\n"
                "- missing_jd_items: JD 요구사항/역량 중 부족한 항목 설명 리스트 (문자열 리스트)\n"
                "- quality_score: 0.0~1.0\n"
                "- needs_retry: bool\n"
                "- retry_hints: 재질의 힌트 문자열 리스트"
            ),
        ]
    )

async def evidence_map_node(state: ReportState) -> ReportState:
    prompt = _evidence_template()
    log_short = (state.get("log_ctx") or "")[:SUMMARY_MAX_LOG_CHARS]
    result = await _safe_structured_invoke_async(
        prompt,
        EvidenceEvalOut,
        {
            "resume": state.get("resume_ctx", ""),
            "jd": state.get("jd_ctx", ""),
            "log": log_short,
            "axes_keys": ", ".join(state.get("axes_keys") or []),
        },
    )
    if result is None:
        state["evidence_summary"] = {}
        state["evidence_quality_score"] = 0.0
        state["evidence_needs_retry"] = False
        state["evidence_retry_hints"] = []
        state.setdefault("agent_logs", []).append(
            {"agent": "7. 증거 매핑", "message": "LLM 오류 → 기본값 사용"}
        )
        return state

    state["evidence_summary"] = result.model_dump()
    state["evidence_quality_score"] = float(result.quality_score)
    state["evidence_needs_retry"] = bool(result.needs_retry)
    state["evidence_retry_hints"] = result.retry_hints
    state.setdefault("agent_logs", []).append(
        {
            "agent": "7. 증거 매핑",
            "message": f"품질={result.quality_score:.2f}, 재시도={result.needs_retry}",
        }
    )
    return state



# ==================== 8. EvidenceRouter (루프 1) ====================

def evidence_router_node(state: ReportState) -> str:
    retry_count = state.get("retrieval_retry_count", 0)
    needs_retry = state.get("evidence_needs_retry", False)
    if needs_retry and retry_count < REPORT_MAX_RETRIEVAL_RETRY:
        state["retrieval_retry_count"] = retry_count + 1
        state.setdefault("agent_logs", []).append(
            {
                "agent": "8. 증거 라우터",
                "message": f"증거 부족 → 리트리버 재시도 ({state['retrieval_retry_count']})",
            }
        )
        return "retry"
    state.setdefault("agent_logs", []).append(
        {
            "agent": "8. 증거 라우터",
            "message": "증거 충분 또는 재시도 한도 → 교차검증 진행",
        }
    )
    return "next"



# ==================== 9. CrossCheck ====================

def _cross_check_template() -> ChatPromptTemplate:
    return ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "당신은 문서-인터뷰 교차검증 전문가입니다.\n"
                "이력서, JD, 인터뷰 로그 간의 일관성과 모순을 평가합니다.\n"
                "모든 출력은 한국어 JSON입니다.",
            ),
            (
                "human",
                "[이력서]\n{resume}\n\n[JD]\n{jd}\n\n[인터뷰 로그]\n{log}\n\n"
                "다음 필드를 JSON으로 출력하세요.\n"
                "- overall_consistency_score (0~100, 높을수록 일관성 높음)\n"
                "- issues: 발견된 모순/갭 리스트\n"
                "- notes: 추가 메모 리스트",
            ),
        ]
    )


async def cross_check_node(state: ReportState) -> ReportState:
    prompt = _cross_check_template()
    log_short = (state.get("log_ctx") or "")[:SUMMARY_MAX_LOG_CHARS]
    result = await _safe_structured_invoke_async(
        prompt,
        CrossCheckOut,
        {
            "resume": state.get("resume_ctx", ""),
            "jd": state.get("jd_ctx", ""),
            "log": log_short,
        },
    )
    if result is None:
        state["cross_check"] = {}
        state.setdefault("agent_logs", []).append(
            {"agent": "9. 교차검증", "message": "LLM 오류 → 기본 교차검증 사용"}
        )
        return state

    state["cross_check"] = result.model_dump()
    state.setdefault("agent_logs", []).append(
        {
            "agent": "9. 교차검증",
            "message": f"일관성 점수={result.overall_consistency_score}",
        }
    )
    return state




# ==================== 10-A. CompScore ====================

def _comp_score_template() -> ChatPromptTemplate:
    return ChatPromptTemplate.from_messages(
        [
            ("system",
             "당신은 면접 핵심역량 평가 전문가 입니다"
             " 면접 핵심역량 평가 전문가입니다.\n"
             "이력서와 인터뷰 로그를 기반으로 축별 점수, 헤드라인, 키워드를 생성합니다.\n"
             "모든 출력은 한국어 JSON입니다."),
            ("human",
             "평가 축: {axes_keys}\n렌즈 관점: {lens_perspective}\n\n"
             "[이력서]\n{resume}\n\n[인터뷰 로그]\n{log}\n\n"
             "**[사용자 핵심 지시사항]**\n{user_prompt}\n\n"
             "위 **[사용자 핵심 지시사항]**을 최우선으로 반영하여 다음 필드를 JSON으로 출력하세요.\n\n"
             "1. **scores**: 각 평가 축별 점수 (0~100점)\n"
             "2. **headline**: \n"
             "   - summary: 후보자 특징을 한 문장으로 요약 (예: '리더십과 협업 역량 보유, 커뮤니케이션 강점 있으나 팀 역량 관리 개선 필요')\n"
             "   - overall_summary: 인터뷰 전반에 대한 평가 (350-400자)\n"
             "   - tag: 후보자를 대표하는 키워드 3-5개 (예: ['리더십', '협업', '문제해결', '커뮤니케이션'])\n"
             "3. **reasoning**: 점수 산정 근거 리스트\n"
             "4. **competency_comments**: 각 역량별 점수 이유 (2-3문장)\n"
             "5. **scoring_quality_score**: 평가 품질 점수 (0.0~1.0)\n"
             "6. **scoring_needs_retry**: 재평가 필요 여부\n"
             "7. **scoring_retry_hints**: 재평가 시 힌트")
        ]
    )


async def comp_score_node(state: ReportState) -> ReportState:
    opt = OptimizedPrompt.model_validate(state.get("optimized_prompt") or {})
    log_short = (state.get("log_ctx") or "")[:SUMMARY_MAX_LOG_CHARS]

    result = await _safe_structured_invoke_async(
        _comp_score_template(),
        CompetencyEvalOut,
        {
            "axes_keys": ", ".join(state.get("axes_keys") or []),
            "lens_perspective": opt.lens_perspective,
            "user_prompt": state.get("user_prompt") or "없음",
            "resume": state.get("resume_ctx", ""),
            "log": log_short,
        },
    )

    if result is None:
        # ✅ LLM 실패 시에도 최소 유효 구조 생성
        axes_keys = state.get("axes_keys") or []
        fallback_scores = (
            [ScoreItem(key=k, value=0) for k in axes_keys]
            or [ScoreItem(key="overall", value=0)]
        )
        fallback_headline = Headline(
            summary="평가 생성 실패",
            overall_summary="LLM 오류로 평가 축/헤드라인을 생성하지 못해 기본값을 사용합니다.",
            tag=["fallback"],
        )
        comp = CompetencyEvalOut(
            scores=fallback_scores,
            headline=fallback_headline,
            reasoning=["LLM 평가 생성 실패, 기본 점수 사용"],
            competency_comments=[],
            scoring_quality_score=0.0,
            scoring_needs_retry=False,
            scoring_retry_hints=["LLM 평가 실패"],
        )
        return {
            "comp_eval": comp.model_dump(),
            "scoring_quality_score": comp.scoring_quality_score,
            "scoring_needs_retry": comp.scoring_needs_retry,
            "scoring_retry_hints": comp.scoring_retry_hints,
            "agent_logs": [
                {"agent": "10-A. 핵심축 점수", "message": "LLM 오류, 기본 점수 사용"}
            ],
        }

    # ✅ 정상 케이스는 그대로
    return {
        "comp_eval": result.model_dump(),
        "scoring_quality_score": float(result.scoring_quality_score),
        "scoring_needs_retry": bool(result.scoring_needs_retry),
        "scoring_retry_hints": result.scoring_retry_hints,
        "agent_logs": [
            {
                "agent": "10-A. 핵심축 점수",
                "message": f"축 {len(result.scores)}개, 품질={result.scoring_quality_score:.2f}",
            }
        ],
    }





# ==================== 10-B. JDCover ====================

def _comp_cover_template() -> ChatPromptTemplate:
    return ChatPromptTemplate.from_messages(
        [
            ("system",
             "당신은 역량(competency) 기준 커버리지를 평가하는 전문가입니다.\n"
             "각 역량별 요구사항/기대/충족도와 근거를 표 형식으로 정리합니다.\n"
             "모든 출력은 한국어 JSON입니다."),
            ("human",
             "평가 역량 축: {axes_keys}\n"
             "역량 ID 매핑: {comp_id_list}\n\n"
             "[JD]\n{jd}\n\n[이력서]\n{resume}\n\n[인터뷰 로그]\n{log}\n\n"
             "증거 ID 목록: {eid_list}\n\n"
             "위 평가 역량 축 각각에 대해 다음 필드를 JSON으로 출력하세요.\n"
             "- compCoverage: {{cid(예: C01, C02...), requirement, expectation, fulfillment, evidence[eid...]}} 리스트 (역량 축 개수만큼)\n"
             "- evidence: {{eid(예: E01, E02...), source, content, cid}} 리스트\n"
             "- coverage_quality_score(0.0~1.0)\n- coverage_needs_retry(bool)\n- coverage_retry_hints\n\n"
             "**중요**: cid는 반드시 comp_id_list에서 제공된 ID(C01, C02 등)를 사용하세요.")
        ]
    )

async def jd_cover_node(state: ReportState) -> ReportState:
    log_short = (state.get("log_ctx") or "")[:SUMMARY_MAX_LOG_CHARS]
    axes_keys = state.get("axes_keys") or []
    comp_id_list = state.get("comp_id_list") or []
    
    result = await _safe_structured_invoke_async(
        _comp_cover_template(),
        CompCoverageOut,
        {
            "axes_keys": ", ".join(axes_keys),
            "comp_id_list": ", ".join(comp_id_list),
            "jd": state.get("jd_ctx", ""),
            "resume": state.get("resume_ctx", ""),
            "log": log_short,
            "eid_list": ", ".join(state.get("eid_list") or []),
        },
    )

    if result is None:
        return {
            "comp_cover_result": {},
            "coverage_quality_score": 0.0,
            "coverage_needs_retry": False,
            "coverage_retry_hints": ["커버리지 분석 실패"],
            "agent_logs": [
                {"agent": "10-B. 커버리지", "message": "LLM 오류, 기본값 사용"}
            ],
        }

    return {
        "comp_cover_result": result.model_dump(),
        "coverage_quality_score": float(result.coverage_quality_score),
        "coverage_needs_retry": bool(result.coverage_needs_retry),
        "coverage_retry_hints": result.coverage_retry_hints,
        "agent_logs": [
            {
                "agent": "10-B. 커버리지",
                "message": f"항목 {len(result.compCoverage)}개, 품질={result.coverage_quality_score:.2f}",
            }
        ],
    }


# ==================== 10-C. InterviewQuality ====================

def _interview_quality_template() -> ChatPromptTemplate:
    return ChatPromptTemplate.from_messages(
        [
            ("system",
             "당신은 인터뷰 품질(모순, 깊이, 신뢰도)을 평가하는 전문가입니다.\n"
             "모든 출력은 한국어 JSON입니다."),
            ("human",
             "[인터뷰 로그]\n{log}\n\n"
             "다음 필드를 JSON으로 출력하세요.\n"
             "- contradiction_score, contradiction_reason\n"
             "- depth_score, depth_reason\n"
             "- reliability_score, reliability_reason\n"
             "- positive_aspects, negative_aspects, final_comment\n"
             "- analysis_quality_score(0.0~1.0)\n- analysis_needs_retry(bool)\n- analysis_retry_hints")
        ]
    )

async def interview_quality_node(state: ReportState) -> ReportState:
    log_short = (state.get("log_ctx") or "")[:SUMMARY_MAX_LOG_CHARS]
    result = await _safe_structured_invoke_async(
        _interview_quality_template(),
        InterviewAnalysisOut,
        {"log": log_short},
    )
    if result is None:
        return {
            "interview_analysis": {},
            "analysis_quality_score": 0.0,
            "analysis_needs_retry": False,
            "analysis_retry_hints": ["인터뷰 품질 분석 실패"],
            "agent_logs": [
                {"agent": "10-C. 인터뷰 품질", "message": "LLM 오류, 기본값 사용"}
            ],
        }

    return {
        "interview_analysis": result.model_dump(),
        "analysis_quality_score": float(result.analysis_quality_score),
        "analysis_needs_retry": bool(result.analysis_needs_retry),
        "analysis_retry_hints": result.analysis_retry_hints,
        "agent_logs": [
            {
                "agent": "10-C. 인터뷰 품질",
                "message": (
                    f"모순={result.contradiction_score}, "
                    f"깊이={result.depth_score}, "
                    f"신뢰도={result.reliability_score}"
                ),
            }
        ],
    }

# ==================== 10-D. SummaryStats ====================

def _summary_stats_template() -> ChatPromptTemplate:
    return ChatPromptTemplate.from_messages(
        [
            ("system",
             "당신은 인터뷰 요약·통계 전문가입니다.\n"
             "모든 출력은 한국어 JSON입니다."),
            ("human",
             "[인터뷰 로그]\n{log}\n\n"
             "다음 필드를 JSON으로 출력하세요.\n"
             "- talkSummary.items[{{topic, summary}}]\n"
             "- convStats[{{k, v}}]\n"
             "- interview_stats(optional)\n"
             "- summary_quality_score(0.0~1.0)\n- summary_needs_retry(bool)\n- summary_retry_hints")
        ]
    )

async def summary_stats_node(state: ReportState) -> ReportState:
    log_short = (state.get("log_ctx") or "")[:SUMMARY_MAX_LOG_CHARS]
    result = await _safe_structured_invoke_async(
        _summary_stats_template(),
        SummaryStatsOut,
        {"log": log_short},
    )
    if result is None:
        # ✅ LLM 실패 시에도 최소 유효 구조 생성
        fallback_talk = TalkSummary(
            items=[
                TalkSummaryItem(
                    topic="요약 생성 실패",
                    talkSummary="LLM 오류로 대화 요약/통계를 생성하지 못해 기본값을 사용합니다.",
                )
            ]
        )
        summary = SummaryStatsOut(
            talkSummary=fallback_talk,
            convStats=[],
            interview_stats=None,
            summary_quality_score=0.0,
            summary_needs_retry=False,
            summary_retry_hints=["요약/통계 생성 실패"],
        )
        return {
            "summary_stats": summary.model_dump(),
            "summary_quality_score": summary.summary_quality_score,
            "summary_needs_retry": summary.summary_needs_retry,
            "summary_retry_hints": summary.summary_retry_hints,
            "agent_logs": [
                {"agent": "10-D. 요약·통계", "message": "LLM 오류, 기본 요약 사용"}
            ],
        }

    # ✅ 정상 케이스
    return {
        "summary_stats": result.model_dump(),
        "summary_quality_score": float(result.summary_quality_score),
        "summary_needs_retry": bool(result.summary_needs_retry),
        "summary_retry_hints": result.summary_retry_hints,
        "agent_logs": [
            {
                "agent": "10-D. 요약·통계",
                "message": f"요약 항목={len(result.talkSummary.items)}, 품질={result.summary_quality_score:.2f}",
            }
        ],
    }





# ==================== 11. Debate (간단 품질 집계) ====================

def debate_node(state: ReportState) -> ReportState:
    scores = [
        state.get("scoring_quality_score", 0.0),
        state.get("coverage_quality_score", 0.0),
        state.get("analysis_quality_score", 0.0),
        state.get("summary_quality_score", 0.0),
    ]
    avg = sum(scores) / len(scores) if scores else 0.0
    need_retry = []
    if state.get("scoring_needs_retry"):
        need_retry.append("핵심역량")
    if state.get("coverage_needs_retry"):
        need_retry.append("커버리지")
    if state.get("analysis_needs_retry"):
        need_retry.append("인터뷰 품질")
    if state.get("summary_needs_retry"):
        need_retry.append("요약/통계")

    state["debate_result"] = {
        "avg_quality_score": avg,
        "need_retry_sections": need_retry,
    }
    state.setdefault("agent_logs", []).append(
        {
            "agent": "11. 디베이트 검증",
            "message": f"평균 품질={avg:.2f}, 재시도={', '.join(need_retry) if need_retry else '없음'}",
        }
    )
    return state

# ==================== 12. QualityRouter (루프 1/2 제어) ====================

def quality_router_node(state: ReportState) -> str:
    analysis_retry_count = state.get("analysis_retry_count", 0)

    if state.get("evidence_needs_retry") and state.get("retrieval_retry_count", 0) < REPORT_MAX_RETRIEVAL_RETRY:
        state.setdefault("agent_logs", []).append(
            {"agent": "12. 품질 라우터", "message": "증거 부족 → 리트리버 재실행"}
        )
        return "retry_retrieval"

    needs_analysis_retry = any(
        [
            state.get("scoring_needs_retry"),
            state.get("coverage_needs_retry"),
            state.get("analysis_needs_retry"),
            state.get("summary_needs_retry"),
        ]
    )
    if needs_analysis_retry and analysis_retry_count < REPORT_MAX_ANALYSIS_RETRY:
        state["analysis_retry_count"] = analysis_retry_count + 1
        state.setdefault("agent_logs", []).append(
            {
                "agent": "12. 품질 라우터",
                "message": f"섹션 분석 재실행({state['analysis_retry_count']})",
            }
        )
        return "retry_analysis"

    state.setdefault("agent_logs", []).append(
        {"agent": "12. 품질 라우터", "message": "품질 양호/한도 도달 → 최종 권고 진행"}
    )
    return "ok"




# ==================== 13. FinalRec ====================

def _final_rec_template() -> ChatPromptTemplate:
    return ChatPromptTemplate.from_messages(
        [
            ("system",
             "당신은 최종 채용 권고를 작성하는 전문가입니다.\n"
             "모든 출력은 한국어 JSON입니다."),
            ("human",
             "핵심역량 헤드라인: {headline}\n"
             "JD 이슈: {jd_issues}\n"
             "인터뷰 품질: {quality}\n"
             "교차검증: {cross}\n\n"
             "**[사용자 핵심 지시사항]**\n{user_prompt}\n\n"
             "위 **[사용자 핵심 지시사항]**을 최우선으로 고려하여 다음 필드를 JSON으로 출력하세요.\n"
             "- final_comment(250~300자)\n"
             "- hiring_decision: 예) '채용 권고', '보류', '비채용\n"
             "- decision_reasons: key reasons list (max 5, importance order)")
        ]
    )


async def final_rec_node(state: ReportState) -> ReportState:
    comp_raw = state.get("comp_eval") or {}
    qa_raw = state.get("interview_analysis") or {}
    cross_raw = state.get("cross_check") or {}
    headline = comp_raw.get("headline", {})
    jd_issues = (state.get("evidence_summary") or {}).get("missing_comp_items", [])
    quality = {
        "contradiction_score": qa_raw.get("contradiction_score"),
        "depth_score": qa_raw.get("depth_score"),
        "reliability_score": qa_raw.get("reliability_score"),
    }
    result = await _safe_structured_invoke_async(
        _final_rec_template(),
        FinalRecommendation,
        {
            "headline": headline,
            "jd_issues": jd_issues,
            "quality": quality,
            "cross": cross_raw,
            "user_prompt": state.get("user_prompt") or "없음",
        },
    )
    if result is None:
        state["final_recommendation"] = {}
        state.setdefault("agent_logs", []).append(
            {"agent": "13. 최종 권고", "message": "LLM 오류 → 기본 권고 사용"}
        )
        return state

    state["final_recommendation"] = result.model_dump()
    state.setdefault("agent_logs", []).append(
        {"agent": "13. 최종 권고", "message": f"최종 결정: {result.hiring_decision}"}
    )
    return state



# ==================== 14. Assemble (ReportOut 스키마 검증) ====================

def assemble_node(state: ReportState) -> ReportState:
    try:
        meta = ReportMetadata(
            candidate_name=state["candidate_name"],
            position_applied=(state.get("meta") or {}).get("position", "미정"),
            interview_date=(state.get("meta") or {}).get("interview_date", datetime.now().date().isoformat()),
            report_date=datetime.now().date().isoformat(),
            session_id=state["session_id"],
        )

        # axes_keys를 key와 label로 변환
        axes_keys = state.get("axes_keys") or []
        axes = [Competency(key=k, label=k) for k in axes_keys]
        
        comp = CompetencyEvalOut.model_validate(state.get("comp_eval") or {})
        cov = CompCoverageOut.model_validate(state.get("comp_cover_result") or {})
        summary = SummaryStatsOut.model_validate(state.get("summary_stats") or {})

        iq_raw = state.get("interview_analysis") or {}
        if iq_raw:
            iq = InterviewAnalysisOut.model_validate(iq_raw)
        else:
            iq = InterviewAnalysisOut(
                contradiction_score=50,
                contradiction_reason="분석 실패로 기본값 사용",
                depth_score=50,
                depth_reason="분석 실패로 기본값 사용",
                reliability_score=50,
                reliability_reason="분석 실패로 기본값 사용",
                positive_aspects="LLM 분석 실패로 상세 긍정 포인트를 추출하지 못했습니다.",
                negative_aspects="LLM 분석 실패로 상세 우려 사항을 추출하지 못했습니다.",
                final_comment="LLM 분석 실패로 인터뷰 품질 분석을 기본 값으로 대체했습니다.",
            )

        rec_raw = state.get("final_recommendation") or {}
        if rec_raw:
            rec = FinalRecommendation.model_validate(rec_raw)
        else:
            rec = FinalRecommendation(
                final_comment="LLM 오류로 최종 권고를 생성하지 못해 기본 권고를 사용합니다.",
                hiring_decision="보류",
                decision_reasons=["LLM 분석 실패로 정확한 판단이 어려움"],
            )

        headline = comp.headline.model_copy(
            update={
                "contradiction_score": iq.contradiction_score,
                "contradiction_reason": iq.contradiction_reason,
                "depth_score": iq.depth_score,
                "depth_reason": iq.depth_reason,
                "reliability_score": iq.reliability_score,
                "reliability_reason": iq.reliability_reason,
            }
        )

        talk_items = list(summary.talkSummary.items)
        if iq.positive_aspects and iq.positive_aspects.strip():
            talk_items.append(
                TalkSummaryItem(
                    topic="긍정 의견",
                    talkSummary=iq.positive_aspects.strip()
                )
            )
        if iq.negative_aspects and iq.negative_aspects.strip():
            talk_items.append(
                TalkSummaryItem(
                    topic="부정 의견",
                    talkSummary=iq.negative_aspects.strip()
                )
            )
        enhanced_talk_summary = TalkSummary(items=talk_items)

        # cid를 axes.key와 강제 동기화
        axes_key_map = {f"C{str(i+1).zfill(2)}": ax.key for i, ax in enumerate(axes)}
        axes_label_map = {ax.key: ax.label for ax in axes}

        # compCoverage cid 보정
        compCoverage_fixed = []
        for i, row in enumerate(cov.compCoverage):
            fixed_cid = axes_key_map.get(row.cid, row.cid)
            compCoverage_fixed.append(row.model_copy(update={"cid": fixed_cid}))

        # evidence cid 보정
        evidence_fixed = []
        for ev in cov.evidence:
            fixed_cid = axes_key_map.get(ev.cid, ev.cid) if ev.cid else None
            evidence_fixed.append(ev.model_copy(update={"cid": fixed_cid}))

        report = ReportOut(
            metadata=meta,
            axes=axes,
            scores=comp.scores,
            headline=headline,
            talkSummary=enhanced_talk_summary,
            convStats=summary.convStats,
            compCoverage=compCoverage_fixed,
            evidence=evidence_fixed,
            competency_comments=comp.competency_comments,
            interview_stats=summary.interview_stats,
            recommendation=rec,
        )
        state["report"] = report.model_dump(by_alias=True)
        state.setdefault("agent_logs", []).append(
            {"agent": "14. 리포트 조립", "message": "ReportOut 조립/검증 완료 (cid/이름 동기화)"}
        )
    except Exception as e:
        logger.error("리포트 조립 실패: %s", e, exc_info=True)
        state["error"] = f"리포트 조립 실패: {e}"
    return state


# ==================== 15. Output ====================

def output_node(state: ReportState) -> ReportState:
    if "report" not in state:
        state["error"] = state.get("error") or "리포트 데이터 없음"
    else:
        state.setdefault("agent_logs", []).append(
            {"agent": "15. 출력 어댑터", "message": "리포트 출력 준비 완료"}
        )
    return state


# ==================== LangGraph 워크플로우 구성 ====================

def build_report_graph() -> StateGraph:
    workflow = StateGraph(ReportState)

    # 노드 등록
    workflow.add_node("precheck", precheck_node)          # 0
    workflow.add_node("seg_prep", seg_prep_node)          # 1
    workflow.add_node("seg_summary", seg_summary_node)    # 2-A
    workflow.add_node("seg_emotion", seg_emotion_node)    # 2-B
    workflow.add_node("seg_risk", seg_risk_node)          # 2-C
    workflow.add_node("seg_merge", seg_merge_node)        # 3
    workflow.add_node("prompt_lens", prompt_lens_node)    # 4
    workflow.add_node("query_planner", query_planner_node)  # 5
    workflow.add_node("retriever", retriever_node)        # 6
    workflow.add_node("evidence_map", evidence_map_node)  # 7
    workflow.add_node("evidence_router", lambda s: s)     # dummy, conditional uses separate fn
    workflow.add_node("cross_check", cross_check_node)    # 9
    workflow.add_node("comp_score", comp_score_node)      # 10-A
    workflow.add_node("jd_cover", jd_cover_node)          # 10-B
    workflow.add_node("interview_quality", interview_quality_node)  # 10-C
    workflow.add_node("summary_stats", summary_stats_node)          # 10-D
    workflow.add_node("debate", debate_node)              # 11
    workflow.add_node("quality_router", lambda s: s)      # 12 dummy
    workflow.add_node("final_rec", final_rec_node)        # 13
    workflow.add_node("assemble", assemble_node)          # 14
    workflow.add_node("output", output_node)              # 15

    workflow.add_edge(START, "precheck")
    workflow.add_edge("precheck", "seg_prep")

    # 플로우 정의 (순차 + 조건 분기)
    workflow.add_edge("seg_prep", "seg_summary")
    workflow.add_edge("seg_prep", "seg_emotion")
    workflow.add_edge("seg_prep", "seg_risk")
    workflow.add_edge("seg_summary", "seg_merge")
    workflow.add_edge("seg_emotion", "seg_merge")
    workflow.add_edge("seg_risk", "seg_merge")

    workflow.add_edge("seg_merge", "prompt_lens")
    workflow.add_edge("prompt_lens", "query_planner")
    workflow.add_edge("query_planner", "retriever")
    workflow.add_edge("retriever", "evidence_map")

    workflow.add_conditional_edges(
        "evidence_map",
        evidence_router_node,
        {"retry": "retriever", "next": "cross_check"},
    )


    workflow.add_edge("cross_check", "comp_score")
    workflow.add_edge("cross_check", "jd_cover")
    workflow.add_edge("cross_check", "interview_quality")
    workflow.add_edge("cross_check", "summary_stats")

    workflow.add_edge("comp_score", "debate")
    workflow.add_edge("jd_cover", "debate")
    workflow.add_edge("interview_quality", "debate")
    workflow.add_edge("summary_stats", "debate")

    workflow.add_conditional_edges(
        "debate",
        quality_router_node,
        {
            "retry_retrieval": "retriever",
            "retry_analysis": "cross_check",
            "ok": "final_rec",
        },
    )

    workflow.add_edge("final_rec", "assemble")
    workflow.add_edge("assemble", "output")
    workflow.add_edge("output", END)

    return workflow.compile()


REPORT_GRAPH = build_report_graph()


# ==================== 메인 생성 함수 (스트리밍) ====================

async def create_report_async(
    session_id: str,
    candidate_name: str,
    axes_keys: List[str],
    user_prompt: Optional[str] = None,
    has_portfolio: bool = False,
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    LangGraph 기반 인터뷰 리포트 생성 (스트리밍)
    """
    initial_state: ReportState = {
        "session_id": session_id,
        "candidate_name": candidate_name,
        "axes_keys": axes_keys,
        "user_prompt": user_prompt or "",
        "meta": {"has_portfolio": has_portfolio},
        "agent_logs": [],
        "retrieval_retry_count": 0,
        "analysis_retry_count": 0,
    }

    # 로그는 state 안에만 쌓고, SSE로는 안 보낼 거면 카운트도 굳이 필요 없음
    # last_log_len = 0

    # LangGraph 스트림
    stream = REPORT_GRAPH.astream(initial_state)
    try:
        async for event in stream:
            for _node, state in event.items():
                if state.get("error"):
                    yield {"type": "error", "message": state["error"]}
                    return

                if state.get("report"):
                    yield {"type": "report", "data": state["report"]}
                    return
    finally:
        await stream.aclose()




def create_report(
    session_id: str,
    candidate_name: str,
    axes_keys: List[str],
    user_prompt: Optional[str] = None,
    has_portfolio: bool = False,
) -> Dict[str, Any]:
    """
    동기 버전 래퍼
    """
    import asyncio as _asyncio

    async def _run():
        final_report: Optional[Dict[str, Any]] = None
        async for event in create_report_async(
            session_id=session_id,
            candidate_name=candidate_name,
            axes_keys=axes_keys,
            user_prompt=user_prompt,
            has_portfolio=has_portfolio,
        ):
            if event["type"] == "error":
                return _error("AGENT_ERROR", event["message"])
            if event["type"] == "report":
                final_report = event["data"]
        return final_report or _error("NO_REPORT", "리포트 생성 실패")

    try:
        loop = _asyncio.get_event_loop()
    except RuntimeError:
        loop = _asyncio.new_event_loop()
        _asyncio.set_event_loop(loop)

    return loop.run_until_complete(_run())
