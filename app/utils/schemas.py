"""
공통으로 사용하는 Pydantic 모델
"""

from pydantic import BaseModel
from typing import List, Optional, Dict, Any


# ==================== 문서 구조화 스키마 ==================== #

class ResumeBasicInfo(BaseModel):
    """이력서 기본 정보"""
    name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    github: Optional[str] = None
    linkedin: Optional[str] = None
    blog: Optional[str] = None
    summary: Optional[str] = None  # 한 줄 소개


class ResumeEducation(BaseModel):
    """학력 정보"""
    school: str
    major: Optional[str] = None
    degree: Optional[str] = None
    period: Optional[str] = None
    gpa: Optional[str] = None
    activities: Optional[List[str]] = None


class ResumeExperience(BaseModel):
    """경력 정보"""
    company: str
    role: str
    period: str
    description: Optional[str] = None
    achievements: List[str] = []
    tech_stack: List[str] = []


class ResumeProject(BaseModel):
    """프로젝트 정보"""
    name: str
    period: Optional[str] = None
    description: str
    role: Optional[str] = None
    achievements: List[str] = []
    tech_stack: List[str] = []
    url: Optional[str] = None


class ResumeSkills(BaseModel):
    """기술 스택"""
    technical: List[str] = []  # 기술 스킬
    languages: List[str] = []  # 프로그래밍 언어
    frameworks: List[str] = []  # 프레임워크
    tools: List[str] = []  # 도구
    soft_skills: List[str] = []  # 소프트 스킬


class StructuredResume(BaseModel):
    """구조화된 이력서"""
    basic_info: ResumeBasicInfo
    education: List[ResumeEducation] = []
    experience: List[ResumeExperience] = []
    projects: List[ResumeProject] = []
    skills: ResumeSkills = ResumeSkills()
    certifications: List[str] = []
    awards: List[str] = []
    languages: List[str] = []  # 외국어 능력
    cover_letter: Optional[str] = None  # 자기소개서 (전체 텍스트)
    additional_info: Optional[str] = None  # 🆕 기타 정보 (스키마에 없는 내용)
    raw_text: str  # 원본 텍스트


class JDRequirements(BaseModel):
    """JD 요구사항"""
    required_skills: List[str] = []
    preferred_skills: List[str] = []
    required_experience: Optional[str] = None
    preferred_experience: Optional[str] = None
    education: Optional[str] = None
    certifications: List[str] = []


class StructuredJD(BaseModel):
    """구조화된 JD"""
    company: Optional[str] = None
    position: str
    department: Optional[str] = None
    employment_type: Optional[str] = None  # 정규직, 계약직 등
    location: Optional[str] = None
    requirements: JDRequirements = JDRequirements()
    responsibilities: List[str] = []
    tech_stack: List[str] = []
    preferred_qualifications: List[str] = []
    benefits: List[str] = []
    additional_info: Optional[str] = None  # 🆕 기타 정보 (스키마에 없는 내용)
    raw_text: str  # 원본 텍스트


# ==================== 기존 스키마 (호환성 유지) ==================== #

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


class InterviewQuestionsAndMetrics(BaseModel):
    """
    QuestionAgent 출력용 데이터 구조
    """
    questions: List[str]
    metrics: List[str]  # 예: ["문제해결력", "논리적 사고", "협업능력", ...]