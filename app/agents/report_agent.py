from __future__ import annotations
from typing import List, Dict, Optional, Any, Tuple
from pathlib import Path
from uuid import uuid4
from datetime import datetime
import json, re, os

from pydantic import BaseModel, Field, ValidationError, StringConstraints
from typing_extensions import Annotated
from annotated_types import Ge, Le

from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI

# ---------- 환경 ----------
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

def _get_llm() -> ChatOpenAI:
    # function-calling 가능한 모델 권장
    return ChatOpenAI(model_name=OPENAI_MODEL, temperature=0.2)

# ---------- 임시 저장소 ----------
_STORE: Dict[str, Dict] = {}

# ---------- 스키마 ----------
KeyStr  = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[a-z][a-z0-9_]{1,32}$")]
NonEmpty = Annotated[str, StringConstraints(min_length=1)]
E_ID    = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^E\d{2,3}$")]
J_ID    = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^J\d{2,3}$")]
Score   = Annotated[int, Ge(0), Le(100)]
Weight  = Annotated[int, Ge(0), Le(100)]

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
    긍정의견: NonEmpty
    부정의견: NonEmpty
    tag: NonEmpty

class TalkSummaryItem(BaseModel):
    주제: NonEmpty
    발언요약: NonEmpty

class TalkSummary(BaseModel):
    items: List[TalkSummaryItem] = Field(min_length=1)

class JDCoverRow(BaseModel):
    jid: J_ID
    요구사항: NonEmpty
    기대치: NonEmpty
    충족도: NonEmpty
    근거: List[E_ID] = Field(min_length=1)

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

# ---------- 파일 유틸 ----------
ROOT = Path(__file__).resolve().parents[1]  # app/..

def _resolve(path_str: str) -> Path:
    p = (path_str or "").strip().replace("\\", "/")
    cand = [
        Path(p),
        ROOT / p,
        ROOT / "data" / Path(p).name,
        ROOT / "app" / "data" / Path(p).name,
    ]
    for c in cand:
        if c.exists():
            return c
    return Path(p)  # 마지막 시도

def _read_text(path_str: str) -> str:
    return _resolve(path_str).read_text(encoding="utf-8", errors="ignore").strip()

# ---------- 간단한 통계/키워드 기반 fallback ----------
_KW = {
    "tech":  r"python|java|backend|api|db|sql|oracle|docker|k8s|kubernetes|aws|gcp|ci/?cd",
    "comm":  r"커뮤니케이션|communication|보고|프레젠테이션|고객|요구사항|조율|설득|문서화",
    "exec":  r"운영|장애|모니터링|배포|릴리즈|성능|최적화|troubleshoot|stability",
    "fit":   r"문화|가치|팀워크|ownership|책임감|리더십|collaborat",
    "learn": r"학습|스터디|자기계발|자격증|교육|최신|논문|readme",
}

def _score_from_text(keys: List[str], text: str) -> Dict[str, int]:
    text_l = text.lower()
    out = {}
    mx = 1
    counts: Dict[str, int] = {}
    for k in keys:
        pat = _KW.get(k, k)
        n = len(re.findall(pat, text_l))
        counts[k] = n
        mx = max(mx, n)
    for k, n in counts.items():
        out[k] = int(round((n / mx) * 100)) if mx > 0 else 20
    # 모두 0이면 20 고정
    if all(v == 0 for v in out.values()):
        out = {k: 20 for k in keys}
    return out

def _weights_from_jd(keys: List[str], jd_text: str) -> Dict[str, int]:
    raw = _score_from_text(keys, jd_text)
    s = sum(raw.values())
    if s == 0:
        return {k: int(100/len(keys)) for k in keys}
    # 100 합으로 정수 분배
    weights = {k: int(round(v * 100 / s)) for k, v in raw.items()}
    diff = 100 - sum(weights.values())
    # 합 보정
    for k in keys:
        if diff == 0: break
        weights[k] += 1 if diff > 0 else -1
        diff += -1 if diff > 0 else 1
    return weights

def _conv_stats(log_text: str) -> List[ConvKV]:
    qs = len(re.findall(r"(?m)^Q\d+", log_text))
    turns = len([l for l in log_text.splitlines() if l.strip()])
    chars = len(log_text)
    return [
        ConvKV(k="질문 수", v=str(qs)),
        ConvKV(k="발화 줄 수", v=str(turns)),
        ConvKV(k="로그 길이(문자)", v=str(chars)),
    ]

def _first_sentences(txt: str, sent=3) -> str:
    s = re.split(r"(?<=[.!?。!?])\s+", txt.strip())
    return " ".join(s[:sent]).strip() or txt[:200]

# ---------- LLM 체인 ----------
def _prompt() -> ChatPromptTemplate:
    return ChatPromptTemplate.from_messages([
        ("system",
         "너는 채용 리포트 보조자다. 반드시 아래 Pydantic 스키마(ReportOut)를 정확히 채워 "
         "JSON이 아닌 '구조화된 Python 객체'로 응답한다. "
         "axes는 사용자가 준 순서를 유지하고, scores(0~100, int)와 "
         "weights(정수, 합계=100)는 모든 key를 포함한다. "
         "headline은 한두 문단으로 간결히. talkSummary.items는 3개 이상."),
        ("human",
         "선택된 축 키: {axes_keys}\n\n[공고]\n{jd}\n\n[이력서]\n{resume}\n\n[인터뷰로그]\n{log}")
    ])

# ---------- 공개 API ----------
def create_report_from_files(
    resume_path: str,
    jd_path: str,
    log_path: str,
    axes_keys: List[str],
) -> Dict[str, Any]:
    resume_txt = _read_text(resume_path)
    jd_txt     = _read_text(jd_path)
    log_txt    = _read_text(log_path)

    # 1) 구조화 출력 체인으로 시도
    try:
        print("🤖 LLM 호출 시작...")
        print(f"   - 이력서: {len(resume_txt)}자")
        print(f"   - JD: {len(jd_txt)}자")
        print(f"   - 로그: {len(log_txt)}자")
        print(f"   - 평가축: {axes_keys}")
        
        chain = _prompt() | _get_llm().with_structured_output(ReportOut)
        result: ReportOut = chain.invoke({
            "resume": resume_txt,
            "jd": jd_txt,
            "log": log_txt,
            "axes_keys": ", ".join(axes_keys)
        })
        print("✅ LLM 응답 성공!")
        obj = result.model_dump(mode="json")
        
        # List[ScoreItem]을 Dict[str, int]로 변환
        obj['scores'] = {item['key']: item['value'] for item in obj['scores']}
        obj['weights'] = {item['key']: item['value'] for item in obj['weights']}
        
    except Exception as e:
        print(f"❌ LLM 호출 실패: {type(e).__name__}: {str(e)}")
        import traceback
        traceback.print_exc()
        # 2) 실패 시 키워드 기반 fallback
        axes = [ {"key": k, "label": k} for k in axes_keys ]
        scores  = _score_from_text(axes_keys, "\n".join([resume_txt, jd_txt, log_txt]))
        weights = _weights_from_jd(axes_keys, jd_txt)
        # 더미 JD/Evidence 최소 1개씩
        jd_cov = [{
            "jid":"J01","요구사항":"핵심 요구 정리","기대치":"업무 즉시 기여",
            "충족도":"미상","근거":["E01"]
        }]
        evid = [{
            "eid":"E01","출처":"이력서",
            "내용":_first_sentences(resume_txt,2) or "요약 없음",
            "jid":"J01"
        }]
        obj = {
            "axes": axes,
            "scores": scores,
            "weights": weights,
            "headline": {   
                "긍정의견": _first_sentences(resume_txt,3) + "\n\n" + _first_sentences(log_txt,2),
                "부정의견": "추가 검증 필요. JD 핵심 요구 일부는 인터뷰 로그에서 명시적 근거가 부족합니다.",
                "tag": "#초안"
            },
            "talkSummary": {
                "items": [
                    {"주제":"자기소개", "발언요약": _first_sentences(log_txt,2) or "정보 부족"},
                    {"주제":"핵심역량", "발언요약": _first_sentences(jd_txt,2)},
                    {"주제":"직무적합성", "발언요약": "경험과 JD의 정합성을 추가 확인 필요"}
                ]
            },
            "convStats": [ kv.model_dump() for kv in _conv_stats(log_txt) ],
            "jdCoverage": jd_cov,
            "evidence":  evid
        }

    # 3) 최종 저장 - scores/weights는 이미 Dict 형태
    rid = uuid4().hex
    obj.update({"report_id": rid, "created_at": datetime.utcnow().isoformat()+"Z"})
    _STORE[rid] = obj
    return obj

def validate_and_save(report_json: Dict) -> Dict:
    # scores/weights가 Dict로 들어올 경우 List로 변환
    if 'scores' in report_json and isinstance(report_json['scores'], dict):
        report_json['scores'] = [{'key': k, 'value': v} for k, v in report_json['scores'].items()]
    if 'weights' in report_json and isinstance(report_json['weights'], dict):
        report_json['weights'] = [{'key': k, 'value': v} for k, v in report_json['weights'].items()]
    
    obj = ReportOut(**report_json).model_dump(mode="json")
    
    # 다시 Dict로 변환해서 저장
    obj['scores'] = {item['key']: item['value'] for item in obj['scores']}
    obj['weights'] = {item['key']: item['value'] for item in obj['weights']}
    
    rid = uuid4().hex
    obj.update({"report_id": rid, "created_at": datetime.utcnow().isoformat()+"Z"})
    _STORE[rid] = obj
    return obj

def get_report(report_id: str) -> Optional[Dict]:
    return _STORE.get(report_id)

def apply_feedback(rid: str, patch: Dict) -> Optional[Dict]:
    rpt = _STORE.get(rid)
    if not rpt:
        return None
    for k in ("scores","weights","headline"):
        if k in patch and isinstance(patch[k], dict):
            rpt[k].update(patch[k])
    if "talkSummary" in patch and isinstance(patch["talkSummary"], dict):
        rpt["talkSummary"] = patch["talkSummary"]
    if "convStats" in patch and isinstance(patch["convStats"], list):
        rpt["convStats"] = patch["convStats"]
    ReportOut(**{k: rpt[k] for k in (
        "axes","scores","weights","headline","talkSummary","convStats","jdCoverage","evidence"
    )})
    return rpt
