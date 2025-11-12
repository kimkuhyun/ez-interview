from app.db.db_connection import get_connection
from app.utils.embedding import get_embedding

def search_similar_chunks(query: str, session_id: str = None, top_k=3):
    print(f"\n📚 [RAG Retriever] 검색 시작")
    print(f"   - Query: {query[:100]}...")
    print(f"   - Session ID: {session_id}")
    print(f"   - Top-K: {top_k}")
    
    try:
        # 1️⃣ Query 임베딩 생성
        print(f"   1️⃣ Query 임베딩 생성 중...")
        emb = get_embedding(query)
        print(f"      ✅ 임베딩 생성 완료: 길이={len(emb)}")
        
        # 2️⃣ DB 연결
        print(f"   2️⃣ DB 연결 중...")
        conn = get_connection()
        cur = conn.cursor()
        print(f"      ✅ DB 연결 완료")
        
        # 3️⃣ 유사도 검색 쿼리 실행
        print(f"   3️⃣ 유사도 검색 쿼리 실행 중...")
        
        # session_id가 있으면 해당 세션의 문서만 검색
        if session_id:
            cur.execute("""
                SELECT content, doc_type, 1 - (embedding <=> %s::vector) AS score
                FROM rag.documents
                WHERE session_id = %s
                ORDER BY embedding <-> %s::vector
                LIMIT %s;
            """, (emb, session_id, emb, top_k))
        else:
            # session_id 없으면 전체 검색
            cur.execute("""
                SELECT content, doc_type, 1 - (embedding <=> %s::vector) AS score
                FROM rag.documents
                ORDER BY embedding <-> %s::vector
                LIMIT %s;
            """, (emb, emb, top_k))
        
        results = cur.fetchall()
        print(f"      ✅ 검색 완료: {len(results)}개 결과 반환")
        
        # 4️⃣ 결과 출력
        if results:
            for idx, (content, doc_type, score) in enumerate(results):
                print(f"      [{idx+1}] doc_type={doc_type}, score={score:.4f}, content_len={len(content)}")
        else:
            print(f"      ⚠️  검색 결과가 없습니다!")
        
        cur.close()
        conn.close()
        print(f"   ✅ [RAG Retriever] 검색 완료\n")
        
        return results
        
    except Exception as e:
        print(f"   ❌ [RAG Retriever] 검색 실패: {e}")
        import traceback
        traceback.print_exc()
        return []
