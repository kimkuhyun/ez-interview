import fitz                # PDF 추출(PyMuPDF)
from docx import Document  # DOCX 추출(python-docx)
import base64
from openai import OpenAI
from app.config.config import Config

client = OpenAI(api_key=Config.OPENAI_API_KEY)

def extract_text(file_storage) -> str:
    """파일 확장자에 따라 자동 추출"""
    if not file_storage:
        return ""

    filename = file_storage.filename.lower()

    # 매번 read() 하면 파일 포인터가 끝으로 감
    file_bytes = file_storage.read()
    file_storage.seek(0)  # 다음 작업을 위해 다시 0으로 되돌리기

    if filename.endswith(".pdf"):
        return extract_pdf_text(file_bytes)
    elif filename.endswith(".txt"):
        return file_storage.read().decode("utf-8")
    elif filename.endswith(".docx"):
        return extract_docx_text(file_bytes)
    else:
        return ""

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


# -------- PDF 추출 (텍스트 → OCR fallback) -------- #
def extract_pdf_text(file_bytes: bytes) -> str:
    """PDF 텍스트 추출 (텍스트 기반 → OCR fallback)"""

    print("   📄 [PDF] 텍스트 기반 추출 시도…")
    text = ""

    # 1️⃣ 텍스트 기반 추출
    try:
        pdf = fitz.open(stream=file_bytes, filetype="pdf")

        for page in pdf:
            text += page.get_text()

        text = text.strip()
    except Exception as e:
        print(f"   ❌ PyMuPDF 에러 발생: {e}")
        text = ""

    # 2️⃣ 텍스트가 충분한지 판별
    if len(text) > 80:   # 기준: 80자 이상이면 텍스트 PDF로 판단
        print("   ✔ 텍스트 기반 PDF로 판단 → OCR 불필요")
        return text

    print("   ⚠ 텍스트 부족 → OCR 실행합니다…")

    # 3️⃣ OCR 실행
    try:
        return ocr_pdf(file_bytes)
    except Exception as e:
        print(f"   ❌ OCR 실패: {e}")
        return text  # OCR도 실패하면 기존 텍스트라도 반환


# -------- OCR 함수 (OpenAI Vision API) -------- #
def ocr_pdf(file_bytes: bytes) -> str:
    """PDF를 이미지로 변환 후 OpenAI Vision API로 OCR 처리"""
    print("   🔍 [OCR] PDF → 이미지 변환 → OpenAI Vision API")

    try:
        # 1️⃣ PDF를 이미지로 변환 (PyMuPDF)
        pdf = fitz.open(stream=file_bytes, filetype="pdf")
        all_text = []
        
        for page_num in range(len(pdf)):
            print(f"      ▶ 페이지 {page_num + 1}/{len(pdf)} 변환 중...")
            
            # PDF 페이지를 고해상도 이미지로 변환 (300 DPI)
            page = pdf[page_num]
            pix = page.get_pixmap(matrix=fitz.Matrix(300/72, 300/72))
            img_bytes = pix.tobytes("png")
            
            # 이미지를 base64로 인코딩
            base64_image = base64.b64encode(img_bytes).decode('utf-8')
            
            # 2️⃣ OpenAI Vision API로 OCR (gpt-4o-mini 먼저 시도)
            try:
                print(f"         OCR 실행 (gpt-4o-mini)...")
                
                response = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[
                        {
                            "role": "system",
                            "content": "You are an OCR assistant. Extract all text from the image exactly as shown, including Korean and English. Output only the transcribed text without explanations."
                        },
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "text",
                                    "text": "Please read and transcribe all visible text from this image. Output ONLY the transcribed text, maintaining the original structure. Include Korean and English text exactly as shown."
                                },
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": f"data:image/png;base64,{base64_image}"
                                    }
                                }
                            ]
                        }
                    ],
                    max_tokens=4000
                )
                
                page_text = response.choices[0].message.content
                
                # 결과 품질 체크
                if "can't assist" in page_text.lower() or "cannot" in page_text.lower() or len(page_text) < 50:
                    print(f"         ⚠️  mini 결과 부실 → gpt-4o로 재시도")
                    raise ValueError("Mini model result insufficient")
                
                print(f"         ✅ 페이지 {page_num + 1} OCR 완료 (mini)")
                all_text.append(page_text.strip())
                
            except Exception as e:
                print(f"         ⚠️  mini 실패: {str(e)[:100]}")
                
                # gpt-4o로 재시도
                try:
                    print(f"         OCR 재시도 (gpt-4o)...")
                    
                    response = client.chat.completions.create(
                        model="gpt-4o",
                        messages=[
                            {
                                "role": "system",
                                "content": "You are an OCR assistant. Extract all text from the image exactly as shown, including Korean and English. Output only the transcribed text without explanations."
                            },
                            {
                                "role": "user",
                                "content": [
                                    {
                                        "type": "text",
                                        "text": "Please read and transcribe all visible text from this image. Output ONLY the transcribed text, maintaining the original structure. Include Korean and English text exactly as shown."
                                    },
                                    {
                                        "type": "image_url",
                                        "image_url": {
                                            "url": f"data:image/png;base64,{base64_image}"
                                        }
                                    }
                                ]
                            }
                        ],
                        max_tokens=4000
                    )
                    
                    page_text = response.choices[0].message.content
                    print(f"         ✅ 페이지 {page_num + 1} OCR 완료 (gpt-4o)")
                    all_text.append(page_text.strip())
                    
                except Exception as e2:
                    print(f"         ❌ gpt-4o도 실패: {e2}")
                    all_text.append("")
        
        pdf.close()
        
        # 모든 페이지의 텍스트 합치기
        final_text = "\n\n".join([t for t in all_text if t])
        print(f"   ✅ JD 추출 완료: {len(final_text)} 자")
        
        if not final_text:
            print(f"      ⚠️  추출된 텍스트가 없습니다!")
        
        return final_text
        
    except Exception as e:
        print(f"   ❌ OCR 전체 실패: {e}")
        return ""