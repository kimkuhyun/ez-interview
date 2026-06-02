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
            ("system", """너는 STT 텍스트 교정 전문가야.

⚠️ **절대 규칙**:
1. 원본 의미를 절대 바꾸지 마라
2. 교정할 내용이 없으면 **원본을 그대로 반환**
3. 불분명하다는 말 하지 마라
4. 설명 추가하지 마라
5. 교정된 문장만 출력

STT 오인식 패턴 (문맥 기반으로만 수정):
- "잔고" → "Django" (프레임워크 맥락에서만)
- "광고" → "장고" (개발 맥락에서만)
- "리렉트" → "React"
- "뷰" → "Vue" (프레임워크 맥락에서만)
- "도커" → "Docker"
- "에이피아이" → "API"
- "레스트풀" → "RESTful"

교정 범위:
1. 명백한 STT 오류만 수정 (예: "레스트 에이피아이" → "REST API")
2. 기술 용어는 문맥상 확실할 때만 수정
3. 중복어("어어", "음음") 제거
4. 맞춤법 교정
5. **애매하면 원본 그대로 반환**

예시:
입력: "저는 잔고로 개발했습니다"
출력: "저는 Django로 개발했습니다"

입력: "네 알겠습니다"
출력: "네 알겠습니다"

입력: "음 그거는 좀"
출력: "그거는 좀"

⚠️ **중요**: 교정할 내용이 없으면 원본을 그대로 반환하고, "교정할 내용이 없습니다" 같은 메타 설명은 절대 하지 마라."""),
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
            str: 교정된 텍스트 (교정 불필요 시 원본 그대로 반환)
        
        예시:
            입력: "저는 장고로 레스트 에이피아이를 개발했습니다"
            출력: "저는 Django로 REST API를 개발했습니다"
            
            입력: "네 알겠습니다"
            출력: "네 알겠습니다" (원본 그대로)
        """
        if not stt_text or len(stt_text.strip()) < 3:
            # 너무 짧은 텍스트는 교정 불필요
            return stt_text
        
        try:
            corrected = self.chain.invoke({"stt_text": stt_text})
            corrected = corrected.strip()
            
            # 메타 설명 응답 필터링
            if "교정할 내용이 없습니다" in corrected or "불분명" in corrected:
                print(f"[STT 교정] 원본 그대로 반환 (메타 응답 감지)")
                return stt_text
            
            # 원본과 비교하여 변경사항 로그
            if corrected != stt_text.strip():
                print(f"[STT 교정] 원본: {stt_text}")
                print(f"[STT 교정] 수정: {corrected}")
            
            return corrected
            
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
