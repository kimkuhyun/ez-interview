from __future__ import annotations

from typing import Any, Dict, Optional, List, Tuple

from pathlib import Path
import json
import re
import hashlib

from app.agents.report import ReportOut, TMP_DIR, get_embeddings, ReportError

"""
임시 인메모리 저장소 + 데이터 로딩/인덱싱 유틸리티
- 생성된 리포트를 메모리에 보관하고 조회/피드백 반영을 지원합니다.
- 원본 파일 로딩, 텍스트 정규화/분할, 임베딩 및 JSON 기반 임시 벡터 캐시까지 처리합니다.
  (실제 벡터 DB 연결 직전까지의 책임)
"""

_STORE: Dict[str, Dict[str, Any]] = {}


# -------------------------
# 인메모리 리포트 저장소
# -------------------------
def save(report: Dict[str, Any]) -> Dict[str, Any]:
    _STORE[report["report_id"]] = report
    return report


def get(report_id: str) -> Optional[Dict[str, Any]]:
    return _STORE.get(report_id)


def apply_feedback(report_id: str, patch: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    report = _STORE.get(report_id)
    if not report:
        return None
    for key in ("scores", "weights", "headline"):
        if isinstance(patch.get(key), dict):
            report.setdefault(key, {}).update(patch[key])
    if isinstance(patch.get("talkSummary"), dict):
        report["talkSummary"] = patch["talkSummary"]
    if isinstance(patch.get("convStats"), list):
        report["convStats"] = patch["convStats"]
    ReportOut(
        axes=report["axes"],
        scores=[{"key": k, "value": v} for k, v in report["scores"].items()],
        weights=[{"key": k, "value": v} for k, v in report["weights"].items()],
        headline=report["headline"],
        talkSummary=report["talkSummary"],
        convStats=report.get("convStats", []),
        jdCoverage=report["jdCoverage"],
        evidence=report["evidence"],
    )
    return report


# -------------------------
# 데이터 로딩/인덱싱 유틸
# -------------------------
ROOT = Path(__file__).resolve().parents[1]


def resolve_path(value: str) -> Path:
    value = (value or "").strip().replace("\\", "/")
    for cand in [
        Path(value),
        ROOT / value,
        ROOT / "data" / Path(value).name,
        ROOT / "app" / "data" / Path(value).name,
    ]:
        if cand.exists():
            return cand
    return Path(value)


def read_texts(resume_path: str, jd_path: str, log_path: str = "") -> Tuple[str, str, str]:
    """
    파일 경로를 해석하고 UTF-8로 텍스트를 읽어 반환합니다. (비어 있으면 예외)
    """
    resume_txt = resolve_path(resume_path).read_text(encoding="utf-8", errors="ignore").strip()
    jd_txt = resolve_path(jd_path).read_text(encoding="utf-8", errors="ignore").strip()
    log_txt = resolve_path(log_path).read_text(encoding="utf-8", errors="ignore").strip() if log_path else ""
    if min(len(resume_txt), len(jd_txt)) == 0:
        raise ReportError("EMPTY_INPUT", "이력서 혹은 공고가 비어 있습니다.")
    return resume_txt, jd_txt, log_txt


def _normalize(text: str) -> str:
    return re.sub(r"\r\n?", "\n", (text or "").strip())


def _chunk_paragraph(text: str, size: int = 700, overlap: int = 120) -> List[str]:
    text = _normalize(text)
    if not text:
        return []
    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: List[str] = []
    for para in paras:
        if len(para) <= size:
            chunks.append(para)
            continue
        import re as _re

        sents = _re.split(r"(?<=[.!?])\s+|\n", para)
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
    out: List[str] = [chunks[0]]
    for i in range(1, len(chunks)):
        out.append((chunks[i - 1][-overlap:] + " " + chunks[i]).strip())
    return out


def _chunk_log(text: str) -> List[str]:
    return [b.strip() for b in re.split(r"(?m)^(?=Q\d+)", _normalize(text)) if b.strip()]


class JsonVecStore:
    """
    간단한 JSON 스토어(디버깅/검수용)로, 임베딩된 문서를 파일로 캐싱합니다.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.items: List[Dict[str, Any]] = []

    def upsert(self, docs: List[Dict[str, Any]]) -> None:
        self.items = docs
        self.persist()

    def persist(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.items, ensure_ascii=False), encoding="utf-8")


def _hash_sources(resume_txt: str, jd_txt: str, log_txt: str) -> str:
    h = hashlib.sha1()
    for x in (resume_txt, jd_txt, log_txt):
        h.update(x.encode("utf-8", errors="ignore"))
    return h.hexdigest()[:10]


def build_index_from_texts(resume_txt: str, jd_txt: str, log_txt: str) -> List[Dict[str, Any]]:
    """
    텍스트를 문단/블록으로 분할한 뒤 임베딩하여 {doc_type,text,vec} 형태로 반환하고
    TMP_DIR에 JSON으로 캐시합니다. (벡터 DB 연결 전 단계)
    """
    docs: List[Dict[str, Any]] = []
    for t in _chunk_paragraph(jd_txt):
        docs.append({"doc_type": "JD", "text": t})
    for t in _chunk_paragraph(resume_txt):
        docs.append({"doc_type": "RESUME", "text": t})
    for t in _chunk_log(log_txt):
        docs.append({"doc_type": "INTERVIEW_LOG", "text": t})
    if not docs:
        return []
    emb = get_embeddings()
    vecs = emb.embed_documents([d["text"] for d in docs])
    for d, v in zip(docs, vecs):
        d["vec"] = v
    JsonVecStore(TMP_DIR / f"rag_{_hash_sources(resume_txt, jd_txt, log_txt)}.json").upsert(docs)
    return docs


def load_and_index(resume_path: str, jd_path: str, log_path: str = "") -> Tuple[List[Dict[str, Any]], Tuple[str, str, str]]:
    """
    파일을 읽어 텍스트를 얻고, 분할/임베딩까지 수행하여 문서 리스트와 원문 텍스트를 함께 반환합니다.
    """
    resume_txt, jd_txt, log_txt = read_texts(resume_path, jd_path, log_path)
    docs = build_index_from_texts(resume_txt, jd_txt, log_txt)
    return docs, (resume_txt, jd_txt, log_txt)


# -------------------------
# 로컬 인덱스 질의(RAG) 유틸
# -------------------------
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
        if best_i is None:
            break
        chosen.append(best_i)
        remain.remove(best_i)
    return chosen


def _embed_query_from_axes(axes_keys: List[str]) -> Tuple[str, List[float]]:
    query = " / ".join(axes_keys + ["JD 요구사항 충족", "핵심 역량", "강점 요약"])
    qv = get_embeddings().embed_query(query)
    return query, qv


def query_local_index(
    docs: List[Dict[str, Any]],
    axes_keys: List[str],
    *,
    top_k: int = 18,
    type_weights: Optional[Dict[str, float]] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    임시 인덱스(docs)에 대해 쿼리 임베딩+MMR로 상위 문서를 선별합니다.
    반환: (선택 문서 리스트, 통계)
    """
    if not docs:
        return [], {"max_sim": 0.0, "selected": 0, "ctx_chars": {}}
    query, qv = _embed_query_from_axes(axes_keys)
    tw = type_weights or {"JD": 1.0, "RESUME": 0.9, "INTERVIEW_LOG": 1.1}
    sims = [_cos(qv, d.get("vec") or []) * tw.get(d.get("doc_type", ""), 1.0) for d in docs]
    max_sim = max(sims) if sims else 0.0
    order = _mmr(qv, [d.get("vec") or [] for d in docs], k=min(top_k, len(docs)))
    selected = [docs[i] for i in order]
    stats = {"max_sim": round(max_sim, 4), "selected": len(selected), "query": query}
    return selected, stats


def retrieve_via_local_index(
    resume_txt: str,
    jd_txt: str,
    log_txt: str,
    axes_keys: List[str],
    *,
    top_k: int = 18,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    텍스트를 인덱싱한 뒤, 로컬 인덱스에서 쿼리 임베딩+MMR로 선별합니다.
    이후 벡터DB 전환 시 이 함수만 교체하면 됩니다.
    """
    docs = build_index_from_texts(resume_txt, jd_txt, log_txt)
    return query_local_index(docs, axes_keys, top_k=top_k)
