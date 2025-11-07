import fitz                # PDF 추출(PyMuPDF)
from docx import Document  # DOCX 추출(python-docx)

def extract_text(file_storage) -> str:
    """파일 확장자에 따라 자동 추출"""
    if not file_storage:
        return ""

    filename = file_storage.filename.lower()
    if filename.endswith(".pdf"):
        return extract_pdf_text(file_storage)
    elif filename.endswith(".txt"):
        return file_storage.read().decode("utf-8")
    elif filename.endswith(".docx"):
        return extract_docx_text(file_storage)
    else:
        return ""

def extract_pdf_text(file_storage) -> str:
    """PDF 에서 텍스트 추출"""
    text = ""
    with fitz.open(stream=file_storage.read(), filetype="pdf") as pdf:
        for page in pdf:
            text += page.get_text()
    return text.strip()

def extract_docx_text(file_storage) -> str:
    """DOCX 에서 텍스트 추출"""
    text = ""
    try:
        doc = Document(file_storage)
        paragraphs = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
        text = "\n".join(paragraphs)
    except Exception as e:
        print(f"[extract_docx_text] DOCX 추출 오류: {e}")
    return text.strip()