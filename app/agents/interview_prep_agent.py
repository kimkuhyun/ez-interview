from app.agents.parse_agent import ParseAgent
from app.agents.jd_agent import JDAgent
from app.agents.question_agent import QuestionAgent
from app.utils.schemas import InterviewPlan


class InterviewPrepAgent:
    """
    1) 문서들 파싱
    2) JD 분석
    3) 질문 생성
    을 한 번에 돌리고, Stream Agent / Report Agent 에 필요한 값을 반환
    """

    def __init__(self):
        self.parse_agent = ParseAgent()
        self.jd_agent = JDAgent()
        self.question_agent = QuestionAgent()

    def run(self, resume_text: str, jd_text: str) -> InterviewPlan:

        # 1. Parse Agent 호출: PDF를 읽은 text를 LLM 으로 구조화 (JSON)
        resume_parsed = self.parse_agent.parse_raw_text(resume_text)
        jd_parsed = self.parse_agent.parse_raw_text(jd_text)
        parsed_docs = [resume_parsed, jd_parsed]

        # 2. JD Agent 호출: JD 를 분석해, 요약, 평가 기준, 기술 요구 정도 return
        jd_analysis = self.jd_agent.analyze_jd(jd_parsed.raw_text)

        # 3. Question Agent 호출: 구조화 된 이력서, JD 를 토대로 대질문 5개 생성 (List)

        # 구조화 안된 텍스트 (나중에 비교 용으로 쓰자)
        # questions = self.question_agent.generate_questions(
        #     resume_text=resume_text,
        #     jd_text=jd_text,
        #     num_questions=5,
        # )

        # 구조화 된 텍스트
        questions = self.question_agent.generate_questions(
            resume_text=resume_parsed.structured["llm_raw"],
            jd_text=jd_parsed.structured["llm_raw"],
            num_questions=5,
        )

        return InterviewPlan(
            parsed_docs=parsed_docs,
            jd_analysis=jd_analysis,
            interview_questions=questions,
        )
