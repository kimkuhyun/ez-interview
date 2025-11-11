from openai import OpenAI
from app.config.config import Config

client = OpenAI(api_key=Config.OPENAI_API_KEY)

def get_embedding(text: str, model="text-embedding-3-small"):
    text = text.replace("\n", " ")
    response = client.embeddings.create(model=model, input=text)
    return response.data[0].embedding