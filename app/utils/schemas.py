"""
공통으로 사용하는 Pydantic 모델
"""

from pydantic import BaseModel
from typing import List, Optional, Dict, Any


class ParsedDoc(BaseModel):
    doc_type: str              # "resume" | "cover_letter" | "jd" | "unknown"
    raw_text: str
    structured: Dict[str, Any] = {}


class JDAnalysis(BaseModel):
    jd_text: str
    suggested_criteria: List[str]  # 평가항목 10개
    coverage_summary: str          # JD 커버리지 설명
    skill_levels: Dict[str, str]   # ex: {"Python": "상", "DB": "중"}


class InterviewQuestions(BaseModel):
    questions: List[str]


class InterviewPlan(BaseModel):
    parsed_docs: List[ParsedDoc]
    jd_analysis: Optional[JDAnalysis] = None
    interview_questions: Optional[InterviewQuestions] = None
