from flask import Blueprint, request, jsonify, render_template
from app.db.db_connection import get_connection
from werkzeug.utils import secure_filename
import uuid
import os
from pathlib import Path

position_bp = Blueprint('positions', __name__, url_prefix='/api/positions')

# 파일 업로드 설정
UPLOAD_FOLDER = Path(__file__).parent.parent.parent / 'uploads' / 'jds'
UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)
ALLOWED_EXTENSIONS = {'pdf', 'docx', 'txt'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@position_bp.route('', methods=['GET'])
def get_positions():
    """포지션 목록 조회"""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT jd_id, title, file_path, keywords, is_active, created_at
        FROM interview.job_descriptions
        WHERE is_active = true
        ORDER BY created_at DESC
    """)
    rows = cur.fetchall()
    
    positions = []
    for row in rows:
        # 각 포지션의 지원자 수 조회
        cur.execute("""
            SELECT COUNT(*) 
            FROM interview.candidates 
            WHERE position = %s AND status = 'pending'
        """, (row[1],))
        candidate_count = cur.fetchone()[0]
        
        positions.append({
            "id": str(row[0]),
            "name": row[1],
            "jd_file": row[2],
            "keywords": row[3] or [],
            "created_at": row[5].isoformat() if row[5] else None,
            "candidate_count": candidate_count
        })
    
    cur.close()
    conn.close()
    
    return jsonify({"positions": positions})

@position_bp.route('', methods=['POST'])
def create_position():
    """포지션 등록 (파일 업로드 포함)"""
    name = request.form.get('name')
    file = request.files.get('file')
    
    if not name:
        return jsonify({"error": "name이 필요합니다"}), 400
    
    # jd_id 먼저 생성
    jd_id = str(uuid.uuid4())
    
    jd_file_path = None
    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        file_ext = filename.rsplit('.', 1)[1] if '.' in filename else 'pdf'
        # jd_id를 파일명으로 사용
        unique_filename = f"JD_{jd_id}.{file_ext}"
        file_path = UPLOAD_FOLDER / unique_filename
        file.save(file_path)
        jd_file_path = f"jds/{unique_filename}"  # 상대 경로만 저장
    
    conn = get_connection()
    cur = conn.cursor()
    
    cur.execute("""
        INSERT INTO interview.job_descriptions (jd_id, title, file_path, is_active)
        VALUES (%s, %s, %s, true)
        RETURNING jd_id
    """, (jd_id, name, jd_file_path))
    
    conn.commit()
    cur.close()
    conn.close()
    
    return jsonify({"jd_id": jd_id, "message": "포지션 등록 완료", "file_path": jd_file_path})

@position_bp.route('/<jd_id>', methods=['GET'])
def get_position(jd_id):
    """포지션 상세 조회"""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT jd_id, title, file_path, keywords, is_active, created_at
        FROM interview.job_descriptions
        WHERE jd_id = %s::uuid
    """, (jd_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    
    if not row:
        return jsonify({"error": "포지션을 찾을 수 없습니다"}), 404
    
    return jsonify({
        "id": str(row[0]),
        "name": row[1],
        "jd_file": row[2],
        "keywords": row[3] or [],
        "created_at": row[5].isoformat() if row[5] else None
    })

@position_bp.route('/<jd_id>/recommend-keywords', methods=['POST'])
def recommend_keywords(jd_id):
    """AI 키워드 추천 (GPT-4o)"""
    from app.utils.file_utils import extract_text
    from openai import OpenAI
    from app.config.config import Config
    
    print(f"\n🔍 [키워드 추천] jd_id: {jd_id}")
    
    conn = get_connection()
    cur = conn.cursor()
    
    # 1️⃣ DB에서 JD 파일 경로 조회
    cur.execute("""
        SELECT file_path FROM interview.job_descriptions 
        WHERE jd_id = %s::uuid
    """, (jd_id,))
    
    row = cur.fetchone()
    cur.close()
    conn.close()
    
    if not row or not row[0]:
        return jsonify({"error": "JD 파일을 찾을 수 없습니다"}), 404
    
    # 2️⃣ 파일 읽기
    file_path = Path(__file__).parent.parent.parent / 'uploads' / row[0]
    
    if not file_path.exists():
        return jsonify({"error": "JD 파일이 존재하지 않습니다"}), 404
    
    print(f"   📄 파일 경로: {file_path}")
    
    # 3️⃣ 파일에서 텍스트 추출 (PDF/DOCX/이미지 모두 지원)
    try:
        with open(file_path, 'rb') as f:
            from werkzeug.datastructures import FileStorage
            file_storage = FileStorage(
                stream=f,
                filename=file_path.name,
                content_type='application/octet-stream'
            )
            jd_text = extract_text(file_storage)
        
        print(f"   ✅ 텍스트 추출 완료: {len(jd_text)} 자")
        
        if not jd_text or len(jd_text) < 50:
            return jsonify({"error": "JD 파일에서 텍스트를 추출할 수 없습니다"}), 400
        
    except Exception as e:
        print(f"   ❌ 파일 읽기 실패: {e}")
        return jsonify({"error": f"파일 읽기 실패: {str(e)}"}), 500
    
    # 4️⃣ GPT-4o로 키워드 추출
    try:
        client = OpenAI(api_key=Config.OPENAI_API_KEY)
        
        print(f"   🤖 GPT-4o로 키워드 추출 중...")
        
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": """당신은 채용 키워드 추출 전문가입니다.
채용공고(JD)에서 10-15개의 핵심 기술 스킬, 도구, 자격요건을 추출하세요.
다음 항목에 집중하세요:
- 프로그래밍 언어 (Python, Java 등)
- 프레임워크 (React, Django 등)
- 도구 (Docker, Git, AWS 등)
- 기술 개념 (REST API, Microservices 등)
- 자격요건 (경력 년수, 학력 등)

반드시 JSON 배열 형식으로만 출력하세요. JD에 나타난 그대로 한글 또는 영어로 작성하세요.
예시: ["Python", "Django", "PostgreSQL", "Docker", "AWS", "3년 이상", "REST API"]"""
                },
                {
                    "role": "user",
                    "content": f"다음 채용공고에서 핵심 키워드를 추출해주세요:\n\n{jd_text[:7000]}"
                }
            ],
            temperature=0.3,
            max_tokens=3000
        )
        
        result = response.choices[0].message.content.strip()
        print(f"   ✅ GPT 응답: {result[:200]}")
        
        # JSON 파싱
        import json
        
        # ```json ``` 제거
        if result.startswith("```json"):
            result = result[7:]
        if result.startswith("```"):
            result = result[3:]
        if result.endswith("```"):
            result = result[:-3]
        
        keywords = json.loads(result.strip())
        
        print(f"   ✅ 키워드 추출 완료: {len(keywords)}개")
        print(f"      {keywords}")
        
        return jsonify({"keywords": keywords})
        
    except Exception as e:
        print(f"   ❌ GPT 키워드 추출 실패: {e}")
        import traceback
        traceback.print_exc()
        
        # Fallback: 기본 키워드 반환
        fallback_keywords = ['']
        return jsonify({"keywords": fallback_keywords, "warning": "AI 추천 실패, 기본 키워드 반환"})

@position_bp.route('/<jd_id>/keywords', methods=['PUT'])
def update_keywords(jd_id):
    """키워드 업데이트"""
    data = request.get_json()
    keywords = data.get('keywords', [])
    
    conn = get_connection()
    cur = conn.cursor()
    
    cur.execute("""
        UPDATE interview.job_descriptions
        SET keywords = %s, updated_at = NOW()
        WHERE jd_id = %s::uuid
    """, (keywords, jd_id))
    
    conn.commit()
    cur.close()
    conn.close()
    
    return jsonify({"message": "키워드 업데이트 완료", "keywords": keywords})

@position_bp.route('/<jd_id>', methods=['DELETE'])
def delete_position(jd_id):
    """포지션 삭제"""
    conn = get_connection()
    cur = conn.cursor()
    
    cur.execute("""
        UPDATE interview.job_descriptions
        SET is_active = false
        WHERE jd_id = %s::uuid
    """, (jd_id,))
    
    conn.commit()
    cur.close()
    conn.close()
    
    return jsonify({"message": "포지션이 삭제되었습니다"})

@position_bp.route('/<jd_id>/jd', methods=['PUT'])
def update_jd_file(jd_id):
    """JD 파일 변경 (실제 파일 업로드)"""
    file = request.files.get('file')
    
    if not file or not allowed_file(file.filename):
        return jsonify({"error": "유효한 파일을 업로드해주세요"}), 400
    
    filename = secure_filename(file.filename)
    file_ext = filename.rsplit('.', 1)[1] if '.' in filename else 'pdf'
    # jd_id를 파일명으로 사용
    unique_filename = f"JD_{jd_id}.{file_ext}"
    file_path = UPLOAD_FOLDER / unique_filename
    file.save(file_path)
    
    conn = get_connection()
    cur = conn.cursor()
    
    # 기존 파일 경로 조회 (나중에 삭제할 수 있도록)
    cur.execute("SELECT file_path FROM interview.job_descriptions WHERE jd_id = %s::uuid", (jd_id,))
    old_file = cur.fetchone()
    
    # 새 파일 경로로 업데이트 (상대 경로로 저장)
    relative_path = f"jds/{unique_filename}"
    cur.execute("""
        UPDATE interview.job_descriptions
        SET file_path = %s, updated_at = NOW()
        WHERE jd_id = %s::uuid
    """, (relative_path, jd_id))
    
    conn.commit()
    cur.close()
    conn.close()
    
    # 기존 파일 삭제 (선택사항)
    if old_file and old_file[0]:
        old_path = Path(__file__).parent.parent.parent / 'uploads' / old_file[0]
        if old_path.exists():
            old_path.unlink()
    
    return jsonify({"message": "JD 파일이 변경되었습니다", "file_path": relative_path})

@position_bp.route('/<jd_id>/match', methods=['POST'])
def match_candidates(jd_id):
    """키워드 기반 지원자 매칭 (최적화 버전)"""
    from app.utils.rag_retriever import search_similar_chunks
    
    conn = get_connection()
    cur = conn.cursor()
    
    # 요청 바디에서 선택된 키워드 받기
    data = request.get_json()
    keywords = data.get('keywords', [])
    
    if not keywords:
        # 바디에 없으면 DB에서 조회 (하위 호환)
        cur.execute("SELECT keywords FROM interview.job_descriptions WHERE jd_id = %s::uuid", (jd_id,))
        row = cur.fetchone()
        if not row or not row[0]:
            cur.close()
            conn.close()
            return jsonify({"error": "키워드가 설정되지 않았습니다"}), 400
        keywords = row[0]
    
    # ✅ 키워드를 하나의 쿼리로 합침 (API 호출 최소화)
    combined_query = " ".join(keywords)
    print(f"🔍 매칭 쿼리: {combined_query}")
    print(f"   📝 선택된 키워드 {len(keywords)}개: {keywords}")
    
    # ✅ 포지션의 title 조회 (candidates.position과 매칭하기 위해)
    cur.execute("SELECT title FROM interview.job_descriptions WHERE jd_id = %s::uuid", (jd_id,))
    position_row = cur.fetchone()
    if not position_row:
        cur.close()
        conn.close()
        return jsonify({"error": "포지션을 찾을 수 없습니다"}), 404
    
    position_title = position_row[0]
    print(f"   📍 포지션명: {position_title}")
    
    # ✅ pending 지원자 조회 (candidates.position = job_descriptions.title로 매칭)
    cur.execute("""
        SELECT session_id, name 
        FROM interview.candidates 
        WHERE status = 'pending' 
          AND position = %s
    """, (position_title,))
    candidates = cur.fetchall()
    
    print(f"   📋 매칭 대상 지원자: {len(candidates)}명 (포지션: {position_title})")
    
    results = []
    for session_id, name in candidates:
        print(f"👤 지원자 매칭 중: {name} ({session_id})")
        
        try:
            # ✅ 이력서 + 포트폴리오 통합 검색
            resume_chunks = search_similar_chunks(combined_query, str(session_id), "resume", top_k=3)
            portfolio_chunks = search_similar_chunks(combined_query, str(session_id), "portfolio", top_k=2)
            chunks = resume_chunks + portfolio_chunks
            
            # 매칭된 이력서 내용 추출
            matched_snippets = []
            valid_chunks = [c for c in chunks if c['score'] >= 0.3]
            
            if valid_chunks:
                match_score = sum(c['score'] for c in valid_chunks) / len(valid_chunks)
                print(f"   ✅ 매칭 점수: {match_score:.4f} ({len(valid_chunks)}개 유효 청크)")
                
                for chunk in valid_chunks:
                    content = chunk['content'].strip()
                    section = chunk.get('metadata', {}).get('section', '').lower()
                    
                    # 불필요한 섹션 스킵
                    if any(x in section for x in ['name', 'contact', '이름', '연락처', '기간', '날짜']):
                        continue
                    
                    # 첫 의미있는 문장 추출
                    for line in content.split('\n'):
                        line = line.strip()
                        if len(line) >= 20 and not line.startswith(('•', '-', '*', '●')) and '~' not in line:
                            matched_snippets.append(line[:50] + ('...' if len(line) > 50 else ''))
                            break
                    
                    if len(matched_snippets) >= 3:
                        break
                
                for i, s in enumerate(matched_snippets[:3]):
                    print(f"      [{i+1}] {s}")
            else:
                match_score = 0
                print(f"   ⚠️  유효한 매칭 없음 (점수 0.3 미만)")
        
        except Exception as e:
            print(f"   ❌ 매칭 중 에러: {e}")
            match_score = 0
            matched_snippets = []
        
        # jd_id 업데이트
        cur.execute("""
            UPDATE interview.candidates 
            SET jd_id = %s::uuid 
            WHERE session_id = %s::uuid
        """, (jd_id, str(session_id)))
        
        results.append({
            "session_id": str(session_id),
            "name": name,
            "score": round(match_score * 100, 1),
            "matched_snippets": matched_snippets,  # 매칭된 이력서 내용
            "has_portfolio": False  # 나중에 구현
        })
    
    conn.commit()
    cur.close()
    conn.close()
    
    print(f"✅ 매칭 완료: {len(results)}명")
    return jsonify({"candidates": sorted(results, key=lambda x: x['score'], reverse=True)})

@position_bp.route('/candidates/<session_id>/resume', methods=['GET'])
def get_candidate_resume(session_id):
    """지원자 이력서 조회"""
    from flask import send_file
    
    conn = get_connection()
    cur = conn.cursor()
    
    cur.execute("""
        SELECT file_path FROM interview.files 
        WHERE session_id = %s::uuid AND type = 'resume'
    """, (session_id,))
    
    row = cur.fetchone()
    cur.close()
    conn.close()
    
    if row and row[0]:
        # 상대 경로를 절대 경로로 변환
        file_path = Path(__file__).parent.parent.parent / 'uploads' / row[0]
        if file_path.exists():
            return send_file(file_path, mimetype='application/pdf')
    
    return jsonify({"error": "이력서를 찾을 수 없습니다"}), 404

@position_bp.route('/candidates/<session_id>/portfolio', methods=['GET'])
def get_candidate_portfolio(session_id):
    """지원자 포트폴리오 조회"""
    from flask import send_file
    
    conn = get_connection()
    cur = conn.cursor()
    
    cur.execute("""
        SELECT file_path FROM interview.files 
        WHERE session_id = %s::uuid AND type = 'portfolio'
    """, (session_id,))
    
    row = cur.fetchone()
    cur.close()
    conn.close()
    
    if row and row[0]:
        # 상대 경로를 절대 경로로 변환
        file_path = Path(__file__).parent.parent.parent / 'uploads' / row[0]
        if file_path.exists():
            return send_file(file_path, mimetype='application/pdf')
    
    return jsonify({"error": "포트폴리오를 찾을 수 없습니다"}), 404

@position_bp.route('/candidates/<session_id>/status', methods=['PUT'])
def update_candidate_status(session_id):
    """지원자 상태 변경"""
    data = request.get_json()
    status = data.get('status')
    
    if not status:
        return jsonify({"error": "status가 필요합니다"}), 400
    
    conn = get_connection()
    cur = conn.cursor()
    
    cur.execute("""
        UPDATE interview.candidates
        SET status = %s, updated_at = NOW()
        WHERE session_id = %s::uuid
    """, (status, session_id))
    
    affected_rows = cur.rowcount
    conn.commit()
    
    # ✅ DB 업데이트 확인
    cur.execute("""
        SELECT name, status FROM interview.candidates 
        WHERE session_id = %s::uuid
    """, (session_id,))
    result = cur.fetchone()
    
    cur.close()
    conn.close()
    
    if affected_rows > 0 and result:
        print(f"✅ [{result[0]}] 상태 변경: {result[1]} (DB 업데이트 성공)")
        return jsonify({
            "success": True,
            "message": f"상태 변경 완료: {status}",
            "name": result[0],
            "status": result[1]
        })
    else:
        print(f"❌ 상태 변경 실패: session_id={session_id}")
        return jsonify({"success": False, "error": "업데이트 실패"}), 500
