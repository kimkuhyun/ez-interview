from __future__ import annotations

from typing import Any, Dict, List, Optional
from dataclasses import dataclass


@dataclass
class ReportInput:
    """보고서 입력 데이터."""
    documents: Dict[str, Optional[str]]
    questions: List[str]
    transcripts: List[List[Dict[str, str]]]


def generate_report(
    questions: List[str],
    transcripts: List[List[Dict[str, str]]],
    documents: Optional[Dict[str, Optional[str]]] = None,
    use_dummy: bool = False
) -> Dict[str, Any]:
    """
    면접 데이터를 기반으로 구조화된 리포트를 생성합니다.
    
    Args:
        questions: 질문 리스트
        transcripts: 질문별 대화 내역 (각 질문당 턴 리스트)
        documents: 이력서/JD 텍스트 딕셔너리 (선택)
        use_dummy: 더미 데이터 사용 여부
    
    Returns:
        리포트 딕셔너리 (persona, strengths, risks, keywords, jd_coverage, evidence, conv_stats, next_steps 등)
    """
    # 더미 데이터 모드
    if use_dummy:
        return _generate_dummy_report()
    
    total_msgs = sum(len(t or []) for t in transcripts)
    
    # 페르소나/추천 분석 (향후 LLM 기반으로 확장 가능)
    persona = _analyze_persona(questions, transcripts)
    
    # 강점/리스크 추출
    strengths, risks = _extract_strengths_and_risks(questions, transcripts)
    
    # 키워드 추출
    keywords = _extract_keywords(questions, transcripts)
    
    # 차원별 점수 계산 (육각형 차트용)
    dimension_scores = _calculate_dimension_scores(questions, transcripts, documents)
    
    # 최종 점수 계산
    final_score = _calculate_final_score_from_dimensions(dimension_scores)
    
    # 증거 맵 생성
    evidence = _build_evidence_map(transcripts)
    
    # JD 커버리지 분석
    jd_coverage = _analyze_jd_coverage(documents, transcripts, evidence) if documents else []
    
    # 대화 통계
    conv_stats = _calculate_conversation_stats(questions, transcripts)
    
    # 다음 단계 권고
    next_steps = _generate_next_steps(questions, transcripts, jd_coverage)
    
    # Red Flags 카운트
    red_flags = _count_red_flags(questions, transcripts, jd_coverage)
    
    return {
        "persona": persona,
        "dimension_scores": dimension_scores,
        "final_score": final_score,
        "strengths": strengths,
        "risks": risks,
        "keywords": keywords,
        "red_flags": red_flags,
        "jd_coverage": jd_coverage,
        "evidence": evidence,
        "conv_stats": conv_stats,
        "next_steps": next_steps,
    }


def _analyze_persona(questions: List[str], transcripts: List[List[Dict[str, str]]]) -> Dict[str, str]:
    """페르소나 분석 (긍정/부정 평가, 태그)"""
    total_turns = sum(len(t or []) for t in transcripts)
    avg_turns = total_turns / len(questions) if questions else 0
    
    if avg_turns >= 3:
        pos = "답변 충실, 구조적 설명"
        tag = "#구조적사고"
    elif avg_turns >= 1:
        pos = "기본 답변 제공"
        tag = "#간결형"
    else:
        pos = "답변 시도"
        tag = "#미완성"
    
    neg = "일부 구체적 증거 부족" if avg_turns < 3 else "추가 검증 필요"
    
    return {"pos": pos, "neg": neg, "tag": tag}


def _extract_strengths_and_risks(
    questions: List[str], 
    transcripts: List[List[Dict[str, str]]]
) -> tuple[List[str], List[str]]:
    """강점과 리스크를 추출"""
    strengths = []
    risks = []
    
    for i, q in enumerate(questions):
        turn_count = len(transcripts[i]) if i < len(transcripts) else 0
        
        if turn_count >= 3:
            strengths.append(f"Q{i+1}: 충분한 대화 진행 ({turn_count}턴)")
        elif turn_count == 0:
            risks.append(f"Q{i+1}: 답변 없음")
        elif turn_count == 1:
            risks.append(f"Q{i+1}: 답변 단답형 (1턴)")
    
    # 기본값 제공
    if not strengths:
        strengths = ["대화 진행됨"]
    
    return strengths, risks


def _extract_keywords(questions: List[str], transcripts: List[List[Dict[str, str]]]) -> List[str]:
    """질문과 대화에서 키워드 추출 (샘플 로직)"""
    # 질문에서 키워드 추출
    question_words = set()
    for q in questions:
        words = [w for w in q.split() if len(w) > 2]
        question_words.update(words[:3])  # 각 질문당 최대 3개
    
    # 대화에서 키워드 추출 (향후 TF-IDF나 LLM 기반으로 확장)
    transcript_words = set()
    for turns in transcripts:
        for turn in (turns or []):
            text = turn.get("text", "")
            words = [w for w in text.split() if len(w) > 2]
            transcript_words.update(words[:2])
    
    all_keywords = list(question_words | transcript_words)[:10]
    return all_keywords if all_keywords else ["면접", "질문", "답변"]


def _calculate_final_score(questions: List[str], transcripts: List[List[Dict[str, str]]]) -> float:
    """최종 점수 계산 (샘플 로직: 답변 충실도 기반)"""
    if not questions:
        return 0.0
    
    total_turns = sum(len(t or []) for t in transcripts)
    avg_turns = total_turns / len(questions)
    
    # 간단한 점수 로직: 평균 턴 수를 기반으로 5점 만점 계산
    score = min(5.0, 2.0 + avg_turns * 0.5)
    return round(score, 1)


def _calculate_dimension_scores(
    questions: List[str],
    transcripts: List[List[Dict[str, str]]],
    documents: Optional[Dict[str, Optional[str]]]
) -> List[Dict[str, Any]]:
    """
    차원별 점수 계산 (육각형 차트용)
    
    6개 차원: 직무/기술, 문제해결, 실행력, 협업/적응, 커뮤니케이션, 학습민첩성
    """
    # 질문이나 대화가 없으면 빈 리스트 반환
    if not questions:
        return []
    
    total_turns = sum(len(t or []) for t in transcripts)
    if total_turns == 0:
        return []
    
    dimensions = [
        {"key": "job", "label": "직무/기술"},
        {"key": "problem", "label": "문제해결"},
        {"key": "execution", "label": "실행력"},
        {"key": "collab", "label": "협업/적응"},
        {"key": "comm", "label": "커뮤니케이션"},
        {"key": "learning", "label": "학습민첩성"},
    ]
    
    # 간단한 점수 계산 (향후 LLM이나 키워드 매칭으로 고도화 가능)
    avg_turns = total_turns / len(questions)
    
    # 기본 점수 (평균 턴 기반)
    base_score = min(5.0, 2.0 + avg_turns * 0.5)
    
    # 각 차원별로 약간의 변동 추가 (실제로는 키워드/내용 분석 필요)
    scores = []
    for i, dim in enumerate(dimensions):
        # 간단한 변동: 차원마다 +/- 0.5 정도
        variation = (i % 3 - 1) * 0.3  # -0.3, 0, 0.3 등
        score = max(1.0, min(5.0, base_score + variation))
        scores.append({
            "key": dim["key"],
            "label": dim["label"],
            "score": round(score, 1)
        })
    
    return scores


def _calculate_final_score_from_dimensions(dimension_scores: List[Dict[str, Any]]) -> float:
    """차원별 점수로부터 최종 점수 계산 (가중평균)"""
    if not dimension_scores:
        return 0.0
    
    # 가중치 (직무/기술에 더 높은 가중치)
    weights = {
        "job": 0.30,
        "problem": 0.15,
        "execution": 0.15,
        "collab": 0.15,
        "comm": 0.15,
        "learning": 0.10,
    }
    
    total_weight = sum(weights.values())
    weighted_sum = sum(
        s["score"] * weights.get(s["key"], 0.1)
        for s in dimension_scores
    )
    
    return round(weighted_sum / total_weight, 1)


def _analyze_jd_coverage(
    documents: Dict[str, Optional[str]], 
    transcripts: List[List[Dict[str, str]]],
    evidence: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """
    JD 커버리지 분석
    
    JD 텍스트에서 요구사항을 추출하고, 대화 내용과 매칭하여 충족도 평가
    """
    jd_text = documents.get("jd", "") or ""
    if not jd_text:
        return []
    
    # JD에서 요구사항 키워드 추출 (간단한 휴리스틱)
    requirements = _extract_jd_requirements(jd_text)
    
    # 대화에서 언급된 키워드 추출
    mentioned_keywords = set()
    for turns in transcripts:
        for turn in (turns or []):
            text = turn.get("text", "").lower()
            mentioned_keywords.update(text.split())
    
    # 각 요구사항별 충족도 평가
    coverage = []
    for i, req in enumerate(requirements):
        req_keywords = set(req["keywords"])
        matched = req_keywords & mentioned_keywords
        
        fulfillment_rate = len(matched) / len(req_keywords) if req_keywords else 0
        
        if fulfillment_rate >= 0.7:
            fulfillment = "충분"
            gap = ""
        elif fulfillment_rate >= 0.4:
            fulfillment = "적정"
            gap = ""
        else:
            fulfillment = "확인 필요"
            gap = "⚠"
        
        # 연결된 evidence ID 찾기 (간단한 매칭)
        linked_evidence = [
            ev["eid"] for ev in evidence
            if any(kw in ev["statement"].lower() for kw in req_keywords)
        ]
        
        coverage.append({
            "id": f"JD-{i+1}",
            "requirement": req["name"],
            "expectation": req["importance"],
            "fulfillment": fulfillment,
            "gap": gap,
            "evidence": linked_evidence[:3]  # 최대 3개
        })
    
    return coverage


def _extract_jd_requirements(jd_text: str) -> List[Dict[str, Any]]:
    """
    JD 텍스트에서 요구사항 추출 (간단한 키워드 기반)
    
    실제로는 LLM이나 NLP 파싱으로 개선 가능
    """
    # 기술 스택 관련 키워드
    tech_keywords = ["api", "python", "java", "react", "sql", "docker", "kubernetes", "aws", "gcp"]
    soft_keywords = ["협업", "커뮤니케이션", "리더십", "문제해결", "teamwork", "communication"]
    
    text_lower = jd_text.lower()
    
    requirements = []
    
    # 기술 요구사항
    matched_tech = [kw for kw in tech_keywords if kw in text_lower]
    if matched_tech:
        requirements.append({
            "name": "기술 스택 경험",
            "importance": "높음",
            "keywords": matched_tech[:3]
        })
    
    # 소프트 스킬
    matched_soft = [kw for kw in soft_keywords if kw in text_lower]
    if matched_soft:
        requirements.append({
            "name": "협업/소프트 스킬",
            "importance": "보통",
            "keywords": matched_soft[:2]
        })
    
    # 경력/프로젝트 경험
    if any(word in text_lower for word in ["경력", "년", "프로젝트", "경험"]):
        requirements.append({
            "name": "경력/프로젝트 경험",
            "importance": "높음",
            "keywords": ["경력", "프로젝트", "경험"]
        })
    
    return requirements if requirements else [
        {"name": "일반 요구사항", "importance": "보통", "keywords": ["경험", "역량"]}
    ]


def _build_evidence_map(transcripts: List[List[Dict[str, str]]]) -> List[Dict[str, Any]]:
    """
    증거 맵 생성
    
    대화 내용에서 주요 발언(증거)을 추출하고 EID 부여
    """
    evidence_list = []
    eid_counter = 1
    
    for q_idx, turns in enumerate(transcripts):
        for turn_idx, turn in enumerate(turns or []):
            text = turn.get("text", "").strip()
            role = turn.get("role", "user")
            
            # 길이가 충분한 발언만 증거로 간주
            if len(text) < 10:
                continue
            
            # 신뢰도 평가 (간단한 휴리스틱: 길이 기반)
            if len(text) > 100:
                reliability = "높음"
            elif len(text) > 50:
                reliability = "보통"
            else:
                reliability = "낮음"
            
            evidence_list.append({
                "eid": f"E{eid_counter}",
                "source": f"인터뷰 Q{q_idx+1}-T{turn_idx+1}",
                "statement": text[:100] + ("..." if len(text) > 100 else ""),  # 요약
                "reliability": reliability,
                "jd": []  # JD 연결은 coverage에서 역참조
            })
            
            eid_counter += 1
            
            # 최대 20개까지만
            if len(evidence_list) >= 20:
                return evidence_list
    
    return evidence_list


def _calculate_conversation_stats(
    questions: List[str], 
    transcripts: List[List[Dict[str, str]]]
) -> List[Dict[str, str]]:
    """대화 통계 계산"""
    total_questions = len(questions)
    total_turns = sum(len(t or []) for t in transcripts)
    avg_turns = total_turns / total_questions if total_questions else 0
    
    return [
        {"key": "총 질문 수", "value": str(total_questions)},
        {"key": "총 대화 턴", "value": str(total_turns)},
        {"key": "평균 턴/질문", "value": f"{avg_turns:.1f}"},
    ]


def _generate_next_steps(
    questions: List[str], 
    transcripts: List[List[Dict[str, str]]],
    jd_coverage: List[Dict[str, Any]]
) -> List[Dict[str, str]]:
    """다음 단계 권고 생성"""
    next_steps = []
    
    if not questions:
        next_steps.append({
            "trigger": "질문 없음",
            "action": "질문 생성 후 재시작",
            "due": "즉시"
        })
    
    total_turns = sum(len(t or []) for t in transcripts)
    if total_turns == 0:
        next_steps.append({
            "trigger": "대화 없음",
            "action": "인터뷰 진행 필요",
            "due": "즉시"
        })
    
    # JD 커버리지 기반 권고 (향후 확장)
    for item in jd_coverage:
        if item.get("gap"):
            next_steps.append({
                "trigger": f"{item.get('requirement')} 미충족",
                "action": "추가 검증 필요",
                "due": "3일"
            })
    
    return next_steps


def _count_red_flags(
    questions: List[str], 
    transcripts: List[List[Dict[str, str]]],
    jd_coverage: List[Dict[str, Any]]
) -> int:
    """Red Flags 개수 카운트"""
    flags = 0
    
    # 답변 없는 질문
    for i, q in enumerate(questions):
        if i < len(transcripts) and len(transcripts[i] or []) == 0:
            flags += 1
    
    # JD 필수 항목 미충족 (향후 확장)
    for item in jd_coverage:
        if item.get("gap") and item.get("expectation") == "높음":
            flags += 1
    
    return flags


def build_report_payload(
    documents: Dict[str, Optional[str]], 
    questions: List[str], 
    transcripts: List[List[Dict[str, str]]]
) -> ReportInput:
    """ReportInput을 생성해 반환합니다."""
    return ReportInput(documents=documents, questions=questions, transcripts=transcripts)


def _generate_dummy_report() -> Dict[str, Any]:
    """더미 데이터를 포함한 리포트 생성 (UI 테스트용)"""
    return {
        "is_dummy": True,
        "persona": {
            "pos": "문제 해결 능력이 우수하고 커뮤니케이션이 명확함",
            "neg": "일부 기술 스택 경험이 부족하며 대규모 프로젝트 경험 제한적",
            "tag": "주니어-미들 엔지니어"
        },
        "strengths": [
            "Python, FastAPI를 활용한 백엔드 개발 경험 보유",
            "문제 해결 과정을 논리적으로 설명하는 능력 우수",
            "새로운 기술 습득에 적극적이며 학습 의지가 강함"
        ],
        "risks": [
            "대규모 분산 시스템 설계 경험 부족",
            "프론트엔드 프레임워크 실무 경험 제한적",
            "프로젝트 리더십 경험이 거의 없음"
        ],
        "keywords": [
            "Python", "FastAPI", "REST API", "Docker", "PostgreSQL", 
            "문제해결", "학습능력", "커뮤니케이션"
        ],
        "dimension_scores": [
            {"label": "직무적합성", "score": 4.2},
            {"label": "문제해결", "score": 4.5},
            {"label": "실행력", "score": 3.8},
            {"label": "협업능력", "score": 4.0},
            {"label": "커뮤니케이션", "score": 4.3},
            {"label": "학습능력", "score": 4.7}
        ],
        "final_score": 4.25,
        "jd_coverage": [
            {
                "requirement": "Python 백엔드 개발",
                "expectation": "3년 이상",
                "fulfillment": "충분",
                "gap": "",
                "evidence": ["E001", "E003"]
            },
            {
                "requirement": "REST API 설계",
                "expectation": "높음",
                "fulfillment": "적정",
                "gap": "",
                "evidence": ["E002"]
            },
            {
                "requirement": "대규모 시스템 경험",
                "expectation": "높음",
                "fulfillment": "부족",
                "gap": "⚠️",
                "evidence": []
            }
        ],
        "evidence": [
            {
                "eid": "E001",
                "source": "Q1 대화",
                "statement": "FastAPI로 마이크로서비스 3개를 구축하고 Docker로 배포한 경험이 있습니다.",
                "reliability": "높음",
                "jd": ["Python 백엔드", "DevOps"]
            },
            {
                "eid": "E002",
                "source": "Q2 대화",
                "statement": "RESTful API 설계 원칙을 준수하며 Swagger 문서화를 항상 작성합니다.",
                "reliability": "높음",
                "jd": ["REST API"]
            },
            {
                "eid": "E003",
                "source": "Q3 대화",
                "statement": "PostgreSQL을 주로 사용하며 쿼리 최적화 경험이 있습니다.",
                "reliability": "보통",
                "jd": ["데이터베이스"]
            }
        ],
        "conv_stats": [
            {"key": "총 질문 수", "value": "5"},
            {"key": "총 턴 수", "value": "28"},
            {"key": "평균 응답 길이", "value": "142자"},
            {"key": "소요 시간", "value": "35분"}
        ],
        "next_steps": [
            {
                "trigger": "기술 스택 검증 필요",
                "action": "실무 코딩 테스트 진행",
                "due": "3일 이내"
            },
            {
                "trigger": "시스템 설계 역량 확인",
                "action": "시스템 디자인 인터뷰 추가",
                "due": "1주일 이내"
            }
        ],
        "red_flags": 1
    }

