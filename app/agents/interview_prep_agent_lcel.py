import time
from langchain_core.runnables import RunnableLambda, RunnableSequence
from app.agents.parse_agent import ParseAgent
from app.agents.jd_agent import JDAgent
from app.agents.question_agent import QuestionAgent
from app.utils.schemas import InterviewPlan, ParsedDoc


class InterviewPrepAgentLCEL:
    """
    Parse → JD 분석 → 질문 생성
    전체를 LangChain Runnable Sequence로 구성
    """

    def __init__(self):
        self.parse_agent = ParseAgent().as_runnable()
        self.jd_agent = JDAgent().as_runnable()
        self.question_agent = QuestionAgent().as_runnable()

        # ✅ LCEL Sequence (trace-friendly)
        self.chain = RunnableSequence(
            RunnableLambda(self._run_parse),
            RunnableLambda(self._run_jd),
            RunnableLambda(self._run_question),
            RunnableLambda(self._summarize_result),
        )

    # ----------------------------- 단계별 처리 -----------------------------
    def _run_parse(self, inputs: dict):
        """1️⃣ Parse 단계"""
        start = time.perf_counter()
        resume_parsed = self.parse_agent.invoke({"text": inputs["resume_text"]})
        jd_parsed = self.parse_agent.invoke({"text": inputs["jd_text"]})
        parsed_docs = [resume_parsed, jd_parsed]
        elapsed = time.perf_counter() - start

        return {
            "resume_parsed": resume_parsed,
            "jd_parsed": jd_parsed,
            "parsed_docs": parsed_docs,
            "timings": {"parse_agent": elapsed},
        }

    def _run_jd(self, inputs: dict):
        """2️⃣ JD 분석"""
        start = time.perf_counter()
        jd_parsed = inputs["jd_parsed"]

        # ✅ ParsedDoc 이면 .raw_text, dict면 ["raw_text"]
        if isinstance(jd_parsed, ParsedDoc):
            jd_text = jd_parsed.raw_text
        else:
            jd_text = jd_parsed["raw_text"]
        jd_analysis = self.jd_agent.invoke({"jd_text": jd_text})

        elapsed = time.perf_counter() - start

        inputs["timings"]["jd_agent"] = elapsed
        inputs["jd_analysis"] = jd_analysis
        return inputs

    def _run_question(self, inputs: dict):
        """3️⃣ 질문 생성"""
        start = time.perf_counter()
        resume_parsed = inputs["resume_parsed"]
        jd_parsed = inputs["jd_parsed"]

        # ✅ 둘 다 ParsedDoc 일 가능성이 높으니까 동일하게 처리
        if isinstance(resume_parsed, ParsedDoc):
            resume_text = resume_parsed.raw_text
        else:
            resume_text = resume_parsed["raw_text"]

        if isinstance(jd_parsed, ParsedDoc):
            jd_text = jd_parsed.raw_text
        else:
            jd_text = jd_parsed["raw_text"]
        questions = self.question_agent.invoke({
            "resume_text": resume_text,
            "jd_text": jd_text,
            "num_questions": 5,
        })
        elapsed = time.perf_counter() - start

        inputs["timings"]["question_agent"] = elapsed
        inputs["interview_questions"] = questions
        return inputs

    def _summarize_result(self, inputs: dict):
        """4️⃣ 결과 요약 및 Pydantic으로 래핑"""
        timings = inputs["timings"]
        timings["total_ai_time"] = (
            timings["parse_agent"] + timings["jd_agent"] + timings["question_agent"]
        )
        timings["total_runtime"] = sum(timings.values())

        print("\n[⏱ InterviewPrepAgent Time Report]")
        for k, v in timings.items():
            print(f" - {k:20s}: {v:6.2f} sec")

        return InterviewPlan(
            parsed_docs=inputs["parsed_docs"],
            jd_analysis=inputs["jd_analysis"],
            interview_questions=inputs["interview_questions"],
        )

    # ----------------------------- 외부 인터페이스 -----------------------------
    def run(self, resume_text: str, jd_text: str) -> InterviewPlan:
        """기존 Flask API와 호환"""
        return self.chain.invoke({"resume_text": resume_text, "jd_text": jd_text})

    def as_runnable(self):
        """LangGraph 노드 연결용"""
        return self.chain
