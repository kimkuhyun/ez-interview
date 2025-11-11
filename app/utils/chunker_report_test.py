"""
임시 청킹 유틸 - 추후 VectorDB 통합 시 삭제 예정
로컬 텍스트 파일을 청킹하는 임시 함수들
"""
from typing import List, Dict, Any
import re


def _norm(t: str) -> str:
    """텍스트 정규화"""
    return re.sub(r"\r\n?", "\n", t).strip()


def chunk_paragraph_report_test(text: str, size: int = 600, overlap: int = 100) -> List[str]:
    """
    단락 기반 청킹 (임시)
    TODO: VectorDB 통합 시 app/utils/embedding.py의 청킹 함수로 교체
    """
    text = _norm(text)
    if not text:
        return []
    
    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks = []
    
    for para in paras:
        if len(para) <= size:
            chunks.append(para)
            continue
        
        # 문장 분리
        sents = re.split(r"(?<=[.!?。…])\s+|\n", para)
        buf = ""
        for s in sents:
            if not s.strip():
                continue
            cand = (buf + " " + s).strip() if buf else s.strip()
            if len(cand) <= size:
                buf = cand
            else:
                if buf:
                    chunks.append(buf)
                buf = s.strip()
        if buf:
            chunks.append(buf)
    
    if not chunks:
        return []
    
    # 오버랩 추가
    out = [chunks[0]]
    for i in range(1, len(chunks)):
        out.append((chunks[i-1][-overlap:] + " " + chunks[i]).strip())
    return out


def chunk_log_report_test(log: str) -> List[Dict[str, Any]]:
    """
    인터뷰 로그를 질문-답변 쌍으로 파싱 (임시)
    형식: Q1 주제\n[00:00] 면접관: ...\n[00:05] 응답자: ...
    TODO: VectorDB 통합 시 app/utils/embedding.py의 로그 청킹 함수로 교체
    """
    blocks = re.split(r"(?m)^(?=Q\d+)", _norm(log))
    qa_pairs = []
    
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        
        # Q 번호와 주제 추출
        q_match = re.match(r"^Q(\d+)\s+(.+?)$", block, re.MULTILINE)
        if not q_match:
            continue
        
        q_num = q_match.group(1)
        q_topic = q_match.group(2).strip()
        
        # 면접관 질문 추출 (첫 번째 면접관 발언)
        interviewer_lines = re.findall(r'\[[\d:]+\]\s*면접관[^:]*:\s*(.+?)(?=\n|$)', block)
        q_text = interviewer_lines[0] if interviewer_lines else q_topic
        
        # 응답자 답변 추출 (모든 응답자 발언 결합)
        respondent_lines = re.findall(r'\[[\d:]+\]\s*응답자[^:]*:\s*(.+?)(?=\n|$)', block)
        a_text = " ".join(respondent_lines) if respondent_lines else ""
        
        qa_pairs.append({
            "q_num": q_num,
            "question": q_text,
            "answer": a_text,
            "topic": q_topic,
            "full_text": block
        })
    
    return qa_pairs


def extract_clean_question_report_test(question_text: str) -> str:
    """
    질문 텍스트 정리 (임시)
    예: "[00:00] 면접관: 간단히 자기소개 부탁드립니다." → "간단히 자기소개 부탁드립니다?"
    TODO: VectorDB 통합 시 삭제
    """
    # 타임스탬프 제거: [00:00], [01:23] 등
    text = re.sub(r'\[\d{2}:\d{2}\]', '', question_text)
    # 역할 레이블 제거: "면접관:", "응답자:" 등
    text = re.sub(r'(면접관|응답자)\s*:', '', text)
    # 공백 정리
    text = re.sub(r'\s+', ' ', text).strip()
    # 물음표가 없으면 추가
    if text and not text.endswith('?') and not text.endswith('.'):
        text += '?'
    return text
