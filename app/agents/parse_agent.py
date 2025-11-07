import os
from langchain_openai import ChatOpenAI
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.runnables import RunnableLambda, RunnableSequence
from app.utils.schemas import ParsedDoc

class ParseAgent:
    def __init__(self):
        self.llm = ChatOpenAI(
            model="gpt-4o-mini",
            temperature=0,
            openai_api_key=os.getenv("OPENAI_API_KEY"),
        )

        # JSON 전용 OutputParser
        self.parser = JsonOutputParser()

        # PromptTemplate 구성 (LangChain 표준, LLM에게 JSON 형식 강제)
        self.prompt = ChatPromptTemplate.from_template(
            "너는 HR 보조 AI야.\n"
            "아래 문서가 이력서(resume), 자기소개서(cover_letter), JD(job_description) 중 무엇인지 판별하고, "
            "가능하면 항목별로 구조화해.\n\n"
            "출력 형식은 반드시 JSON이며, 아래 스키마를 따라야 한다:\n"
            "{format_instructions}\n\n"
            "문서 내용:\n{text}"
        )

        # LangChain Runnable Sequence (prompt → llm → json parser)
        self.chain = self.prompt | self.llm | self.parser

        # (옵션) 텍스트 스플리터: 나중에 RAG 적용 시 활용 가능
        self.splitter = RecursiveCharacterTextSplitter(chunk_size=1500, chunk_overlap=200)

    def parse_raw_text(self, text: str) -> ParsedDoc:
        """LLM으로 문서 타입 추론 + 구조화"""
        """
        LangChain 기반 문서 파서
        입력: text
        출력: ParsedDoc (Pydantic 모델)
        """
        try:
            # LLM 실행
            result = self.chain.invoke({
                "text": text[:4000],
                "format_instructions": self.parser.get_format_instructions(),
            })
        except Exception as e:
            print("[ParseAgent] ⚠️ JSON 파싱 실패:", e)
            # fallback: LLM 출력이 JSON이 아닐 경우 기본 구조 반환
            result = {"doc_type": "unknown", "structured": {"llm_raw": text[:1000]}}

        # 결과를 ParsedDoc 객체로 감싸 반환
        return ParsedDoc(
            doc_type=result.get("doc_type", "unknown"),
            raw_text=text,
            structured=result.get("structured", result),
        )

    # LangGraph 호환: Runnable 형태로 wrapping
    def as_runnable(self):
        """LangGraph 연결용 Runnable 변환"""
        return RunnableLambda(lambda inputs: self.parse_raw_text(inputs["text"]))