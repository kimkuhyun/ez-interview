"""
guard_grounding(draft: dict, j_ids: list[str], e_ids: list[str]) -> list[str]
입력: draft(JSON), j_ids(JD ID 리스트), e_ids(Evidence ID 리스트)
출력: errors: list[str]
설명: JD↔Evidence 연속성/포함성/양방향 매핑 위반을 감지한다.

build_id_sets(logs: list[dict]) -> tuple[list[str], list[str], list[str]]
입력: logs([{id:str, q:str, a:str, ...}])
출력: (j_ids, e_ids, qa_ids)
설명: 로그에서 JD/Evidence/QA 식별자 세트를 추출한다.
"""
