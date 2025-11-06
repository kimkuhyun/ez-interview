# app/agents/question_agent.py
import os
from typing import List, Optional
from langchain_openai import ChatOpenAI
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.vectorstores import FAISS
from langchain_community.embeddings import OpenAIEmbeddings
from app.utils.schemas import InterviewQuestions

from app.utils.schemas import ParsedDoc


class QuestionAgent:
    def __init__(self):
        api_key = os.getenv("OPENAI_API_KEY")
        self.llm = ChatOpenAI(
            model="gpt-4o-mini",
            temperature=0.5,
            openai_api_key=api_key,
        )
        self.embeddings = OpenAIEmbeddings(openai_api_key=api_key)
        self.splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)
        self.retriever = None

    def build_faiss_from_docs(self, docs: List[str]):
        chunks = []
        for d in docs:
            chunks.extend(self.splitter.split_text(d))
        vs = FAISS.from_texts(chunks, self.embeddings)
        self.retriever = vs.as_retriever(search_kwargs={"k": 4})

    def generate_questions(
        self,
        resume_text: str,
        jd_text: str,
        num_questions: int = 5,
    ) -> InterviewQuestions:
        """이력서 + 자소서 + JD 기반 질문 생성"""
        # 우선 retriever 없으면 빌드
        if not self.retriever:
            self.build_faiss_from_docs([resume_text, jd_text])

        # 컨텍스트 몇 개 뽑기
        results = self.retriever.invoke(jd_text)
        context_text = "\n\n".join([r.page_content for r in results])

        prompt = (
            "너는 기술면접 보조 AI야. 아래 지원자의 이력/자기소개/채용공고를 보고, "
            "지원자의 경험을 파악할 수 있는 질문을 만들어라.\n"
            f"[JD]\n{jd_text}\n\n"
            f"[RESUME]\n{resume_text[:2000]}\n\n"
            f"[RETRIEVED CONTEXT]\n{context_text}\n\n"
            f"총 {num_questions}개 질문을 bullet 없이 한 줄씩 출력해."
        )

        resp = self.llm.invoke([{"role": "user", "content": prompt}])
        lines = [l.strip() for l in resp.content.split("\n") if l.strip()]
        return InterviewQuestions(questions=lines[:num_questions])
