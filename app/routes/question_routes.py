from flask import Blueprint, request, jsonify, render_template
from app.agents.interview_prep_agent import InterviewPrepAgent
from app.utils.file_utils import extract_text

question_bp = Blueprint("question", __name__)

@question_bp.route("/panel/question")
def question_panel():
    return render_template("agents/question.html")

@question_bp.route("/api/interview-prep", methods=["POST"])
def build_interview_plan():
    # 파일 받음
    resume_file = request.files.get("resume")
    jd_file = request.files.get("jd")

    # PDF 텍스트 추출
    resume_text = extract_text(resume_file)
    jd_text = extract_text(jd_file)

    # Interview Prep 에이전트 호출
    agent = InterviewPrepAgent()
    plan = agent.run(resume_text, jd_text)

    return jsonify(plan.model_dump())   # pydantic → dict → json
