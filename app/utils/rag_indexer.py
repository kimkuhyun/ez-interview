from app.db.db_connection import get_connection
from app.utils.embedding import get_embedding, get_embeddings_batch
from app.utils.text_splitter import split_text
import uuid
import time

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
    total_start = time.time()
    
    conn = get_connection()
    cur = conn.cursor()
    
    # 새로운 문서 UUID 생성 (doc_id)
    doc_id = str(uuid.uuid4())
    print(f"   📝 생성된 doc_id: {doc_id}")

    # ⏱️ 1단계: 텍스트 분할
    split_start = time.time()
    chunks = split_text(text)
    split_time = time.time() - split_start
    print(f"   ⏱️  [Step 1] 텍스트 분할: {split_time:.2f}초 ({len(chunks)}개 청크)")
    
    # ⏱️ 2단계: 배치 임베딩 생성
    emb_start = time.time()
    embeddings = get_embeddings_batch(chunks)
    emb_time = time.time() - emb_start
    print(f"   ⏱️  [Step 2] 배치 임베딩 생성: {emb_time:.2f}초")
    
    # ⏱️ 3단계: DB 저장
    db_start = time.time()
    for idx, (chunk, emb) in enumerate(zip(chunks, embeddings)):
        cur.execute("""
            INSERT INTO rag.documents 
            (session_id, doc_id, doc_type, file_name, chunk_index, content, embedding)
            VALUES (%s, %s, %s, %s, %s, %s, %s);
        """, (session_id, doc_id, doc_type, file_name, idx, chunk, emb))
    
    conn.commit()
    db_time = time.time() - db_start
    print(f"   ⏱️  [Step 3] DB 저장: {db_time:.2f}초 ({len(chunks)}개 행)")
    
    cur.close()
    conn.close()
    
    total_time = time.time() - total_start
    print(f"   ✅ {doc_type} 임베딩 저장 완료: {len(chunks)}개 청크, doc_id={doc_id}")
    print(f"   📊 총 소요 시간: {total_time:.2f}초 (분할: {split_time:.2f}초 + 임베딩: {emb_time:.2f}초 + DB: {db_time:.2f}초)")
    
    return doc_id  # ✅ 문서 UUID 반환

