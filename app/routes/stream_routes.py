from flask import Blueprint, render_template, request, jsonify
from datetime import datetime

stream_bp = Blueprint("stream", __name__)

interview_logs = []

@stream_bp.route("/panel/stream")
def stream_panel():
    """Stream Agent 패널 로드"""
    return render_template("agents/stream.html")

@stream_bp.route("/send", methods=["POST"])
def send_message():
    """사용자 메시지를 받고 응답 반환"""
    data = request.get_json()
    text = data.get("text")

    if not text:
        return jsonify({"error": "text는 필수입니다"}), 400

    interview_logs.append({
        "role": "user",
        "content": text,
        "timestamp": datetime.now().isoformat()
    })

    bot_reply = f"🤖 '{text}'에 대한 LLM의 예시 응답입니다."
    interview_logs.append({
        "role": "assistant",
        "content": bot_reply,
        "timestamp": datetime.now().isoformat()
    })
    print(interview_logs)
    return jsonify({"reply": bot_reply})
