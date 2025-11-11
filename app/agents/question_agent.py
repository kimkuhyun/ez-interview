import os
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.runnables import RunnableLambda, RunnableSequence
from app.utils.schemas import InterviewQuestions


class QuestionAgent:
    """
    이 Agent는 RAG 검색은 수행하지 않고,
    구조화된 resume_text, jd_text를 그대로 사용해 LLM 질문 생성만 담당한다.
    """

    def __init__(self):
        api_key = os.getenv("OPENAI_API_KEY")

        # LLM 초기화
        self.llm = ChatOpenAI(
            model="gpt-4o-mini",
            temperature=0.5,
            openai_api_key=api_key,
        )

        # JSON 파서 (출력 구조를 명시)
        self.parser = JsonOutputParser()

        # 프롬프트 템플릿 정의
        self.prompt = ChatPromptTemplate.from_template(
            "너는 기술면접 보조 AI야. 아래 지원자의 이력과 JD를 기반으로, "
            "지원자의 경험을 파악할 수 있는 면접 질문을 JSON 형태로 생성해.\n"
            "출력은 반드시 다음 형식을 따라야 해:\n"
            "{format_instructions}\n\n"
            "[JD]\n{jd_text}\n\n"
            "[RESUME]\n{resume_text}\n\n"
            "총 {num_questions}개의 질문을 bullet 없이 한 줄씩 출력해."
            "질문만 포함하고, 인사말·요약문·설명문은 쓰지 마."
        )

        # LLM Runnable 체인 (prompt → llm → parser)
        self.chain = self.prompt | self.llm | self.parser

    # 메인 로직
    def generate_questions(self, resume_text: str, jd_text: str, num_questions: int = 5, ) -> InterviewQuestions:
        """이력서 + 자소서 + JD 기반 질문 생성"""

        # LLM 실행
        try:
            response = self.chain.invoke({
                "jd_text": jd_text,
                "resume_text": resume_text[:2000],
                "num_questions": num_questions,
                "format_instructions": self.parser.get_format_instructions(),
            })
        except Exception as e:
            print("[QuestionAgent] ⚠️ JSON 파싱 실패:", e)
            response = {"questions": []}

        # 결과 파싱
        questions = response.get("questions", [])
        return InterviewQuestions(questions=questions[:num_questions])

    # LangGraph 연결용 Runnable
    def as_runnable(self):
        return RunnableLambda(
            lambda inputs: self.generate_questions(
                resume_text=inputs["resume_text"],
                jd_text=inputs["jd_text"],
                num_questions=inputs.get("num_questions", 5)
            )
        )