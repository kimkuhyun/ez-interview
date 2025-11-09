"""
normalize_score_payload(draft: dict) -> dict
입력: draft(JSON)
출력: draft': dict
설명: 점수 라운딩, 가중치 정규화(합 100), 키 정렬을 수행한다.

finalize_report(draft: dict) -> dict
입력: draft(JSON)
출력: final_report: dict(고정 DTO)
설명: 필드 정리, 참조 ID 고정, verify 통과 보장 형태로 최종 산출한다.
"""
