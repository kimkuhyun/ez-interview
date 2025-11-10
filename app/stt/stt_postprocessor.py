"""
STT 텍스트 후처리 모듈

역할: STT로 인식된 텍스트를 LLM으로 보정
- 맞춤법 교정
- 기술 용어 정확도 개선
- 문장 완성도 향상
"""

from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from app.config.config import Config


class STTPostprocessor:
    """STT 텍스트 후처리기 (LLM 기반)"""
    
    def __init__(self):
        # 빠른 응답을 위해 gpt-4o-mini 사용
        self.llm = ChatOpenAI(
            model="gpt-4o-mini",
            temperature=0,  # 창의성 불필요, 정확성 우선
            openai_api_key=Config.OPENAI_API_KEY,
        )
        
        # 프롬프트: 간결하고 명확하게
        self.prompt = ChatPromptTemplate.from_messages([
            ("system", """너는 면접 대화 전문 교정 AI야.
STT로 인식된 텍스트를 문맥에 맞게 교정해줘.

⚠️ STT 자주 오인식하는 단어들:
- "잔고" → "Django" (장고)
- "광고" → "장고" 또는 "React" (문맥에 따라)
- "케이스" → "케이스" 또는 "case" (그대로)
- "리렉트" → "React"
- "뷰" → "Vue"
- "레스트풀" → "RESTful"
- "에이피아이" → "API"
- "도커" → "Docker"
- "쿠버네티스" → "Kubernetes"

교정 규칙:
1. 문맥을 보고 기술 용어인지 판단
2. "~를 개발했습니다", "~로 개발했습니다" 같은 패턴에서 기술명 추론
3. 의미 없는 반복어("응응", "어어") 제거
4. 온점(.) 마침표 추가하되 자연스럽게
5. 원본이 이미 완벽하면 그대로 반환
6. 추가 설명 없이 교정된 문장만 출력"""),
            ("user", "{stt_text}")
        ])
        
        self.parser = StrOutputParser()
        self.chain = self.prompt | self.llm | self.parser
    
    def correct(self, stt_text: str) -> str:
        """
        STT 텍스트 교정
        
        Args:
            stt_text (str): STT로 인식된 원본 텍스트
        
        Returns:
            str: 교정된 텍스트
        
        예시:
            입력: "저는 장고로 레스트 에이피아이를 개발했습니다"
            출력: "저는 Django로 REST API를 개발했습니다"
        """
        if not stt_text or len(stt_text.strip()) < 3:
            # 너무 짧은 텍스트는 교정 불필요
            return stt_text
        
        try:
            corrected = self.chain.invoke({"stt_text": stt_text})
            
            # 디버그 로그
            if corrected.strip() != stt_text.strip():
                print(f"[STT 교정] 원본: {stt_text}")
                print(f"[STT 교정] 수정: {corrected}")
            
            return corrected.strip()
            
        except Exception as e:
            print(f"❌ STT 후처리 오류: {e}")
            # 오류 시 원본 반환
            return stt_text


# 싱글톤 인스턴스 (재사용)
_postprocessor = None

def get_postprocessor() -> STTPostprocessor:
    """후처리기 싱글톤 인스턴스 반환"""
    global _postprocessor
    if _postprocessor is None:
        _postprocessor = STTPostprocessor()
    return _postprocessor
