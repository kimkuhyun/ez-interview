from langchain_openai import ChatOpenAI
import os


class QuestionAgent:
    def __init__(self):
        # ✅ 최신 ChatOpenAI import 및 key 인자명 변경
        self.llm = ChatOpenAI(
            model="gpt-4o-mini",
            temperature=0.7,
            openai_api_key=os.getenv("OPENAI_API_KEY")
        )
