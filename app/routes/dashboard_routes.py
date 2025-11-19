from flask import Blueprint, render_template, Response
from pathlib import Path

dashboard_bp = Blueprint('dashboard', __name__)

@dashboard_bp.route("/admin")
def admin_dashboard():
    return render_template("base.html")

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
        
        cur.close()
        conn.close()
        
        return jsonify({
            "success": True,
            "files": files
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
        
        # 새 세션 생성 (동명이인 가능성 고려)
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
