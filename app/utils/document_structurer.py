"""
OpenAI API를 사용한 문서 구조화 모듈
Resume와 JD를 구조화된 형태로 변환
"""

from openai import OpenAI
from app.config.config import Config
from app.utils.schemas import StructuredResume, StructuredJD
import json
from typing import Optional

client = OpenAI(api_key=Config.OPENAI_API_KEY)


def structure_resume(raw_text: str) -> StructuredResume:
    """
    이력서 텍스트를 구조화된 형태로 변환
    
    Args:
        raw_text: 원본 이력서 텍스트
        
    Returns:
        StructuredResume: 구조화된 이력서 객체
    """
    print("   🔄 [Resume] OpenAI API로 구조화 중...")
    
    system_prompt = """당신은 이력서 파싱 전문가입니다. 주어진 이력서 텍스트를 분석하여 구조화된 정보를 추출하세요.
아래 스키마에 맞는 유효한 JSON 객체만 출력하세요. 설명이나 마크다운 형식은 포함하지 마세요.

스키마:
{
  "basic_info": {
    "name": "문자열 또는 null",
    "email": "문자열 또는 null",
    "phone": "문자열 또는 null",
    "github": "문자열 또는 null",
    "linkedin": "문자열 또는 null",
    "blog": "문자열 또는 null",
    "summary": "문자열 또는 null"
  },
  "education": [
    {
      "school": "문자열",
      "major": "문자열 또는 null",
      "degree": "문자열 또는 null",
      "period": "문자열 또는 null",
      "gpa": "문자열 또는 null",
      "activities": ["문자열"]
    }
  ],
  "experience": [
    {
      "company": "문자열 (회사명)",
      "role": "문자열 (직책/역할)",
      "period": "문자열 (재직기간)",
      "description": "문자열 또는 null (회사/부서 설명)",
      "achievements": ["문자열 (담당 업무, 성과를 각각 배열 요소로)"],
      "tech_stack": ["문자열 (사용 기술)"]
    }
  ],
  "projects": [
    {
      "name": "문자열 (프로젝트명)",
      "period": "문자열 또는 null (프로젝트 기간)",
      "description": "문자열 (프로젝트 목적, 배경, 개요)",
      "role": "문자열 또는 null (프로젝트 내 역할)",
      "achievements": ["문자열 (구현 내용, 성과를 각각 배열 요소로)"],
      "tech_stack": ["문자열 (사용 기술, 도구)"],
      "url": "문자열 또는 null"
    }
  ],
  "skills": {
    "technical": ["문자열"],
    "languages": ["문자열 (프로그래밍 언어)"],
    "frameworks": ["문자열"],
    "tools": ["문자열"],
    "soft_skills": ["문자열"]
  },
  "certifications": ["문자열"],
  "awards": ["문자열"],
  "languages": ["문자열 (외국어)"],
  "cover_letter": "문자열 또는 null (자기소개서 전문)",
  "additional_info": "문자열 또는 null (취미, 병역, 봉사활동, 교육이수 등 기타 정보)"
}

**🚨 중요: 완전성 우선 원칙 🚨**
1. **절대 생략 금지**: 모든 정보를 원문 그대로 추출하세요. 요약, 압축, 의역 일체 금지.
   - 예시: "React 프로젝트 3건 수행" (X) → 각 프로젝트를 개별 객체로 모두 나열 (O)
   - 예시: achievements가 10개면 10개 모두 배열에 포함

2. **배열 요소 개수 유지**: 원문에 항목이 5개면 배열에도 정확히 5개.
   - 나열된 업무/성과는 한 줄도 빠뜨리지 말고 전부 배열에 추가
   - "등" "외 다수" 같은 표현으로 뭉치지 말고 모두 열거

3. **텍스트 길이 유지**: 문장/문단을 축약하지 마세요.
   - 3줄짜리 설명 → 3줄 그대로 유지
   - "상세 내용 생략" 같은 표현 절대 금지

4. **우선순위**: 토큰이 부족하면 아래 순서로 우선 추출 (절대 생략 X)
   1) experience (경력) - 전체 내용
   2) projects (프로젝트) - 전체 내용
   3) skills, education
   4) 나머지 필드

5. **경력 vs 프로젝트 구분:**
   - "경력", "재직 이력", "회사명 명시" → experience
   - "수행 업무", "프로젝트", "개인 프로젝트", "팀 프로젝트" → projects
   - 애매하면 둘 다 포함 (중복 허용)

6. **자기소개서**: cover_letter 필드에 **전문** 그대로 넣으세요 (한 글자도 빠뜨리지 말 것).

7. **분류 안 되는 정보**: additional_info에 **모두** 넣으세요. 버리지 마세요.

8. **없는 필드**: null 또는 빈 배열([])을 사용하세요.

9. **🔒 개인정보 제외 (CRITICAL)**: 다음 개인정보는 **절대 추출하지 마세요** (블라인드 채용):
   - 이름 (name): 항상 null
   - 전화번호 (phone): 항상 null
   - 생년월일, 주소: 추출하지 마세요 (스키마에 없으므로 무시)
   - 이메일 (email): null로 설정
   ⚠️ GitHub, LinkedIn, Blog, Portfolio URL은 **유지** (기술 평가용)

**검증**: 추출 완료 후 원문과 대조하여 누락된 항목이 있는지 스스로 확인하세요."""

    user_prompt = f"""이 이력서를 분석하여 구조화된 정보를 추출하세요. JSON 객체만 출력하고 다른 텍스트는 포함하지 마세요.

이력서:
{raw_text}"""

    try:
        # gpt-4o-mini 먼저 시도 (비용 절감)
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.1,
            max_tokens=8000
        )
        
        result_text = response.choices[0].message.content.strip()
        
        # JSON 파싱 시도
        # 마크다운 코드 블록 제거
        if result_text.startswith("```json"):
            result_text = result_text[7:]
        if result_text.startswith("```"):
            result_text = result_text[3:]
        if result_text.endswith("```"):
            result_text = result_text[:-3]
        result_text = result_text.strip()
        
        structured_data = json.loads(result_text)
        structured_data["raw_text"] = raw_text
        
        resume = StructuredResume(**structured_data)
        print(f"   ✅ [Resume] 구조화 완료 (gpt-4o-mini)")
        return resume
        
    except Exception as e:
        print(f"   ⚠️  [Resume] mini 실패: {str(e)[:100]}")
        
        # gpt-4o로 재시도
        try:
            print("   🔄 [Resume] gpt-4o로 재시도...")
            response = client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.1,
                max_tokens=8000
            )
            
            result_text = response.choices[0].message.content.strip()
            
            # JSON 파싱
            if result_text.startswith("```json"):
                result_text = result_text[7:]
            if result_text.startswith("```"):
                result_text = result_text[3:]
            if result_text.endswith("```"):
                result_text = result_text[:-3]
            result_text = result_text.strip()
            
            structured_data = json.loads(result_text)
            structured_data["raw_text"] = raw_text
            
            resume = StructuredResume(**structured_data)
            print(f"   ✅ [Resume] 구조화 완료 (gpt-4o)")
            return resume
            
        except Exception as e2:
            print(f"   ❌ [Resume] gpt-4o도 실패: {str(e2)[:100]}")
            # 최소한의 구조화 데이터 반환
            return StructuredResume(
                basic_info={},
                raw_text=raw_text
            )


def structure_jd(raw_text: str) -> StructuredJD:
    """
    JD 텍스트를 구조화된 형태로 변환
    
    Args:
        raw_text: 원본 JD 텍스트
        
    Returns:
        StructuredJD: 구조화된 JD 객체
    """
    print("   🔄 [JD] OpenAI API로 구조화 중...")
    
    system_prompt = """당신은 채용공고(JD) 파싱 전문가입니다. 주어진 JD 텍스트를 분석하여 구조화된 정보를 추출하세요.
아래 스키마에 맞는 유효한 JSON 객체만 출력하세요. 설명이나 마크다운 형식은 포함하지 마세요.

스키마:
{
  "company": "문자열 또는 null",
  "position": "문자열",
  "department": "문자열 또는 null",
  "employment_type": "문자열 또는 null",
  "location": "문자열 또는 null",
  "requirements": {
    "required_skills": ["문자열"],
    "preferred_skills": ["문자열"],
    "required_experience": "문자열 또는 null",
    "preferred_experience": "문자열 또는 null",
    "education": "문자열 또는 null",
    "certifications": ["문자열"]
  },
  "responsibilities": ["문자열"],
  "tech_stack": ["문자열"],
  "preferred_qualifications": ["문자열"],
  "benefits": ["문자열"],
  "additional_info": "문자열 또는 null (위 카테고리에 맞지 않는 중요 정보: 기업문화, 근무시간, 특이사항 등)"
}

JD에서 최대한 많은 정보를 추출하세요. 해당 필드가 없으면 null 또는 빈 배열을 사용하세요.
중요: 표준 필드에 맞지 않는 정보는 반드시 'additional_info'에 넣어 데이터 손실을 방지하세요."""

    user_prompt = f"""이 채용공고를 분석하여 구조화된 정보를 추출하세요. JSON 객체만 출력하고 다른 텍스트는 포함하지 마세요.

채용공고:
{raw_text}"""

    try:
        # gpt-4o-mini 먼저 시도
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.1,
            max_tokens=8000
        )
        
        result_text = response.choices[0].message.content.strip()
        
        # JSON 파싱
        if result_text.startswith("```json"):
            result_text = result_text[7:]
        if result_text.startswith("```"):
            result_text = result_text[3:]
        if result_text.endswith("```"):
            result_text = result_text[:-3]
        result_text = result_text.strip()
        
        structured_data = json.loads(result_text)
        structured_data["raw_text"] = raw_text
        
        jd = StructuredJD(**structured_data)
        print(f"   ✅ [JD] 구조화 완료 (gpt-4o-mini)")
        return jd
        
    except Exception as e:
        print(f"   ⚠️  [JD] mini 실패: {str(e)[:100]}")
        
        # gpt-4o로 재시도
        try:
            print("   🔄 [JD] gpt-4o로 재시도...")
            response = client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.1,
                max_tokens=8000
            )
            
            result_text = response.choices[0].message.content.strip()
            
            # JSON 파싱
            if result_text.startswith("```json"):
                result_text = result_text[7:]
            if result_text.startswith("```"):
                result_text = result_text[3:]
            if result_text.endswith("```"):
                result_text = result_text[:-3]
            result_text = result_text.strip()
            
            structured_data = json.loads(result_text)
            structured_data["raw_text"] = raw_text
            
            jd = StructuredJD(**structured_data)
            print(f"   ✅ [JD] 구조화 완료 (gpt-4o)")
            return jd
            
        except Exception as e2:
            print(f"   ❌ [JD] gpt-4o도 실패: {str(e2)[:100]}")
            # 최소한의 구조화 데이터 반환
            return StructuredJD(
                position="Unknown Position",
                raw_text=raw_text
            )
