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

DEFAULT_TOKEN_BUDGET = 8000
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

class JudgeOut(BaseModel):
    score: float = Field(ge=0.0, le=1.0)
    reasons: List[str] = Field(default_factory=list)

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
         "당신은 [JD], [이력서], [인터뷰로그] 내용을 종합하여 평가 리포트를 생성하는 AI 전문가입니다. "
         "반드시 주어진 컨텍스트에만 근거하여 'ReportOut' JSON 스키마에 맞춰 결과를 출력해야 합니다. "
         "절대로 컨텍스트에 없는 내용을 추측하거나 과장해서는 안 됩니다.\n\n"
         "--- 출력 규칙 ---\n"
         "1.  **axes**: 제공된 'axes_keys'를 'key'로, 자연스러운 한글 레이블을 'label'로 하여 5개의 역량 평가 축을 구성합니다.\n"
         "2.  **scores**: 'axes'의 각 역량(key)에 대해 0~100점 사이의 정수 점수를 부여합니다.\n"
         "3.  **weights**: 'axes'의 각 역량(key)에 대해 중요도 가중치를 부여합니다. 총 합은 반드시 100이 되어야 합니다.\n"
         "4.  **headline**: 후보자에 대한 핵심 평가를 요약합니다.\n"
         "    - `summary`: 후보자의 핵심 역량과 경험을 바탕으로, JD(직무기술서)와의 적합성을 고려하여 평가를 한 문장으로 제시합니다. 간결하고 명확한 전문가적 어조를 사용하세요.\n"
         "    - `tag`: 후보자의 핵심 특징을 나타내는 키워드 3~5개를 쉼표로 구분하여 제시합니다.\n"
         "5.  **talkSummary**: 인터뷰 대화 내용을 구조화하여 요약합니다. '주제'는 다음을 정확히 따라야 합니다.\n"
         "    - `{{'주제': '인터뷰 요약', '발언요약': '인터뷰 전체 흐름을 2~3문장으로 요약'}}`\n"
         "    - `{{'주제': 'Q. [질문 내용]', '발언요약': '[해당 질문에 대한 답변 요약 1줄]'}}` (인터뷰 질문/답변 순서대로 반복)\n"
         "    - `{{'주제': '긍정 의견', '발언요약': '컨텍스트 기반의 강점 및 우수 역량에 대한 종합 의견 (200~300자)'}}`\n"
         "    - `{{'주제': '부정 의견', '발언요약': '컨텍스트 기반의 약점 및 개선점에 대한 종합 의견 (200~300자)'}}`\n"
         "6.  **jdCoverage**: JD의 각 요구사항(`Jxx`)에 대해 후보자가 얼마나 충족하는지를 평가합니다.\n"
         "    - `jid`: JD 요구사항 ID (`J01`, `J02`, ...)\n"
         "    - `요구사항`: 해당 JD의 핵심 요구사항\n"
         "    - `기대치`: 해당 요구사항에 대한 회사의 기대 수준\n"
         "    - `충족도`: '상', '중', '하' 또는 구체적인 서술로 후보자의 충족 수준을 평가\n"
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

def _judge_prompt() -> ChatPromptTemplate:
    return ChatPromptTemplate.from_messages([
        ("system", "너는 심판이다. 아래 리포트 일부가 주어진 컨텍스트와 논리적으로 일치하는지 평가한다. "
                   "평가 기준: 근거충분성, 정확성, 누락(중요 요구 미반영). 0~1 점수와 이유 2~3개만 JSON으로 반환."),
        ("human", "[공고]\n{jd}\n\n[이력서]\n{resume}\n\n[인터뷰로그]\n{log}\n\n[리포트 핵심]\n{core}")
    ])

def _chain():
    return _prompt() | _llm().with_structured_output(ReportOut)

def _judge(jd: str, resume: str, log: str, core: Dict[str, Any]) -> JudgeOut:
    prompt = _judge_prompt()
    j = prompt | _llm().with_structured_output(JudgeOut)
    return j.invoke({"jd": jd, "resume": resume, "log": log, "core": json.dumps(core, ensure_ascii=False)})

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
