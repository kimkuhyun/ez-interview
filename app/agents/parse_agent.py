import os
from langchain_openai import ChatOpenAI
from langchain_text_splitters import RecursiveCharacterTextSplitter
from app.utils.schemas import ParsedDoc

class ParseAgent:
    def __init__(self):
        self.llm = ChatOpenAI(
            model="gpt-4o-mini",
            temperature=0,
            openai_api_key=os.getenv("OPENAI_API_KEY"),
        )
        self.splitter = RecursiveCharacterTextSplitter(chunk_size=1500, chunk_overlap=200)

    def parse_raw_text(self, text: str) -> ParsedDoc:
        """LLM으로 문서 타입 추론 + 구조화"""
        system_prompt = (
            "너는 HR 보조 AI야. 아래 문서가 이력서(resume), 자기소개서(cover_letter), JD(job_description) 중 무엇인지 파악하고, "
            "가능하면 항목별로 구조화해.\n"
            "출력은 JSON만 줘. keys: doc_type, structured.\n"
        )
        user_msg = f"문서 내용:\n{text[:4000]}"  # 너무 길면 앞부분만

        resp = self.llm.invoke(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_msg},
            ],
            response_format={"type": "json_object"}
        )

        content = resp.content

        return ParsedDoc(
            doc_type="unknown",
            raw_text=text,
            structured={"llm_raw": content},
        )
