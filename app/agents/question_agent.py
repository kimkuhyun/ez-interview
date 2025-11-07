import os
from typing import List
from langchain_openai import ChatOpenAI
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.vectorstores import FAISS
from langchain_community.embeddings import OpenAIEmbeddings
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.runnables import RunnableLambda, RunnableSequence
from app.utils.schemas import InterviewQuestions


class QuestionAgent:
    def __init__(self):
        api_key = os.getenv("OPENAI_API_KEY")
        self.llm = ChatOpenAI(
            model="gpt-4o-mini",
            temperature=0.5,
            openai_api_key=api_key,
        )
        # 임베딩 및 Retriever 준비
        self.embeddings = OpenAIEmbeddings(openai_api_key=api_key)
        self.splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)
        self.retriever = None

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
            "[RETRIEVED CONTEXT]\n{context}\n\n"
            "총 {num_questions}개의 질문을 bullet 없이 한 줄씩 출력해."
            "질문만 포함하고, 인사말·요약문·설명문은 쓰지 마."
        )

        # LLM Runnable 체인 (prompt → llm → parser)
        self.chain = self.prompt | self.llm | self.parser

    # 내부 유틸 (RAG 구축)
    def build_faiss_from_docs(self, docs: List[str]):
        """문서들을 FAISS 벡터스토어로 변환"""
        chunks = []
        for d in docs:
            chunks.extend(self.splitter.split_text(d))
        vs = FAISS.from_texts(chunks, self.embeddings)
        self.retriever = vs.as_retriever(search_kwargs={"k": 4})

    # 메인 로직
    def generate_questions(
        self,
        resume_text: str,
        jd_text: str,
        num_questions: int = 5,
    ) -> InterviewQuestions:
        """이력서 + 자소서 + JD 기반 질문 생성"""
        # retriever 없으면 빌드
        if not self.retriever:
            self.build_faiss_from_docs([resume_text, jd_text])

        # 검색 수행
        results = self.retriever.invoke(jd_text)
        context_text = "\n\n".join([r.page_content for r in results])

        # LLM 실행
        try:
            response = self.chain.invoke({
                "jd_text": jd_text,
                "resume_text": resume_text[:2000],
                "context": context_text,
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