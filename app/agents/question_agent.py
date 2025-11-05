from langchain_openai import ChatOpenAI
from langchain_community.embeddings import OpenAIEmbeddings
from langchain_community.vectorstores import FAISS
from langchain.prompts import PromptTemplate
from langchain.chains import ConversationalRetrievalChain
from langchain.memory import ConversationBufferMemory
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain.schema import Document
import os


class QuestionAgent:
    def __init__(self):
        # ✅ 최신 ChatOpenAI import 및 key 인자명 변경
        self.llm = ChatOpenAI(
            model="gpt-4o-mini",
            temperature=0.7,
            openai_api_key=os.getenv("OPENAI_API_KEY")
        )

        # ✅ memory 구조 그대로 유지
        self.memory = ConversationBufferMemory(
            memory_key="chat_history",
            input_key="question",
            output_key="answer",
            return_messages=True,
        )

        # ✅ Embedding import 최신화 + key 인자명 변경
        self.embeddings = OpenAIEmbeddings(
            openai_api_key=os.getenv("OPENAI_API_KEY")
        )

        self.retriever = None
        self.chain = None

    def build_vectorstore(self, docs):
        """문서를 벡터스토어로 구축"""
        splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)
        all_docs = [
            Document(page_content=c)
            for d in docs
            for c in splitter.split_text(d)
        ]
        store = FAISS.from_documents(all_docs, self.embeddings)
        self.retriever = store.as_retriever(search_kwargs={"k": 4})
        return store

    def build_chain(self):
        """LLM + Retriever + Memory로 ConversationalRetrievalChain 구성"""
        prompt = PromptTemplate(
            input_variables=["context", "question", "chat_history"],
            template=(
                "너는 면접관 보조 AI Agent야.\n"
                "아래 문서를 기반으로 지원자의 경험과 직무 관련성을 평가할 질문을 생성해.\n\n"
                "{context}\n\n질문 요청: {question}\n\n이전 대화: {chat_history}\n\n"
                "출력은 JSON 형식으로: "
                '[{"type": "기술", "main_question": "...", "follow_up": "..."}]'
            ),
        )

        self.chain = ConversationalRetrievalChain.from_llm(
            llm=self.llm,
            retriever=self.retriever,
            memory=self.memory,
            combine_docs_chain_kwargs={"prompt": prompt},
        )

    def generate_questions(self, request_text: str):
        """질문 생성"""
        if not self.chain:
            raise ValueError("Chain이 초기화되지 않았습니다. 먼저 build_chain()을 호출하세요.")
        result = self.chain({"question": request_text})
        return result["answer"]
