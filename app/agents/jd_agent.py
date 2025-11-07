import os, json, re
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.runnables import RunnableLambda
from app.utils.schemas import JDAnalysis


class JDAgent:
    def __init__(self):
        self.llm = ChatOpenAI(
            model="gpt-4o-mini",
            temperature=0.3,
            openai_api_key=os.getenv("OPENAI_API_KEY"),
        )

        # Prompt 구성
        self.prompt = ChatPromptTemplate.from_messages([
            (
                "system",
                "너는 HR 인터뷰 설계 도우미야. "
                "아래의 JD 구조화 데이터를 기반으로 평가 항목과 기술 역량 분석을 수행해."
            ),
            (
                "user",
                # LangChain 템플릿은 { } 를 변수로 보기 때문에, 실제로 보여줄 JSON은 {{ }} 로 감싸야 함
                "다음 채용 공고(JD)를 분석해서 아래 정보를 만들어줘.\n"
                "1) 평가항목 후보 10개 (기술 + 태도 포함, 문장 말고 짧은 라벨로)\n"
                "2) JD가 커버하는 역할/책임을 한 문단으로 요약\n"
                "3) JD에서 요구하는 기술/역량(기술/태도 모두)에 대해 숙련도 또는 중요도를 상/중/하로 추정\n"
                "JSON으로만 출력:\n"
                "{{\n"
                '  "suggested_criteria": ["...", "..."],\n'
                '  "coverage_summary": "...",\n'
                '  "skill_levels": {{"Python": "상", "DB": "중", "협업": "상", "태도": "중"}}\n'
                "}}\n"
                "JD 데이터:\n{jd_text}"
            ),
        ])

        self.parser = JsonOutputParser()

        # Runnable 체인 구성 (prompt → llm → parser)
        self.chain = self.prompt | self.llm | self.parser

    def analyze_jd(self, jd_text: str) -> JDAnalysis:
        """LangChain Runnable 기반 JD 분석"""
        try:
            jd_data = self.chain.invoke({"jd_text": jd_text})
        except Exception as e:
            print("[JDAgent] JSON 파싱 실패:", e)
            jd_data = {}

        return JDAnalysis(
            jd_text=jd_text,
            suggested_criteria=jd_data.get("suggested_criteria", []),
            coverage_summary=jd_data.get("coverage_summary", ""),
            skill_levels=jd_data.get("skill_levels", {}),
        )

    def as_runnable(self):
        """LangGraph 연결용 Runnable"""
        return RunnableLambda(lambda inputs: self.analyze_jd(inputs["jd_text"]))