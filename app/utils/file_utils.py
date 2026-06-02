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


def extract_portfolio_multimodal(file_storage) -> str:
    """
    포트폴리오 전용 멀티모달 추출
    - 텍스트 + 이미지 + 차트 + 디자인 모두 분석
    - OpenAI Vision API로 각 페이지의 시각적 요소까지 해석
    """
    if not file_storage:
        return ""

    filename = file_storage.filename.lower()
    file_bytes = file_storage.read()
    file_storage.seek(0)

    print("\n   🎨 [Portfolio] 멀티모달 분석 시작")

    if filename.endswith(".pdf"):
        return extract_portfolio_pdf_multimodal(file_bytes)
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


# -------- 포트폴리오 멀티모달 분석 (OpenAI Vision API) -------- #
def extract_portfolio_pdf_multimodal(file_bytes: bytes) -> str:
    """
    포트폴리오 PDF를 멀티모달로 분석
    - 텍스트 추출뿐만 아니라 이미지, 차트, 디자인 등 시각적 요소도 해석
    - OpenAI Vision API 사용
    """
    print("   🔍 [Portfolio Multimodal] PDF → 이미지 변환 → Vision API 분석")

    try:
        # 1️⃣ PDF를 이미지로 변환
        pdf = fitz.open(stream=file_bytes, filetype="pdf")
        all_descriptions = []
        
        for page_num in range(len(pdf)):
            print(f"      ▶ 페이지 {page_num + 1}/{len(pdf)} 분석 중...")
            
            # PDF 페이지를 고해상도 이미지로 변환 (300 DPI)
            page = pdf[page_num]
            pix = page.get_pixmap(matrix=fitz.Matrix(300/72, 300/72))
            img_bytes = pix.tobytes("png")
            
            # 이미지를 base64로 인코딩
            base64_image = base64.b64encode(img_bytes).decode('utf-8')
            
            # 2️⃣ OpenAI Vision API로 멀티모달 분석 (mini → 4o fallback)
            try:
                print(f"         멀티모달 분석 실행 (gpt-4o-mini)...")
                
                system_prompt = """You are a portfolio analysis assistant. Analyze portfolio pages comprehensively, including both text and visual elements.
Your analysis should cover:
1. All visible text content (titles, descriptions, body text)
2. Images, photos, and their context/meaning
3. Diagrams, charts, graphs and what they represent
4. Design elements, layouts, color schemes
5. Code snippets or technical content
6. Any project descriptions or achievements

Output a detailed description in Korean that captures both the textual and visual information."""

                user_prompt = """이 포트폴리오 페이지를 분석해주세요. 다음 내용을 포함해주세요:

1. **텍스트 내용**: 제목, 설명, 본문 등 모든 텍스트
2. **이미지/사진**: 무엇을 보여주는지, 맥락과 의미
3. **차트/그래프**: 데이터가 나타내는 내용
4. **디자인 요소**: 레이아웃, 색상, 스타일
5. **기술적 내용**: 코드, 기술 스택, 구현 내용
6. **프로젝트 성과**: 달성한 결과나 성과

시각적 요소와 텍스트를 모두 포함하여 상세히 설명해주세요."""
                
                response = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[
                        {
                            "role": "system",
                            "content": system_prompt
                        },
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "text",
                                    "text": user_prompt
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
                    max_tokens=2000
                )
                
                page_description = response.choices[0].message.content
                
                # 결과 품질 체크
                if "can't assist" in page_description.lower() or "cannot" in page_description.lower() or len(page_description) < 200:
                    print(f"         ⚠️  mini 결과 부실 → gpt-4o로 재시도")
                    raise ValueError("Mini model result insufficient")
                
                print(f"         ✅ 페이지 {page_num + 1} 분석 완료 (mini, {len(page_description)} 자)")
                
                # 페이지 번호와 함께 저장
                all_descriptions.append(f"=== 페이지 {page_num + 1} ===\n{page_description.strip()}")
                
            except Exception as e:
                print(f"         ⚠️  mini 실패: {str(e)[:100]}")
                
                # gpt-4o로 재시도
                try:
                    print(f"         멀티모달 분석 재시도 (gpt-4o)...")
                    
                    response = client.chat.completions.create(
                        model="gpt-4o",
                        messages=[
                            {
                                "role": "system",
                                "content": system_prompt
                            },
                            {
                                "role": "user",
                                "content": [
                                    {
                                        "type": "text",
                                        "text": user_prompt
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
                        max_tokens=2000
                    )
                    
                    page_description = response.choices[0].message.content
                    print(f"         ✅ 페이지 {page_num + 1} 분석 완료 (gpt-4o, {len(page_description)} 자)")
                    
                    # 페이지 번호와 함께 저장
                    all_descriptions.append(f"=== 페이지 {page_num + 1} ===\n{page_description.strip()}")
                    
                except Exception as e2:
                    print(f"         ❌ gpt-4o도 실패: {str(e2)[:100]}")
                    all_descriptions.append(f"=== 페이지 {page_num + 1} ===\n(분석 실패)")
        
        pdf.close()
        
        # 모든 페이지의 분석 결과 합치기
        final_description = "\n\n".join([d for d in all_descriptions if d])
        print(f"   ✅ Portfolio 분석 완료: {len(final_description)} 자")
        
        if not final_description:
            print(f"      ⚠️  분석 결과가 없습니다!")
        
        return final_description
        
    except Exception as e:
        print(f"   ❌ Portfolio 멀티모달 분석 실패: {e}")
        import traceback
        traceback.print_exc()
        return ""