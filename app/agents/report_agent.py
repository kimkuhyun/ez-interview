# app/agents/report_agent.py
from __future__ import annotations
from typing import List, Dict, Optional, Any, Tuple
from pathlib import Path
from uuid import uuid4
from datetime import datetime
import json, re, os, hashlib

from pydantic import BaseModel, Field, ValidationError, StringConstraints
from typing_extensions import Annotated
from annotated_types import Ge, Le

from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI, OpenAIEmbeddings

# ==== 환경 ====
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")
EMBED_MODEL  = os.getenv("OPENAI_EMBED_MODEL", "text-embedding-3-small")
TMP_DIR      = Path(os.getenv("RAG_TMP_DIR", "app/.tmp"))
TMP_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_TOKEN_BUDGET = 32000
TOKEN_TO_CHARS = 4
SAFETY_RATIO = 0.7

def _llm() -> ChatOpenAI:
    return ChatOpenAI(model_name=OPENAI_MODEL, temperature=0.1, timeout=60)

def _emb() -> OpenAIEmbeddings:
    return OpenAIEmbeddings(model=EMBED_MODEL)

# ==== 에러 ====
class ReportError(Exception):
    def __init__(self, code: str, message: str, http: int = 400, details: Dict[str, Any] | None = None):
        super().__init__(message)
        self.code, self.message, self.http, self.details = code, message, http, details or {}

def _error(code: str, message: str, http: int = 400, details: Dict[str, Any] | None = None) -> Dict[str, Any]:
    return {
        "status": "failed",
        "code": code,
        "message": message,
        "http": http,
        "trace_id": uuid4().hex,
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "details": details or {},
    }

# ==== 스키마 ====
KeyStr  = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[a-z][a-z0-9_]{1,32}$")]
NonEmpty = Annotated[str, StringConstraints(min_length=1)]
E_ID   = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^E\d{2,3}$")]
J_ID   = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^J\d{2,3}$")]
Score  = Annotated[int, Ge(0), Le(100)]
Weight = Annotated[int, Ge(0), Le(100)]

class Competency(BaseModel): key: KeyStr; label: NonEmpty
class ScoreItem(BaseModel): key: KeyStr; value: Score
class WeightItem(BaseModel): key: KeyStr; value: Weight
class Headline(BaseModel): summary: NonEmpty; tag: NonEmpty
class TalkSummaryItem(BaseModel): 주제: NonEmpty; 발언요약: NonEmpty
class TalkSummary(BaseModel): items: List[TalkSummaryItem] = Field(min_length=1)
class JDCoverRow(BaseModel): jid: J_ID; 요구사항: NonEmpty; 기대치: NonEmpty; 충족도: NonEmpty; 근거: List[E_ID] = Field(min_length=1)
class EvidenceRow(BaseModel): eid: E_ID; 출처: NonEmpty; 내용: NonEmpty; jid: J_ID
class ConvKV(BaseModel): k: NonEmpty; v: NonEmpty

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

# ==== 상태/파일 ====
_STORE: Dict[str, Dict] = {}
ROOT = Path(__file__).resolve().parents[1]

def _resolve(p: str) -> Path:
    p = (p or "").strip().replace("\\", "/")
    for c in [Path(p), ROOT / p, ROOT / "data" / Path(p).name, ROOT / "app" / "data" / Path(p).name]:
        if c.exists():
            return c
    return Path(p)

def _read(p: str) -> str:
    return _resolve(p).read_text(encoding="utf-8", errors="ignore").strip()

def _norm(t: str) -> str:
    return re.sub(r"\r\n?", "\n", t).strip()

# ==== 청크 ====
def _chunk_paragraph(text: str, size: int = 700, overlap: int = 120) -> List[str]:
    text = _norm(text)
    if not text:
        return []
    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: List[str] = []
    for para in paras:
        if len(para) <= size:
            chunks.append(para); continue
        sents = re.split(r"(?<=[.!?。…])\s+|\n", para); buf = ""
        for s in sents:
            if not s.strip(): continue
            cand = (buf + " " + s).strip() if buf else s.strip()
            if len(cand) <= size: buf = cand
            else:
                if buf: chunks.append(buf)
                buf = s.strip()
        if buf: chunks.append(buf)
    if not chunks:
        return []
    out: List[str] = [chunks[0]]
    for i in range(1, len(chunks)):
        out.append((chunks[i - 1][-overlap:] + " " + chunks[i]).strip())
    return out

def _chunk_log(log: str) -> List[str]:
    return [b.strip() for b in re.split(r"(?m)^(?=Q\d+)", _norm(log)) if b.strip()]

# ==== 임시 JSON 벡터스토어 ====
class JsonVecStore:
    def __init__(self, path: Path):
        self.path = path
        self.items: List[Dict[str, Any]] = []
    def load(self):
        if self.path.exists():
            self.items = json.loads(self.path.read_text(encoding="utf-8"))
        return self
    def persist(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.items, ensure_ascii=False), encoding="utf-8")
    def upsert(self, docs: List[Dict[str, Any]]):
        self.items = docs

# ==== 유사도/MMR ====
def _cos(a: List[float], b: List[float]) -> float:
    da = sum(x * x for x in a) ** 0.5 if a else 0.0
    db = sum(x * x for x in b) ** 0.5 if b else 0.0
    if da == 0.0 or db == 0.0:
        return 0.0
    return sum(x * y for x, y in zip(a, b)) / (da * db)

def _mmr(qv: List[float], cand: List[List[float]], k: int = 12, lamb: float = 0.6) -> List[int]:
    chosen: List[int] = []
    remain = list(range(len(cand)))
    sims_q = [_cos(qv, v) for v in cand]
    while remain and len(chosen) < k:
        best_i, best_score = None, float("-inf")
        for i in remain:
            div = 0.0 if not chosen else max(_cos(cand[i], cand[j]) for j in chosen)
            score = lamb * sims_q[i] - (1 - lamb) * div
            if score > best_score:
                best_i, best_score = i, score
        chosen.append(best_i)  # type: ignore[arg-type]
        remain.remove(best_i)  # type: ignore[arg-type]
    return chosen

def _budget_chars(tokens: int = DEFAULT_TOKEN_BUDGET) -> int:
    return int(tokens * TOKEN_TO_CHARS * SAFETY_RATIO)

# ==== 컨텍스트 ====
def _attach_ids(chunks: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[str], List[str]]:
    jmap: Dict[int, str] = {}; emap: Dict[int, str] = {}; j_cnt = e_cnt = 0
    for i, c in enumerate(chunks):
        if c["doc_type"] == "JD":
            j_cnt += 1; jmap[i] = f"J{j_cnt:02d}"
        else:
            e_cnt += 1; emap[i] = f"E{e_cnt:02d}"
    jd_idx = [i for i in range(len(chunks)) if i in jmap]
    for i in range(len(chunks)):
        if i in jmap: continue
        best, bs = None, -1.0
        for j in jd_idx:
            s = _cos(chunks[i]["vec"], chunks[j]["vec"])
            if s > bs: best, bs = j, s
        if best is not None:
            chunks[i]["jid"] = jmap.get(best)
    out: List[Dict[str, Any]] = []; used_e: List[str] = []; used_j: List[str] = []
    for i, c in enumerate(chunks):
        if i in jmap:
            out.append({**c, "text": f"[{jmap[i]}][JD]\n{c['text']}", "jid": jmap[i]})
            used_j.append(jmap[i])
        else:
            out.append({**c, "text": f"[{emap[i]}][{c['doc_type']}]\n{c['text']}", "eid": emap[i]})
            used_e.append(emap[i])
    return out, sorted(set(used_e)), sorted(set(used_j))

def _build_context(selected: List[Dict[str, Any]], budget: int) -> Tuple[Tuple[str, str, str], List[str], List[str]]:
    buf: List[str] = []; used = 0; eids: List[str] = []; jids: List[str] = []
    for c in selected:
        t = c["text"].strip()
        if not t: continue
        if used + len(t) + 2 > budget: break
        buf.append(t); used += len(t) + 2
        if c.get("eid"): eids.append(c["eid"])
        if c.get("jid"): jids.append(c["jid"])
    jd  = "\n\n".join([t for t in buf if "[JD]" in t])
    res = "\n\n".join([t for t in buf if "[이력서]" in t])
    log = "\n\n".join([t for t in buf if "[인터뷰로그]" in t])
    return (jd, res, log), sorted(set(eids)), sorted(set(jids))

# ==== 프롬프트/체인 ====
def _prompt() -> ChatPromptTemplate:
    return ChatPromptTemplate.from_messages([
        ("system",
         "당신은 검증 가능한 채용 평가 리포트를 만드는 전문가입니다. 입력은 [JD], [이력서], [인터뷰로그]입니다. "
         "반드시 주어진 컨텍스트에만 근거하여 'ReportOut' JSON 스키마에 맞춰 결과를 출력해야 합니다. "
         "오직 제공된 컨텍스트만 근거로 사용하고, 추측·일반론·키워드 일치(단어만 포함 됐다는 이유로 가점)금지\n\n"
         "--- 주요 원칙 ---\n"
         "1) 의미판단(NLI): JD 요구와 증거 문장의 뜻이 실제로 부합하는지로 충족/부분/부족을 결정\n"
         "2) 구조 짝맞춤: 증거에서 역할-행동-성과(예: 역할-백엔드, 행동-성능튜닝, 성과-지연 40%↓)를 뽑아 JD 항목과 최적 짝을 찾습니다.\n"
         "3) 자기일관성: 내부적으로 표현을 바꿔 최소 3회 재평가 후 합의 결과만 출력합니다(내부 계산·중간표는 출력 금지)\n"
         "4) 상대 비교 점수: 축별 강약을 서로 비교해 순위를 정하고 0~100으로 분포화합니다(특정 단어 출현 수·상한캡 사용 금지)\n"
         "5) 근거가 모호/적을수록 점수와 표현을 보수적으로 낮춥니다.\n"
         "6) 동의어/가까운 개념 고려: 스킬의 뜻이 같은/가까운 표현도 인식하되, 실제 문장의미가 일치할 떄만 인정합니다.\n"
         "7) 점수의 기준: 40점 미만-자격미달, 41~60점 - 부족함, 61~80점-보통, 80~95점- 탁월, 95점 이상 매우 탁월(내부 계산·중간표는 출력 금지)\n"
         "8) 평가 스탠스: 채용 전문가로서, 후보자의 역량을 보수적으로 평가하되 객관적이고 공평한 자세를 유지할 것\n\n"
         "9) 질문은 한 문장으로 쓰고 반드시 ‘?’로 끝낼 것. 답변은 간접화법·과거 시제 ‘~라고 하였음.’으로 마무리할 것. 명사에는 ‘~이라고 하였음’.\n\n"

         "--- 출력 규칙 ---\n"
         "1.  **axes**: 제공된 'axes_keys'를 'key'로, 자연스러운 한글 레이블을 'label'로 하여 5개의 역량 평가 축을 구성합니다.\n"
         "2.  **scores**: 'axes'의 각 역량(key)에 대해 0~100점 사이의 정수 점수를 부여합니다. 내부 합의 결과만 반영.\n"
         "3.  **weights**: 'axes'의 각 역량(key)에 대해 중요도 가중치를 부여합니다. 총 합은 반드시 100이 되어야 합니다.\n"
         "4.  **headline**: 후보자에 대한 핵심 평가를 요약합니다.\n"
         "    - `summary`: 후보자의 핵심 역량과 경험을 바탕으로, JD(직무기술서)와의 적합성을 고려하여 평가를 한 문장을 보수적으로 제시합니다. 간결하고 명확한 전문가적 어조를 사용하세요.\n"
         "    - `tag`: 후보자의 핵심 특징을 나타내는 키워드 3~5개를 쉼표로 구분하여 제시합니다.\n"
         "5.  **talkSummary**: 인터뷰 대화 내용을 구조화하여 요약합니다. '주제'는 다음을 정확히 따라야 합니다.\n"
         "    - `{{'주제': '인터뷰 요약', '발언요약': '인터뷰 전체 흐름을 2~3문장으로 요약'}}`\n"
         "    - `{{'주제': '<질문 내용, 질문을 짧게 쓸 것, 꺾쇠는 출력하지 말 것.>', '발언요약': '(라고|이라고) 하였음\.$, 답변은 1줄 요약할 것'}}` (인터뷰 질문/답변 순서대로 반복)\n"
         "    - `{{'주제': '긍정 의견', '발언요약': '컨텍스트 기반의 강점 및 우수 역량에 대한 종합 의견 (200~300자)'}}`\n"
         "    - `{{'주제': '부정 의견', '발언요약': '컨텍스트 기반의 약점 및 개선점에 대한 종합 의견 (200~300자)'}}`\n"
         "6.  **jdCoverage**: JD의 각 요구사항(`Jxx`)에 대해 후보자가 얼마나 충족하는지를 평가합니다.\n"
         "    - `jid`: JD 요구사항 ID (`J01`, `J02`, ...)\n"
         "    - `요구사항`: 해당 JD의 핵심 요구사항\n"
         "    - `기대치`: 해당 요구사항에 대한 회사의 기대 수준\n"
         "    - `충족도`: '적합', '보통', '부족' 또는 구체적인 서술로 후보자의 충족 수준을 평가\n"
         "    - `근거`: 평가의 근거가 되는 이력서/인터뷰로그 증거 ID (`Exx`) 목록\n"
         "7.  **evidence**: 리포트 작성에 사용된 모든 증거(`Exx`)의 출처와 내용을 명시합니다.\n"
         "    - `eid`: 증거 ID (`E01`, `E02`, ...)\n"
         "    - `출처`: '이력서' 또는 '인터뷰로그'\n"
         "    - `내용`: 해당 증거의 원본 텍스트 또는 요약\n"
         "    - `jid`: 해당 증거가 가장 관련 깊은 JD 요구사항 ID (`Jxx`)\n"
         "8.  **convStats**: 인터뷰의 주요 통계 정보를 `[{{'k': '항목명', 'v': '값'}}]` 형식으로 제공합니다. (예: 총 질문 수, 답변 평균 길이 등)\n"
         "9.  **근거 제시**: `jdCoverage`의 '근거' 필드와 같이, 모든 주장은 반드시 컨텍스트에 존재하는 증거 ID(`Exx`, `Jxx`)와 연결되어야 합니다."),

        ("human", "평가 축: {axes_keys}\n\n[공고]\n{jd}\n\n[이력서]\n{resume}\n\n[인터뷰로그]\n{log}")
    ])

def _verify_prompt() -> ChatPromptTemplate:
    return ChatPromptTemplate.from_messages([
        ("system",
         "당신은 '검증자'입니다. 아래 Draft(초안) 리포트의 주장을 오직 [JD],[이력서],[인터뷰로그]만으로 재평가하세요. "
         "키워드 일치 금지, 의미 일치(NLI)로 판정하고, 역할-행동-성과 구조로 짝맞춥니다. "
         "표현을 바꿔 1회 평가하되(비판적 톤), 결과는 구조화된 JSON으로만 출력하세요.\n\n"
         "출력:\n"
         "- coverage: 각 Jxx에 대해 p_satisfy/p_partial/p_unsatisfied(합≈1), verdict(충족|부분|미충족), valid_eids, reasons(2~3).\n"
         "- axis: 축별 재산정 점수(0~100)와 confidence(0~1). 상대 비교로 분포화, 근거가 약할수록 낮춤.\n"
         "- headline(선택): 근거 기반 1문장(보수적), tag 3~5개.\n"
         "- positives/negatives: 각 3~5개, 항목마다 [Exx] 인용."),
        ("human",
         "[공고]\n{jd}\n\n[이력서]\n{resume}\n\n[인터뷰로그]\n{log}\n\n[Draft]\n{draft_json}")
    ])

def _chain():
    return _prompt() | _llm().with_structured_output(ReportOut)

def verify_report(jd: str, resume: str, log: str, draft: Dict[str, Any], attempts: int = 3) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    - Draft(ReportOut 형식)를 의미 기반으로 재검증하고 점수/커버리지/헤드라인을 보정.
    - 하드 규칙/상한캡 없이 확률+합의(자기일관성)로만 집계.
    - 반환: (refined_report, meta)
    """
    # 표준화
    obj = json.loads(json.dumps(draft, ensure_ascii=False))
    if isinstance(obj.get("scores"), list):
        obj["scores"] = {x["key"]: x["value"] for x in obj["scores"]}
    if isinstance(obj.get("weights"), list):
        obj["weights"] = {x["key"]: x["value"] for x in obj["weights"]}

    chain = _verify_prompt() | _llm().with_structured_output(VerifyLLMOut)

    # 다회 재평가(자기일관성)
    outs: List[VerifyLLMOut] = []
    styles = ["비판적 재평가", "중립적 재평가", "신중한 재평가"]
    for i in range(min(attempts, 3)):
        out = chain.invoke({
            "jd": jd, "resume": resume, "log": log,
            "draft_json": json.dumps(obj, ensure_ascii=False)
        })
        outs.append(out)

    # 커버리지 확률 집계
    cov_map: Dict[str, Dict[str, Any]] = {}
    for out in outs:
        for c in out.coverage:
            m = cov_map.setdefault(c.jid, {"p_sat": [], "p_par": [], "p_uns": [], "valid_eids": []})
            m["p_sat"].append(c.p_satisfy)
            m["p_par"].append(c.p_partial)
            m["p_uns"].append(c.p_unsatisfied)
            m["valid_eids"].extend(c.valid_eids)

    # 평균 확률로 최종 판정
    jd_rows = obj.get("jdCoverage", [])
    row_by_jid = {r.get("jid"): r for r in jd_rows}
    consensus = []
    for jid, m in cov_map.items():
        ps, pp, pu = sum(m["p_sat"]) / len(m["p_sat"]), sum(m["p_par"]) / len(m["p_par"]), sum(m["p_uns"]) / len(m["p_uns"])
        verdict = "충족" if ps >= max(pp, pu) else ("부분" if pp >= pu else "미충족")
        r = row_by_jid.get(jid)
        if r:
            r["충족여부"] = verdict
            # 근거 EID 교차(검증자가 인정한 것만 유지)
            valid = sorted(set(m["valid_eids"]))
            if valid:
                r["근거"] = [e for e in r.get("근거", []) if e in valid]
        consensus.append(max(ps, pp, pu))

    # 축 점수 재산정(상대 비교)
    axis_scores: Dict[str, List[float]] = {}
    axis_conf: Dict[str, List[float]] = {}
    for out in outs:
        for a in out.axis:
            axis_scores.setdefault(a.key, []).append(a.score)
            axis_conf.setdefault(a.key, []).append(a.confidence)
    for k, lst in axis_scores.items():
        obj["scores"][k] = int(round(sum(lst) / len(lst)))
    # 신뢰도 요약을 convStats에 기록(스키마 외 확장)
    conf_stats = [{"k": f"axis_conf_{k}", "v": f"{sum(axis_conf[k])/len(axis_conf[k]):.2f}"} for k in axis_conf]
    consensus_mean = sum(consensus) / len(consensus) if consensus else 0.0
    obj.setdefault("convStats", [])
    obj["convStats"].append({"k": "verify_consensus", "v": f"{consensus_mean:.2f}"})
    obj["convStats"].extend(conf_stats)

    # 헤드라인/의견 보수화(검증자가 제시하면 교체)
    best_headline = None
    pos_pool, neg_pool = [], []
    for out in outs:
        if out.headline:
            best_headline = out.headline
        if out.positives:
            pos_pool.extend(out.positives)
        if out.negatives:
            neg_pool.extend(out.negatives)
    if best_headline:
        obj["headline"] = best_headline.model_dump(mode="json")
    # 의견 반영(요약 항목에 녹여 넣기)
    if pos_pool or neg_pool:
        items = obj.get("talkSummary", {}).get("items", [])
        # 기존 긍/부정 교체
        items = [it for it in items if it.get("주제") not in ("긍정 의견", "부정 의견")]
        if pos_pool:
            items.append({"주제": "긍정 의견", "발언요약": "; ".join(pos_pool[:5])})
        if neg_pool:
            items.append({"주제": "부정 의견", "발언요약": "; ".join(neg_pool[:5])})
        obj["talkSummary"] = {"items": items}

    # 스키마 재검증(예외 시 원본 유지)
    try:
        _validate_schema(obj, [a["key"] if isinstance(a, dict) else a.key for a in obj["axes"]])
    except Exception:
        pass

    meta = {
        "attempts": len(outs),
        "consensus": round(consensus_mean, 3),
        "axis_conf": {k: round(sum(v)/len(v), 3) for k, v in axis_conf.items()}
    }
    return obj, meta

# ==== 검증 ====
def _validate_schema(obj: Dict, axes_keys: List[str]) -> None:
    # 1차: 모델 검증
    v = ReportOut(**{
        "axes": obj["axes"],
        "scores": [{"key": k, "value": v} for k, v in obj["scores"].items()],
        "weights": [{"key": k, "value": v} for k, v in obj["weights"].items()],
        "headline": obj["headline"],
        "talkSummary": obj["talkSummary"],
        "convStats": obj.get("convStats", []),
        "jdCoverage": obj["jdCoverage"],
        "evidence": obj["evidence"],
    })
    # 2차: 추가 정합성
    axes_set = {a.key for a in v.axes}
    if sorted(axes_set) != sorted(axes_keys):
        raise ReportError("AXES_KEYS", "axes keys mismatch", details={"axes": sorted(axes_set), "expect": axes_keys})
    score_keys = {s.key for s in v.scores}
    weight_keys = {w.key for w in v.weights}
    if score_keys != axes_set:
        raise ReportError("SCORES_KEYS", "scores keys mismatch", details={"scores": sorted(score_keys)})
    if weight_keys != axes_set:
        raise ReportError("WEIGHTS_KEYS", "weights keys mismatch", details={"weights": sorted(weight_keys)})
    wsum = sum(w.value for w in v.weights)
    if wsum != 100:
        raise ReportError("WEIGHT_SUM", "weights sum != 100", details={"sum": wsum})

def _validate_grounding(obj: Dict, allowed_eids: List[str], allowed_jids: List[str]) -> None:
    eids, jids = set(allowed_eids), set(allowed_jids)
    for row in obj.get("jdCoverage", []):
        jid = row.get("jid")
        if jid not in jids:
            raise ReportError("JID_UNKNOWN", f"unknown JID {jid}")
        for e in row.get("근거", []):
            if e not in eids:
                raise ReportError("EID_UNKNOWN", f"unknown EID {e}")
    for ev in obj.get("evidence", []):
        if ev.get("jid") not in jids:
            raise ReportError("JID_UNKNOWN", f"unknown JID {ev.get('jid')}")
        if ev.get("eid") not in eids:
            raise ReportError("EID_UNKNOWN", f"unknown EID {ev.get('eid')}")

# ==== RAG ====
def _hash_sources(resume_txt: str, jd_txt: str, log_txt: str) -> str:
    h = hashlib.sha1()
    for x in (resume_txt, jd_txt, log_txt):
        h.update(x.encode())
    return h.hexdigest()[:10]

def _build_index(resume_txt: str, jd_txt: str, log_txt: str) -> List[Dict[str, Any]]:
    emb = _emb()
    docs: List[Dict[str, Any]] = []
    for t in _chunk_paragraph(jd_txt): docs.append({"doc_type": "JD", "text": t})
    for t in _chunk_paragraph(resume_txt): docs.append({"doc_type": "이력서", "text": t})
    for t in _chunk_log(log_txt): docs.append({"doc_type": "인터뷰로그", "text": t})
    if not docs:
        return []
    vecs = emb.embed_documents([d["text"] for d in docs])
    for d, v in zip(docs, vecs): d["vec"] = v
    JsonVecStore(TMP_DIR / f"rag_{_hash_sources(resume_txt, jd_txt, log_txt)}.json").upsert(docs)
    return docs

def _retrieve(resume_txt: str, jd_txt: str, log_txt: str, axes_keys: List[str], top_k: int = 18
) -> Tuple[Tuple[str, str, str], List[str], List[str], Dict[str, Any]]:
    docs = _build_index(resume_txt, jd_txt, log_txt)
    emb = _emb()
    q = " / ".join(axes_keys + ["JD 요구사항 충족도", "전반 요약", "핵심 답변"])
    qv = emb.embed_query(q)
    type_w = {"JD": 1.0, "이력서": 0.9, "인터뷰로그": 1.1}
    sims = [_cos(qv, d["vec"]) * type_w.get(d["doc_type"], 1.0) for d in docs]
    max_sim = max(sims) if sims else 0.0
    if max_sim < 0.18:
        raise ReportError("RETRIEVE_LOW_CONF", "검색 신뢰도가 기준 미달입니다.", details={"max_sim": round(max_sim, 4)})
    order = _mmr(qv, [d["vec"] for d in docs], k=min(top_k, len(docs)))
    selected = [docs[i] for i in order]
    selected, eids, jids = _attach_ids(selected)
    (jd, res, log), used_e, used_j = _build_context(selected, _budget_chars(DEFAULT_TOKEN_BUDGET))
    if not any([jd, res, log]):
        raise ReportError("RAG_EMPTY_CONTEXT", "컨텍스트가 비어 있습니다.")
    return (jd, res, log), used_e, used_j, {
        "max_sim": round(max_sim, 4),
        "selected": len(selected),
        "ctx_chars": {"jd": len(jd), "resume": len(res), "log": len(log)}
    }

# ==== 공개 API ====
def create_report_from_files(resume_path: str, jd_path: str, log_path: str, axes_keys: List[str]) -> Dict[str, Any]:
    try:
        resume_txt, jd_txt, log_txt = _read(resume_path), _read(jd_path), _read(log_path)
        if min(len(resume_txt), len(jd_txt)) == 0:
            raise ReportError("EMPTY_INPUT", "이력서/공고 중 하나가 비어 있습니다.")
        (jd, resume, log), eids, jids, rinfo = _retrieve(resume_txt, jd_txt, log_txt, axes_keys)

        # 1) 생성
        result: ReportOut = _chain().invoke({
            "resume": resume, "jd": jd, "log": log, "axes_keys": ", ".join(axes_keys)
        })
        obj = result.model_dump(mode="json")
        obj["scores"]  = {x["key"]: x["value"] for x in obj["scores"]}
        obj["weights"] = {x["key"]: x["value"] for x in obj["weights"]}

        # 1-1) talkSummary 필수 항목 확인
        items = obj.get("talkSummary", {}).get("items", [])
        need = {"인터뷰 요약", "긍정 의견", "부정 의견"}
        have = {it.get("주제", "") for it in items}
        if not need.issubset(have):
            raise ReportError("STRUCT_MISSING", "talkSummary 필수 항목 누락")

        # 2) 스키마·근거 검증
        _validate_schema(obj, axes_keys)
        _validate_grounding(obj, eids, jids)

        # 3) 저장
        rid = uuid4().hex
        obj.update({"status": "ok", "report_id": rid, "created_at": datetime.utcnow().isoformat() + "Z"})
        _STORE[rid] = obj
        return obj

    except ReportError as e:
        return _error(e.code, e.message, http=e.http, details=e.details)
    except ValidationError as e:
        return _error("SCHEMA_MISMATCH", "출력 검증 실패", http=400, details={"why": str(e)})
    except Exception as e:
        return _error("INTERNAL", f"내부 오류: {type(e).__name__}", http=500, details={"why": str(e)})

def validate_and_save(report_json: Dict) -> Dict:
    if isinstance(report_json.get("scores"), dict):
        report_json["scores"] = [{"key": k, "value": v} for k, v in report_json["scores"].items()]
    if isinstance(report_json.get("weights"), dict):
        report_json["weights"] = [{"key": k, "value": v} for k, v in report_json["weights"].items()]
    obj = ReportOut(**report_json).model_dump(mode="json")
    obj["scores"]  = {x["key"]: x["value"] for x in obj["scores"]}
    obj["weights"] = {x["key"]: x["value"] for x in obj["weights"]}
    rid = uuid4().hex
    obj.update({"status": "ok", "report_id": rid, "created_at": datetime.utcnow().isoformat() + "Z"})
    _STORE[rid] = obj
    return obj

def get_report(report_id: str) -> Optional[Dict]:
    return _STORE.get(report_id)

def apply_feedback(rid: str, patch: Dict) -> Optional[Dict]:
    rpt = _STORE.get(rid)
    if not rpt or rpt.get("status") != "ok":
        return None
    for k in ("scores", "weights", "headline"):
        if isinstance(patch.get(k), dict):
            rpt[k].update(patch[k])
    if isinstance(patch.get("talkSummary"), dict):
        rpt["talkSummary"] = patch["talkSummary"]
    if isinstance(patch.get("convStats"), list):
        rpt["convStats"] = patch["convStats"]
    # 최종 유효성
    ReportOut(**{
        "axes": rpt["axes"],
        "scores": [{"key": k, "value": v} for k, v in rpt["scores"].items()],
        "weights": [{"key": k, "value": v} for k, v in rpt["weights"].items()],
        "headline": rpt["headline"],
        "talkSummary": rpt["talkSummary"],
        "convStats": rpt.get("convStats", []),
        "jdCoverage": rpt["jdCoverage"],
        "evidence": rpt["evidence"],
    })
    return rpt
