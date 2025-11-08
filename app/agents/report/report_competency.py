from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Tuple

from langchain_core.prompts import ChatPromptTemplate

from app.agents.report import (
    DEFAULT_TOKEN_BUDGET,
    SAFETY_RATIO,
    TOKEN_TO_CHARS,
    TMP_DIR,
    ReportError,
    ReportOut,
    get_embeddings,
    get_llm,
)
from app.agents.report.TEMP_report_db import retrieve_via_local_index

"""
보고서 초안 생성을 위한 검색(RAG) + LLM 노드.
- JD/이력서/인터뷰 로그를 분할하고 임베딩하여 MMR로 선별합니다.
- 선택된 컨텍스트만으로 ReportOut 스키마 형태의 초안을 생성합니다.
"""

DOC_JD = "JD"
DOC_RESUME = "RESUME"
DOC_LOG = "INTERVIEW_LOG"


@dataclass
class RetrievalResult:
    context: Tuple[str, str, str]
    evidence_ids: List[str]
    jd_ids: List[str]
    stats: Dict[str, Any]


def _normalize(text: str) -> str:
    return re.sub(r"\r\n?", "\n", (text or "").strip())


def _chunk_paragraph(text: str, size: int = 700, overlap: int = 120) -> List[str]:
    # 로컬 사용이 남아있을 수 있어 남겨두지만, 실제 분할/임베딩은 TEMP_report_db에서 수행합니다.
    text = _normalize(text)
    if not text:
        return []
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: List[str] = []
    for para in paragraphs:
        if len(para) <= size:
            chunks.append(para)
            continue
        sentences = re.split(r"(?<=[.!?])\s+|\n", para)
        buffer = ""
        for sent in sentences:
            if not sent.strip():
                continue
            candidate = f"{buffer} {sent}".strip() if buffer else sent.strip()
            if len(candidate) <= size:
                buffer = candidate
            else:
                if buffer:
                    chunks.append(buffer)
                buffer = sent.strip()
        if buffer:
            chunks.append(buffer)
    if not chunks:
        return []
    stitched: List[str] = [chunks[0]]
    for idx in range(1, len(chunks)):
        stitched.append((chunks[idx - 1][-overlap:] + " " + chunks[idx]).strip())
    return stitched


def _chunk_log(text: str) -> List[str]:
    return [block.strip() for block in re.split(r"(?m)^(?=Q\d+)", _normalize(text)) if block.strip()]


def _cosine(a: List[float], b: List[float]) -> float:
    da = sum(x * x for x in a) ** 0.5 if a else 0.0
    db = sum(x * x for x in b) ** 0.5 if b else 0.0
    if da == 0.0 or db == 0.0:
        return 0.0
    return sum(x * y for x, y in zip(a, b)) / (da * db)


def _mmr(query_vec: List[float], candidates: List[List[float]], k: int = 12, lamb: float = 0.6) -> List[int]:
    selected: List[int] = []
    remaining = list(range(len(candidates)))
    sims = [_cosine(query_vec, vec) for vec in candidates]
    while remaining and len(selected) < k:
        best_idx, best_score = None, float("-inf")
        for idx in remaining:
            diversity = 0.0 if not selected else max(_cosine(candidates[idx], candidates[s]) for s in selected)
            score = lamb * sims[idx] - (1 - lamb) * diversity
            if score > best_score:
                best_idx, best_score = idx, score
        if best_idx is None:
            break
        selected.append(best_idx)
        remaining.remove(best_idx)
    return selected


class JsonVecStore:
    """
    (이 모듈 내에서는 사용하지 않도록 이동 예정. TEMP_report_db가 담당)
    """
    def __init__(self, path: Path) -> None:
        self.path = path
        self.items: List[Dict[str, Any]] = []
    def upsert(self, docs: List[Dict[str, Any]]) -> None:
        self.items = docs
    def persist(self) -> None:
        pass


def _attach_ids(chunks: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[str], List[str]]:
    jd_map: Dict[int, str] = {}
    evidence_map: Dict[int, str] = {}
    jd_count = evidence_count = 0
    for idx, chunk in enumerate(chunks):
        if chunk["doc_type"] == DOC_JD:
            jd_count += 1
            jd_map[idx] = f"J{jd_count:02d}"
        else:
            evidence_count += 1
            evidence_map[idx] = f"E{evidence_count:02d}"
    jd_indices = list(jd_map.keys())
    for idx in range(len(chunks)):
        if idx in jd_map:
            continue
        best = None
        best_score = -1.0
        for jd_idx in jd_indices:
            score = _cosine(chunks[idx]["vec"], chunks[jd_idx]["vec"])
            if score > best_score:
                best = jd_idx
                best_score = score
        if best is not None:
            chunks[idx]["jid"] = jd_map.get(best)
    labeled: List[Dict[str, Any]] = []
    evidence_ids: List[str] = []
    jd_ids: List[str] = []
    for idx, chunk in enumerate(chunks):
        if idx in jd_map:
            jid = jd_map[idx]
            labeled.append({**chunk, "text": f"[{jid}][JD]\n{chunk['text']}", "jid": jid})
            jd_ids.append(jid)
        else:
            eid = evidence_map[idx]
            labeled.append({**chunk, "text": f"[{eid}][{chunk['doc_type']}]\n{chunk['text']}", "eid": eid})
            evidence_ids.append(eid)
    return labeled, sorted(set(evidence_ids)), sorted(set(jd_ids))


def _budget_chars(tokens: int = DEFAULT_TOKEN_BUDGET) -> int:
    return int(tokens * TOKEN_TO_CHARS * SAFETY_RATIO)


def _build_context(chunks: List[Dict[str, Any]], budget: int) -> Tuple[Tuple[str, str, str], List[str], List[str]]:
    buffer: List[str] = []
    used_chars = 0
    evidence_ids: List[str] = []
    jd_ids: List[str] = []
    for chunk in chunks:
        text = chunk["text"].strip()
        if not text:
            continue
        pending = len(text) + 2
        if used_chars + pending > budget:
            break
        buffer.append(text)
        used_chars += pending
        if chunk.get("eid"):
            evidence_ids.append(chunk["eid"])
        if chunk.get("jid"):
            jd_ids.append(chunk["jid"])
    jd_text = "\n\n".join([t for t in buffer if "[JD]" in t])
    resume_text = "\n\n".join([t for t in buffer if "[RESUME]" in t])
    log_text = "\n\n".join([t for t in buffer if "[INTERVIEW_LOG]" in t])
    return (jd_text, resume_text, log_text), sorted(set(evidence_ids)), sorted(set(jd_ids))


def _hash_sources(resume_txt: str, jd_txt: str, log_txt: str) -> str:
    import hashlib

    sha = hashlib.sha1()
    for chunk in (resume_txt, jd_txt, log_txt):
        sha.update(chunk.encode("utf-8", errors="ignore"))
    return sha.hexdigest()[:10]


def _build_index(resume_txt: str, jd_txt: str, log_txt: str) -> List[Dict[str, Any]]:
    # 유지 호환용: 직접 인덱스가 필요하면 TEMP_report_db 사용
    docs, _stats = retrieve_via_local_index(resume_txt, jd_txt, log_txt, axes_keys=[])  # axes_keys 미사용
    return docs


def build_retrieval_context(
    resume_txt: str,
    jd_txt: str,
    log_txt: str,
    axes_keys: List[str],
    *,
    top_k: int = 18,
) -> RetrievalResult:
    selected, base_stats = retrieve_via_local_index(resume_txt, jd_txt, log_txt, axes_keys, top_k=top_k)
    if not selected:
        raise ReportError("RAG_EMPTY_INPUT", "입력 문서가 비어 있습니다.", http=400)
    selected, _, _ = _attach_ids(selected)
    (jd_ctx, resume_ctx, log_ctx), used_eids, used_jids = _build_context(selected, _budget_chars())
    if not any([jd_ctx, resume_ctx, log_ctx]):
        raise ReportError("RAG_EMPTY_CONTEXT", "컨텍스트를 구성하지 못했습니다.", http=500)
    if base_stats.get("max_sim", 0.0) < 0.18:
        raise ReportError("RETRIEVE_LOW_CONF", "검색 신뢰도가 낮습니다.", details={"max_sim": base_stats.get("max_sim")})
    stats = {**base_stats, "ctx_chars": {"jd": len(jd_ctx), "resume": len(resume_ctx), "log": len(log_ctx)}}
    return RetrievalResult(context=(jd_ctx, resume_ctx, log_ctx), evidence_ids=used_eids, jd_ids=used_jids, stats=stats)


def _prompt() -> ChatPromptTemplate:
    return ChatPromptTemplate.from_messages(
        [
            (
                "system",
                (
                    "당신은 채용 바레이지 역할의 리뷰어입니다. 제공된 [JD], [RESUME], [INTERVIEW_LOG] 컨텍스트만을 근거로 "
                    "ReportOut 스키마에 맞춘 JSON을 작성하세요. 증거 ID는 임의로 만들지 말고 컨텍스트의 [Jxx], [Exx] 표기를 재사용합니다. "
                    "각 축은 입력으로 주어진 축 키와 정확히 대응해야 합니다. 또한 talkSummary에는 '인터뷰요약', '긍정 의견', '부정 의견' 항목을 반드시 포함하세요."
                ),
            ),
            (
                "human",
                "축 키: {axes_keys}\n\n[JD]\n{jd}\n\n[RESUME]\n{resume}\n\n[INTERVIEW_LOG]\n{log}",
            ),
        ]
    )


def generate_report_draft(context: Tuple[str, str, str], axes_keys: List[str]) -> Dict[str, Any]:
    jd_ctx, resume_ctx, log_ctx = context
    chain = _prompt() | get_llm(temperature=0.1).with_structured_output(ReportOut)
    result = chain.invoke(
        {
            "axes_keys": ", ".join(axes_keys),
            "jd": jd_ctx,
            "resume": resume_ctx,
            "log": log_ctx,
        }
    )
    obj = result.model_dump(mode="json")
    obj["scores"] = {item["key"]: item["value"] for item in obj["scores"]}
    obj["weights"] = {item["key"]: item["value"] for item in obj["weights"]}
    return obj
