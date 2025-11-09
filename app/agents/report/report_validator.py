"""
schema_validate(draft: dict) -> list[str]
입력: draft(JSON)
출력: errors: list[str]
설명: 필수 키/타입/키셋(axes=scores=weights) 존재 여부를 fail-fast로 검증.

light_validate(draft: dict, j_ids: list[str], e_ids: list[str], qa_ids: list[str]) -> list[str]
입력: draft(JSON), j_ids, e_ids, qa_ids
출력: errors: list[str]
설명: 연속성(J/E), 매핑(JD↔Evidence), 고아 Evidence, QA 커버리지, 점수/가중치 범위를 규칙 기반으로 검증.

verify_report(final: dict) -> list[str]
입력: final(JSON; finalize 후)
출력: errors: list[str]
설명: 최종 DTO에 대해 전체 재검증을 수행한다.

repair_structure(draft: dict, errors: list[str]) -> dict
입력: draft, errors(light_validate 결과)
출력: draft': dict
설명: 키셋/가중치/ID 포맷 등 구조적 오류를 1회 자동 보정한다(시맨틱 변경 금지).
"""
