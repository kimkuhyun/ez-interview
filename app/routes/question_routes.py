from flask import Blueprint, request, jsonify, render_template
from app.agents.question_agent import QuestionAgent

question_bp = Blueprint("question", __name__)

@question_bp.route("/panel/question")
def question_panel():
    return render_template("agents/question.html")

@question_bp.route("/api/question", methods=["POST"])
def generate_question():
    agent = QuestionAgent()
    data = request.json
    resume = data.get("resume")
    cover = data.get("cover_letter")
    jd = data.get("job_desc")

    # Vectorstore + Chain 초기화
    agent.build_vectorstore([resume, cover, jd])
    agent.build_chain()

    # 질문 생성
    result = agent.generate_questions("지원자의 역량과 경험을 평가할 질문을 만들어줘.")
    return jsonify({"questions": result})
