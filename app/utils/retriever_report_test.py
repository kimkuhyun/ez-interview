"""
임시 리트리버 유틸 - 추후 VectorDB 통합 시 삭제 예정
로컬 벡터에서 검색하는 임시 함수들
"""
from typing import List, Dict


def retrieve_context_report_test(docs: List[Dict], doc_type: str) -> str:
    """
    문서 타입별 컨텍스트 추출 (임시)
    TODO: VectorDB 통합 시 app/utils/retriever.py의 검색 함수로 교체
    
    Args:
        docs: 임베딩된 문서 리스트
        doc_type: 문서 타입 ("JD", "이력서", "인터뷰로그")
    
    Returns:
        연결된 컨텍스트 문자열
    """
    filtered = [d["text"] for d in docs if d["doc_type"] == doc_type]
    return "\n\n".join(filtered)


def assign_ids_report_test(docs: List[Dict], cos_fn) -> tuple:
    """
    문서에 ID 할당 (임시 - 순차적 JID, EID)
    TODO: VectorDB 통합 시 삭제 (VectorDB에서 ID 관리)
    
    Args:
        docs: 임베딩된 문서 리스트
        cos_fn: 코사인 유사도 함수
    
    Returns:
        (ID 할당된 문서 리스트, JID 리스트, EID 리스트)
    """
    jid_counter = 0
    eid_counter = 0
    jid_list = []
    eid_list = []
    jd_indices = []
    
    # 1단계: JD에 JID 할당
    for i, doc in enumerate(docs):
        if doc["doc_type"] == "JD":
            jid_counter += 1
            jid = f"J{jid_counter:02d}"
            doc["jid"] = jid
            doc["text"] = f"[{jid}][JD]\n{doc['text']}"
            jid_list.append(jid)
            jd_indices.append(i)
    
    # 2단계: 증거에 EID 할당 및 JID 매핑
    for i, doc in enumerate(docs):
        if doc["doc_type"] == "JD":
            continue
        
        # 가장 가까운 JD 찾기
        best_jid_idx = jd_indices[0] if jd_indices else None
        best_sim = -1.0
        
        for j_idx in jd_indices:
            sim = cos_fn(doc["vec"], docs[j_idx]["vec"])
            if sim > best_sim:
                best_sim = sim
                best_jid_idx = j_idx
        
        eid_counter += 1
        eid = f"E{eid_counter:02d}"
        doc["eid"] = eid
        doc["jid"] = docs[best_jid_idx].get("jid", "J01") if best_jid_idx is not None else "J01"
        doc["text"] = f"[{eid}][{doc['doc_type']}]\n{doc['text']}"
        eid_list.append(eid)
    
    return docs, jid_list, eid_list
