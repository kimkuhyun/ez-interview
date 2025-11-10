from openai import OpenAI
from app.config.config import Config

class StreamAgent:
    """면접 스트림 AI 에이전트"""
    
    def __init__(self):
        self.client = OpenAI(api_key=Config.OPENAI_API_KEY)
    
    def generate_followups(self, text, question_id, history=None, regen=False):
        """후속 질문 3개 생성
        
        Args:
            text (str): 면접자의 최신 답변
            question_id (str): 현재 질문 ID
            history_text (str, optional): 이전 대화 기록
            regen (bool, optional): 재생성 여부
        
        Returns:
            list[str]: 후속 질문 리스트
        """
        # 이전 대화 기록이 리스트(딕셔너리)로 넘어오면 문자열로 변환
        if history and isinstance(history, (list, tuple)):
            history_text = "\n".join(
                f"{m.get('role','')}: {m.get('content','')}" for m in history
            )
        else:
            history_text = history if history else '(이전 대화 없음)'

        # 프롬프트 구성
        prompt = f"""
너는 면접관 AI야.
아래는 지금까지의 대화야.
면접자의 최신 답변을 참고해서 후속 질문 3개를 자연스럽게 만들어줘.

[이전 대화 기록]
{history_text}

[면접자의 최신 답변]
{text}

요구사항:
- 각 질문은 한 줄씩 출력
- 번호 없이 순수 질문만 3줄
"""
        try:
            response = self.client.chat.completions.create(
                model="gpt-4",
                messages=[
                    {"role": "system", "content": "너는 면접관 AI야."},
                    {"role": "user", "content": prompt}
                ]
            )
            
            ai_reply = response.choices[0].message.content.strip()
            # 한 줄씩 split
            questions = [q.strip("-• ").strip() for q in ai_reply.split("\n") if q.strip()]

            return questions[:3]
            
        except Exception as e:
            print(f"❌ OpenAI API 오류: {e}")
            return [
                "죄송합니다. AI 질문 생성 중 오류가 발생했습니다.",
                "잠시 후 다시 시도해주세요.",
                "또는 직접 질문을 입력해주세요."
            ]
    
    def analyze_answer(self, text, criteria=None):
        """답변 분석 (추후 구현)
        
        Args:
            text (str): 면접자 답변
            criteria (dict, optional): 평가 기준
        
        Returns:
            dict: 분석 결과
        """
        # TODO: 답변 분석 로직 구현
        pass
