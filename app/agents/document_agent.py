from __future__ import annotations

from typing import Dict, List
import os
import logging

from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass
class DocumentInput:
    resume_text: Optional[str]
    jd_text: Optional[str]

@dataclass
class QuestionPrompt:
    system: str
    user: str
    n: int = 5

def generate_questions(docs: DocumentInput, topic: str = "초기 면접 질문", n: int = 5) -> List[str]:
    """문서로부터 질문 리스트를 생성합니다."""
    try:
        from langchain_openai import ChatOpenAI

        api_key = os.getenv("OPENAI_API_KEY")

        llm_kwargs = dict(model="gpt-4o-mini", temperature=0.2)
        if api_key:
            llm_kwargs["api_key"] = api_key

        llm = ChatOpenAI(**llm_kwargs)
        
        context = "\n\n".join(filter(None, [docs.resume_text, docs.jd_text]))
        prompt = build_question_prompt(topic, context, n)
        
        response = llm.invoke([
            {"role": "system", "content": prompt.system},
            {"role": "user", "content": prompt.user}
        ])
        
        lines = [
            line.strip("-• ").strip() 
            for line in response.content.splitlines() 
            if line.strip()
        ]
        
        questions = [
            line for line in lines 
            if len(line) > 1
        ][:n]
        
        return questions if questions else _generate_fallback_questions(topic, n)
        
    except Exception as e:
        logging.warning("LLM 질문 생성 실패: %s", str(e))
        return _generate_fallback_questions(topic, n)

def _generate_fallback_questions(topic: str, n: int) -> List[str]:
    """LLM 실패시 반환할 기본 질문을 생성."""
    return [
        f"[로컬] {i+1}. {topic} 관련 확인 질문" 
        for i in range(n)
    ]


def generate_questions_from_text(topic: str, context: str, n: int = 5) -> List[str]:
    """문맥 문자열로부터 질문을 생성하는 래퍼."""
    docs = DocumentInput(resume_text=context, jd_text=None)
    # context는 이미 결합된 텍스트인 경우가 많으므로 resume_text에 전달
    return generate_questions(docs, topic, n)

def build_question_prompt(topic: str, context: str, n: int = 5) -> QuestionPrompt:
    """프롬프트(system/user)를 구성하여 반환합니다."""
    system = (
        "역할: AI 면접관 보조\n"
        "임무: 제공된 문서(이력서/직무기술서)를 기반으로 면접 질문 생성\n"
        "요구사항:\n"
        "- 각 질문은 한 줄로 작성\n"
        "- 문서 내용과 직접 연관된 구체적인 질문\n"
        "- 지원자의 경험과 역량을 확인하는 질문\n"
        "- 직무 적합성을 평가하는 질문\n"
        "- 중복되는 개념이나 주제 피하기"
    )
    
    user = (
        f"주제: {topic}\n\n"
        f"문서 내용:\n{context}\n\n"
        f"요청: 위 문서들을 분석하여 지원자 평가에 적합한 질문 {n}개를 생성해주세요."
    )
    
    return QuestionPrompt(
        system=system,
        user=user,
        n=n
    )