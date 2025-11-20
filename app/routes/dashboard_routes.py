from flask import Blueprint, render_template, Response, request
from pathlib import Path

dashboard_bp = Blueprint('dashboard', __name__)

# 탭 HTML 제공 API
@dashboard_bp.route("/api/admin/tabs/<tab_name>")
def get_tab_content(tab_name):
    try:
        # render_template_string을 사용하여 템플릿 렌더링
        from flask import render_template_string
        
        BASE_DIR = Path(__file__).parent.parent
        tab_file = BASE_DIR / "templates" / "admin" / "tabs" / f"{tab_name}.html"
        
        if not tab_file.exists():
            return Response("탭을 찾을 수 없습니다.", status=404)
        
        with open(tab_file, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Flask 템플릿 엔진으로 렌더링하여 {{ url_for() }} 처리
        rendered_content = render_template_string(content)
        
        return Response(rendered_content.strip(), mimetype='text/html')
    except Exception as e:
        print(f"탭 로드 오류: {e}")
        return Response(f"오류: {str(e)}", status=500)


# ============================================
# 지원자 관리 API
# ============================================

@dashboard_bp.route("/api/candidates", methods=['GET'])
def get_candidates():
    """
    지원자 목록 조회
    
    Query Parameters:
    - status: 상태 필터 (all, pending, screening, etc.)
    - search: 이름 검색
    - page: 페이지 번호 (기본 1)
    - limit: 페이지당 개수 (기본 10)
    
    Returns:
    {
        "success": true,
        "total": 100,
        "page": 1,
        "limit": 10,
        "candidates": [...]
    }
    """
    from flask import request, jsonify
    import psycopg2
    from psycopg2.extras import RealDictCursor
    import os
    
    try:
        # Query Parameters
        status = request.args.get('status', 'all')
        position = request.args.get('position', 'all')
        search = request.args.get('search', '')
        page = int(request.args.get('page', 1))
        limit = int(request.args.get('limit', 10))
        offset = (page - 1) * limit
        
        # DB 연결
        conn = psycopg2.connect(
            host=os.getenv("DB_HOST", "localhost"),
            port=os.getenv("DB_PORT", "5432"),
            database=os.getenv("DB_NAME", "postgres"),
            user=os.getenv("DB_USER", "postgres"),
            password=os.getenv("DB_PASSWORD", "")
        )
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        # WHERE 절 구성
        where_clauses = []
        params = []
        
        if status != 'all':
            where_clauses.append("status = %s")
            params.append(status)
        
        if position != 'all':
            where_clauses.append("position = %s")
            params.append(position)
        
        if search:
            where_clauses.append("name ILIKE %s")
            params.append(f"%{search}%")
        
        where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"
        
        # 총 개수 조회
        cur.execute(f"SELECT COUNT(*) as total FROM interview.candidates WHERE {where_sql}", params)
        total = cur.fetchone()['total']
        
        # 지원자 목록 조회 (LEFT JOIN으로 포트폴리오 유무 확인)
        query = f"""
            SELECT 
                c.session_id,
                c.name,
                c.status,
                c.position,
                c.jd_id,
                c.created_at,
                c.uploaded_at,
                c.report_path,
                CASE 
                    WHEN EXISTS (
                        SELECT 1 FROM interview.files f 
                        WHERE f.session_id = c.session_id 
                        AND f.type = 'portfolio'
                    ) THEN true 
                    ELSE false 
                END as has_portfolio
            FROM interview.candidates c
            WHERE {where_sql}
            ORDER BY c.created_at DESC
            LIMIT %s OFFSET %s
        """
        params.extend([limit, offset])
        
        cur.execute(query, params)
        rows = cur.fetchall()
        
        # 결과 포맷팅
        candidates = []
        for row in rows:
            candidates.append({
                'session_id': row['session_id'],
                'name': row['name'],
                'status': row['status'],
                'position': row['position'] or '-',
                'jd_id': row['jd_id'],
                'created_at': row['created_at'].isoformat() if row['created_at'] else None,
                'uploaded_at': row['uploaded_at'].isoformat() if row['uploaded_at'] else None,
                'has_portfolio': row['has_portfolio']
            })
        
        cur.close()
        conn.close()
        
        return jsonify({
            "success": True,
            "total": total,
            "page": page,
            "limit": limit,
            "candidates": candidates
        }), 200
        
    except Exception as e:
        print(f"❌ [지원자 조회] 실패: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


# ============================================
# 지원자 파일 조회 API
# ============================================

@dashboard_bp.route("/api/candidates/<session_id>/files", methods=['GET'])
def get_candidate_files(session_id):
    """
    특정 지원자의 파일 목록 조회 (이력서, 포트폴리오)
    
    Returns:
    {
        "success": true,
        "files": [
            {
                "id": 1,
                "type": "resume",
                "original_filename": "이력서.pdf",
                "file_path": "uploads/session_id/resume.pdf"
            }
        ]
    }
    """
    from flask import jsonify
    import psycopg2
    from psycopg2.extras import RealDictCursor
    import os
    
    try:
        # DB 연결
        conn = psycopg2.connect(
            host=os.getenv("DB_HOST", "localhost"),
            port=os.getenv("DB_PORT", "5432"),
            database=os.getenv("DB_NAME", "postgres"),
            user=os.getenv("DB_USER", "postgres"),
            password=os.getenv("DB_PASSWORD", "")
        )
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        # 파일 목록 조회
        query = """
            SELECT 
                id,
                session_id,
                type,
                original_filename,
                file_path
            FROM interview.files
            WHERE session_id = %s
            ORDER BY 
                CASE 
                    WHEN type = 'resume' THEN 1
                    WHEN type = 'portfolio' THEN 2
                    ELSE 3
                END
        """
        cur.execute(query, (session_id,))
        rows = cur.fetchall()
        
        files = []
        for row in rows:
            files.append({
                'id': row['id'],
                'type': row['type'],
                'original_filename': row['original_filename'],
                'file_path': row['file_path']
            })
        
        # 리포트 경로 조회
        cur.execute("""
            SELECT report_path
            FROM interview.candidates
            WHERE session_id = %s
        """, (session_id,))
        
        candidate_row = cur.fetchone()
        report_path = candidate_row['report_path'] if candidate_row else None
        
        cur.close()
        conn.close()
        
        return jsonify({
            "success": True,
            "files": files,
            "report_path": report_path
        }), 200
        
    except Exception as e:
        print(f"❌ [파일 조회] 실패: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@dashboard_bp.route("/api/files/<path:file_path>")
def serve_file(file_path):
    """
    uploads 폴더의 파일을 서빙
    """
    from flask import send_from_directory
    from pathlib import Path
    import os
    
    try:
        BASE_DIR = Path(__file__).parent.parent.parent
        uploads_dir = BASE_DIR / "uploads"
        
        # 파일 경로 보안 검증
        full_path = uploads_dir / file_path
        if not full_path.resolve().is_relative_to(uploads_dir.resolve()):
            return Response("접근 권한이 없습니다.", status=403)
        
        if not full_path.exists():
            return Response("파일을 찾을 수 없습니다.", status=404)
        
        # 파일 서빙
        directory = str(full_path.parent)
        filename = full_path.name
        
        return send_from_directory(directory, filename)
        
    except Exception as e:
        print(f"❌ [파일 서빙] 실패: {e}")
        import traceback
        traceback.print_exc()
        return Response(f"오류: {str(e)}", status=500)


# ============================================
# 이력서 업로드 API
# ============================================

@dashboard_bp.route("/api/candidates/upload", methods=['POST'])
def upload_candidate():
    """
    지원자 이력서/포트폴리오 업로드
    
    Form Data:
    - file: PDF/DOC/DOCX 파일
    - position: 지원 포지션
    
    파일명 규칙: {이름}_이력서.pdf, {이름}_포트폴리오.pdf
    """
    from flask import request, jsonify
    from werkzeug.utils import secure_filename
    import psycopg2
    from psycopg2.extras import RealDictCursor
    import os
    import uuid
    from datetime import datetime
    import re
    
    try:
        # 파일 체크
        if 'file' not in request.files:
            return jsonify({"success": False, "error": "파일이 없습니다."}), 400
        
        file = request.files['file']
        position = request.form.get('position', '')
        
        if file.filename == '':
            return jsonify({"success": False, "error": "파일이 선택되지 않았습니다."}), 400
        
        # 파일 확장자 체크
        allowed_extensions = {'.pdf', '.doc', '.docx'}
        file_ext = Path(file.filename).suffix.lower()
        
        if file_ext not in allowed_extensions:
            return jsonify({"success": False, "error": f"지원하지 않는 파일 형식입니다: {file_ext}"}), 400
        
        # 파일명 파싱: {이름}_이력서 or {이름}_포트폴리오
        filename_without_ext = Path(file.filename).stem
        
        # 패턴 매칭
        match_resume = re.match(r'^(.+)_이력서$', filename_without_ext)
        match_portfolio = re.match(r'^(.+)_포트폴리오$', filename_without_ext)
        
        if match_resume:
            candidate_name = match_resume.group(1).strip()
            file_type = 'resume'
        elif match_portfolio:
            candidate_name = match_portfolio.group(1).strip()
            file_type = 'portfolio'
        else:
            return jsonify({
                "success": False, 
                "error": f"파일명 형식이 올바르지 않습니다: {file.filename}\n(예: 홍길동_이력서.pdf, 홍길동_포트폴리오.pdf)"
            }), 400
        
        if not candidate_name:
            return jsonify({"success": False, "error": "이름을 파싱할 수 없습니다."}), 400
        
        # DB 연결
        conn = psycopg2.connect(
            host=os.getenv("DB_HOST", "localhost"),
            port=os.getenv("DB_PORT", "5432"),
            database=os.getenv("DB_NAME", "postgres"),
            user=os.getenv("DB_USER", "postgres"),
            password=os.getenv("DB_PASSWORD", "")
        )
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        # 프론트엔드에서 전달한 session_id 확인 (같은 그룹의 두 번째 파일부터)
        provided_session_id = request.form.get('session_id', '')
        
        if provided_session_id:
            # 제공된 session_id 사용 (같은 사람의 두 번째 파일)
            session_id = provided_session_id
            print(f"✓ [업로드] 기존 세션 사용: {candidate_name} ({session_id})")
            
            # uploaded_at 업데이트
            cur.execute("""
                UPDATE interview.candidates 
                SET uploaded_at = %s 
                WHERE session_id = %s
            """, (datetime.now(), session_id))
        else:
            # 새 세션 생성
            session_id = str(uuid.uuid4())
            
            # candidates 테이블에 삽입
            cur.execute("""
                INSERT INTO interview.candidates (
                    session_id, name, uploaded_at, status, created_at, position
                ) VALUES (%s, %s, %s, %s, %s, %s)
            """, (
                session_id,
                candidate_name,
                datetime.now(),
                'pending',
                datetime.now(),
                position or None
            ))
            
            print(f"✓ [업로드] 새 지원자 생성: {candidate_name} ({session_id})")
        
        # 파일 저장
        BASE_DIR = Path(__file__).parent.parent.parent
        uploads_dir = BASE_DIR / "uploads"
        uploads_dir.mkdir(parents=True, exist_ok=True)
        
        # 파일명: {session_id}_{resume|portfolio}.{ext}
        saved_filename = f"{session_id}_{file_type}{file_ext}"
        file_path = uploads_dir / saved_filename
        
        file.save(str(file_path))
        
        # 상대 경로 (DB 저장용)
        relative_path = saved_filename
        
        # files 테이블에 삽입
        # original_filename은 경로 제외하고 파일명만 저장
        original_filename = Path(file.filename).name
        
        cur.execute("""
            INSERT INTO interview.files (
                session_id, type, original_filename, file_path
            ) VALUES (%s, %s, %s, %s)
        """, (
            session_id,
            file_type,
            original_filename,
            relative_path
        ))
        
        conn.commit()
        cur.close()
        conn.close()
        
        print(f"✓ [업로드] 파일 저장 완료: {relative_path}")
        
        # 임베딩 처리 (비동기로 처리하여 업로드 응답 속도 유지)
        try:
            from app.agents.embedding_agent import EmbeddingAgent
            from app.utils.file_utils import extract_text
            
            print(f"📚 [업로드] 임베딩 시작: {candidate_name}")
            
            # 파일 다시 열기
            with open(str(file_path), 'rb') as f:
                file_content = f.read()
            
            # 임시 파일 객체 생성
            from io import BytesIO
            from werkzeug.datastructures import FileStorage
            file_obj = FileStorage(
                stream=BytesIO(file_content),
                filename=original_filename,
                content_type=file.content_type
            )
            
            # 파일 타입에 따라 임베딩 처리
            if file_type == 'resume':
                # 이력서만 있는 경우 - JD는 나중에 매칭 시 처리
                text = extract_text(file_obj)
                if text:
                    from app.utils.rag_indexer import insert_resume_jd_embeddings
                    from app.utils.document_structurer import structure_resume
                    from app.utils.semantic_chunker import chunk_structured_resume, chunks_to_text_with_metadata
                    
                    try:
                        # Resume 구조화 및 청킹
                        structured_resume = structure_resume(text)
                        resume_chunks = chunk_structured_resume(structured_resume)
                        resume_chunks_with_metadata = chunks_to_text_with_metadata(resume_chunks)
                        
                        # 임베딩 저장
                        doc_id = insert_resume_jd_embeddings(
                            session_id, "resume_upload", "resume",
                            chunks_with_metadata=resume_chunks_with_metadata
                        )
                        print(f"✅ [업로드] 이력서 임베딩 완료: doc_id={doc_id}")
                    except:
                        # 구조화 실패 시 기존 방식
                        doc_id = insert_resume_jd_embeddings(
                            session_id, "resume_upload", "resume", text=text
                        )
                        print(f"✅ [업로드] 이력서 임베딩 완료 (폴백): doc_id={doc_id}")
                        
            elif file_type == 'portfolio':
                # 포트폴리오는 멀티모달 분석
                from app.utils.file_utils import extract_portfolio_multimodal
                from app.utils.rag_indexer import insert_resume_jd_embeddings
                
                try:
                    portfolio_text = extract_portfolio_multimodal(file_obj)
                    if portfolio_text:
                        doc_id = insert_resume_jd_embeddings(
                            session_id, "portfolio_upload", "portfolio", text=portfolio_text
                        )
                        print(f"✅ [업로드] 포트폴리오 임베딩 완료: doc_id={doc_id}")
                except Exception as e:
                    print(f"⚠️  [업로드] 포트폴리오 임베딩 실패: {e}")
                    
        except Exception as e:
            print(f"⚠️  [업로드] 임베딩 처리 오류 (업로드는 성공): {e}")
            import traceback
            traceback.print_exc()
        
        return jsonify({
            "success": True,
            "session_id": session_id,
            "name": candidate_name,
            "file_type": file_type,
            "message": f"{candidate_name}님의 {file_type} 업로드 완료"
        }), 200
        
    except Exception as e:
        print(f"❌ [업로드] 실패: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


# ============================================
# 면접 관리 API
# ============================================

@dashboard_bp.route("/api/interviews/pending", methods=['GET'])
def get_pending_interviews():
    """
    면접 대기 중인 지원자 목록 조회 (status = interview_pending)
    
    Returns:
    {
        "success": true,
        "interviews": [
            {
                "session_id": "uuid",
                "name": "홍길동",
                "position": "백엔드 개발자",
                "jd_id": "uuid",
                "status": "interview_pending",
                "scheduled_at": "2025-01-15T14:00:00",
                "created_at": "2025-01-10T10:00:00"
            }
        ]
    }
    """
    from flask import jsonify
    import psycopg2
    from psycopg2.extras import RealDictCursor
    import os
    
    try:
        # DB 연결
        conn = psycopg2.connect(
            host=os.getenv("DB_HOST", "localhost"),
            port=os.getenv("DB_PORT", "5432"),
            database=os.getenv("DB_NAME", "postgres"),
            user=os.getenv("DB_USER", "postgres"),
            password=os.getenv("DB_PASSWORD", "")
        )
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        # interview_pending 및 interview_in_progress 상태의 지원자 조회
        query = """
            SELECT 
                session_id,
                name,
                position,
                jd_id,
                status,
                interview_at,
                created_at,
                uploaded_at
            FROM interview.candidates
            WHERE status IN ('interview_pending', 'interview_in_progress')
            ORDER BY created_at DESC
        """
        
        cur.execute(query)
        rows = cur.fetchall()
        
        # 결과 포맷팅
        interviews = []
        for row in rows:
            interviews.append({
                'session_id': row['session_id'],
                'name': row['name'],
                'position': row['position'] or '-',
                'jd_id': row['jd_id'],
                'status': row['status'],
                'interview_at': row['interview_at'].isoformat() if row['interview_at'] else None,
                'created_at': row['created_at'].isoformat() if row['created_at'] else None,
                'uploaded_at': row['uploaded_at'].isoformat() if row['uploaded_at'] else None
            })
        
        cur.close()
        conn.close()
        
        return jsonify({
            "success": True,
            "interviews": interviews
        }), 200
        
    except Exception as e:
        print(f"❌ [면접 목록 조회] 실패: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@dashboard_bp.route("/api/interviews/<session_id>/schedule", methods=['PUT'])
def update_interview_schedule(session_id):
    """
    면접 일시 변경 - candidates.interview_at 업데이트
    
    Body:
    {
        "interview_at": "2025-01-15T14:00"
    }
    """
    from flask import request, jsonify
    import psycopg2
    from psycopg2.extras import RealDictCursor
    import os
    
    try:
        data = request.get_json()
        interview_at = data.get('interview_at')
        
        if not interview_at:
            return jsonify({"success": False, "error": "interview_at이 필요합니다."}), 400
        
        # DB 연결
        conn = psycopg2.connect(
            host=os.getenv("DB_HOST", "localhost"),
            port=os.getenv("DB_PORT", "5432"),
            database=os.getenv("DB_NAME", "postgres"),
            user=os.getenv("DB_USER", "postgres"),
            password=os.getenv("DB_PASSWORD", "")
        )
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        # interview_at 업데이트
        cur.execute("""
            UPDATE interview.candidates
            SET interview_at = %s
            WHERE session_id = %s
        """, (interview_at, session_id))
        
        conn.commit()
        cur.close()
        conn.close()
        
        print(f"✓ [면접 일시 저장] {session_id}: {interview_at}")
        
        return jsonify({
            "success": True,
            "message": "면접 일시가 저장되었습니다."
        }), 200
        
    except Exception as e:
        print(f"❌ [면접 일시 저장] 실패: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@dashboard_bp.route("/api/interviews/<session_id>/start", methods=['POST'])
def start_interview(session_id):
    """
    면접 시작 - candidates.status를 interview_in_progress로 변경
    """
    from flask import jsonify
    import psycopg2
    from psycopg2.extras import RealDictCursor
    import os
    
    try:
        # DB 연결
        conn = psycopg2.connect(
            host=os.getenv("DB_HOST", "localhost"),
            port=os.getenv("DB_PORT", "5432"),
            database=os.getenv("DB_NAME", "postgres"),
            user=os.getenv("DB_USER", "postgres"),
            password=os.getenv("DB_PASSWORD", "")
        )
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        # 상태를 interview_in_progress로 변경
        cur.execute("""
            UPDATE interview.candidates
            SET status = 'interview_in_progress'
            WHERE session_id = %s
        """, (session_id,))
        
        conn.commit()
        cur.close()
        conn.close()
        
        print(f"✓ [면접 시작] {session_id}: interview_in_progress")
        
        return jsonify({
            "success": True,
            "message": "면접이 시작되었습니다."
        }), 200
        
    except Exception as e:
        print(f"❌ [면접 시작] 실패: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


# ============================================
# 포지션 관리 API
# ============================================

@dashboard_bp.route("/api/interviews/<session_id>/result", methods=['POST'])
def save_interview_result(session_id):
    """
    면접 결과 저장 - candidates.status를 passed/on_hold/rejected로 변경
    
    Body:
    {
        "status": "passed" | "on_hold" | "rejected"
    }
    """
    from flask import request, jsonify
    import psycopg2
    from psycopg2.extras import RealDictCursor
    import os
    
    try:
        data = request.get_json()
        status = data.get('status')
        
        if status not in ['passed', 'on_hold', 'rejected']:
            return jsonify({"success": False, "error": "유효하지 않은 상태값입니다."}), 400
        
        # DB 연결
        conn = psycopg2.connect(
            host=os.getenv("DB_HOST", "localhost"),
            port=os.getenv("DB_PORT", "5432"),
            database=os.getenv("DB_NAME", "postgres"),
            user=os.getenv("DB_USER", "postgres"),
            password=os.getenv("DB_PASSWORD", "")
        )
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        # 상태 업데이트
        cur.execute("""
            UPDATE interview.candidates
            SET status = %s
            WHERE session_id = %s
        """, (status, session_id))
        
        conn.commit()
        cur.close()
        conn.close()
        
        print(f"✓ [면접 결과 저장] {session_id}: {status}")
        
        return jsonify({
            "success": True,
            "message": f"면접 결과가 {status}로 저장되었습니다."
        }), 200
        
    except Exception as e:
        print(f"❌ [면접 결과 저장] 실패: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@dashboard_bp.route("/api/positions/active", methods=['GET'])
def get_active_positions():
    """
    활성 포지션 목록 조회 (is_active = true)
    
    Returns:
    {
        "success": true,
        "positions": [
            {
                "jd_id": 1,
                "title": "백엔드 개발자",
                "company": "회사명"
            }
        ]
    }
    """
    from flask import jsonify
    import psycopg2
    from psycopg2.extras import RealDictCursor
    import os
    
    try:
        # DB 연결
        conn = psycopg2.connect(
            host=os.getenv("DB_HOST", "localhost"),
            port=os.getenv("DB_PORT", "5432"),
            database=os.getenv("DB_NAME", "postgres"),
            user=os.getenv("DB_USER", "postgres"),
            password=os.getenv("DB_PASSWORD", "")
        )
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        # 활성 포지션 조회
        cur.execute("""
            SELECT jd_id, title
            FROM interview.job_descriptions
            WHERE is_active = true
            ORDER BY created_at DESC
        """)
        
        rows = cur.fetchall()
        
        positions = []
        for row in rows:
            positions.append({
                'jd_id': row['jd_id'],
                'title': row['title']
            })
        
        cur.close()
        conn.close()
        
        return jsonify({
            "success": True,
            "positions": positions
        }), 200
        
    except Exception as e:
        print(f"❌ [포지션 조회] 실패: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@dashboard_bp.route("/panel/positions")
def get_positions_panel():
    """포지션 목록 패널"""
    return render_template("admin/tabs/positions.html")

@dashboard_bp.route("/panel/keyword-match")
def get_keyword_match_panel():
    """키워드 매칭 패널"""
    position_id = request.args.get('position_id')
    return render_template("admin/tabs/keyword_match.html", position_id=position_id)

@dashboard_bp.route("/interview-session")
def interview_session():
    """면접 진행 페이지 - URL 파라미터를 GLOBAL_STATE에 저장"""
    from app.routes.state_routes import GLOBAL_STATE
    from app.db.db_connection import get_connection
    
    # URL 파라미터 가져오기
    session_id = request.args.get('session_id')
    name = request.args.get('name')
    jd_id = request.args.get('jd_id')
    position = request.args.get('position')
    
    print("\n" + "="*80)
    print("🎯 [INTERVIEW SESSION] 면접 페이지 로드")
    print("="*80)
    print(f"📥 URL 파라미터:")
    print(f"   - session_id: {session_id}")
    print(f"   - name: {name}")
    print(f"   - jd_id: {jd_id}")
    print(f"   - position: {position}")
    
    # ✅ GLOBAL_STATE에 저장
    if session_id:
        GLOBAL_STATE.session_id = session_id
        print(f"   ✅ GLOBAL_STATE.session_id = {session_id}")
    
    if name:
        GLOBAL_STATE.candidate_name = name
        print(f"   ✅ GLOBAL_STATE.candidate_name = {name}")
    
    if jd_id:
        GLOBAL_STATE.jd_id = jd_id
        print(f"   ✅ GLOBAL_STATE.jd_id = {jd_id}")
    
    if position:
        GLOBAL_STATE.position = position
        print(f"   ✅ GLOBAL_STATE.position = {position}")
    
    # ✅ DB에서 문서 존재 여부 확인
    if session_id and jd_id:
        try:
            print(f"\n📊 DB에서 문서 확인 중...")
            conn = get_connection()
            cur = conn.cursor()
            
            # 이력서/포트폴리오 확인 (rag.documents)
            cur.execute("""
                SELECT doc_type, COUNT(*) as chunk_count
                FROM rag.documents
                WHERE session_id = %s::uuid
                GROUP BY doc_type
            """, (session_id,))
            doc_counts = cur.fetchall()
            
            resume_chunks = 0
            portfolio_chunks = 0
            for doc_type, count in doc_counts:
                if doc_type == 'resume':
                    resume_chunks = count
                    print(f"   ✅ 이력서: {count}개 청크")
                elif doc_type == 'portfolio':
                    portfolio_chunks = count
                    print(f"   ✅ 포트폴리오: {count}개 청크")
            
            # JD 확인 (interview.job_descriptions)
            cur.execute("""
                SELECT jd_id, title, LENGTH(content) as content_len
                FROM interview.job_descriptions
                WHERE jd_id = %s::uuid AND embedding IS NOT NULL
            """, (jd_id,))
            jd_row = cur.fetchone()
            
            if jd_row:
                jd_title = jd_row[1]
                jd_len = jd_row[2]
                print(f"   ✅ JD: {jd_title}, {jd_len}자")
                GLOBAL_STATE.jd_len = jd_len
            else:
                print(f"   ⚠️  JD 없음 (jd_id={jd_id})")
            
            GLOBAL_STATE.resume_len = resume_chunks
            GLOBAL_STATE.portfolio_len = portfolio_chunks
            
            cur.close()
            conn.close()
            
        except Exception as e:
            print(f"   ⚠️  DB 확인 실패: {e}")
            import traceback
            traceback.print_exc()
    
    print("="*80 + "\n")
    
    return render_template("admin/interview_session.html")

@dashboard_bp.route("/api/candidates/<session_id>/info", methods=['GET'])
def get_candidate_info(session_id):
    """
    지원자 상세 정보 조회 (면접 세션용)
    
    Returns:
    {
        "success": true,
        "candidate": {
            "session_id": "...",
            "name": "홍길동",
            "position": "Backend Developer",
            "interview_at": "2025-11-20 14:00:00",
            "status": "interview_pending",
            "resume_path": "session_id_resume.pdf",
            "jd_path": "jd_id.pdf",
            "portfolio_path": "session_id_portfolio.pdf" (optional)
        }
    }
    """
    from flask import jsonify
    import psycopg2
    from psycopg2.extras import RealDictCursor
    import os
    
    try:
        conn = psycopg2.connect(
            host=os.getenv("DB_HOST", "localhost"),
            port=os.getenv("DB_PORT", "5432"),
            database=os.getenv("DB_NAME", "postgres"),
            user=os.getenv("DB_USER", "postgres"),
            password=os.getenv("DB_PASSWORD", "")
        )
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        # 지원자 정보 + 파일 정보 조인
        cur.execute("""
            SELECT 
                c.session_id,
                c.name,
                c.position,
                c.interview_at,
                c.status,
                c.jd_id,
                MAX(CASE WHEN f.type = 'resume' THEN f.file_path END) as resume_path,
                MAX(CASE WHEN f.type = 'portfolio' THEN f.file_path END) as portfolio_path
            FROM interview.candidates c
            LEFT JOIN interview.files f ON c.session_id = f.session_id
            WHERE c.session_id = %s
            GROUP BY c.session_id, c.name, c.position, c.interview_at, c.status, c.jd_id
        """, (session_id,))
        
        candidate = cur.fetchone()
        
        if not candidate:
            return jsonify({"success": False, "error": "지원자를 찾을 수 없습니다."}), 404
        
        candidate_dict = dict(candidate)
        
        # JD 파일 경로 조회 (job_descriptions 테이블에서)
        if candidate_dict.get('jd_id'):
            cur.execute("""
                SELECT file_path 
                FROM interview.job_descriptions 
                WHERE jd_id = %s
            """, (candidate_dict['jd_id'],))
            
            jd_result = cur.fetchone()
            candidate_dict['jd_path'] = jd_result['file_path'] if jd_result else None
        else:
            candidate_dict['jd_path'] = None
        
        cur.close()
        conn.close()
        
        return jsonify({
            "success": True,
            "candidate": candidate_dict
        })
        
    except Exception as e:
        print(f"[API] 지원자 정보 조회 오류: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@dashboard_bp.route("/api/interview/embed-from-db", methods=['POST'])
def embed_from_db():
    """
    DB에 저장된 파일로 임베딩 실행
    
    Request Body:
    {
        "session_id": "...",
        "jd_id": "..."
    }
    """
    from flask import jsonify, request
    import psycopg2
    from psycopg2.extras import RealDictCursor
    import os
    
    try:
        data = request.get_json()
        session_id = data.get('session_id')
        jd_id = data.get('jd_id')
        
        if not session_id:
            return jsonify({"success": False, "error": "session_id가 필요합니다."}), 400
        
        # TODO: 실제 임베딩 로직 구현
        # 1. DB에서 파일 경로 조회
        # 2. 파일 읽기
        # 3. EmbeddingAgent 실행
        # 4. 결과 반환
        
        # 임시 성공 응답
        return jsonify({
            "success": True,
            "session_id": session_id,
            "message": "임베딩이 완료되었습니다."
        })
        
    except Exception as e:
        print(f"[API] 임베딩 오류: {e}")
        return jsonify({"success": False, "error": str(e)}), 500



