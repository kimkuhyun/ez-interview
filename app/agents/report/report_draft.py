"""
generate_draft(ctx: dict) -> dict
입력: ctx {resume:str, jd:str, logs:list[dict], selected_axes:list[str]}
출력: draft: dict(axes/scores/weights/headline/talkSummary/jdCoverage/evidence/evidenceMap)
설명: RAG 컨텍스트로 최종 리포트의 초안을 생성한다(수정·정규화는 하지 않음).

retrieve_context(sources: dict | None, paths: list[str] | None) -> dict
입력: sources(메모리/DB 핸들) 또는 paths(텍스트 파일 경로)
출력: ctx: dict { resume:str, jd:str, logs:list[dict], selected_axes:list[str], meta:dict }
설명: RAG용 컨텍스트를 수집·정제한다(캐시 키는 resume+jd 해시).

"""
