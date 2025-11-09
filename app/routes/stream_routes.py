from flask import Blueprint, render_template, request, jsonify
from openai import OpenAI
from app.config.config import Config
from app.state import state, now_iso

client = OpenAI(api_key=Config.OPENAI_API_KEY)
stream_bp = Blueprint("stream", __name__)

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

    print("🗒️ 초기 로그 구조화:", state["interview_logs"]) 
    return render_template("agents/stream.html", question_list=question_list)


@stream_bp.route("/send", methods=["POST"])
def send_message():
    """로그에 면접관/면접자 메시지 추가 (role 포함).
    클라이언트는 body에 {text, question_id, role}을 보내야 합니다.
    """
    data = request.get_json()
    print(f"[INCOMING /send] {data}")
    text = data.get("text")
    qid = data.get("question_id")
    role = data.get("role", "면접관")

    if not text or not qid:
        return jsonify({"error": "text와 question_id는 필수입니다"}), 400

    current_conv = next((q for q in state["interview_logs"] if q["question_id"] == qid), None)
    if not current_conv:
        return jsonify({"error": f"{qid} 대화를 찾을 수 없습니다"}), 404

    offset = data.get("offset")
    item = {
        "role": role,
        "content": text,
    }
    if offset is not None:
        try:
            item["offset_sec"] = int(offset)
        except Exception:
            pass

    current_conv["followups"].append(item)
    print(f"[STATE] Added followup to {qid}: {item}")

    return jsonify({"status": "ok"})


@stream_bp.route("/stt_final_time", methods=["POST"])
def stt_final_time():
    """STT가 최종 문장으로 종료된 시각을 기록합니다. 클라이언트에서 stt_final_stop 수신시 호출됩니다."""
    data = request.get_json()
    print(f"[INCOMING /stt_final_time] {data}")
    qid = data.get("question_id")
    # we expect an offset (seconds from session start). ts strings are not needed.
    offset = data.get("offset")

    if not qid:
        return jsonify({"error": "question_id가 필요합니다"}), 400

    current_conv = next((q for q in state["interview_logs"] if q["question_id"] == qid), None)
    if not current_conv:
        return jsonify({"error": f"{qid} 대화를 찾을 수 없습니다"}), 404

    if offset is not None:
        try:
            off = int(offset)
            current_conv.setdefault("stt_end_times", []).append(off)
            print(f"[STATE] Recorded STT final for {qid}: offset_sec={off}")
            return jsonify({"status": "ok", "offset": off})
        except Exception:
            pass

    current_conv.setdefault("stt_end_times", []).append(None)
    print(f"[STATE] Recorded STT final for {qid}: offset_sec=None")
    return jsonify({"status": "ok", "offset": None})


@stream_bp.route("/ai_followup", methods=["POST"])
def ai_followup():
    """후속 질문 3개 생성"""
    data = request.get_json()
    qid = data.get("question_id")
    latest_answer = data.get("text", "")

    if not qid or not latest_answer:
        return jsonify({"error": "question_id와 text가 필요합니다"}), 400

    current_conv = next((q for q in state["interview_logs"] if q["question_id"] == qid), None)
    if not current_conv:
        return jsonify({"error": f"{qid} 대화가 없습니다"}), 404

    history_text = "\n".join(
        f"{m['role']}: {m['content']}" for m in current_conv["followups"]
    )

    prompt = f"""
너는 면접관 AI야.
아래는 지금까지의 대화야.
면접자의 최신 답변을 참고해서 후속 질문 3개를 자연스럽게 만들어줘.

[이전 대화 기록]
{history_text}

[면접자의 최신 답변]
{latest_answer}

요구사항:
- 각 질문은 한 줄씩 출력
- 번호 없이 순수 질문만 3줄
"""

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "너는 면접관 AI야."},
                {"role": "user", "content": prompt}
            ]
        )

        ai_reply = response.choices[0].message.content.strip()
        # 한 줄씩 split
        questions = [q.strip("-• ").strip() for q in ai_reply.split("\n") if q.strip()]

        return jsonify({"questions": questions})

    except Exception as e:
        print(f"❌ OpenAI API 오류: {e}")
        return jsonify({"error": str(e)}), 500


@stream_bp.route("/debug/state")
def debug_state():
    """디버그용: 현재 in-memory 상태를 반환합니다. 운영 환경에서는 제거하세요."""
    # 안전을 위해 간단한 복사본을 반환
    try:
        return jsonify(state)
    except Exception as e:
        print("debug_state error:", e)
        return jsonify({"error": "unable to serialize state"}), 500


@stream_bp.route('/question_activated', methods=['POST'])
def question_activated():
    """Record that a question (prompt) was presented to the user at a given offset (seconds).
    Client sends {question_id, offset} where offset is seconds from session start.
    """
    data = request.get_json()
    qid = data.get('question_id')
    offset = data.get('offset')

    if not qid:
        return jsonify({'error': 'question_id required'}), 400

    current_conv = next((q for q in state['interview_logs'] if q['question_id'] == qid), None)
    if not current_conv:
        return jsonify({'error': f'{qid} not found'}), 404

    try:
        if offset is not None:
            current_conv['prompt_offset_sec'] = int(offset)
            print(f"[STATE] Recorded prompt offset for {qid}: offset_sec={offset}")
            return jsonify({'status': 'ok', 'offset': int(offset)})
    except Exception as e:
        print('question_activated error:', e)

    # fallback
    current_conv['prompt_offset_sec'] = None
    print(f"[STATE] Recorded prompt offset for {qid}: offset_sec=None")
    return jsonify({'status': 'ok', 'offset': None})

