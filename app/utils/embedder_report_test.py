"""
임시 임베딩 유틸 - 추후 VectorDB 통합 시 삭제 예정
로컬 벡터 인덱스 구축하는 임시 함수들
"""
from typing import List, Dict, Tuple
from langchain_openai import OpenAIEmbeddings
import os


def get_embeddings_report_test() -> OpenAIEmbeddings:
    """
    임베딩 모델 가져오기 (임시)
    TODO: VectorDB 통합 시 app/utils/embedding.py의 임베딩 함수로 교체
    """
    EMBED_MODEL = os.getenv("OPENAI_EMBED_MODEL", "text-embedding-3-small")
    return OpenAIEmbeddings(model=EMBED_MODEL)


def build_index_report_test(resume_txt: str, jd_txt: str, log_txt: str,
                            chunk_paragraph_fn, chunk_log_fn) -> Tuple[List[Dict], List[Dict]]:
    """
    벡터 인덱스 구축 (임시)
    TODO: VectorDB 통합 시 app/utils/retriever.py의 검색 함수로 교체
    
    Args:
        resume_txt: 이력서 텍스트
        jd_txt: 공고 텍스트
        log_txt: 인터뷰 로그
        chunk_paragraph_fn: 단락 청킹 함수
        chunk_log_fn: 로그 청킹 함수
    
    Returns:
        (문서 청크 리스트, QA 쌍 리스트)
    """
    emb = get_embeddings_report_test()
    docs = []
    
    # JD 청킹
    for t in chunk_paragraph_fn(jd_txt, size=500):
        docs.append({"doc_type": "JD", "text": t})
    
    # 이력서 청킹
    for t in chunk_paragraph_fn(resume_txt, size=600):
        docs.append({"doc_type": "이력서", "text": t})
    
    # 인터뷰 로그 청킹 (QA 단위)
    qa_pairs = chunk_log_fn(log_txt)
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


def cos_similarity_report_test(a: List[float], b: List[float]) -> float:
    """
    코사인 유사도 계산 (임시)
    TODO: VectorDB 통합 시 삭제
    """
    da = sum(x * x for x in a) ** 0.5 if a else 0.0
    db = sum(x * x for x in b) ** 0.5 if b else 0.0
    if da == 0.0 or db == 0.0:
        return 0.0
    return sum(x * y for x, y in zip(a, b)) / (da * db)
