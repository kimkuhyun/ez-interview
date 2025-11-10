"""
개선된 리포트 생성 시스템 v2
- 핵심역량 평가와 JD-증거 매핑을 분리
- Light/Heavy Validation 구현
- ID 순차성 보장
- 인터뷰 로그 완전 요약
"""
from __future__ import annotations
from typing import List, Dict, Optional, Any, Tuple, Literal
from pathlib import Path
from uuid import uuid4
from datetime import datetime
import json, re, os, hashlib
from collections import defaultdict

from pydantic import BaseModel, Field, ValidationError, StringConstraints
from typing_extensions import Annotated
from annotated_types import Ge, Le

from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI, OpenAIEmbeddings

# ==================== 환경 설정 ====================
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")
EMBED_MODEL = os.getenv("OPENAI_EMBED_MODEL", "text-embedding-3-small")
TMP_DIR = Path(os.getenv("RAG_TMP_DIR", "app/.tmp"))
TMP_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_TOKEN_BUDGET = 32000
TOKEN_TO_CHARS = 4
SAFETY_RATIO = 0.7

def _llm() -> ChatOpenAI:
    return ChatOpenAI(model_name=OPENAI_MODEL, temperature=0.1, timeout=90)

def _emb() -> OpenAIEmbeddings:
    return OpenAIEmbeddings(model=EMBED_MODEL)

# ==================== 스키마 정의 ====================
KeyStr = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[a-z][a-z0-9_]{1,32}$")]
NonEmpty = Annotated[str, StringConstraints(min_length=1)]
E_ID = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^E\d{2,3}$")]
COMP_ID = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[JC]\d{2,3}$")]  # J 또는 C로 시작
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
    근거: List[E_ID]  # min_length 제약 제거 - Light Validation에서 검증

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
class Turn(BaseModel):
    id: str
    role: Literal["Q", "A"]
    text: str
    eids: List[E_ID] = Field(default_factory=list)

class LightIssue(BaseModel):
    code: str
    where: Optional[str] = None
    detail: str
    severity: Literal["error", "warning"] = "error"

class LightValidationResult(BaseModel):
    ok: bool
    issues: List[LightIssue] = Field(default_factory=list)
    stats: Dict[str, int] = Field(default_factory=dict)

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

# ==================== 파일 처리 ====================
ROOT = Path(__file__).resolve().parents[1]

def _resolve(p: str) -> Path:
    p = (p or "").strip().replace("\\", "/")
    candidates = [
        Path(p),
        ROOT / p,
        ROOT / "data" / Path(p).name,
        ROOT / "app" / "data" / Path(p).name
    ]
    for c in candidates:
        if c.exists():
            return c
    return Path(p)

def _read(p: str) -> str:
    return _resolve(p).read_text(encoding="utf-8", errors="ignore").strip()

def _norm(t: str) -> str:
    return re.sub(r"\r\n?", "\n", t).strip()

# ==================== RAG: 청킹 ====================
def _chunk_paragraph(text: str, size: int = 600, overlap: int = 100) -> List[str]:
    """단락 기반 청킹"""
    text = _norm(text)
    if not text:
        return []
    
    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks = []
    
    for para in paras:
        if len(para) <= size:
            chunks.append(para)
            continue
        
        # 문장 분리
        sents = re.split(r"(?<=[.!?。…])\s+|\n", para)
        buf = ""
        for s in sents:
            if not s.strip():
                continue
            cand = (buf + " " + s).strip() if buf else s.strip()
            if len(cand) <= size:
                buf = cand
            else:
                if buf:
                    chunks.append(buf)
                buf = s.strip()
        if buf:
            chunks.append(buf)
    
    if not chunks:
        return []
    
    # 오버랩 추가
    out = [chunks[0]]
    for i in range(1, len(chunks)):
        out.append((chunks[i-1][-overlap:] + " " + chunks[i]).strip())
    return out

def _chunk_log(log: str) -> List[Dict[str, Any]]:
    """
    인터뷰 로그를 질문-답변 쌍으로 파싱
    형식: Q1 주제\n[00:00] 면접관: ...\n[00:05] 응답자: ...
    """
    blocks = re.split(r"(?m)^(?=Q\d+)", _norm(log))
    qa_pairs = []
    
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        
        # Q 번호와 주제 추출
        q_match = re.match(r"^Q(\d+)\s+(.+?)$", block, re.MULTILINE)
        if not q_match:
            continue
        
        q_num = q_match.group(1)
        q_topic = q_match.group(2).strip()
        
        # 면접관 질문 추출 (첫 번째 면접관 발언)
        interviewer_lines = re.findall(r'\[[\d:]+\]\s*면접관[^:]*:\s*(.+?)(?=\n|$)', block)
        q_text = interviewer_lines[0] if interviewer_lines else q_topic
        
        # 응답자 답변 추출 (모든 응답자 발언 결합)
        respondent_lines = re.findall(r'\[[\d:]+\]\s*응답자[^:]*:\s*(.+?)(?=\n|$)', block)
        a_text = " ".join(respondent_lines) if respondent_lines else ""
        
        qa_pairs.append({
            "q_num": q_num,
            "question": q_text,
            "answer": a_text,
            "topic": q_topic,
            "full_text": block
        })
    
    return qa_pairs

def _extract_clean_question(question_text: str) -> str:
    """
    질문 텍스트에서 타임스탬프와 역할 레이블을 제거하고 깔끔한 질문만 추출
    예: "[00:00] 면접관: 간단히 자기소개 부탁드립니다." → "간단히 자기소개 부탁드립니다"
    """
    # 타임스탬프 제거: [00:00], [01:23] 등
    text = re.sub(r'\[\d{2}:\d{2}\]', '', question_text)
    # 역할 레이블 제거: "면접관:", "응답자:" 등
    text = re.sub(r'(면접관|응답자)\s*:', '', text)
    # 공백 정리
    text = re.sub(r'\s+', ' ', text).strip()
    # 물음표가 없으면 추가
    if text and not text.endswith('?') and not text.endswith('.'):
        text += '?'
    return text

# ==================== RAG: 벡터 스토어 ====================
def _cos(a: List[float], b: List[float]) -> float:
    """코사인 유사도"""
    da = sum(x * x for x in a) ** 0.5 if a else 0.0
    db = sum(x * x for x in b) ** 0.5 if b else 0.0
    if da == 0.0 or db == 0.0:
        return 0.0
    return sum(x * y for x, y in zip(a, b)) / (da * db)

def _mmr(qv: List[float], cand: List[List[float]], k: int = 12, lamb: float = 0.6) -> List[int]:
    """MMR 알고리즘"""
    chosen = []
    remain = list(range(len(cand)))
    sims_q = [_cos(qv, v) for v in cand]
    
    while remain and len(chosen) < k:
        best_i, best_score = None, float("-inf")
        for i in remain:
            div = 0.0 if not chosen else max(_cos(cand[i], cand[j]) for j in chosen)
            score = lamb * sims_q[i] - (1 - lamb) * div
            if score > best_score:
                best_i, best_score = i, score
        
        chosen.append(best_i)
        remain.remove(best_i)
    
    return chosen

def _build_index(resume_txt: str, jd_txt: str, log_txt: str) -> Tuple[List[Dict], List[Dict]]:
    """
    벡터 인덱스 구축
    Returns: (문서 청크, QA 쌍)
    """
    emb = _emb()
    docs = []
    
    # JD 청킹
    for t in _chunk_paragraph(jd_txt, size=500):
        docs.append({"doc_type": "JD", "text": t})
    
    # 이력서 청킹
    for t in _chunk_paragraph(resume_txt, size=600):
        docs.append({"doc_type": "이력서", "text": t})
    
    # 인터뷰 로그 청킹 (QA 단위)
    qa_pairs = _chunk_log(log_txt)
    for qa in qa_pairs:
        docs.append({
            "doc_type": "인터뷰로그",
            "text": qa["full_text"],
            "q_num": qa["q_num"]
        })
    
    if not docs:
        return [], qa_pairs
    
    # 임베딩
    vecs = emb.embed_documents([d["text"] for d in docs])
    for d, v in zip(docs, vecs):
        d["vec"] = v
    
    return docs, qa_pairs

# ==================== ID 관리 ====================
class IDManager:
    """순차적 ID 생성 및 관리"""
    
    def __init__(self):
        self.jid_counter = 0
        self.eid_counter = 0
        self.jid_map: Dict[int, str] = {}  # doc_idx -> JID
        self.eid_map: Dict[int, str] = {}  # doc_idx -> EID
        self.eid_to_jid: Dict[str, str] = {}  # EID -> JID
    
    def assign_jid(self, doc_idx: int) -> str:
        """JID 할당"""
        self.jid_counter += 1
        jid = f"J{self.jid_counter:02d}"
        self.jid_map[doc_idx] = jid
        return jid
    
    def assign_eid(self, doc_idx: int, closest_jid_idx: int) -> str:
        """EID 할당 및 JID 매핑"""
        self.eid_counter += 1
        eid = f"E{self.eid_counter:02d}"
        self.eid_map[doc_idx] = eid
        
        # 가장 가까운 JD에 매핑
        if closest_jid_idx in self.jid_map:
            self.eid_to_jid[eid] = self.jid_map[closest_jid_idx]
        
        return eid
    
    def get_jid_list(self) -> List[str]:
        return sorted(self.jid_map.values(), key=lambda x: int(x[1:]))
    
    def get_eid_list(self) -> List[str]:
        return sorted(self.eid_map.values(), key=lambda x: int(x[1:]))

def _attach_ids_v2(docs: List[Dict]) -> Tuple[List[Dict], IDManager]:
    """개선된 ID 할당 - 순차성 보장"""
    id_mgr = IDManager()
    jd_indices = []
    
    # 1단계: JD에 JID 할당
    for i, doc in enumerate(docs):
        if doc["doc_type"] == "JD":
            jid = id_mgr.assign_jid(i)
            doc["jid"] = jid
            doc["text"] = f"[{jid}][JD]\n{doc['text']}"
            jd_indices.append(i)
    
    # 2단계: 증거에 EID 할당 및 JID 매핑
    for i, doc in enumerate(docs):
        if doc["doc_type"] == "JD":
            continue
        
        # 가장 가까운 JD 찾기
        best_jid_idx = jd_indices[0] if jd_indices else None
        best_sim = -1.0
        
        for j_idx in jd_indices:
            sim = _cos(doc["vec"], docs[j_idx]["vec"])
            if sim > best_sim:
                best_sim = sim
                best_jid_idx = j_idx
        
        eid = id_mgr.assign_eid(i, best_jid_idx)
        doc["eid"] = eid
        doc["jid"] = id_mgr.eid_to_jid.get(eid, "J01")
        doc["text"] = f"[{eid}][{doc['doc_type']}]\n{doc['text']}"
    
    return docs, id_mgr

# ==================== 체인 1: 핵심역량 평가 ====================
class CompetencyEvalOut(BaseModel):
    """핵심역량 평가 출력"""
    scores: List[ScoreItem]  # 각 역량별 점수
    weights: List[WeightItem]  # 각 역량별 가중치
    headline: Headline
    reasoning: List[str] = Field(default_factory=list)  # 평가 근거

def _competency_prompt() -> ChatPromptTemplate:
    return ChatPromptTemplate.from_messages([
        ("system",
         "당신은 엄격한 면접 평가 전문가입니다. 이력서와 인터뷰 로그를 바탕으로 후보자의 핵심역량을 **보수적이고 엄격하게** 평가하세요.\n\n"
         "평가 원칙:\n"
         "1. 실제 증거에 기반한 평가 (추측 금지)\n"
         "2. 구체적인 성과와 수치가 있어야 높은 점수\n"
         "3. 막연한 표현이나 일반론은 낮은 점수\n"
         "4. 증거가 약하면 과감히 낮은 점수 부여\n"
         "5. 가중치 합계는 정확히 100\n\n"
         "점수 기준 (엄격 적용):\n"
         "- 0-30: 증거 없음 또는 매우 부족\n"
         "- 31-50: 기본적 수준, 구체성 부족\n"
         "- 51-65: 보통 수준, 일부 구체적 사례 있음\n"
         "- 66-75: 양호, 명확한 성과 입증\n"
         "- 76-85: 우수, 복수의 구체적 성과와 수치\n"
         "- 86-95: 탁월, 예외적인 성과와 깊이\n"
         "- 96-100: 최고 수준 (거의 부여하지 않음)\n\n"
         "주의사항:\n"
         "- 대부분의 후보자는 50-70점 범위에 분포해야 함\n"
         "- 80점 이상은 확실한 증거가 있을 때만\n"
         "- 모든 역량에 비슷한 점수는 지양 (차별화 필수)\n\n"
         "**필수 출력 필드:**\n"
         "- scores: List[{{key: str, value: int}}] - 각 역량 키에 대한 점수 배열 (예: [{{key: 'problem_solving', value: 65}}, ...])\n"
         "- weights: List[{{key: str, value: int}}] - 각 역량 키에 대한 가중치 배열, 합계 반드시 100\n"
         "- headline: {{summary: str, tag: str}} - 1줄 요약 + 키워드 3-5개 (쉼표 구분)\n"
         "- reasoning: List[str] - 각 역량에 대한 평가 근거 (5개, 각 역량당 1개씩)"),
        ("human",
         "평가 축: {axes_str}\n\n"
         "[이력서]\n{resume}\n\n"
         "[인터뷰로그]\n{log}\n\n"
         "위 평가 축에 대한 scores, weights, headline, reasoning을 모두 반환하세요.")
    ])

def evaluate_competency(resume: str, log: str, axes_keys: List[str]) -> CompetencyEvalOut:
    """핵심역량 평가 체인"""
    chain = _competency_prompt() | _llm().with_structured_output(CompetencyEvalOut, method="function_calling")
    
    result = chain.invoke({
        "axes_str": ", ".join(axes_keys),
        "resume": resume,
        "log": log
    })
    
    # 가중치 정규화 (List[WeightItem] 형식)
    weights_dict = {w.key: w.value for w in result.weights}
    total_w = sum(weights_dict.values())
    if total_w != 100:
        factor = 100 / total_w
        weights_dict = {k: int(round(v * factor)) for k, v in weights_dict.items()}
        # 반올림 오차 보정
        diff = 100 - sum(weights_dict.values())
        if diff != 0:
            max_key = max(weights_dict, key=weights_dict.get)
            weights_dict[max_key] += diff
        # 다시 List[WeightItem]로 변환
        result.weights = [WeightItem(key=k, value=v) for k, v in weights_dict.items()]
    
    return result

# ==================== 체인 2: 핵심역량-증거 매핑 ====================
class CompetencyEvidenceOut(BaseModel):
    """핵심역량-증거 매핑 출력"""
    competencyCoverage: List[JDCoverRow]  # JDCoverRow 재사용 (jid를 역량 ID로 사용)
    evidence: List[EvidenceRow]  # EvidenceRow 형식

def _competency_evidence_prompt() -> ChatPromptTemplate:
    return ChatPromptTemplate.from_messages([
        ("system",
         "당신은 핵심역량과 증거를 매핑하는 전문가입니다.\n\n"
         "작업:\n"
         "1. 각 핵심역량에 대해 관련 증거(Exx)를 이력서와 인터뷰 로그에서 찾으세요\n"
         "2. 증거는 반드시 제공된 컨텍스트에서만 추출하세요\n"
         "3. 충족도는 증거의 강도에 따라 평가하세요:\n"
         "   - 적합: 구체적이고 명확한 증거가 2개 이상 있으며 실제 성과 입증\n"
         "   - 보통: 관련 증거가 1-2개 있으나 다소 간접적이거나 구체성 부족\n"
         "   - 부족: 직접적 증거가 없거나 매우 약함\n"
         "4. 각 증거는 짧고 명확한 1-2문장으로 요약하세요 (20-30단어)\n\n"
         "매핑 원칙:\n"
         "- 의미적 일치 우선 (키워드 일치는 참고만)\n"
         "- 역할-행동-성과 구조 고려\n"
         "- 증거가 많고 구체적일수록 높은 충족도 평가\n"
         "- **중요: 모든 핵심역량은 최소 1개 이상의 EID가 근거 배열에 포함되어야 합니다**\n"
         "- 직접적 증거가 없어도 간접적/관련 증거라도 반드시 1개 이상 찾으세요\n\n"
         "**필수 출력 필드:**\n"
         "- competencyCoverage: 핵심역량별 평가 배열 (jid=역량ID, 요구사항=역량명, 기대치=역량설명, 충족도, 근거[EID 배열])\n"
         "- evidence: 증거 배열 (eid, 출처, 내용, jid=역량ID)\n\n"
         "**주의:** 근거 배열이 빈 배열 []인 역량이 있으면 안 됩니다. 약한 증거라도 반드시 1개 이상 매핑하세요."),
        ("human",
         "[핵심역량 목록]\n{competencies}\n\n"
         "[이력서]\n{resume}\n\n"
         "[인터뷰로그]\n{log}\n\n"
         "핵심역량별 ID와 설명:\n{competency_details}\n\n"
         "사용 가능한 EID: {eid_list}\n\n"
         "위 형식에 맞춰 competencyCoverage와 evidence를 모두 반환하세요.\n"
         "충족도는 증거의 양과 질을 고려하여 적합/보통/부족 중 하나로 평가하세요.")
    ])

def map_competency_evidence(axes_keys: List[str], resume: str, log: str, 
                            eid_list: List[str]) -> CompetencyEvidenceOut:
    """핵심역량-증거 매핑 체인"""
    
    # 역량명 매핑
    competency_names = {
        "problem_solving": "문제해결능력",
        "communication": "커뮤니케이션",
        "self_driven_initiative": "자기주도성",
        "collaboration": "협업능력",
        "professional_expertise": "전문성"
    }
    
    # 역량별 상세 설명
    competency_details = "\n".join([
        f"C{i+1:02d} ({axes_keys[i]}): {competency_names.get(axes_keys[i], axes_keys[i])}"
        for i in range(len(axes_keys))
    ])
    
    # 역량 목록 텍스트
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
    """인터뷰 요약 출력"""
    overall: str  # 전체 요약 (2-3문장)
    qa_summaries: List[Dict[str, str]] = Field(default_factory=list)  # [{q_num, question_short, answer_summary}] - LLM이 생략할 수 있으므로 기본값 설정
    positive: str  # 긍정 의견 (200-300자)
    negative: str  # 부정 의견 (200-300자)
    stats: Dict[str, str]  # 통계 정보

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
    """인터뷰 요약 체인"""
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
    
    # 1. 역량 ID 연속성 (C01, C02, ... 또는 J01, J02, ...)
    first_char = comp_id_list[0][0] if comp_id_list else 'C'
    expected_ids = [f"{first_char}{i+1:02d}" for i in range(len(comp_id_list))]
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
    
    # 3. 역량 커버리지 체크
    comp_in_coverage = {row["jid"] for row in report.get("jdCoverage", [])}
    missing_comps = set(comp_id_list) - comp_in_coverage
    if missing_comps:
        issues.append(LightIssue(
            code="MISSING_COMPETENCY",
            detail=f"누락된 역량: {sorted(missing_comps)}",
            severity="warning"
        ))
    
    # 3-1. 빈 근거 배열 체크 (Light Validation 추가 요구사항)
    for row in report.get("jdCoverage", []):
        if not row.get("근거") or len(row.get("근거", [])) == 0:
            issues.append(LightIssue(
                code="EMPTY_EVIDENCE",
                where=row["jid"],
                detail=f"JD {row['jid']}에 근거가 없습니다 (충족도: {row.get('충족도')})",
                severity="warning"  # 경고로 처리, 에러로 하려면 "error"로 변경
            ))
    
    # 4. 증거 완전성 (누락된 증거는 경고로만 처리)
    evidence_in_map = {ev["eid"] for ev in report.get("evidence", [])}
    missing_evs = set(eid_list) - evidence_in_map
    if missing_evs:
        issues.append(LightIssue(
            code="MISSING_EVIDENCE",
            detail=f"누락된 증거: {sorted(missing_evs)}",
            severity="warning"  # 모든 증거를 포함하지 않은 것은 경고
        ))
    
    # 4-1. 참조되었으나 존재하지 않는 증거 (이것은 에러)
    referenced_eids = set()
    for row in report.get("jdCoverage", []):
        referenced_eids.update(row.get("근거", []))
    
    invalid_refs = referenced_eids - evidence_in_map
    if invalid_refs:
        issues.append(LightIssue(
            code="INVALID_EVIDENCE_REF",
            detail=f"존재하지 않는 증거가 참조됨: {sorted(invalid_refs)}",
            severity="error"  # 잘못된 참조는 에러
        ))
    
    # 5. 양방향 매핑 체크
    jd_to_eids = defaultdict(set)
    for row in report.get("jdCoverage", []):
        for eid in row.get("근거", []):
            jd_to_eids[row["jid"]].add(eid)
    
    eid_to_jid = {ev["eid"]: ev["jid"] for ev in report.get("evidence", [])}
    
    for jid, eids in jd_to_eids.items():
        for eid in eids:
            if eid_to_jid.get(eid) != jid:
                issues.append(LightIssue(
                    code="BIDIR_MISMATCH",
                    where=f"{jid} <-> {eid}",
                    detail=f"양방향 매핑 불일치",
                    severity="warning"
                ))
    
    # 6. 축 키셋 일치
    axes_keys = {a["key"] for a in report.get("axes", [])}
    score_keys = {s["key"] for s in report.get("scores", [])}
    weight_keys = {w["key"] for w in report.get("weights", [])}
    
    if not (axes_keys == score_keys == weight_keys):
        issues.append(LightIssue(
            code="AXIS_KEYSET_DIFF",
            detail="axes, scores, weights 키셋 불일치"
        ))
    
    # 7. 가중치 합계
    weight_sum = sum(w["value"] for w in report.get("weights", []))
    if abs(weight_sum - 100) > 1:
        issues.append(LightIssue(
            code="WEIGHT_SUM",
            detail=f"가중치 합계가 100이 아님: {weight_sum}"
        ))
    
    # 8. 인터뷰 로그 완전성
    qa_in_summary = len([
        item for item in report.get("talkSummary", {}).get("items", [])
        if item.get("주제") not in ["인터뷰 요약", "긍정 의견", "부정 의견"]
    ])
    expected_qa = len(qa_pairs)
    
    if qa_in_summary < expected_qa:
        issues.append(LightIssue(
            code="LOG_INCOMPLETE",
            detail=f"인터뷰 요약 누락: {qa_in_summary}/{expected_qa}",
            severity="warning"
        ))
    
    stats = {
        "total_competencies": len(comp_id_list),
        "total_evidences": len(eid_list),
        "total_qa": len(qa_pairs),
        "summarized_qa": qa_in_summary
    }
    
    return LightValidationResult(
        ok=(len([i for i in issues if i.severity == "error"]) == 0),
        issues=issues,
        stats=stats
    )

# ==================== 메인 생성 함수 ====================
def create_report_v2(resume_path: str, jd_path: str, log_path: str, 
                     axes_keys: List[str]) -> Dict[str, Any]:
    """
    개선된 리포트 생성 - 3단계 체인
    """
    try:
        # 1. 파일 읽기
        resume_txt = _read(resume_path)
        jd_txt = _read(jd_path)
        log_txt = _read(log_path)
        
        if not resume_txt or not jd_txt:
            raise ReportError("EMPTY_INPUT", "이력서 또는 JD가 비어있습니다")
        
        # 2. RAG 인덱스 구축
        docs, qa_pairs = _build_index(resume_txt, jd_txt, log_txt)
        docs, id_mgr = _attach_ids_v2(docs)
        
        # 컨텍스트 구성
        jd_ctx = "\n\n".join([d["text"] for d in docs if d["doc_type"] == "JD"])
        resume_ctx = "\n\n".join([d["text"] for d in docs if d["doc_type"] == "이력서"])
        log_ctx = "\n\n".join([d["text"] for d in docs if d["doc_type"] == "인터뷰로그"])
        
        jid_list = id_mgr.get_jid_list()
        eid_list = id_mgr.get_eid_list()
        
        # 3. 체인 실행
        # 3-1. 핵심역량 평가
        comp_result = evaluate_competency(resume_ctx, log_ctx, axes_keys)
        
        # 3-2. 핵심역량-증거 매핑
        comp_ev_result = map_competency_evidence(axes_keys, resume_ctx, log_ctx, eid_list)
        
        # 3-3. 인터뷰 요약
        interview_result = summarize_interview(log_ctx, qa_pairs)
        
        # 4. 결과 조합
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
            "convStats": [{"k": k, "v": v} for k, v in interview_result.stats.items()],
            "jdCoverage": [comp.model_dump() for comp in comp_ev_result.competencyCoverage],
            "evidence": [ev.model_dump() for ev in comp_ev_result.evidence]
        }
        
        # 5. Light Validation
        validation = validate_light(report, jid_list, eid_list, qa_pairs)
        
        if not validation.ok:
            # 치명적 오류가 있으면 실패
            errors = [i for i in validation.issues if i.severity == "error"]
            if errors:
                return _error(
                    "VALIDATION_FAILED",
                    "리포트 검증 실패",
                    details={
                        "errors": [i.model_dump() for i in errors],
                        "stats": validation.stats
                    }
                )
        
        # 6. 최종 검증 및 저장
        ReportOut(**report)  # Pydantic 검증
        
        report.update({
            "status": "ok",
            "report_id": uuid4().hex,
            "created_at": datetime.utcnow().isoformat() + "Z",
            "validation": {
                "light": validation.model_dump(),
                "warnings": len([i for i in validation.issues if i.severity == "warning"])
            }
        })
        
        return report
        
    except ReportError as e:
        return _error(e.code, e.message, e.http, e.details)
    except ValidationError as e:
        print(f"\n[SCHEMA_ERROR] 검증 실패 상세:")
        for err in e.errors():
            print(f"  위치: {err.get('loc')}")
            print(f"  메시지: {err.get('msg')}")
            print(f"  타입: {err.get('type')}")
            print()
        return _error("SCHEMA_ERROR", "스키마 검증 실패", 400, {"errors": e.errors()})
    except Exception as e:
        import traceback
        print(f"\n[INTERNAL] 내부 오류:")
        print(traceback.format_exc())
        return _error("INTERNAL", f"내부 오류: {type(e).__name__}", 500, {
            "why": str(e),
            "trace": traceback.format_exc()
        })

# ==================== Heavy Validation (LLM) ====================
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
class RepairSuggestion(BaseModel):
    """수정 제안"""
    field: str  # 수정할 필드
    action: Literal["update", "add", "remove"]
    value: Any
    reason: str

def auto_repair(report: Dict, validation: HeavyValidationOut) -> Tuple[Dict, List[str]]:
    """
    검증 결과를 바탕으로 자동 수정 시도
    Returns: (수정된 리포트, 적용된 수정 목록)
    """
    repairs_applied = []
    
    # 1. 헤드라인 품질 개선
    if not validation.headline_quality.is_good:
        if validation.headline_quality.suggestions:
            # LLM에게 헤드라인 재생성 요청
            # (간단한 버전: 여기서는 경고만 기록)
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

# ==================== 메인 생성 함수 (Heavy Validation 포함) ====================
def create_report_with_validation(resume_path: str, jd_path: str, log_path: str,
                                  axes_keys: List[str],
                                  enable_heavy: bool = True,
                                  auto_fix: bool = True) -> Dict[str, Any]:
    """
    개선된 리포트 생성 - Light + Heavy Validation
    
    Args:
        enable_heavy: Heavy Validation 활성화 (느림, 하지만 품질 보장)
        auto_fix: 자동 수정 활성화
    """
    try:
        # 1. 파일 읽기
        resume_txt = _read(resume_path)
        jd_txt = _read(jd_path)
        log_txt = _read(log_path)
        
        if not resume_txt or not jd_txt:
            raise ReportError("EMPTY_INPUT", "이력서 또는 JD가 비어있습니다")
        
        # 2. RAG 인덱스 구축
        docs, qa_pairs = _build_index(resume_txt, jd_txt, log_txt)
        docs, id_mgr = _attach_ids_v2(docs)
        
        # 컨텍스트 구성
        jd_ctx = "\n\n".join([d["text"] for d in docs if d["doc_type"] == "JD"])
        resume_ctx = "\n\n".join([d["text"] for d in docs if d["doc_type"] == "이력서"])
        log_ctx = "\n\n".join([d["text"] for d in docs if d["doc_type"] == "인터뷰로그"])
        
        jid_list = id_mgr.get_jid_list()
        eid_list = id_mgr.get_eid_list()
        
        # 3. 체인 실행
        comp_result = evaluate_competency(resume_ctx, log_ctx, axes_keys)
        comp_ev_result = map_competency_evidence(axes_keys, resume_ctx, log_ctx, eid_list)
        interview_result = summarize_interview(log_ctx, qa_pairs)
        
        # 역량 ID 리스트 생성 (C01, C02, ...)
        comp_id_list = [f"C{i+1:02d}" for i in range(len(axes_keys))]
        
        # 디버깅: interview_result 확인
        print(f"\n[DEBUG] interview_result 필드:")
        print(f"  - overall: {len(interview_result.overall)} chars")
        print(f"  - qa_summaries: {len(interview_result.qa_summaries)} items")
        if interview_result.qa_summaries:
            for i, qa in enumerate(interview_result.qa_summaries[:3]):
                print(f"    [{i+1}] {qa}")
        else:
            print(f"    (빈 배열 - LLM이 qa_summaries를 반환하지 않음)")
        print(f"  - positive: {len(interview_result.positive)} chars")
        print(f"  - negative: {len(interview_result.negative)} chars")
        print(f"  - stats: {interview_result.stats}")
        print()
        
        # 4. 결과 조합
        # qa_summaries가 비어있으면 qa_pairs에서 기본 요약 생성
        qa_summary_items = []
        if interview_result.qa_summaries and len(interview_result.qa_summaries) > 0:
            qa_summary_items = [
                {
                    "주제": qa["question_short"],
                    "발언요약": qa["answer_summary"]
                }
                for qa in interview_result.qa_summaries
            ]
        else:
            # 폴백: qa_pairs에서 간단한 요약 생성
            print(f"[WARNING] LLM이 qa_summaries를 반환하지 않음. qa_pairs로 기본 요약 생성...")
            qa_summary_items = []
            for qa in qa_pairs[:12]:  # 최대 12개
                q_text = qa.get("question", "")
                a_text = qa.get("answer", "")
                topic = qa.get("topic", "")
                
                if not q_text and not topic:
                    continue
                
                # 질문 정리: topic이 있으면 topic 사용 (이미 짧음), 없으면 question 사용
                if topic and len(topic.strip()) > 0:
                    clean_q = topic.strip()
                else:
                    clean_q = _extract_clean_question(q_text) if q_text else "질문"
                
                # 질문을 30자로 제한 (더 짧게)
                if len(clean_q) > 30:
                    clean_q = clean_q[:30] + "?"
                elif not clean_q.endswith("?"):
                    clean_q = clean_q + "?"
                
                # 답변 요약 (150자, 1~3줄)
                if a_text and len(a_text.strip()) > 0:
                    # 문장 단위로 자르기 (최대 150자)
                    sentences = re.split(r'[.!?]\s+', a_text)
                    summary = ""
                    for sent in sentences[:3]:  # 최대 3문장
                        if len(summary) + len(sent) > 150:
                            break
                        summary += sent + ". "
                    summary = summary.strip()
                    if len(summary) == 0 and len(a_text) > 0:
                        summary = a_text[:150] + ("..." if len(a_text) > 150 else "")
                else:
                    summary = "답변 내용을 확인할 수 없습니다."
                
                qa_summary_items.append({
                    "주제": clean_q,
                    "발언요약": summary
                })
            
            # 빈 문자열이 있으면 기본 텍스트로 채우기
            for item in qa_summary_items:
                if not item["발언요약"] or len(item["발언요약"].strip()) == 0:
                    item["발언요약"] = "답변 내용을 확인할 수 없습니다."
        
        talkSummary_items = [
            {"주제": "인터뷰 요약", "발언요약": interview_result.overall}
        ] + qa_summary_items + [
            {"주제": "긍정 의견", "발언요약": interview_result.positive},
            {"주제": "부정 의견", "발언요약": interview_result.negative}
        ]
        
        print(f"\n[DEBUG] talkSummary.items 구성:")
        print(f"  - 총 {len(talkSummary_items)}개 항목")
        for i, item in enumerate(talkSummary_items):
            print(f"  [{i+1}] 주제: '{item['주제']}', 발언요약: {len(item['발언요약'])} chars")
        print()
        
        report = {
            "axes": [{"key": k, "label": k.replace("_", " ").title()} for k in axes_keys],
            "scores": [s.model_dump() for s in comp_result.scores],
            "weights": [w.model_dump() for w in comp_result.weights],
            "headline": comp_result.headline.model_dump(),
            "talkSummary": {
                "items": talkSummary_items
            },
            "convStats": [{"k": k, "v": v} for k, v in interview_result.stats.items()],
            "jdCoverage": [comp.model_dump() for comp in comp_ev_result.competencyCoverage],
            "evidence": [ev.model_dump() for ev in comp_ev_result.evidence]
        }
        
        # 5. Light Validation (역량 ID 사용)
        light_validation = validate_light(report, comp_id_list, eid_list, qa_pairs)
        
        # 6. Heavy Validation (선택적)
        heavy_validation = None
        repairs_applied = []
        
        if enable_heavy:
            heavy_validation = validate_heavy(report, jd_txt, resume_txt, log_txt)
            
            # 자동 수정
            if auto_fix and heavy_validation.overall_grade in ["C", "D", "F"]:
                report, repairs_applied = auto_repair(report, heavy_validation)
        
        # 7. 최종 검증
        errors = [i for i in light_validation.issues if i.severity == "error"]
        if errors:
            print(f"\n[Light Validation FAILED] 검증 오류 {len(errors)}건:")
            for err in errors:
                print(f"  - [{err.code}] {err.detail}")
                if err.where:
                    print(f"    위치: {err.where}")
            print()
            return _error(
                "VALIDATION_FAILED",
                "리포트 검증 실패",
                details={
                    "errors": [i.model_dump() for i in errors],
                    "stats": light_validation.stats
                }
            )
        
        ReportOut(**report)  # Pydantic 검증
        
        # 8. 결과 반환
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
        print(f"\n[create_report_from_files SCHEMA_ERROR] 검증 실패 상세:")
        for err in e.errors():
            print(f"  위치: {err.get('loc')}")
            print(f"  메시지: {err.get('msg')}")
            print(f"  타입: {err.get('type')}")
            print(f"  입력값: {err.get('input', 'N/A')}")
            print()
        return _error("SCHEMA_ERROR", "스키마 검증 실패", 400, {"errors": e.errors()})
    except Exception as e:
        import traceback
        print(f"\n[create_report_from_files INTERNAL] 내부 오류:")
        print(traceback.format_exc())
        return _error("INTERNAL", f"내부 오류: {type(e).__name__}", 500, {
            "why": str(e),
            "trace": traceback.format_exc()
        })

# ==================== API 호환성 ====================
def create_report_from_files(resume_path: str, jd_path: str, log_path: str, 
                             axes_keys: List[str]) -> Dict[str, Any]:
    """기존 API 호환 래퍼 - Heavy Validation 기본 활성화"""
    return create_report_with_validation(
        resume_path, jd_path, log_path, axes_keys,
        enable_heavy=True,
        auto_fix=True
    )