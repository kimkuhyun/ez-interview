from flask import Blueprint, render_template, request, jsonify
from app.agents.stream_agent import StreamAgent
from app.stt.stt_postprocessor import get_postprocessor

stream_bp = Blueprint("stream", __name__)
stream_agent = StreamAgent()
stt_postprocessor = get_postprocessor()  # 후처리기 초기화

# 면접 대화 로그 (인메모리 저장)
interview_logs = []


# ========================================
# 공통 함수
# ========================================
def get_request_data():
    """요청 JSON 데이터 가져오기"""
    return request.get_json()


def find_conversation(question_id):
    """질문 ID로 대화 찾기"""
    return next(
        (q for q in interview_logs if q["question_id"] == question_id),
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
    question_list = {
        "q1": "자기소개를 해주세요.",
        "q2": "가장 어려웠던 프로젝트는 무엇인가요?",
        "q3": "팀 내에서 갈등을 어떻게 해결하셨나요?",
        "q4": "5년 뒤 본인의 커리어 목표는 무엇인가요?",
        "q5": "최근 관심있는 기술 트렌드는 무엇인가요?"
    }
    
    # 세션 초기화
    global interview_logs
    interview_logs = [
        {
            "question_id": qid,
            "followups": [{"role": "면접관", "content": question}]
        }
        for qid, question in question_list.items()
    ]
    
    return render_template("agents/stream.html", question_list=question_list)

@stream_bp.route("/send", methods=["POST"])
def send_message():
    """메시지 저장"""
    data = get_request_data()
    
    # 필수 파라미터 검증
    is_valid, error_msg = validate_required_params(data, "text", "question_id")
    if not is_valid:
        return error_response(error_msg)

    # 대화 찾기
    conversation = find_conversation(data.get("question_id"))
    if not conversation:
        return error_response(f"{data.get('question_id')} 대화를 찾을 수 없습니다", 404)

    # 메시지 저장
    message = {
        "role": data.get("role", "면접관"),
        "content": data.get("text")
    }
    
    offset = data.get("offset")
    if offset is not None:
        try:
            message["offset_sec"] = int(offset)
        except (ValueError, TypeError):
            pass
    
    conversation["followups"].append(message)
    return success_response()


@stream_bp.route("/stt_final_time", methods=["POST"])
def stt_final_time():
    """STT 종료 엔드포인트 (레거시 호환성 유지)"""
    return success_response()

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

    # AI 에이전트 호출
    questions = stream_agent.generate_followups(
        text=data.get("text", ""),
        question_id=data.get("question_id"),
        history=conversation.get("followups", []),
        regen=data.get("regen", False),
    )
    
    return success_response({"questions": questions})

@stream_bp.route("/question_activated", methods=["POST"])
def question_activated():
    """질문 활성화 시점 기록"""
    data = get_request_data()
    
    # 필수 파라미터 검증
    is_valid, error_msg = validate_required_params(data, "question_id")
    if not is_valid:
        return error_response(error_msg)

    # 대화 찾기
    conversation = find_conversation(data.get("question_id"))
    if not conversation:
        return error_response(f"{data.get('question_id')} not found", 404)

    # offset 저장
    offset = data.get("offset")
    try:
        if offset is not None:
            conversation["prompt_offset_sec"] = int(offset)
            return success_response({"offset": int(offset)})
    except (ValueError, TypeError):
        pass

    conversation["prompt_offset_sec"] = None
    return success_response({"offset": None})

@stream_bp.route("/end_interview", methods=["POST"])
def end_interview():
    """면접 종료 및 로그 출력"""
    import json
    
    print("\n" + "="*80)
    print("📋 면접 종료 - Interview Logs")
    print("="*80)
    
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
    
    # TODO: 추후 VectorDB 저장 로직 추가
    # vector_db.insert(interview_logs)
    
    return success_response({
        "message": "면접 종료 완료",
        "total_questions": len(interview_logs)
    })
