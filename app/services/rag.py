"""문서 청킹 및 벡터 검색 유틸리티."""

from typing import List, Optional, Dict
from langchain_core.retrievers import BaseRetriever
from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain_community.vectorstores import FAISS
import re

__all__ = ["chunk_text", "build_store", "get_retriever", "index_documents"]

# 로컬 임베딩 고정
_EMB = HuggingFaceEmbeddings(
    model_name="sentence-transformers/all-MiniLM-L6-v2",
    model_kwargs={"device": "cpu"}
)

def chunk_text(text: str, size: int = 700, overlap: int = 120) -> List[str]:
    """문서를 size 단위의 중첩 청크로 분할해 반환."""
    text = re.sub(r"\r\n?", "\n", text).strip()
    if not text:
        return []
    
    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: List[str] = []
    
    for para in paras:
        if len(para) <= size:
            chunks.append(para)
            continue
        sents = re.split(r"(?<=[.!?。…])\s+|\n", para)
        buf = ""
        for s in sents:
            cand = (buf + " " + s).strip() if buf else s
            if len(cand) <= size:
                buf = cand
            else:
                if buf: chunks.append(buf)
                buf = (chunks[-1][-overlap:] + " " + s) if (overlap and chunks) else s
        if buf: chunks.append(buf)
    return chunks

def build_store(chunks: List[str]) -> FAISS:
    """청크 리스트로 FAISS 벡터 스토어를 생성합니다."""
    if not chunks:
        raise ValueError("청크 리스트가 비어있습니다")
    return FAISS.from_texts(texts=chunks, embedding=_EMB)

def get_retriever(vs: FAISS, k: int = 5) -> BaseRetriever:
    """FAISS로부터 검색기(retriever)를 반환합니다."""
    if not isinstance(vs, FAISS):
        raise TypeError("벡터 스토어는 FAISS 인스턴스여야 합니다")
    return vs.as_retriever(search_kwargs={"k": k})


def index_documents(resume_text: Optional[str], jd_text: Optional[str]) -> Dict[str, Optional[FAISS]]:
    """주어진 이력서/직무기술서 텍스트로부터 필요한 벡터 스토어들을 생성하여 반환합니다.

    반환값은 각 문서 종류별로 생성된 FAISS 인스턴스를 포함하는 딕셔너리입니다.
    키: 'resume', 'jd', 'combined'
    """
    stores: Dict[str, Optional[FAISS]] = {"resume": None, "jd": None, "combined": None}

    if resume_text:
        stores["resume"] = build_store(chunk_text(resume_text))

    if jd_text:
        stores["jd"] = build_store(chunk_text(jd_text))

    if resume_text and jd_text:
        combined = "\n\n".join([resume_text, jd_text])
        stores["combined"] = build_store(chunk_text(combined))

    if not any(stores.values()):
        raise ValueError("인덱싱할 텍스트가 없습니다")

    return stores
