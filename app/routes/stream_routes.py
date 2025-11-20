from flask import Blueprint, render_template, request, jsonify
from app.agents.stream_agent import StreamAgent
from app.agents.grammar_agent import get_postprocessor
from app.routes.state_routes import GLOBAL_STATE
import time

stream_bp = Blueprint("stream", __name__)
stream_agent = StreamAgent()
stt_postprocessor = get_postprocessor()  # 후처리기 초기화

# 세션 시작 시간 (서버 기준)
session_start_time = None


# ========================================
# 공통 함수
# ========================================
def get_request_data():
    """요청 JSON 데이터 가져오기"""
    return request.get_json()


def find_conversation(question_id):
    """질문 ID로 대화 찾기"""
    if not GLOBAL_STATE.interview_logs:
        return None
    
    return next(
        (q for q in GLOBAL_STATE.interview_logs if q["question_id"] == question_id),
        None
    )


def validate_required_params(data, *params):
    """필수 파라미터 검증"""
    missing = [p for p in params if not data.get(p)]
    if missing:
        return False, f"{', '.join(missing)}는 필수입니다"
    return True, None


def error_response(message, status_code=400):
    """에러 응답 생성"""
    return jsonify({"error": message}), status_code


def success_response(data=None):
    """성공 응답 생성"""
    return jsonify(data or {"status": "ok"})


# ========================================
# 라우트 핸들러
# ========================================
@stream_bp.route("/panel/stream")
def stream_panel():
    """면접 페이지 로드"""
    from flask import request
    
    # URL 파라미터에서 session_id, name, jd_id, position 가져오기 (interview_session.html에서 전달)
    session_id_param = request.args.get('session_id')
    candidate_name_param = request.args.get('name')
    jd_id_param = request.args.get('jd_id')
    position_param = request.args.get('position')
    
    if session_id_param:
        GLOBAL_STATE.session_id = session_id_param
        print(f"🔄 GLOBAL_STATE.session_id 설정: {session_id_param}")
    
    if candidate_name_param:
        GLOBAL_STATE.candidate_name = candidate_name_param
        print(f"🔄 GLOBAL_STATE.candidate_name 설정: {candidate_name_param}")
    
    if jd_id_param:
        GLOBAL_STATE.jd_id = jd_id_param
        print(f"🔄 GLOBAL_STATE.jd_id 설정: {jd_id_param}")
    
    if position_param:
        GLOBAL_STATE.position = position_param
        print(f"🔄 GLOBAL_STATE.position 설정: {position_param}")
    
    # GLOBAL_STATE의 questions 사용 (필수)
    if not GLOBAL_STATE.questions or len(GLOBAL_STATE.questions) == 0:
        return error_response("질문이 생성되지 않았습니다. Question Agent를 먼저 실행하세요.", 400)
    
    # state에 저장된 질문 사용 (모든 질문 사용)
    question_list = {
        f"q{i+1}": q 
        for i, q in enumerate(GLOBAL_STATE.questions)
    }
    
    # GLOBAL_STATE에 interview_logs 초기화
    global session_start_time
    session_start_time = None  # STT 시작 버튼을 눌러야 시작
    
    GLOBAL_STATE.interview_logs = [
        {
            "question_id": qid,
            "followups": [{"role": "면접관", "content": question}]
        }
        for qid, question in question_list.items()
    ]
    
    print(f"\n🎬 Stream 패널 로드")
    print(f"   - session_id: {GLOBAL_STATE.session_id}")
    print(f"   - 질문 개수: {len(question_list)}개")
    print(f"   - 질문 출처: GLOBAL_STATE\n")
    
    return render_template("agents/stream.html", question_list=question_list)

@stream_bp.route("/send", methods=["POST"])
def send_message():
    """메시지 저장 (서버 타임스탬프 자동 기록)"""
    data = get_request_data()
    
    # 필수 파라미터 검증
    is_valid, error_msg = validate_required_params(data, "text", "question_id")
    if not is_valid:
        return error_response(error_msg)

    # 대화 찾기
    conversation = find_conversation(data.get("question_id"))
    if not conversation:
        return error_response(f"{data.get('question_id')} 대화를 찾을 수 없습니다", 404)

    # 서버 기준 offset 계산
    offset_sec = None
    if session_start_time is not None:
        offset_sec = int(time.time() - session_start_time)
    
    # 메시지 저장
    message = {
        "role": data.get("role", "면접관"),
        "content": data.get("text"),
        "offset_sec": offset_sec  # 서버 타임스탬프
    }
    
    conversation["followups"].append(message)
    
    return success_response({
        "offset_sec": offset_sec
    })


@stream_bp.route("/stt_final_time", methods=["POST"])
def stt_final_time():
    """STT 종료 엔드포인트 (레거시 호환성 유지)"""
    return success_response()

@stream_bp.route("/session_start", methods=["POST"])
def session_start():
    """세션 시작 (STT 첫 시작 시점)"""
    global session_start_time
    
    # 이미 시작된 경우 무시
    if session_start_time is not None:
        return success_response({
            "message": "세션 이미 시작됨",
            "session_started": True
        })
    
    # 세션 시작 시간 기록
    session_start_time = time.time()
    print(f"🎬 면접 세션 시작: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(session_start_time))}")
    
    return success_response({
        "message": "세션 시작됨",
        "session_started": True,
        "start_time": session_start_time
    })

@stream_bp.route("/correct_stt", methods=["POST"])
def correct_stt():
    """STT 텍스트 후처리 (LLM 교정)"""
    data = get_request_data()
    
    # 필수 파라미터 검증
    is_valid, error_msg = validate_required_params(data, "text")
    if not is_valid:
        return error_response(error_msg)
    
    original_text = data.get("text", "")
    
    # LLM으로 텍스트 교정
    corrected_text = stt_postprocessor.correct(original_text)
    
    return success_response({
        "original": original_text,
        "corrected": corrected_text,
        "changed": original_text.strip() != corrected_text.strip()
    })


@stream_bp.route("/ai_followup", methods=["POST"])
def ai_followup():
    """AI 후속 질문 생성"""
    data = get_request_data()
    
    # 필수 파라미터 검증
    is_valid, error_msg = validate_required_params(data, "question_id", "text")
    if not is_valid:
        return error_response(error_msg)

    # 대화 찾기
    conversation = find_conversation(data.get("question_id"))
    if not conversation:
        return error_response(f"{data.get('question_id')} 대화가 없습니다", 404)

    # history에 resume_text, jd_text, portfolio_text 추가
    history_with_docs = conversation.get("followups", []).copy()
    
    # AI 에이전트 호출 (session_id + 문서 텍스트 전달)
    questions = stream_agent.generate_followups(
        text=data.get("text", ""),
        question_id=data.get("question_id"),
        history=history_with_docs,
        session_id=GLOBAL_STATE.session_id,  # RAG 검색용
        resume_text=GLOBAL_STATE.resume_text or "",  # 이력서 원문
        jd_text=GLOBAL_STATE.jd_text or "",  # JD 원문
        portfolio_text=GLOBAL_STATE.portfolio_text or "",  # 포트폴리오 원문
        regen=data.get("regen", False),
    )
    
    return success_response({"questions": questions})

@stream_bp.route("/question_activated", methods=["POST"])
def question_activated():
    """질문 활성화 시점 기록 (서버 타임스탬프)"""
    data = get_request_data()
    
    # 필수 파라미터 검증
    is_valid, error_msg = validate_required_params(data, "question_id")
    if not is_valid:
        return error_response(error_msg)

    # 대화 찾기
    conversation = find_conversation(data.get("question_id"))
    if not conversation:
        return error_response(f"{data.get('question_id')} not found", 404)

    # 서버 기준 offset 계산
    offset_sec = None
    if session_start_time is not None:
        offset_sec = int(time.time() - session_start_time)
    
    conversation["prompt_offset_sec"] = offset_sec
    
    return success_response({"offset_sec": offset_sec})

@stream_bp.route("/end_interview", methods=["POST"])
def end_interview():
    """면접 종료 및 interview_logs DB 저장"""
    import json
    from flask import request
    from app.utils.interview_store import save_interview_logs
    
    # 요청에서 session_id 가져오기
    data = request.get_json() or {}
    session_id_from_request = data.get('session_id')
    
    print("\n" + "="*80)
    print("📋 면접 종료 - Interview Logs")
    print(f"📍 요청받은 Session ID: {session_id_from_request}")
    print("="*80)
    
    # GLOBAL_STATE.interview_logs 사용
    interview_logs = GLOBAL_STATE.interview_logs or []
    
    # interview_logs 전체를 보기 좋게 출력
    print(json.dumps(interview_logs, ensure_ascii=False, indent=2))
    
    print("="*80)
    print(f"총 {len(interview_logs)}개 질문")
    
    # 각 질문별 통계
    for log in interview_logs:
        qid = log.get("question_id", "?")
        followup_count = len(log.get("followups", []))
        print(f"  - {qid}: {followup_count}개 대화")
    
    print("="*80 + "\n")
    
    # DB에 interview_logs 저장 (요청에서 받은 session_id 사용)
    try:
        # 요청에서 받은 session_id를 우선 사용, 없으면 GLOBAL_STATE 사용
        session_id = session_id_from_request or GLOBAL_STATE.session_id
        
        if not session_id:
            return error_response("Session ID가 없습니다.", 400)
        
        session_id = save_interview_logs(interview_logs, session_id)
        print(f"✅ DB 저장 완료 - Session ID: {session_id}\n")
        
        # 면접 상태를 'interview_completed'로 변경
        import psycopg2
        from psycopg2.extras import RealDictCursor
        import os
        
        db_host = os.getenv("DB_HOST", "localhost")
        db_port = os.getenv("DB_PORT", "5432")
        db_name = os.getenv("DB_NAME", "postgres")
        
        print(f"🔍 DB 연결 정보: {db_host}:{db_port}/{db_name}")
        
        conn = psycopg2.connect(
            host=db_host,
            port=db_port,
            database=db_name,
            user=os.getenv("DB_USER", "postgres"),
            password=os.getenv("DB_PASSWORD", "")
        )
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        cur.execute("""
            UPDATE interview.candidates
            SET status = 'interview_completed'
            WHERE session_id = %s
        """, (session_id,))
        
        conn.commit()
        cur.close()
        conn.close()
        
        print(f"✅ 면접 상태 변경 완료: interview_completed\n")
        
        return success_response({
            "message": "면접 종료 및 DB 저장 완료",
            "total_questions": len(interview_logs),
            "session_id": session_id,
            "candidate_name": GLOBAL_STATE.candidate_name or "지원자",
            "position": GLOBAL_STATE.position or "-",
            "redirect": "/panel/report"
        })
        
    except Exception as e:
        print(f"❌ DB 저장 실패: {e}\n")
        return error_response(f"DB 저장 실패: {str(e)}", 500)
