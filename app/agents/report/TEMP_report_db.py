"""
TEMP_report_db.py (IO 정의)
save(report: dict) -> str
  입력: report(JSON) / 출력: report_id(str)
get(report_id: str) -> dict | None
  입력: report_id / 출력: report(JSON) 또는 None
apply_feedback(report_id: str, payload: dict) -> dict
  입력: report_id, payload({scores/tags/decision/...}) / 출력: updated report
read_texts(paths: list[str]) -> list[str]
  입력: 파일 경로 리스트 / 출력: 각 파일의 텍스트 리스트
설명: 임시 저장소 유틸리티. 영속 DB 대체용.
"""
