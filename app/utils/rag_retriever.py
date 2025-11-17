from app.db.db_connection import get_connection
from app.utils.embedding import get_embedding
from typing import Optional, Dict, List

def search_similar_chunks(query: str, session_id: str = None, doc_type: str = None, top_k=3, metadata_filter: Optional[Dict] = None):
    print(f"\n📚 [RAG Retriever] 검색 시작")
    print(f"   - Query: {query[:100]}...")
    print(f"   - Session ID: {session_id}")
    print(f"   - Doc Type: {doc_type}")
    print(f"   - Top-K: {top_k}")
    if metadata_filter:
        print(f"   - 🆕 Metadata Filter: {metadata_filter}")
    
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
        
        # 🆕 Priority 2: 메타데이터 필터링 추가
        where_conditions = []
        params_list = []
        
        if session_id:
            where_conditions.append("session_id = %s")
            params_list.append(session_id)
        
        if doc_type:
            where_conditions.append("doc_type = %s")
            params_list.append(doc_type)
        
        # 🆕 섹션 필터링 (metadata->>'section')
        if metadata_filter and "sections" in metadata_filter:
            sections = metadata_filter["sections"]
            if sections:
                section_conditions = " OR ".join([f"metadata->>'section' = '{s}'" for s in sections])
                where_conditions.append(f"({section_conditions})")
                print(f"      🆕 섹션 필터: {sections}")
        
        where_clause = " AND ".join(where_conditions) if where_conditions else "TRUE"
        
        # PostgreSQL 쿼리
        query_sql = f"""
            SELECT content, doc_type, metadata, 1 - (embedding <=> %s::vector) AS score
            FROM rag.documents
            WHERE {where_clause}
            ORDER BY embedding <-> %s::vector
            LIMIT %s;
        """
        
        # 파라미터 순서: emb, [session_id], [doc_type], emb, top_k
        final_params = [emb] + params_list + [emb, top_k]
        
        cur.execute(query_sql, final_params)
        
        results = cur.fetchall()
        print(f"      ✅ 검색 완료: {len(results)}개 결과 반환")
        
        # 4️⃣ 결과를 dict로 변환
        result_dicts = []
        if results:
            for idx, (content, dtype, metadata, score) in enumerate(results):
                print(f"      [{idx+1}] doc_type={dtype}, score={score:.4f}, content_len={len(content)}, metadata={metadata}")
                result_dicts.append({
                    "content": content,
                    "doc_type": dtype,
                    "metadata": metadata,
                    "score": score
                })
        else:
            print(f"      ⚠️  검색 결과가 없습니다!")
        
        cur.close()
        conn.close()
        print(f"   ✅ [RAG Retriever] 검색 완료\n")
        
        return result_dicts
        
    except Exception as e:
        print(f"   ❌ [RAG Retriever] 검색 실패: {e}")
        import traceback
        traceback.print_exc()
        return []
