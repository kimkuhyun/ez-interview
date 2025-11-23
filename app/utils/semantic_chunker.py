"""
구조화된 문서를 의미 단위로 청킹하는 모듈
Resume와 JD를 섹션별/의미별로 분할하여 RAG 검색 품질 향상
"""

from typing import List, Dict, Any
from app.utils.schemas import StructuredResume, StructuredJD
import json
import hashlib
import hashlib
import copy
import re  

def _scrub_contacts(text: str) -> str:
    text = re.sub(r'[\w.+-]+@[\w.-]+', '[EMAIL]', text)
    text = re.sub(r'\+?\d[\d\s()\-]{7,}\d', '[PHONE]', text)
    return text


def anonymize_personal_info(resume: StructuredResume) -> StructuredResume:
    """
    개인정보를 익명화 처리 (원본은 수정하지 않고 복사본 반환)
    
    익명화 규칙:
    - 이름: "김철수" → "지원자_A3F2" (MD5 해시 기반 익명 ID)
    - 이메일: 완전 제거 (None)
    - 전화번호: 완전 제거 (None)
    - GitHub/LinkedIn/블로그: 유지 (기술 평가에 유용)
    - 주소: 완전 제거 (None) - basic_info에 주소 필드 없지만 대비
    
    Args:
        resume: 원본 구조화된 이력서 객체
        
    Returns:
        StructuredResume: 익명화된 이력서 객체 (복사본)
    """
    # Deep copy로 원본 보호
    anonymized = copy.deepcopy(resume)
    
    if anonymized.basic_info:
        # 1️⃣ 이름 익명화 (해시 기반 익명 ID 생성)
        if anonymized.basic_info.name:
            name_hash = hashlib.md5(anonymized.basic_info.name.encode()).hexdigest()[:4].upper()
            original_name = anonymized.basic_info.name
            anonymized.basic_info.name = f"지원자_{name_hash}"
            print(f"   🔒 [익명화] 이름: '{original_name}' → '{anonymized.basic_info.name}'")
        
        # 2️⃣ 연락처 완전 제거
        if anonymized.basic_info.email:
            print(f"   🔒 [익명화] 이메일 제거: {anonymized.basic_info.email[:10]}...")
            anonymized.basic_info.email = None
        
        if anonymized.basic_info.phone:
            print(f"   🔒 [익명화] 전화번호 제거: {anonymized.basic_info.phone}")
            anonymized.basic_info.phone = None
        
        # 3️⃣ GitHub/LinkedIn/블로그는 유지 (기술 평가용)
        preserved = []
        if anonymized.basic_info.github:
            preserved.append(f"GitHub: {anonymized.basic_info.github}")
        if anonymized.basic_info.linkedin:
            preserved.append(f"LinkedIn: {anonymized.basic_info.linkedin}")
        if anonymized.basic_info.blog:
            preserved.append(f"Blog: {anonymized.basic_info.blog}")
        
        if preserved:
            print(f"   ℹ️  [익명화] 유지된 정보: {', '.join(preserved)}")
    
    return anonymized


def chunk_structured_resume(resume: StructuredResume) -> List[Dict[str, Any]]:
    """
    구조화된 이력서를 의미 단위로 청킹
    
    Args:
        resume: 구조화된 이력서 객체
        
    Returns:
        List[Dict]: 청크 리스트 (content, metadata 포함)
    """
    # 🔒 개인정보 익명화 처리
    print(f"\n🔒 [개인정보 보호] 익명화 시작")
    resume = anonymize_personal_info(resume)
    print(f"✅ [개인정보 보호] 익명화 완료\n")
    
    chunks = []
    
    # 1. 기본 정보 청크
    if resume.basic_info:
        basic_parts = []
        if resume.basic_info.name:
            basic_parts.append(f"이름: {resume.basic_info.name}")
        if resume.basic_info.email:
            basic_parts.append(f"이메일: {resume.basic_info.email}")
        if resume.basic_info.phone:
            basic_parts.append(f"전화번호: {resume.basic_info.phone}")
        if resume.basic_info.github:
            basic_parts.append(f"GitHub: {resume.basic_info.github}")
        if resume.basic_info.linkedin:
            basic_parts.append(f"LinkedIn: {resume.basic_info.linkedin}")
        if resume.basic_info.blog:
            basic_parts.append(f"블로그: {resume.basic_info.blog}")
        if resume.basic_info.summary:
            basic_parts.append(f"소개: {resume.basic_info.summary}")
        
        if basic_parts:
            chunks.append({
                "content": "\n".join(basic_parts),
                "metadata": {
                    "section": "basic_info",
                    "type": "resume"
                }
            })
    
    # 2. 학력 정보 (각 학교별로 독립 청크)
    for idx, edu in enumerate(resume.education):
        edu_parts = [f"학교: {edu.school}"]
        if edu.major:
            edu_parts.append(f"전공: {edu.major}")
        if edu.degree:
            edu_parts.append(f"학위: {edu.degree}")
        if edu.period:
            edu_parts.append(f"기간: {edu.period}")
        if edu.gpa:
            edu_parts.append(f"학점: {edu.gpa}")
        if edu.activities:
            edu_parts.append(f"활동: {', '.join(edu.activities)}")
        
        chunks.append({
            "content": "\n".join(edu_parts),
            "metadata": {
                "section": "education",
                "type": "resume",
                "school": edu.school,
                "index": idx
            }
        })
    
    # 3. 경력 정보 (각 회사별로 독립 청크)
    for idx, exp in enumerate(resume.experience):
        exp_parts = [
            f"회사: {exp.company}",
            f"역할: {exp.role}",
            f"기간: {exp.period}"
        ]
        if exp.description:
            exp_parts.append(f"설명: {exp.description}")
        if exp.achievements:
            exp_parts.append(f"성과:\n- " + "\n- ".join(exp.achievements))
        if exp.tech_stack:
            exp_parts.append(f"기술 스택: {', '.join(exp.tech_stack)}")
        
        chunks.append({
            "content": "\n".join(exp_parts),
            "metadata": {
                "section": "experience",
                "type": "resume",
                "company": exp.company,
                "role": exp.role,
                "period": exp.period,
                "tech_stack": exp.tech_stack,
                "index": idx
            }
        })
    
    # 4. 프로젝트 정보 (각 프로젝트별로 독립 청크)
    for idx, proj in enumerate(resume.projects):
        proj_parts = [
            f"프로젝트: {proj.name}",
            f"설명: {proj.description}"
        ]
        if proj.period:
            proj_parts.append(f"기간: {proj.period}")
        if proj.role:
            proj_parts.append(f"역할: {proj.role}")
        if proj.achievements:
            proj_parts.append(f"성과:\n- " + "\n- ".join(proj.achievements))
        if proj.tech_stack:
            proj_parts.append(f"기술 스택: {', '.join(proj.tech_stack)}")
        if proj.url:
            proj_parts.append(f"URL: {proj.url}")
        
        chunks.append({
            "content": "\n".join(proj_parts),
            "metadata": {
                "section": "projects",
                "type": "resume",
                "project_name": proj.name,
                "tech_stack": proj.tech_stack,
                "index": idx
            }
        })
    
    # 5. 스킬 정보 (카테고리별 청크)
    if resume.skills:
        skills_parts = []
        if resume.skills.technical:
            skills_parts.append(f"기술 스킬: {', '.join(resume.skills.technical)}")
        if resume.skills.languages:
            skills_parts.append(f"프로그래밍 언어: {', '.join(resume.skills.languages)}")
        if resume.skills.frameworks:
            skills_parts.append(f"프레임워크: {', '.join(resume.skills.frameworks)}")
        if resume.skills.tools:
            skills_parts.append(f"도구: {', '.join(resume.skills.tools)}")
        if resume.skills.soft_skills:
            skills_parts.append(f"소프트 스킬: {', '.join(resume.skills.soft_skills)}")
        
        if skills_parts:
            chunks.append({
                "content": "\n".join(skills_parts),
                "metadata": {
                    "section": "skills",
                    "type": "resume",
                    "all_skills": resume.skills.technical + resume.skills.languages + 
                                 resume.skills.frameworks + resume.skills.tools
                }
            })
    
    # 6. 자격증 (하나의 청크로)
    if resume.certifications:
        chunks.append({
            "content": f"자격증:\n- " + "\n- ".join(resume.certifications),
            "metadata": {
                "section": "certifications",
                "type": "resume",
                "certifications": resume.certifications
            }
        })
    
    # 7. 수상 경력 (하나의 청크로)
    if resume.awards:
        chunks.append({
            "content": f"수상 경력:\n- " + "\n- ".join(resume.awards),
            "metadata": {
                "section": "awards",
                "type": "resume",
                "awards": resume.awards
            }
        })
    
    # 8. 언어 능력 (하나의 청크로)
    if resume.languages:
        chunks.append({
            "content": f"언어 능력: {', '.join(resume.languages)}",
            "metadata": {
                "section": "languages",
                "type": "resume",
                "languages": resume.languages
            }
        })
    
    # 9. 기타 정보 (하나의 청크로)
    if resume.additional_info:
        chunks.append({
            "content": f"기타 정보:\n{resume.additional_info}",
            "metadata": {
                "section": "additional_info",
                "type": "resume"
            }
        })
    
    # 10. 자기소개서 (길이에 따라 분할)
    if resume.cover_letter:
        cover_letter_text = resume.cover_letter.strip()
        # 자기소개서가 긴 경우 (2000자 이상) 여러 청크로 분할
        if len(cover_letter_text) > 2000:
            # 1500자씩 분할 (겹침 200자)
            chunk_size = 1500
            overlap = 300
            for i in range(0, len(cover_letter_text), chunk_size - overlap):
                chunk_text = cover_letter_text[i:i + chunk_size]
                chunks.append({
                    "content": f"자기소개서 (Part {i // (chunk_size - overlap) + 1}):\n{chunk_text}",
                    "metadata": {
                        "section": "cover_letter",
                        "type": "resume",
                        "part": i // (chunk_size - overlap) + 1
                    }
                })
        else:
            # 짧은 경우 하나의 청크로
            chunks.append({
                "content": f"자기소개서:\n{cover_letter_text}",
                "metadata": {
                    "section": "cover_letter",
                    "type": "resume"
                }
            })

    # raw_text = _scrub_contacts(resume.raw_text or "").strip()
    # if raw_text:
    #     size, overlap = 1200, 300
    #     step = size - overlap
    #     for i in range(0, len(raw_text), step):
    #         chunk_text = raw_text[i:i + size]
    #         chunks.append({
    #             "content": chunk_text,
    #             "metadata": {
    #                 "section": "raw_text",
    #                 "type": "resume",
    #                 "part": i // step + 1
    #             }
    #         })
    #     print(f"   📄 [원문 청킹] {len(raw_text)}자 → {(len(raw_text) + step - 1) // step}개 청크 생성")

    return chunks


def chunk_structured_jd(jd: StructuredJD) -> List[Dict[str, Any]]:
    """
    구조화된 JD를 의미 단위로 청킹
    
    Args:
        jd: 구조화된 JD 객체
        
    Returns:
        List[Dict]: 청크 리스트 (content, metadata 포함)
    """
    chunks = []
    
    # 1. 포지션 기본 정보
    basic_parts = [f"포지션: {jd.position}"]
    if jd.company:
        basic_parts.append(f"회사: {jd.company}")
    if jd.department:
        basic_parts.append(f"부서: {jd.department}")
    if jd.employment_type:
        basic_parts.append(f"고용 형태: {jd.employment_type}")
    if jd.location:
        basic_parts.append(f"근무지: {jd.location}")
    
    chunks.append({
        "content": "\n".join(basic_parts),
        "metadata": {
            "section": "basic_info",
            "type": "jd",
            "position": jd.position,
            "company": jd.company
        }
    })
    
    # 2. 필수 요구사항
    if jd.requirements:
        req_parts = []
        
        if jd.requirements.required_skills:
            req_parts.append(f"필수 기술:\n- " + "\n- ".join(jd.requirements.required_skills))
        if jd.requirements.required_experience:
            req_parts.append(f"필수 경력: {jd.requirements.required_experience}")
        if jd.requirements.education:
            req_parts.append(f"학력: {jd.requirements.education}")
        if jd.requirements.certifications:
            req_parts.append(f"자격증:\n- " + "\n- ".join(jd.requirements.certifications))
        
        if req_parts:
            chunks.append({
                "content": "\n\n".join(req_parts),
                "metadata": {
                    "section": "required_requirements",
                    "type": "jd",
                    "required_skills": jd.requirements.required_skills,
                    "required_experience": jd.requirements.required_experience
                }
            })
        
        # 3. 우대 요구사항 (별도 청크)
        pref_parts = []
        if jd.requirements.preferred_skills:
            pref_parts.append(f"우대 기술:\n- " + "\n- ".join(jd.requirements.preferred_skills))
        if jd.requirements.preferred_experience:
            pref_parts.append(f"우대 경력: {jd.requirements.preferred_experience}")
        
        if pref_parts:
            chunks.append({
                "content": "\n\n".join(pref_parts),
                "metadata": {
                    "section": "preferred_requirements",
                    "type": "jd",
                    "preferred_skills": jd.requirements.preferred_skills,
                    "preferred_experience": jd.requirements.preferred_experience
                }
            })
    
    # 4. 담당 업무 (각 업무별 청크 또는 그룹화)
    if jd.responsibilities:
        # 업무가 많으면 3-4개씩 그룹화
        chunk_size = 4
        for i in range(0, len(jd.responsibilities), chunk_size):
            resp_group = jd.responsibilities[i:i+chunk_size]
            chunks.append({
                "content": f"담당 업무:\n- " + "\n- ".join(resp_group),
                "metadata": {
                    "section": "responsibilities",
                    "type": "jd",
                    "group_index": i // chunk_size
                }
            })
    
    # 5. 기술 스택
    if jd.tech_stack:
        chunks.append({
            "content": f"기술 스택: {', '.join(jd.tech_stack)}",
            "metadata": {
                "section": "tech_stack",
                "type": "jd",
                "tech_stack": jd.tech_stack
            }
        })
    
    # 6. 우대사항
    if jd.preferred_qualifications:
        chunks.append({
            "content": f"우대사항:\n- " + "\n- ".join(jd.preferred_qualifications),
            "metadata": {
                "section": "preferred_qualifications",
                "type": "jd"
            }
        })
    
    # 7. 복리후생
    if jd.benefits:
        chunks.append({
            "content": f"복리후생:\n- " + "\n- ".join(jd.benefits),
            "metadata": {
                "section": "benefits",
                "type": "jd"
            }
        })
    
    
    # 8. 기타 정보
    if jd.additional_info:
        chunks.append({
            "content": f"기타 정보:\n{jd.additional_info}",
            "metadata": {
                "section": "additional_info",
                "type": "jd"
            }
        })
    
    return chunks


def chunks_to_text_with_metadata(chunks: List[Dict[str, Any]]) -> List[tuple[str, Dict[str, Any]]]:
    """
    청크 리스트를 (텍스트, 메타데이터) 튜플 리스트로 변환
    RAG 인덱싱 시 사용
    
    Args:
        chunks: chunk_structured_resume 또는 chunk_structured_jd 결과
        
    Returns:
        List[tuple]: (content, metadata) 튜플 리스트
    """
    return [(chunk["content"], chunk["metadata"]) for chunk in chunks]
