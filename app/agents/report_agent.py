"""
리포트 생성 에이전트

필요한 입력:
- resume_text: str (이력서 텍스트 - 또는 Vector DB에서 자동 검색)
- jd_text: str (공고 텍스트 - 또는 Vector DB에서 자동 검색)
- log_text: str (인터뷰 로그 텍스트)
- axes_keys: List[str] (핵심역량 키 리스트)
  예: ["problem_solving", "communication", "self_driven_initiative", "collaboration", "professional_expertise"]

반환:
- Dict[str, Any]: 리포트 결과
  - status: "ok" | "failed"
  - report_id: str (성공 시)
  - 기타 리포트 데이터

VectorDB 통합 완료:
- app/utils/retriever.py의 search_similar_chunks 함수 사용
- Vector DB에서 이력서, 공고 문서 검색하여 컨텍스트 구성

사용 예시:
```python
from app.agents.report_agent import create_report

result = create_report(
    resume_text="",  # Vector DB에서 자동 검색됨
    jd_text="",      # Vector DB에서 자동 검색됨
    log_text="...",
    axes_keys=["problem_solving", "communication", "self_driven_initiative", 
               "collaboration", "professional_expertise"]
)

if result["status"] == "ok":
    print("리포트 생성 성공:", result["report_id"])
else:
    print("리포트 생성 실패:", result["message"])
```
"""
from __future__ import annotations
from typing import List, Dict, Optional, Any
from uuid import uuid4
from datetime import datetime
import os
import re
from collections import defaultdict

from pydantic import BaseModel, Field, ValidationError, StringConstraints
from typing_extensions import Annotated
from annotated_types import Ge, Le

from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI

# Vector DB 리트리버 임포트
from app.utils.rag_retriever import search_similar_chunks
from app.utils.interview_store import retrieve_interview_context

# ==================== 환경 설정 ====================
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")

def _llm() -> ChatOpenAI:
    return ChatOpenAI(model_name=OPENAI_MODEL, temperature=0.1, timeout=90)

# ==================== 스키마 정의 ====================
KeyStr = Annotated[str, StringConstraints(strip_whitespace=True)]
NonEmpty = Annotated[str, StringConstraints(min_length=1)]
E_ID = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^E\d{2,3}$")]
COMP_ID = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^C\d{2,3}$")]
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
    jid: COMP_ID
    요구사항: NonEmpty
    기대치: NonEmpty
    충족도: NonEmpty
    근거: List[E_ID]

class EvidenceRow(BaseModel):
    eid: E_ID
    출처: NonEmpty
    내용: NonEmpty
    jid: COMP_ID

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

# ==================== Validation 스키마 ====================
class LightIssue(BaseModel):
    code: str
    where: Optional[str] = None
    detail: str
    severity: str = "error"  # "error" | "warning"

class LightValidationResult(BaseModel):
    ok: bool
    issues: List[LightIssue] = Field(default_factory=list)
    stats: Dict[str, int] = Field(default_factory=dict)

# Heavy Validation 스키마
class QualityAssessment(BaseModel):
    """품질 평가"""
    is_good: bool
    score: float = Field(ge=0.0, le=1.0)
    issues: List[str] = Field(default_factory=list)
    suggestions: List[str] = Field(default_factory=list)

class HeavyValidationOut(BaseModel):
    """LLM 기반 품질 검증 출력"""
    headline_quality: QualityAssessment
    summary_quality: QualityAssessment
    opinion_quality: QualityAssessment
    axis_quality: QualityAssessment
    jd_quality: QualityAssessment
    overall_grade: str  # A+, A, B+, B, C, D, F
    confidence: float = Field(ge=0.0, le=1.0)
    recommendations: List[str] = Field(default_factory=list)

class RepairSuggestion(BaseModel):
    """수정 제안"""
    field: str
    action: str  # "update", "add", "remove"
    value: Any
    reason: str

# ==================== 에러 처리 ====================
class ReportError(Exception):
    def __init__(self, code: str, message: str, http: int = 400, details: Dict[str, Any] = None):
        super().__init__(message)
        self.code, self.message, self.http = code, message, http
        self.details = details or {}

def _error(code: str, message: str, http: int = 400, details: Dict = None) -> Dict:
    return {
        "status": "failed",
        "code": code,
        "message": message,
        "http": http,
        "trace_id": uuid4().hex,
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "details": details or {}
    }

# ==================== VectorDB 연동 ====================
def _extract_qa_pairs(log_text: str) -> List[Dict]:
    """
    인터뷰 로그에서 질문-답변 쌍 추출
    
    Args:
        log_text: 인터뷰 로그 텍스트
    
    Returns:
        질문-답변 쌍 리스트 [{'q_num': '1', 'question': '...', 'answer': '...'}]
    """
    qa_pairs = []
    lines = log_text.split('\n')
    current_q = None
    current_a = []
    q_num = 0
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
        
        # 질문 패턴 감지 (Q:, 면접관:, Question: 등)
        if any(line.startswith(prefix) for prefix in ['Q:', 'q:', '면접관:', 'Question:', 'Q.', '[면접관]']):
            # 이전 Q&A 저장
            if current_q and current_a:
                q_num += 1
                qa_pairs.append({
                    'q_num': str(q_num),
                    'question': current_q,
                    'answer': ' '.join(current_a)
                })
                current_a = []
            
            # 새 질문 시작
            for prefix in ['Q:', 'q:', '면접관:', 'Question:', 'Q.', '[면접관]']:
                if line.startswith(prefix):
                    current_q = line[len(prefix):].strip()
                    break
        
        # 답변 패턴 감지 (A:, 지원자:, Answer: 등)
        elif any(line.startswith(prefix) for prefix in ['A:', 'a:', '지원자:', 'Answer:', 'A.', '[지원자]']):
            for prefix in ['A:', 'a:', '지원자:', 'Answer:', 'A.', '[지원자]']:
                if line.startswith(prefix):
                    answer_text = line[len(prefix):].strip()
                    current_a.append(answer_text)
                    break
        
        # 답변 계속
        elif current_q and current_a:
            current_a.append(line)
    
    # 마지막 Q&A 저장
    if current_q and current_a:
        q_num += 1
        qa_pairs.append({
            'q_num': str(q_num),
            'question': current_q,
            'answer': ' '.join(current_a)
        })
    
    return qa_pairs


def _build_context_from_vectordb(session_id: str, axes_keys: List[str], 
                                 resume_text: str = "", jd_text: str = "", log_text: str = "") -> tuple:
    """
    VectorDB와 interview_store를 활용한 컨텍스트 구성
    
    - 이력서/공고: rag_retriever.search_similar_chunks (session_id 기반)
    - 인터뷰 로그: interview_store.retrieve_interview_context (session_id 기반)
    - 핵심역량: axes_keys 파라미터로 전달받아 처리
    
    Args:
        session_id: 세션 ID (Vector DB 검색 및 interview_store 조회용)
        axes_keys: 핵심역량 키 리스트 (state.metrics에서 전달)
        resume_text: 폴백용 이력서 텍스트
        jd_text: 폴백용 공고 텍스트
        log_text: 폴백용 인터뷰 로그
    
    Returns:
        (jd_ctx, resume_ctx, log_ctx, comp_id_list, eid_list, qa_pairs)
    """
    print(f"\n{'='*80}")
    print(f"🔍 [Context Builder] 시작")
    print(f"   Session ID: {session_id}")
    print(f"   핵심역량: {axes_keys}")
    print(f"{'='*80}\n")
    
    # 1. Vector DB에서 이력서 검색 (session_id 기반)
    resume_query = "경력 프로젝트 기술스택 성과 교육 자격증"
    try:
        resume_results = search_similar_chunks(resume_query, session_id=session_id, top_k=10)
        resume_chunks = [result[0] for result in resume_results if result[1] == 'resume']
        resume_ctx = "\n\n".join(resume_chunks) if resume_chunks else resume_text
        print(f"✅ Vector DB에서 이력서 {len(resume_chunks)}개 chunk 검색")
    except Exception as e:
        print(f"⚠️ Vector DB 이력서 검색 실패: {e}, 폴백 텍스트 사용")
        resume_ctx = resume_text
        resume_results = []
    
    # 2. Vector DB에서 JD 검색 (session_id 기반)
    jd_query = "요구사항 자격요건 우대사항 담당업무 기술스택"
    try:
        jd_results = search_similar_chunks(jd_query, session_id=session_id, top_k=10)
        jd_chunks = [result[0] for result in jd_results if result[1] == 'jd']
        jd_ctx = "\n\n".join(jd_chunks) if jd_chunks else jd_text
        print(f"✅ Vector DB에서 JD {len(jd_chunks)}개 chunk 검색")
    except Exception as e:
        print(f"⚠️ Vector DB JD 검색 실패: {e}, 폴백 텍스트 사용")
        jd_ctx = jd_text
        jd_results = []
    
    # 3. interview_store에서 인터뷰 로그 조회 (session_id 기반)
    try:
        log_ctx = retrieve_interview_context(session_id)
        if not log_ctx:
            print(f"⚠️ interview_store에서 로그 없음, 폴백 텍스트 사용")
            log_ctx = log_text
        else:
            print(f"✅ interview_store에서 로그 조회 완료: {len(log_ctx)} 글자")
    except Exception as e:
        print(f"⚠️ interview_store 조회 실패: {e}, 폴백 텍스트 사용")
        log_ctx = log_text
    
    # 4. 질문-답변 쌍 추출
    qa_pairs = _extract_qa_pairs(log_ctx)
    print(f"✅ 질문-답변 {len(qa_pairs)}개 추출")
    
    # 5. 증거 ID 생성 (Vector DB chunk + 인터뷰 QA 수)
    total_evidence = len(resume_results) + len(jd_results) + len(qa_pairs)
    eid_list = [f"E{i:02d}" for i in range(1, max(total_evidence + 1, 10))]  # 최소 10개
    print(f"✅ 증거 ID {len(eid_list)}개 생성 (이력서:{len(resume_results)}, JD:{len(jd_results)}, QA:{len(qa_pairs)})")
    
    # 6. 역량 ID 생성 (axes_keys 기반)
    comp_id_list = [f"C{i+1:02d}" for i in range(len(axes_keys))]
    print(f"✅ 핵심역량 {len(comp_id_list)}개: {axes_keys}")

    preview_resume = resume_ctx[:200].replace("\n", " ")
    print(f"🔍 resume_ctx preview: {preview_resume}...")

    preview_jd = jd_ctx[:200].replace("\n", " ")
    print(f"🔍 jd_ctx preview: {preview_jd}...")
    
    preview_log = log_ctx[:200].replace("\n", " ")
    print(f"🔍 log_ctx preview: {preview_log}...")
    
    print(f"\n{'='*80}")
    print(f"✅ [Context Builder] 완료")
    print(f"{'='*80}\n")
    
    return jd_ctx, resume_ctx, log_ctx, comp_id_list, eid_list, qa_pairs
    
    # 2. Vector DB에서 JD 검색 (모든 JD chunk 가져오기)
    jd_query = "요구사항 자격요건 우대사항 담당업무 기술스택"
    try:
        jd_results = search_similar_chunks(jd_query, top_k=10)
        jd_chunks = [result[0] for result in jd_results if result[1] == 'jd']
        jd_ctx = "\n\n".join(jd_chunks) if jd_chunks else jd_text
        print(f"✅ Vector DB에서 JD {len(jd_chunks)}개 chunk 검색")
    except Exception as e:
        print(f"⚠️ Vector DB JD 검색 실패: {e}")
        jd_ctx = jd_text
        jd_results = []
    
    # 3. 인터뷰 로그 처리 (로컬에서 직접 처리)
    log_ctx = log_text
    print(f"✅ 인터뷰 로그 길이: {len(log_text)} 글자")
    
    # 4. 질문-답변 쌍 추출
    qa_pairs = _extract_qa_pairs(log_text)
    print(f"✅ 질문-답변 {len(qa_pairs)}개 추출")
    
    # 5. 증거 ID 생성 (Vector DB chunk + 인터뷰 로그 기반)
    # 이력서와 JD에서 가져온 chunk 수 + 인터뷰 QA 수
    total_evidence = len(resume_results) + len(jd_results) + len(qa_pairs)
    eid_list = [f"E{i:02d}" for i in range(1, max(total_evidence + 1, 10))]  # 최소 10개
    print(f"✅ 증거 ID {len(eid_list)}개 생성 (이력서:{len(resume_results)}, JD:{len(jd_results)}, QA:{len(qa_pairs)})")
    
    # 6. 역량 ID 생성 (axes_keys 기반)
    comp_id_list = [f"C{i+1:02d}" for i in range(len(axes_keys))]
    print(f"✅ 핵심역량 {len(comp_id_list)}개: {axes_keys}")

    preview_resume = resume_ctx.replace("\n", " ")
    print(f"🔍 resume_ctx preview : {preview_resume}")

    preview_jd = jd_ctx.replace("\n", " ")
    print(f"🔍 jd_ctx preview : {preview_jd}")
    return jd_ctx, resume_ctx, log_ctx, comp_id_list, eid_list, qa_pairs

# ==================== 체인 1: 핵심역량 평가 ====================
class CompetencyEvalOut(BaseModel):
    scores: List[ScoreItem]
    weights: List[WeightItem]
    headline: Headline
    reasoning: List[str] = Field(default_factory=list)

def _competency_prompt() -> ChatPromptTemplate:
    return ChatPromptTemplate.from_messages([
        ("system",
         "당신은 엄격한 면접 평가 전문가입니다. 이력서와 인터뷰 로그를 바탕으로 후보자의 핵심역량을 **보수적이고 엄격하게** 평가하세요.\n\n"
         "평가 원칙:\n"
         "1. 실제 증거에 기반한 평가 (추측 금지)\n"
         "2. 구체적인 성과와 수치가 있어야 높은 점수\n"
         "3. 막연한 표현이나 일반론은 낮은 점수\n"
         "4. 증거가 약하면 과감히 낮은 점수 부여\n"
         "5. 가중치 합계는 정확히 100\n"
         "6. **기술 스택 나열만으로는 높은 점수 불가** (어떻게 사용했는지 구체적 설명 필수)\n"
         "7. **각 역량마다 명확한 차별화** (최소 15점 이상 점수 편차 필수)\n\n"
         "점수 기준 (엄격 적용):\n"
         "- 0-30: 증거 없음 또는 매우 부족 (단순 기술 나열만 있는 경우 포함)\n"
         "- 31-50: 기본적 수준, 구체성 부족 (HOW/WHY 없이 WHAT만 언급)\n"
         "- 51-65: 보통 수준, 일부 구체적 사례 있음 (문제-해결 과정 일부 설명)\n"
         "- 66-75: 양호, 명확한 성과 입증 (구체적 문제 정의 + 해결 과정 + 결과)\n"
         "- 76-85: 우수, 복수의 구체적 성과와 수치 (정량적 성과 + 깊이 있는 기술 설명)\n"
         "- 86-95: 탁월, 예외적인 성과와 깊이 (복수의 정량적 성과 + 기술적 통찰)\n"
         "- 96-100: 최고 수준 (거의 부여하지 않음)\n\n"
         "⚠️ **중요 경고 - 반드시 지킬 것:**\n"
         "- **단순 기술 스택 나열 (Spring, Docker, AWS 등)만으로는 절대 60점 이상 불가**\n"
         "- **'사용해봤다', '경험 있다'는 구체적 성과 없으면 40점 이하**\n"
         "- **문제 상황-해결 과정-결과가 명확하지 않으면 50점 이하**\n"
         "- **정량적 지표(%, 시간, 건수 등)가 없으면 65점 이하**\n"
         "- **모든 역량 점수가 10점 내외로 비슷하면 안됨 (최소 20점 편차 권장)**\n"
         "- **증거가 강한 1-2개 역량에 집중, 나머지는 과감히 낮게**\n\n"
         "차별화 전략:\n"
         "- 가장 증거가 명확한 역량 1개: 70-80점대\n"
         "- 증거가 있는 역량 1-2개: 55-65점대\n"
         "- 증거가 약한 역량 2-3개: 30-50점대\n"
         "- 증거가 전혀 없는 역량: 20-35점대\n\n"
         "**필수 출력 필드:**\n"
         "- scores: List[{{key: str, value: int}}] - 각 역량 키에 대한 점수 배열 (예: [{{key: 'problem_solving', value: 65}}, ...])\n"
         "- weights: List[{{key: str, value: int}}] - 각 역량 키에 대한 가중치 배열, 합계 반드시 100\n"
         "- headline: {{summary: str, tag: str}} - 1줄 요약 + 키워드 3-5개 (쉼표 구분)\n"
         "- reasoning: List[str] - 각 역량에 대한 평가 근거 (5개, 각 역량당 1개씩, 왜 그 점수인지 명확히)"),
        ("human",
         "평가 축: {axes_str}\n\n"
         "[이력서]\n{resume}\n\n"
         "[인터뷰로그]\n{log}\n\n"
         "⚠️ **평가 전 체크리스트:**\n"
         "1. 각 역량마다 구체적인 증거(문제-해결-결과)가 있는가?\n"
         "2. 기술 스택만 나열한 것은 아닌가?\n"
         "3. 정량적 지표가 있는가?\n"
         "4. 5개 역량의 점수가 모두 비슷하지 않은가? (최소 20점 편차)\n\n"
         "위 체크리스트를 확인한 후, scores, weights, headline, reasoning을 모두 반환하세요.\n"
         "**증거가 약한 역량은 과감히 30-40점대로 평가하세요.**")
    ])

def evaluate_competency(resume: str, log: str, axes_keys: List[str]) -> CompetencyEvalOut:
    chain = _competency_prompt() | _llm().with_structured_output(CompetencyEvalOut, method="function_calling")
    
    result = chain.invoke({
        "axes_str": ", ".join(axes_keys),
        "resume": resume,
        "log": log
    })
    
    # 가중치 정규화
    weights_dict = {w.key: w.value for w in result.weights}
    total_w = sum(weights_dict.values())
    if total_w != 100:
        factor = 100 / total_w
        weights_dict = {k: int(round(v * factor)) for k, v in weights_dict.items()}
        diff = 100 - sum(weights_dict.values())
        if diff != 0:
            max_key = max(weights_dict, key=weights_dict.get)
            weights_dict[max_key] += diff
        result.weights = [WeightItem(key=k, value=v) for k, v in weights_dict.items()]
    
    return result

# ==================== 체인 2: 핵심역량-증거 매핑 ====================
class CompetencyEvidenceOut(BaseModel):
    competencyCoverage: List[JDCoverRow]
    evidence: List[EvidenceRow]

def _competency_evidence_prompt() -> ChatPromptTemplate:
    return ChatPromptTemplate.from_messages([
        ("system",
         "당신은 핵심역량과 증거를 매핑하는 전문가입니다.\n\n"
         "작업:\n"
         "1. 각 핵심역량에 대해 관련 증거(Exx)를 이력서와 인터뷰 로그에서 찾으세요\n"
         "2. 충족도는 증거의 강도에 따라 평가: 적합/보통/부족\n"
         "3. **모든 핵심역량은 최소 1개 이상의 EID 필수**\n"
         "4. 각 증거는 1-2문장으로 요약 (20-30단어)\n\n"
         "**필수 출력:**\n"
         "- competencyCoverage: 핵심역량별 평가 (jid=역량ID, 근거[EID 배열])\n"
         "- evidence: 증거 배열 (eid, 출처, 내용, jid=역량ID)"),
        ("human",
         "[핵심역량]\n{competencies}\n\n"
         "[이력서]\n{resume}\n\n"
         "[인터뷰로그]\n{log}\n\n"
         "역량 ID: {competency_details}\n"
         "사용 가능 EID: {eid_list}\n\n"
         "competencyCoverage와 evidence를 반환하세요.\n"
         "**모든 내용을 한국어로 작성하세요.**")
    ])

def map_competency_evidence(axes_keys: List[str], resume: str, log: str, 
                            eid_list: List[str]) -> CompetencyEvidenceOut:
    competency_names = {
        "problem_solving": "문제해결능력",
        "communication": "커뮤니케이션",
        "self_driven_initiative": "자기주도성",
        "collaboration": "협업능력",
        "professional_expertise": "전문성"
    }
    
    competency_details = "\n".join([
        f"C{i+1:02d} ({axes_keys[i]}): {competency_names.get(axes_keys[i], axes_keys[i])}"
        for i in range(len(axes_keys))
    ])
    
    competencies_text = "\n".join([
        f"{competency_names.get(key, key)}: {key.replace('_', ' ')}"
        for key in axes_keys
    ])
    
    chain = _competency_evidence_prompt() | _llm().with_structured_output(CompetencyEvidenceOut, method="function_calling")
    
    result = chain.invoke({
        "competencies": competencies_text,
        "resume": resume,
        "log": log,
        "competency_details": competency_details,
        "eid_list": ", ".join(eid_list)
    })
    
    return result

# ==================== 체인 3: 인터뷰 요약 ====================
class InterviewSummaryOut(BaseModel):
    overall: str
    qa_summaries: List[Dict[str, str]] = Field(default_factory=list)
    positive: str
    negative: str
    stats: Dict[str, str] = Field(default_factory=dict)

def _interview_summary_prompt() -> ChatPromptTemplate:
    return ChatPromptTemplate.from_messages([
        ("system",
         "당신은 인터뷰 분석 전문가입니다. 인터뷰 로그를 분석하여 요약하세요.\n\n"
         "작업:\n"
         "1. 전체 인터뷰 흐름을 2-3문장으로 요약 (overall 필드)\n"
         "2. 각 질문에 대한 답변을 1~3줄로 요약 (qa_summaries 필드 - 배열)\n"
         "   - 질문은 **5~10단어 이내**로 핵심만 압축 (의문형 유지)\n"
         "   - 예: \"간단히 자기소개 부탁드립니다.\" → \"자기소개?\"\n"
         "   - 예: \"이력서에서 언급한 N+1 개선을 어떻게 했는지?\" → \"N+1 개선 방법?\"\n"
         "   - 예: \"마지막으로 하고 싶은 말이 있나요?\" → \"마지막 한마디?\"\n"
         "   - 답변 요약은 **1~3줄 이내**로 작성 (꼬리물기 질문이 있으면 3줄까지 가능)\n"
         "3. 긍정 의견: 강점과 우수 역량 (200-300자, positive 필드)\n"
         "4. 부정 의견: 약점과 개선점 (200-300자, negative 필드)\n"
         "5. 통계: 총 질문 수, 평균 답변 길이 등 (stats 필드 - Dict)\n\n"
         "**필수 출력 필드:**\n"
         "- overall: string (전체 요약)\n"
         "- qa_summaries: List[Dict] - **반드시 필수** (각 항목: {{q_num: string, question_short: string, answer_summary: string}})\n"
         "  예시: [{{'q_num': '1', 'question_short': '자기소개?', 'answer_summary': 'Spring Boot와 Node.js 기반 백엔드 엔지니어로 REST API 개발, Docker/AWS 배포 경험 보유'}}]\n"
         "- positive: string (긍정 의견)\n"
         "- negative: string (부정 의견)\n"
         "- stats: Dict[string, string] (통계 정보)\n\n"
         "**중요:** \n"
         "- qa_summaries는 빈 배열이 아닌, 모든 질문에 대한 답변 요약을 담은 배열이어야 합니다.\n"
         "- question_short는 5~10단어 이내로 매우 짧게 압축하세요.\n"
         "- answer_summary는 1~3줄로 제한하되, 후속 질문이 있으면 3줄까지 사용하세요."),
        ("human",
         "[인터뷰 로그]\n{log}\n\n"
         "[질문-답변 쌍]\n{qa_pairs}\n\n"
         "위 형식에 맞춰 overall, qa_summaries(필수-배열), positive, negative, stats를 모두 반환하세요.\n"
         "qa_summaries는 반드시 포함되어야 하며, 제공된 모든 질문-답변 쌍에 대한 요약을 포함해야 합니다.\n"
         "question_short는 5~10단어로 매우 짧게, answer_summary는 1~3줄로 작성하세요.")
    ])

def summarize_interview(log: str, qa_pairs: List[Dict]) -> InterviewSummaryOut:
    chain = _interview_summary_prompt() | _llm().with_structured_output(InterviewSummaryOut, method="function_calling")
    
    qa_str = "\n\n".join([
        f"Q{qa['q_num']}: {qa['question'][:100]}...\n"
        f"A{qa['q_num']}: {qa['answer'][:200]}..."
        for qa in qa_pairs
    ])
    
    result = chain.invoke({
        "log": log,
        "qa_pairs": qa_str
    })
    
    return result

# ==================== Light Validation ====================
def validate_light(report: Dict, comp_id_list: List[str], eid_list: List[str], 
                   qa_pairs: List[Dict]) -> LightValidationResult:
    """경량 검증 - 구조적 무결성"""
    issues = []
    
    # 1. 역량 ID 연속성
    expected_ids = [f"C{i+1:02d}" for i in range(len(comp_id_list))]
    if comp_id_list != expected_ids:
        issues.append(LightIssue(
            code="COMP_ID_NOT_SEQUENTIAL",
            detail=f"역량 ID가 연속적이지 않음: {comp_id_list}"
        ))
    
    # 2. EID 연속성
    expected_eids = [f"E{i:02d}" for i in range(1, len(eid_list) + 1)]
    if eid_list != expected_eids:
        issues.append(LightIssue(
            code="EID_NOT_SEQUENTIAL",
            detail=f"EID가 연속적이지 않음: {eid_list}"
        ))
    
    # 3. 빈 근거 배열 체크
    for row in report.get("jdCoverage", []):
        if not row.get("근거") or len(row.get("근거", [])) == 0:
            issues.append(LightIssue(
                code="EMPTY_EVIDENCE",
                where=row["jid"],
                detail=f"{row['jid']}에 근거가 없습니다",
                severity="warning"
            ))
    
    # 4. 증거 참조 유효성
    evidence_in_map = {ev["eid"] for ev in report.get("evidence", [])}
    referenced_eids = set()
    for row in report.get("jdCoverage", []):
        referenced_eids.update(row.get("근거", []))
    
    invalid_refs = referenced_eids - evidence_in_map
    if invalid_refs:
        issues.append(LightIssue(
            code="INVALID_EVIDENCE_REF",
            detail=f"존재하지 않는 증거 참조: {sorted(invalid_refs)}"
        ))
    
    # 5. 가중치 합계
    weight_sum = sum(w["value"] for w in report.get("weights", []))
    if abs(weight_sum - 100) > 1:
        issues.append(LightIssue(
            code="WEIGHT_SUM",
            detail=f"가중치 합계가 100이 아님: {weight_sum}"
        ))
    
    stats = {
        "total_competencies": len(comp_id_list),
        "total_evidences": len(eid_list),
        "total_qa": len(qa_pairs)
    }
    
    return LightValidationResult(
        ok=(len([i for i in issues if i.severity == "error"]) == 0),
        issues=issues,
        stats=stats
    )

# ==================== Heavy Validation (LLM 기반) ====================
def _heavy_validation_prompt() -> ChatPromptTemplate:
    return ChatPromptTemplate.from_messages([
        ("system",
         "당신은 채용 리포트 품질 검증 전문가입니다. 생성된 리포트의 품질을 엄격하게 평가하세요.\n\n"
         "평가 항목:\n"
         "1. **헤드라인**: 1줄 요약이 핵심을 잘 담았는가? 키워드가 적절한가?\n"
         "2. **요약**: 인터뷰 전체 흐름과 각 질문 답변이 빠짐없이 요약되었는가?\n"
         "3. **의견**: 긍정/부정 의견이 구체적이고 균형잡혔는가? 근거가 명확한가?\n"
         "4. **역량 평가**: 점수가 증거에 기반하였는가? 과대/과소 평가는 없는가?\n"
         "5. **JD 충족도**: 각 JD 요구사항에 대한 평가가 정확한가? 증거 매핑이 적절한가?\n\n"
         "품질 기준:\n"
         "- is_good: 해당 항목이 기준을 충족하는가 (True/False)\n"
         "- score: 품질 점수 (0.0~1.0)\n"
         "- issues: 발견된 문제점들 (구체적으로)\n"
         "- suggestions: 개선 제안들\n\n"
         "등급 기준:\n"
         "- A+: 모든 항목 excellent (0.9+)\n"
         "- A: 대부분 good (0.8+)\n"
         "- B+/B: 보통 수준 (0.7+, 0.6+)\n"
         "- C: 개선 필요 (0.5+)\n"
         "- D/F: 심각한 문제 (0.5 미만)"),
        ("human",
         "[원본 데이터]\n"
         "JD: {jd_text}\n"
         "이력서: {resume_text}\n"
         "인터뷰 로그: {log_text}\n\n"
         "[생성된 리포트]\n{report_json}\n\n"
         "위 리포트의 품질을 엄격하게 평가하세요.")
    ])

def validate_heavy(report: Dict, jd_text: str, resume_text: str, 
                   log_text: str) -> HeavyValidationOut:
    """LLM 기반 품질 검증"""
    import json
    chain = _heavy_validation_prompt() | _llm().with_structured_output(HeavyValidationOut, method="function_calling")
    
    # 텍스트 길이 제한 (컨텍스트 윈도우 고려)
    max_len = 3000
    jd_short = jd_text[:max_len] + "..." if len(jd_text) > max_len else jd_text
    resume_short = resume_text[:max_len] + "..." if len(resume_text) > max_len else resume_text
    log_short = log_text[:max_len] + "..." if len(log_text) > max_len else log_text
    
    result = chain.invoke({
        "jd_text": jd_short,
        "resume_text": resume_short,
        "log_text": log_short,
        "report_json": json.dumps(report, ensure_ascii=False, indent=2)
    })
    
    return result

# ==================== 자동 수정 ====================
def auto_repair(report: Dict, validation: HeavyValidationOut) -> tuple:
    """
    검증 결과를 바탕으로 자동 수정 시도
    Returns: (수정된 리포트, 적용된 수정 목록)
    """
    repairs_applied = []
    
    # 1. 헤드라인 품질 개선
    if not validation.headline_quality.is_good:
        if validation.headline_quality.suggestions:
            repairs_applied.append("헤드라인 품질 경고: " + validation.headline_quality.issues[0])
    
    # 2. 점수 보정 (과대평가 방지)
    if not validation.axis_quality.is_good:
        for issue in validation.axis_quality.issues:
            if "과대평가" in issue or "overestimate" in issue.lower():
                # 모든 점수를 10% 하향 조정
                for score_item in report["scores"]:
                    old_val = score_item["value"]
                    score_item["value"] = max(0, int(old_val * 0.9))
                    repairs_applied.append(f"점수 하향 조정 (과대평가 방지): {old_val} -> {score_item['value']}")
    
    # 3. JD 충족도 보정
    if not validation.jd_quality.is_good:
        for row in report.get("jdCoverage", []):
            # 근거가 없거나 약한 경우 충족도 하향
            if len(row.get("근거", [])) == 0:
                row["충족도"] = "부족"
                repairs_applied.append(f"{row['jid']}: 근거 부족으로 충족도 '부족'으로 변경")
    
    return report, repairs_applied

# ==================== 메인 생성 함수 ====================
def create_report_with_validation(session_id: str, axes_keys: List[str],
                                  resume_text: str = "", jd_text: str = "", log_text: str = "",
                                  enable_heavy: bool = False,
                                  auto_fix: bool = False) -> Dict[str, Any]:
    """
    리포트 생성 - Heavy Validation 포함 버전 (VectorDB 기반)
    
    Args:
        session_id: 세션 ID (Vector DB 및 interview_store 조회용)
        axes_keys: 핵심역량 키 리스트 (state.metrics에서 전달)
        resume_text: 폴백용 이력서 텍스트
        jd_text: 폴백용 공고 텍스트
        log_text: 폴백용 인터뷰 로그 텍스트
        enable_heavy: Heavy Validation 활성화 (LLM 품질 검증, 비용↑)
        auto_fix: 자동 수정 활성화
    
    Returns:
        리포트 결과 딕셔너리
    """
    try:
        # 1. VectorDB와 interview_store로 컨텍스트 구성
        jd_ctx, resume_ctx, log_ctx, comp_id_list, eid_list, qa_pairs = \
            _build_context_from_vectordb(session_id, axes_keys, resume_text, jd_text, log_text)
        
        # 2. 체인 실행
        comp_result = evaluate_competency(resume_ctx, log_ctx, axes_keys)
        comp_ev_result = map_competency_evidence(axes_keys, resume_ctx, log_ctx, eid_list)
        interview_result = summarize_interview(log_ctx, qa_pairs)
        
        # 3. 결과 조합
        report = {
            "axes": [{"key": k, "label": k.replace("_", " ").title()} for k in axes_keys],
            "scores": [s.model_dump() for s in comp_result.scores],
            "weights": [w.model_dump() for w in comp_result.weights],
            "headline": comp_result.headline.model_dump(),
            "talkSummary": {
                "items": [
                    {"주제": "인터뷰 요약", "발언요약": interview_result.overall}
                ] + [
                    {
                        "주제": qa["question_short"],
                        "발언요약": qa["answer_summary"]
                    }
                    for qa in interview_result.qa_summaries
                ] + [
                    {"주제": "긍정 의견", "발언요약": interview_result.positive},
                    {"주제": "부정 의견", "발언요약": interview_result.negative}
                ]
            },
            "convStats": [{"k": k, "v": str(v)} for k, v in interview_result.stats.items()],
            "jdCoverage": [comp.model_dump() for comp in comp_ev_result.competencyCoverage],
            "evidence": [ev.model_dump() for ev in comp_ev_result.evidence]
        }
        
        # 4. Light Validation
        light_validation = validate_light(report, comp_id_list, eid_list, qa_pairs)
        
        # 5. Heavy Validation (선택적)
        heavy_validation = None
        repairs_applied = []
        
        if enable_heavy:
            heavy_validation = validate_heavy(report, jd_text, resume_text, log_text)
            
            # 자동 수정
            if auto_fix and heavy_validation.overall_grade in ["C", "D", "F"]:
                report, repairs_applied = auto_repair(report, heavy_validation)
        
        # 6. 최종 검증
        errors = [i for i in light_validation.issues if i.severity == "error"]
        if errors:
            return _error("VALIDATION_FAILED", "리포트 검증 실패",
                         details={"errors": [i.model_dump() for i in errors]})
        
        # 7. 최종 스키마 검증
        try:
            ReportOut(**report)
        except ValidationError as ve:
            print(f"\n[SCHEMA_ERROR] Pydantic 검증 실패:")
            for err in ve.errors():
                print(f"  - 위치: {err.get('loc')}")
                print(f"    메시지: {err.get('msg')}")
                print(f"    타입: {err.get('type')}")
                print(f"    입력값: {err.get('input', 'N/A')}")
            print()
            raise
        
        report.update({
            "status": "ok",
            "report_id": uuid4().hex,
            "created_at": datetime.utcnow().isoformat() + "Z",
            "validation": {
                "light": light_validation.model_dump(),
                "heavy": heavy_validation.model_dump() if heavy_validation else None,
                "warnings": len([i for i in light_validation.issues if i.severity == "warning"]),
                "repairs_applied": repairs_applied
            }
        })
        
        return report
        
    except ReportError as e:
        return _error(e.code, e.message, e.http, e.details)
    except ValidationError as e:
        print(f"\n[VALIDATION_ERROR] 최종 검증 실패:")
        for err in e.errors():
            print(f"  - {err}")
        return _error("SCHEMA_ERROR", "스키마 검증 실패", 400, {"errors": e.errors()})
    except Exception as e:
        import traceback
        return _error("INTERNAL", f"내부 오류: {type(e).__name__}", 500, {"trace": traceback.format_exc()})

# ==================== 간단한 래퍼 함수 ====================
def create_report(session_id: str, axes_keys: List[str],
                 resume_text: str = "", jd_text: str = "", log_text: str = "") -> Dict[str, Any]:
    """
    리포트 생성 - 기본 버전 (Light Validation만 사용, VectorDB 기반)
    
    Args:
        session_id: 세션 ID (Vector DB 및 interview_store 조회용)
        axes_keys: 핵심역량 키 리스트 (state.metrics에서 전달)
        resume_text: 폴백용 이력서 텍스트
        jd_text: 폴백용 공고 텍스트
        log_text: 폴백용 인터뷰 로그 텍스트
    
    Returns:
        리포트 결과 딕셔너리
    """
    return create_report_with_validation(
        session_id, axes_keys, resume_text, jd_text, log_text,
        enable_heavy=False,
        auto_fix=False
    )
