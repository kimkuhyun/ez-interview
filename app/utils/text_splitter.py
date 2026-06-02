from langchain_text_splitters import RecursiveCharacterTextSplitter

def split_text(text: str, chunk_size: int = 500, chunk_overlap: int = 100) -> list[str]:
    """
    긴 텍스트를 RAG 용도로 chunk 단위로 잘라주는 유틸 함수.
    여기서 chunk_size / chunk_overlap 바꾸면 전체 임베딩 개수가 바뀐다.
    """
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
    )
    return splitter.split_text(text)
