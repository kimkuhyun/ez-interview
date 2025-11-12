from app.db.db_connection import get_connection
from app.utils.embedding import get_embedding
from app.utils.text_splitter import split_text
import uuid

def insert_resume_jd_embeddings(session_id: str, file_name: str, doc_type: str, text: str):
    """
    Resume/JD 임베딩을 새로운 rag.documents 테이블에 저장
    
    Args:
        session_id: 전체 서비스 세션 UUID (면접 단위)
        file_name: 파일명
        doc_type: 문서 타입 ("resume" 또는 "jd")
        text: 문서 텍스트
    
    Returns:
        doc_id: 생성된 문서 UUID (해당 doc_type의 모든 청크에 동일)
    """
    conn = get_connection()
    cur = conn.cursor()
    
    # 새로운 문서 UUID 생성 (doc_id)
    doc_id = str(uuid.uuid4())
    print(f"   📝 생성된 doc_id: {doc_id}")

    chunks = split_text(text)
    for idx, chunk in enumerate(chunks):
        emb = get_embedding(chunk)
        cur.execute("""
            INSERT INTO rag.documents 
            (session_id, doc_id, doc_type, file_name, chunk_index, content, embedding)
            VALUES (%s, %s, %s, %s, %s, %s, %s);
        """, (session_id, doc_id, doc_type, file_name, idx, chunk, emb))
    
    conn.commit()
    cur.close()
    conn.close()
    
    print(f"   ✅ {doc_type} 임베딩 저장 완료: {len(chunks)}개 청크, doc_id={doc_id}")
    return doc_id  # ✅ 문서 UUID 반환

