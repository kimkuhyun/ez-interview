from flask import Blueprint, render_template, request, jsonify
from app.config.config import Config
from app.state import state, now_iso
from app.agents.stream_agent import StreamAgent

stream_bp = Blueprint("stream", __name__)
stream_agent = StreamAgent()

# note: using in-memory `state` from app.state


@stream_bp.route("/panel/stream")
def stream_panel():
    """Stream Agent 패널 로드"""
    question_list = {
        "q1": "자기소개를 해주세요.",
        "q2": "가장 어려웠던 프로젝트는 무엇인가요?",
        "q3": "팀 내에서 갈등을 어떻게 해결하셨나요?",
        "q4": "5년 뒤 본인의 커리어 목표는 무엇인가요?",
        "q5": "최근 관심있는 기술 트렌드는 무엇인가요?"
    }

    # ✅ 초기화: 각 질문을 followups에 면접관 메시지로 저장 + 세션 시작 시간 기록
    state["interview_start_ts"] = now_iso()
    state["interview_end_ts"] = None
    state["interview_logs"] = [
        {
            "question_id": key,
            "followups": [
                {
                    "role": "면접관",
                    "content": value
                }
            ],
            "stt_end_times": [],
        }
        for key, value in question_list.items()
    ]

    return render_template("agents/stream.html", question_list=question_list)


@stream_bp.route("/send", methods=["POST"])
def send_message():
    data = request.get_json()
    text = data.get("text")
    qid = data.get("question_id")
    role = data.get("role", "면접관")

    if not text or not qid:
        return jsonify({"error": "text와 question_id는 필수입니다"}), 400

    current_conv = next(
        (q for q in state["interview_logs"] if q["question_id"] == qid),
        None,
    )
    if not current_conv:
        return jsonify({"error": f"{qid} 대화를 찾을 수 없습니다"}), 404

    offset = data.get("offset")
    item = {"role": role, "content": text}
    if offset is not None:
        try:
            item["offset_sec"] = int(offset)
        except Exception:
            pass

    current_conv["followups"].append(item)
    return jsonify({"status": "ok"})


@stream_bp.route("/stt_final_time", methods=["POST"])
def stt_final_time():
    """STT가 최종 문장으로 종료된 시각을 기록합니다. 클라이언트에서 stt_final_stop 수신시 호출됩니다."""
    data = request.get_json()
    qid = data.get("question_id")
    # we expect an offset (seconds from session start). ts strings are not needed.
    offset = data.get("offset")

    if not qid:
        return jsonify({"error": "question_id가 필요합니다"}), 400

    print("======== stt 종료 시점 ===")
    print(state["interview_logs"])
    current_conv = next((q for q in state["interview_logs"] if q["question_id"] == qid), None)
    print("current_conv:", current_conv)
    if not current_conv:
        return jsonify({"error": f"{qid} 대화를 찾을 수 없습니다"}), 404

    if offset is not None:
        try:
            off = int(offset)
            current_conv.setdefault("stt_end_times", []).append(off)
            return jsonify({"status": "ok", "offset": off})
        except Exception:
            pass

    current_conv.setdefault("stt_end_times", []).append(None)
    return jsonify({"status": "ok", "offset": None})


@stream_bp.route("/ai_followup", methods=["POST"])
def ai_followup():
    """후속 질문 3개 생성"""
    data = request.get_json()
    qid = data.get("question_id")
    latest_answer = data.get("text", "")
    regen = data.get("regen", False)

    if not qid or not latest_answer:
        return jsonify({"error": "question_id와 text가 필요합니다"}), 400

    current_conv = next((q for q in state["interview_logs"] if q["question_id"] == qid), None)
    if not current_conv:
        return jsonify({"error": f"{qid} 대화가 없습니다"}), 404

    # AI agent에 질문 생성 요청 (history는 구조체로 전달)
    questions = stream_agent.generate_followups(
        text=latest_answer,
        question_id=qid,
        history=current_conv.get("followups", []),
        regen=regen,
    )

    return jsonify({"questions": questions})



@stream_bp.route('/question_activated', methods=['POST'])
def question_activated():
    """
     각 질문이 사용자에게 제시된 시점 기록
    """
    data = request.get_json()
    qid = data.get('question_id')
    offset = data.get('offset')

    # 질문 ID 유효성 검사
    if not qid:
        return jsonify({'error': 'question_id required'}), 400
    
    # 현재 질문에 해당하는 대화 기록 찾기
    current_conv = next((q for q in state['interview_logs'] if q['question_id'] == qid), None)
    if not current_conv:
        return jsonify({'error': f'{qid} not found'}), 404

    try:
        if offset is not None:
            # 질문 제시 시점 기록 (세션 시작 후 몇 초?)
            current_conv['prompt_offset_sec'] = int(offset)
            return jsonify({'status': 'ok', 'offset': int(offset)})
    except Exception as e:
        pass

    # offset 처리 실패시 None으로 기록
    current_conv['prompt_offset_sec'] = None
    return jsonify({'status': 'ok', 'offset': None})

