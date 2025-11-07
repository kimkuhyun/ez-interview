from flask import Blueprint, render_template, request, jsonify
from openai import OpenAI
from app.config.config import Config

client = OpenAI(api_key=Config.OPENAI_API_KEY)
stream_bp = Blueprint("stream", __name__)

# --- 인터뷰 로그 ---
interview_logs = []


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

    # ✅ 초기화: 각 질문을 followups에 면접관 메시지로 저장
    global interview_logs
    interview_logs = [
        {
            "question_id": key,
            "followups": [
                {
                    "role": "면접관",
                    "content": value
                }
            ]
        }
        for key, value in question_list.items()
    ]

    print("🗒️ 초기 로그 구조화:", interview_logs)
    return render_template("agents/stream.html", question_list=question_list)


@stream_bp.route("/send", methods=["POST"])
def send_message():
    """면접자의 답변 저장"""
    data = request.get_json()
    text = data.get("text")
    qid = data.get("question_id")

    if not text or not qid:
        return jsonify({"error": "text와 question_id는 필수입니다"}), 400

    # ✅ 해당 질문(qid)에 면접자 답변 추가
    current_conv = next((q for q in interview_logs if q["question_id"] == qid), None)
    if not current_conv:
        return jsonify({"error": f"{qid} 대화를 찾을 수 없습니다"}), 404

    current_conv["followups"].append({
        "role": "면접자",
        "content": text
    })

    return jsonify({"status": "ok"})


@stream_bp.route("/ai_followup", methods=["POST"])
def ai_followup():
    """후속 질문 3개 생성"""
    data = request.get_json()
    qid = data.get("question_id")
    latest_answer = data.get("text", "")

    if not qid or not latest_answer:
        return jsonify({"error": "question_id와 text가 필요합니다"}), 400

    current_conv = next((q for q in interview_logs if q["question_id"] == qid), None)
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

