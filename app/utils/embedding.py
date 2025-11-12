from openai import OpenAI
from app.config.config import Config

client = OpenAI(api_key=Config.OPENAI_API_KEY)

def get_embedding(text: str, model="text-embedding-3-small"):
    """
    단일 텍스트 임베딩 생성
    
    Args:
        text: 임베딩할 텍스트
        model: OpenAI 임베딩 모델
    
    Returns:
        list: 임베딩 벡터 (1536차원)
    """
    text = text.replace("\n", " ")
    response = client.embeddings.create(model=model, input=text)
    return response.data[0].embedding


def get_embeddings_batch(texts: list, model="text-embedding-3-small") -> list:
    """
    여러 텍스트를 배치로 임베딩 생성 (API 호출 최적화)
    
    Args:
        texts: 텍스트 리스트 (최대 2048개)
        model: OpenAI 임베딩 모델
    
    Returns:
        list: 임베딩 벡터 리스트 (각 1536차원)
    
    사용 예시:
        # 면접자 답변 10개를 한 번에 임베딩
        answers = ["답변1", "답변2", "답변3", ...]
        embeddings = get_embeddings_batch(answers)
        # 1번의 API 호출로 10개 임베딩 생성
    """
    if not texts:
        return []
    
    # 줄바꿈 제거
    cleaned_texts = [t.replace("\n", " ") for t in texts]
    
    # 배치 API 호출
    response = client.embeddings.create(model=model, input=cleaned_texts)
    
    # 순서대로 임베딩 반환
    return [data.embedding for data in response.data]
