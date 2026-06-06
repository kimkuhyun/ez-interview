-- ==========================================
-- EZ-Interview 초기 스키마
-- 실행 순서: 01_create_tables.sql → 02_create_interview_indexes.sql
--
-- docker-compose.yml 의 pgvector 컨테이너가 /docker-entrypoint-initdb.d/ 로
-- 마운트해 자동 실행하므로 별도 psql 호출은 필요하지 않다.
-- 수동 적용 시:
--   psql -h localhost -p 5433 -U eziuser -d ezinterview -f 01_create_tables.sql
-- ==========================================

CREATE EXTENSION IF NOT EXISTS vector;
CREATE SCHEMA IF NOT EXISTS interview;
CREATE SCHEMA IF NOT EXISTS rag;

-- ── 후보자
CREATE TABLE IF NOT EXISTS interview.candidates (
    session_id          TEXT PRIMARY KEY,
    name                TEXT,
    uploaded_at         TIMESTAMP DEFAULT NOW(),
    status              TEXT      DEFAULT 'pending',
    created_at          TIMESTAMP DEFAULT NOW(),
    position            TEXT,
    positions           TEXT[],
    note                TEXT,
    jd_id               TEXT,
    interview_at        TIMESTAMP,
    report_path         TEXT,
    summary             TEXT,
    hallucination_flags JSONB,
    quality             JSONB,
    weak_links          JSONB
);

-- ── 후보자 첨부 파일 (이력서·포트폴리오)
CREATE TABLE IF NOT EXISTS interview.files (
    id                SERIAL PRIMARY KEY,
    session_id        TEXT REFERENCES interview.candidates(session_id) ON DELETE CASCADE,
    type              TEXT,
    original_filename TEXT,
    file_path         TEXT,
    created_at        TIMESTAMP DEFAULT NOW()
);

-- ── 채용 공고 (JD) — bge-m3 1024d 임베딩
CREATE TABLE IF NOT EXISTS interview.job_descriptions (
    jd_id                    TEXT PRIMARY KEY,
    title                    TEXT,
    file_path                TEXT,
    is_active                BOOLEAN DEFAULT TRUE,
    content                  TEXT,
    embedding                vector(1024),
    company                  TEXT,
    department               TEXT,
    location                 TEXT,
    position                 TEXT,
    employment_type          TEXT,
    tech_stack               TEXT,
    requirements             TEXT,
    preferred_qualifications TEXT,
    responsibilities         TEXT,
    benefits                 TEXT,
    additional_info          TEXT,
    created_at               TIMESTAMP DEFAULT NOW(),
    updated_at               TIMESTAMP DEFAULT NOW()
);

-- ── RAG: 이력서·JD 청크 (세션 종속)
CREATE TABLE IF NOT EXISTS rag.documents (
    id          SERIAL PRIMARY KEY,
    session_id  TEXT,
    doc_id      TEXT,
    doc_type    TEXT,
    file_name   TEXT,
    chunk_index INTEGER,
    content     TEXT,
    embedding   vector(1024),
    metadata    JSONB
);

-- ── RAG: 면접 발화 로그 + HNSW 인덱스 대상
CREATE TABLE IF NOT EXISTS rag.interview_logs (
    id            SERIAL PRIMARY KEY,
    session_id    TEXT,
    question_id   TEXT,
    role          TEXT,
    msg_turn      INTEGER,
    msg           TEXT,
    msg_embedding vector(1024),
    offset_sec    DOUBLE PRECISION,
    created_at    TIMESTAMP DEFAULT NOW()
);

-- ── RAG: 이력서·JD 공통 문서 청크 (세션 독립 보관용)
CREATE TABLE IF NOT EXISTS rag.resume_jd_docs (
    id          SERIAL PRIMARY KEY,
    doc_type    TEXT,
    file_name   TEXT,
    chunk_index INTEGER,
    content     TEXT,
    embedding   vector(1024),
    created_at  TIMESTAMP DEFAULT NOW()
);
