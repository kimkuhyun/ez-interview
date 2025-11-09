"""
validate_heavy(draft: dict, ctx: dict, llm) -> dict
alias: quality_review(draft: dict, ctx: dict, llm) -> dict

입력:
  - draft: dict        # 초안(JSON), 직접 수정 금지
  - ctx: dict          # RAG 스니펫·메타(필요 섹션만 첨부)
  - llm                # LLM 핸들(모델/온도 등 외부 설정)

출력:
  - result: dict = {
      "issues": list[dict],            # 문제 목록
      "flagged_sections": list[str],   # 재검토 섹션 키
      "decision": "pass" | "revise"    # 통과/수정 요청
    }

설명: 요약/의견/헤드라인/축 점수/JD 충족도의 '적절성'을 LLM으로 평가만 한다(수정 없음).
issues 권장 스키마: {type,target,reason,severity,evidence_ids?,fix_hint}
"""
