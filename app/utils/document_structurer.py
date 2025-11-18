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
    
    system_prompt = """You are an expert resume parser. Parse the given resume text and extract structured information.
Output ONLY a valid JSON object matching the provided schema. Do not include any explanations or markdown formatting.

Schema:
{
  "basic_info": {
    "name": "string or null",
    "email": "string or null",
    "phone": "string or null",
    "github": "string or null",
    "linkedin": "string or null",
    "blog": "string or null",
    "summary": "string or null"
  },
  "education": [
    {
      "school": "string",
      "major": "string or null",
      "degree": "string or null",
      "period": "string or null",
      "gpa": "string or null",
      "activities": ["string"]
    }
  ],
  "experience": [
    {
      "company": "string",
      "role": "string",
      "period": "string",
      "description": "string or null",
      "achievements": ["string"],
      "tech_stack": ["string"]
    }
  ],
  "projects": [
    {
      "name": "string",
      "period": "string or null",
      "description": "string",
      "role": "string or null",
      "achievements": ["string"],
      "tech_stack": ["string"],
      "url": "string or null"
    }
  ],
  "skills": {
    "technical": ["string"],
    "languages": ["string"],
    "frameworks": ["string"],
    "tools": ["string"],
    "soft_skills": ["string"]
  },
  "certifications": ["string"],
  "awards": ["string"],
  "languages": ["string"],
  "cover_letter": "string or null (full text of cover letter if present)",
  "additional_info": "string or null (any other important information not fitting above categories: hobbies, military service, volunteer work, training programs, etc.)"
}

Extract as much information as possible from the resume. If a field is not present, use null or empty array.
IMPORTANT: Put any information that doesn't fit the standard fields into 'additional_info' to prevent data loss."""

    user_prompt = f"""Parse this resume and extract structured information. Output ONLY the JSON object, no other text.

Resume:
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
            max_tokens=4000
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
                max_tokens=4000
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
    
    system_prompt = """You are an expert job description parser. Parse the given JD text and extract structured information.
Output ONLY a valid JSON object matching the provided schema. Do not include any explanations or markdown formatting.

Schema:
{
  "company": "string or null",
  "position": "string",
  "department": "string or null",
  "employment_type": "string or null",
  "location": "string or null",
  "requirements": {
    "required_skills": ["string"],
    "preferred_skills": ["string"],
    "required_experience": "string or null",
    "preferred_experience": "string or null",
    "education": "string or null",
    "certifications": ["string"]
  },
  "responsibilities": ["string"],
  "tech_stack": ["string"],
  "preferred_qualifications": ["string"],
  "benefits": ["string"],
  "additional_info": "string or null (any other important information not fitting above categories: company culture, working hours, special notes, etc.)"
}

Extract as much information as possible from the JD. If a field is not present, use null or empty array.
IMPORTANT: Put any information that doesn't fit the standard fields into 'additional_info' to prevent data loss."""

    user_prompt = f"""Parse this job description and extract structured information. Output ONLY the JSON object, no other text.

Job Description:
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
            max_tokens=3000
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
                max_tokens=3000
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
