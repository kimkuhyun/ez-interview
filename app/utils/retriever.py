from app.db.db_connection import get_connection
from app.utils.embedding import get_embedding
from langchain_text_splitters import RecursiveCharacterTextSplitter


# ---------- 문서 Chunk 나누기 ----------
def split_text(text: str, chunk_size=500, chunk_overlap=100):
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size, chunk_overlap=chunk_overlap
    )
    return splitter.split_text(text)

# ---------- 문서 RAG 저장 ----------
def insert_resume_jd_embeddings(file_name: str, doc_type: str, text: str):
    """resume / JD 문서 chunk → 임베딩 → DB 저장"""
    conn = get_connection()
    cur = conn.cursor()

    chunks = split_text(text)
    for idx, chunk in enumerate(chunks):
        emb = get_embedding(chunk)
        cur.execute(
            """
            INSERT INTO rag.resume_jd_docs (doc_type, file_name, chunk_index, content, embedding)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (doc_type, file_name, idx, chunk, emb),
        )

    conn.commit()
    cur.close()
    conn.close()
    print(f"✅ {file_name} ({doc_type}) 저장 완료 ({len(chunks)} chunks)")

# ---------- 문서 검색 ----------
def search_similar_chunks(query: str, top_k=3):
    emb = get_embedding(query)
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT content, doc_type, 1 - (embedding <=> %s::vector) AS score
        FROM rag.resume_jd_docs
        ORDER BY embedding <-> %s::vector
        LIMIT %s;
        """,
        (emb, emb, top_k),
    )

    results = cur.fetchall()
    cur.close()
    conn.close()
    return results
