"""
interview_logs 테이블 CRUD
면접 로그 저장, 조회, 검색 전용 모듈
"""
from app.db.db_connection import get_connection
from app.utils.embedding import get_embedding, get_embeddings_batch
import time
from collections import defaultdict

# 면접 로그를 interview_logs 테이블에 저장
def save_interview_logs(interview_logs: list, session_id: str = None) -> str:
    """
    면접 로그를 interview_logs 테이블에 저장
    
    Args:
        interview_logs: 면접 대화 로그 리스트
        session_id: 세션 ID (없으면 자동 생성)
    
    Returns:
        str: 저장된 세션 ID
    
    데이터 구조:
        [
          {
            "question_id": "q1",
            "followups": [
              {"role": "면접관", "content": "...", "offset_sec": 0},
              {"role": "면접자", "content": "...", "offset_sec": 12}
            ],
            "prompt_offset_sec": 0
          }
        ]
    """
    # 세션 ID 생성 * 추후 제거 예정
    if not session_id:
        session_id = f"interview_{int(time.time())}"
    
    conn = None
    cur = None
    
    try:
        conn = get_connection()
        cur = conn.cursor()
        
        # 1단계: 모든 메시지 추출
        all_messages = []
        
        for log in interview_logs:
            question_id = log.get("question_id")
            followups = log.get("followups", [])
            
            for msg_turn, msg in enumerate(followups, start=1):
                role = msg.get("role")
                content = msg.get("content", "").strip()
                offset_sec = msg.get("offset_sec")
                
                if not content:
                    continue
                
                msg_data = {
                    "session_id": session_id,
                    "question_id": question_id,
                    "role": role,
                    "msg_turn": msg_turn,
                    "content": content,
                    "offset_sec": offset_sec,
                }
                
                all_messages.append(msg_data)
        
        # 2단계: 모든 메시지 배치 임베딩 생성 (면접관 + 면접자)
        if all_messages:
            texts = [m["content"] for m in all_messages]
            embeddings = get_embeddings_batch(texts)
            
            # 임베딩을 메시지 데이터에 매핑
            for msg_data, emb in zip(all_messages, embeddings):
                msg_data["embedding"] = emb
            
            print(f"✅ 임베딩 생성 완료: {len(embeddings)}개 (면접관 + 면접자)")
        
        # 3단계: DB에 저장
        for msg_data in all_messages:
            cur.execute("""
                INSERT INTO rag.interview_logs (
                    session_id, question_id, role, msg_turn, 
                    msg, msg_embedding, offset_sec
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (
                msg_data["session_id"],
                msg_data["question_id"],
                msg_data["role"],
                msg_data["msg_turn"],
                msg_data["content"],
                msg_data["embedding"],
                msg_data["offset_sec"]
            ))
        
        conn.commit()
        
        print(f"\n{'='*80}")
        print(f"✅ 면접 로그 저장 완료")
        print(f"   Session ID: {session_id}")
        print(f"   총 메시지: {len(all_messages)}개")
        print(f"   임베딩 생성: {len(all_messages)}개 (면접관 + 면접자)")
        print(f"{'='*80}\n")
        
        return session_id
        
    except Exception as e:
        if conn:
            conn.rollback()
        print(f"❌ 면접 로그 저장 실패: {e}")
        raise
        
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


# 리포트 생성을 위한 인터뷰 로그 텍스트 추출
def retrieve_interview_context(session_id: str) -> str:
    """
    리포트 생성을 위한 인터뷰 로그 텍스트 추출
    (report_agent의 _build_context_from_vectordb에서 사용)
    
    Args:
        session_id: 세션 ID
    
    Returns:
        str: 포맷된 인터뷰 로그 텍스트
             면접관 질문(Q)과 면접자 답변(A)을 시간순으로 정렬
             
    예시:
        Q: 자기소개 부탁드립니다.
        A: 저는 3년차 백엔드 엔지니어입니다...
        
        Q: Spring Boot 경험이 있나요?
        A: 네, 2년간 Spring Boot로 REST API를 개발했습니다...
    """
    conn = None
    cur = None
    
    try:
        conn = get_connection()
        cur = conn.cursor()
        
        cur.execute("""
            SELECT question_id, role, msg, msg_turn, offset_sec
            FROM rag.interview_logs
            WHERE session_id = %s
            ORDER BY question_id, msg_turn
        """, (session_id,))
        
        rows = cur.fetchall()
        
        if not rows:
            return ""
        
        # 질문별로 그룹핑
        qa_dict = defaultdict(list)
        for row in rows:
            qid, role, msg, turn, offset = row
            qa_dict[qid].append((role, msg, turn))
        
        # 텍스트 포맷팅
        context_lines = []
        for qid in sorted(qa_dict.keys()):
            messages = qa_dict[qid]
            for role, msg, turn in messages:
                prefix = "Q" if role == "면접관" else "A"
                context_lines.append(f"{prefix}: {msg}")
            context_lines.append("")  # 질문 사이 공백
        
        return "\n".join(context_lines)
        
    except Exception as e:
        print(f"❌ 인터뷰 컨텍스트 조회 실패: {e}")
        return ""
        
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()

# 벡터 유사도 검색으로 관련 메시지 찾기
def search_similar_messages(query_text: str, session_id: str = None, 
                           role: str = None, top_k: int = 5) -> list:
    """
    벡터 유사도 검색으로 관련 메시지 찾기
    (report_agent에서 역량별 증거 찾을 때 사용)
    
    Args:
        query_text: 검색할 쿼리 (예: "문제 해결, 디버깅, 성능 최적화")
        session_id: 특정 세션으로 제한 (None이면 전체 검색)
        role: 역할 필터 ("면접관", "면접자", None이면 전체)
        top_k: 반환할 결과 개수
    
    Returns:
        list: 유사도 높은 메시지 리스트
              각 항목: {
                  "session_id": str,
                  "question_id": str,
                  "role": str,
                  "msg": str,
                  "similarity": float (0~1, 높을수록 유사)
              }
    
    사용 예시:
        # 문제해결 역량 증거 찾기
        results = search_similar_messages(
            query_text="문제 해결, 디버깅, 최적화, 성능 개선",
            session_id="interview_12345",
            role="면접자",
            top_k=3
        )
    """
    conn = None
    cur = None
    
    try:
        # 쿼리 텍스트를 임베딩으로 변환
        query_embedding = get_embedding(query_text)
        
        conn = get_connection()
        cur = conn.cursor()
        
        # WHERE 조건 동적 생성
        where_clauses = []
        params = []
        
        if session_id:
            where_clauses.append("session_id = %s")
            params.append(session_id)
        
        if role:
            where_clauses.append("role = %s")
            params.append(role)
        
        where_sql = ""
        if where_clauses:
            where_sql = "WHERE " + " AND ".join(where_clauses)
        
        params.append(top_k)
        
        # pgvector의 코사인 거리 연산자 (<->) 사용
        # 1 - 거리 = 유사도 (0~1)
        # query_embedding을 문자열로 변환하여 ::vector로 캐스팅
        embedding_str = "[" + ",".join(map(str, query_embedding)) + "]"
        
        sql = f"""
            SELECT 
                session_id,
                question_id,
                role,
                msg,
                1 - (msg_embedding <-> '{embedding_str}'::vector) AS similarity
            FROM rag.interview_logs
            {where_sql}
            ORDER BY msg_embedding <-> '{embedding_str}'::vector
            LIMIT %s
        """
        
        cur.execute(sql, params)
        
        results = []
        for row in cur.fetchall():
            results.append({
                "session_id": row[0],
                "question_id": row[1],
                "role": row[2],
                "msg": row[3],
                "similarity": float(row[4])
            })
        
        return results
        
    except Exception as e:
        print(f"❌ 유사도 검색 실패: {e}")
        return []
        
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()

# 세션 전체 로그 조회 (시간순)
def get_session_logs(session_id: str) -> list:
    """
    세션 전체 로그 조회 (시간순)
    
    Args:
        session_id: 세션 ID
    
    Returns:
        list: 메시지 리스트
    """
    conn = None
    cur = None
    
    try:
        conn = get_connection()
        cur = conn.cursor()
        
        cur.execute("""
            SELECT question_id, role, msg_turn, msg, offset_sec, created_at
            FROM rag.interview_logs
            WHERE session_id = %s
            ORDER BY question_id, msg_turn
        """, (session_id,))
        
        results = []
        for row in cur.fetchall():
            results.append({
                "question_id": row[0],
                "role": row[1],
                "msg_turn": row[2],
                "message": row[3],
                "offset_sec": row[4],
                "created_at": row[5].isoformat() if row[5] else None
            })
        
        return results
        
    except Exception as e:
        print(f"❌ 세션 로그 조회 실패: {e}")
        return []
        
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


# 전체 세션 목록 조회
def get_all_sessions() -> list:
    """
    전체 세션 목록 조회
    
    Returns:
        list: 세션 정보 리스트
    """
    conn = None
    cur = None
    
    try:
        conn = get_connection()
        cur = conn.cursor()
        
        cur.execute("""
            SELECT 
                session_id,
                COUNT(*) AS message_count,
                MIN(created_at) AS created_at
            FROM rag.interview_logs
            GROUP BY session_id
            ORDER BY created_at DESC
        """)
        
        results = []
        for row in cur.fetchall():
            results.append({
                "session_id": row[0],
                "message_count": row[1],
                "created_at": row[2].isoformat() if row[2] else None
            })
        
        return results
        
    except Exception as e:
        print(f"❌ 세션 목록 조회 실패: {e}")
        return []
        
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()

# 세션 삭제
def delete_session(session_id: str) -> int:
    """
    세션 삭제
    
    Args:
        session_id: 삭제할 세션 ID
    
    Returns:
        int: 삭제된 메시지 개수
    """
    conn = None
    cur = None
    
    try:
        conn = get_connection()
        cur = conn.cursor()
        
        cur.execute("""
            DELETE FROM rag.interview_logs
            WHERE session_id = %s
        """, (session_id,))
        
        deleted_count = cur.rowcount
        conn.commit()
        
        print(f"✅ {session_id} 삭제 완료: {deleted_count}개 메시지")
        
        return deleted_count
        
    except Exception as e:
        if conn:
            conn.rollback()
        print(f"❌ 세션 삭제 실패: {e}")
        raise
        
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()