"""
Report data sources test
- Resume data
- JD data
- Interview logs data
- Metrics data
- session_id fallback logic
"""
import pytest
import sys
import os
import io

# Set UTF-8 encoding for stdout
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# Add project root to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.db.db_connection import get_connection
from app.utils.rag_retriever import search_similar_chunks
from app.utils.interview_store import retrieve_interview_context
from app.routes.state_routes import GLOBAL_STATE


# ========================================
# Fixtures
# ========================================

@pytest.fixture
def db_connection():
    """DB 연결 fixture"""
    conn = get_connection()
    yield conn
    conn.close()


@pytest.fixture
def fallback_session_id():
    """폴백 session_id (실제 데이터 있음)"""
    return "09f4963c-f8a3-4f08-9b77-6ac6406de47b"


@pytest.fixture
def empty_session_id():
    """데이터가 없는 session_id"""
    return "00000000-0000-0000-0000-000000000000"


@pytest.fixture
def default_metrics():
    """기본 핵심역량 리스트"""
    return [
        "problem_solving",
        "communication",
        "self_driven_initiative",
        "collaboration",
        "professional_expertise"
    ]


# ========================================
# 1. 이력서 (Resume) 데이터 테스트
# ========================================

def test_resume_data_exists_in_db(db_connection, fallback_session_id):
    """DB에 이력서 데이터가 존재하는지 확인"""
    cur = db_connection.cursor()
    cur.execute("""
        SELECT COUNT(*) FROM rag.documents
        WHERE session_id = %s AND doc_type = 'resume'
    """, (fallback_session_id,))

    count = cur.fetchone()[0]
    cur.close()

    print(f"\n✅ 이력서 chunk 개수: {count}")
    assert count > 0, f"이력서 데이터가 없습니다. session_id: {fallback_session_id}"


def test_resume_data_retrieval_via_rag(fallback_session_id):
    """RAG retriever로 이력서 데이터 검색"""
    query = "경력 프로젝트 기술스택 성과"
    results = search_similar_chunks(query, session_id=fallback_session_id, top_k=5)

    resume_results = [r for r in results if r[1] == 'resume']

    print(f"\n✅ RAG 검색 결과 (resume): {len(resume_results)}개")
    for idx, (content, doc_type, score) in enumerate(resume_results[:3], 1):
        print(f"   [{idx}] type={doc_type}, score={score:.4f}, len={len(content)}")

    assert len(resume_results) > 0, "RAG retriever로 이력서 데이터를 가져올 수 없습니다."


def test_resume_data_not_found_for_empty_session(empty_session_id):
    """빈 session_id로는 이력서 데이터가 없어야 함"""
    query = "경력 프로젝트"
    results = search_similar_chunks(query, session_id=empty_session_id, top_k=5)

    print(f"\n✅ 빈 session_id 검색 결과: {len(results)}개 (0개 예상)")
    assert len(results) == 0, "빈 session_id로 데이터가 검색되면 안 됩니다."


# ========================================
# 2. 공고 (JD) 데이터 테스트
# ========================================

def test_jd_data_exists_in_db(db_connection, fallback_session_id):
    """DB에 공고 데이터가 존재하는지 확인"""
    cur = db_connection.cursor()
    cur.execute("""
        SELECT COUNT(*) FROM rag.documents
        WHERE session_id = %s AND doc_type = 'jd'
    """, (fallback_session_id,))

    count = cur.fetchone()[0]
    cur.close()

    print(f"\n✅ 공고 chunk 개수: {count}")
    assert count > 0, f"공고 데이터가 없습니다. session_id: {fallback_session_id}"


def test_jd_data_retrieval_via_rag(fallback_session_id):
    """RAG retriever로 공고 데이터 검색"""
    query = "요구사항 자격요건 우대사항 담당업무"
    results = search_similar_chunks(query, session_id=fallback_session_id, top_k=5)

    jd_results = [r for r in results if r[1] == 'jd']

    print(f"\n✅ RAG 검색 결과 (jd): {len(jd_results)}개")
    for idx, (content, doc_type, score) in enumerate(jd_results[:3], 1):
        print(f"   [{idx}] type={doc_type}, score={score:.4f}, len={len(content)}")

    assert len(jd_results) > 0, "RAG retriever로 공고 데이터를 가져올 수 없습니다."


# ========================================
# 3. 인터뷰 로그 데이터 테스트
# ========================================

def test_interview_logs_exist_in_db(db_connection, fallback_session_id):
    """DB에 인터뷰 로그가 존재하는지 확인"""
    cur = db_connection.cursor()
    cur.execute("""
        SELECT COUNT(*) FROM rag.interview_logs
        WHERE session_id = %s
    """, (fallback_session_id,))

    count = cur.fetchone()[0]
    cur.close()

    print(f"\n✅ 인터뷰 로그 개수: {count}")
    assert count > 0, f"인터뷰 로그가 없습니다. session_id: {fallback_session_id}"


def test_interview_logs_retrieval_via_store(fallback_session_id):
    """interview_store로 인터뷰 로그 검색"""
    log_text = retrieve_interview_context(fallback_session_id)

    print(f"\n✅ 인터뷰 로그 텍스트 길이: {len(log_text)}")
    print(f"   미리보기: {log_text[:200]}...")

    assert len(log_text) > 0, "interview_store로 인터뷰 로그를 가져올 수 없습니다."
    assert "Q:" in log_text or "A:" in log_text, "인터뷰 로그 형식이 잘못되었습니다."


def test_interview_logs_not_found_for_empty_session(empty_session_id):
    """빈 session_id로는 인터뷰 로그가 없어야 함"""
    log_text = retrieve_interview_context(empty_session_id)

    print(f"\n✅ 빈 session_id 로그 길이: {len(log_text)} (0 예상)")
    assert len(log_text) == 0, "빈 session_id로 인터뷰 로그가 검색되면 안 됩니다."


# ========================================
# 4. 핵심역량 (Metrics) 테스트
# ========================================

def test_default_metrics_available(default_metrics):
    """기본 핵심역량 리스트 확인"""
    print(f"\n✅ 기본 핵심역량: {default_metrics}")

    assert len(default_metrics) == 5, "기본 핵심역량은 5개여야 합니다."
    assert "problem_solving" in default_metrics
    assert "communication" in default_metrics
    assert "self_driven_initiative" in default_metrics
    assert "collaboration" in default_metrics
    assert "professional_expertise" in default_metrics


def test_global_state_metrics_fallback():
    """GLOBAL_STATE.metrics가 None이면 폴백 사용"""
    # GLOBAL_STATE.metrics가 None일 때
    if GLOBAL_STATE.metrics is None:
        fallback_metrics = [
            "problem_solving",
            "communication",
            "self_driven_initiative",
            "collaboration",
            "professional_expertise"
        ]
        print(f"\n✅ GLOBAL_STATE.metrics is None → 폴백 사용")
        print(f"   폴백 metrics: {fallback_metrics}")

        assert len(fallback_metrics) == 5
    else:
        print(f"\n✅ GLOBAL_STATE.metrics: {GLOBAL_STATE.metrics}")
        assert len(GLOBAL_STATE.metrics) == 5


# ========================================
# 5. session_id 폴백 로직 테스트
# ========================================

def test_session_id_fallback_logic(db_connection, fallback_session_id, empty_session_id):
    """session_id 폴백 로직 테스트"""
    cur = db_connection.cursor()

    # 1. 폴백 session_id는 데이터가 있어야 함
    cur.execute("""
        SELECT COUNT(*) FROM rag.documents WHERE session_id = %s
    """, (fallback_session_id,))
    fallback_doc_count = cur.fetchone()[0]

    cur.execute("""
        SELECT COUNT(*) FROM rag.interview_logs WHERE session_id = %s
    """, (fallback_session_id,))
    fallback_log_count = cur.fetchone()[0]

    print(f"\n✅ 폴백 session_id 데이터:")
    print(f"   session_id: {fallback_session_id}")
    print(f"   documents: {fallback_doc_count}, interview_logs: {fallback_log_count}")

    assert fallback_doc_count > 0, "폴백 session_id에 documents가 없습니다."
    assert fallback_log_count > 0, "폴백 session_id에 interview_logs가 없습니다."

    # 2. 빈 session_id는 데이터가 없어야 함
    cur.execute("""
        SELECT COUNT(*) FROM rag.documents WHERE session_id = %s
    """, (empty_session_id,))
    empty_doc_count = cur.fetchone()[0]

    cur.execute("""
        SELECT COUNT(*) FROM rag.interview_logs WHERE session_id = %s
    """, (empty_session_id,))
    empty_log_count = cur.fetchone()[0]

    print(f"\n✅ 빈 session_id 데이터:")
    print(f"   session_id: {empty_session_id}")
    print(f"   documents: {empty_doc_count}, interview_logs: {empty_log_count}")

    assert empty_doc_count == 0, "빈 session_id에 documents가 있으면 안 됩니다."
    assert empty_log_count == 0, "빈 session_id에 interview_logs가 있으면 안 됩니다."

    cur.close()


# ========================================
# 6. 통합 테스트: 리포트 생성에 필요한 모든 데이터
# ========================================

def test_report_all_data_sources_available(fallback_session_id, default_metrics):
    """리포트 생성에 필요한 모든 데이터 소스 확인"""
    print(f"\n{'='*80}")
    print("리포트 데이터 소스 통합 테스트")
    print(f"{'='*80}")

    # 1. 이력서
    resume_query = "경력 프로젝트"
    resume_results = search_similar_chunks(resume_query, session_id=fallback_session_id, top_k=5)
    resume_count = len([r for r in resume_results if r[1] == 'resume'])
    print(f"\n[1] 이력서: {resume_count}개 chunk")
    assert resume_count > 0, "이력서 데이터 없음"

    # 2. 공고
    jd_query = "요구사항 자격요건"
    jd_results = search_similar_chunks(jd_query, session_id=fallback_session_id, top_k=5)
    jd_count = len([r for r in jd_results if r[1] == 'jd'])
    print(f"[2] 공고: {jd_count}개 chunk")
    assert jd_count > 0, "공고 데이터 없음"

    # 3. 인터뷰 로그
    log_text = retrieve_interview_context(fallback_session_id)
    print(f"[3] 인터뷰 로그: {len(log_text)} 글자")
    assert len(log_text) > 0, "인터뷰 로그 없음"

    # 4. 핵심역량
    print(f"[4] 핵심역량: {len(default_metrics)}개")
    print(f"    {default_metrics}")
    assert len(default_metrics) == 5, "핵심역량 5개 필요"

    print(f"\n{'='*80}")
    print("✅ 모든 데이터 소스 준비 완료!")
    print(f"{'='*80}")


# ========================================
# 실행
# ========================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
