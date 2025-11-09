"""
refine_summary(draft: dict, issues: dict) -> dict
입력: draft(JSON), issues(quality_review 결과)
출력: draft': dict
설명: 요약·헤드라인·태그를 보강한다.

ensure_talk_tracks(draft: dict) -> dict
입력: draft(JSON)
출력: draft': dict
설명: 대화 요약 트랙(섹션/포인트/근거 ID)의 최소 스키마를 채운다.
"""
