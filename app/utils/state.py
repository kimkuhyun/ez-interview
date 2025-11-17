from pydantic import BaseModel
from typing import List, Optional, Any, Dict
import uuid

class InterviewState(BaseModel):
    """
    면접 프로세스 전체의 상태를 관리하는 객체
    """
    session_id: Optional[str] = None  # 전체 서비스 세션 UUID (면접 단위)
    candidate_name: Optional[str] = None  # 면접자 이름 (필수)
    resume_id: Optional[str] = None   # Resume 문서 UUID (doc_id)
    jd_id: Optional[str] = None       # JD 문서 UUID (doc_id)
    portfolio_id: Optional[str] = None  # Portfolio 문서 UUID (doc_id, 선택)
    resume_len: Optional[int] = None
    jd_len: Optional[int] = None
    portfolio_len: Optional[int] = None
    resume_text: Optional[str] = None  # 이력서 전체 텍스트 (질문 생성용)
    jd_text: Optional[str] = None      # JD 전체 텍스트 (질문 생성용)
    portfolio_text: Optional[str] = None  # 포트폴리오 전체 텍스트 (질문 생성용, 선택)
    
    # 🆕 구조화된 문서 데이터
    structured_resume: Optional[Dict[str, Any]] = None  # StructuredResume JSON
    structured_jd: Optional[Dict[str, Any]] = None      # StructuredJD JSON
    
    questions: Optional[List[str]] = None
    metrics: Optional[List[str]] = None
    interview_logs: Optional[List[Any]] = None  # 면접 대화 로그
    
    def __init__(self, **data):
        super().__init__(**data)
        # session_id가 없으면 자동 생성
        if not self.session_id:
            self.session_id = str(uuid.uuid4())

