"""
build_report_graph(checkpointer, max_attempts: int = 1)
입력: checkpointer(선택), max_attempts(1 권장)
출력: graph(컴파일된 LangGraph)
설명: retrieve → draft → schema → light ↔ repair → heavy → verify → finalize 노드와 분기를 연결한다.
"""

"""
run_report(graph, input_ctx: dict) -> dict
입력: graph, input_ctx(resume/jd/logs/axes 등)
출력: final_report(JSON)
설명: 그래프를 실행해 최종 리포트를 생성한다. 실패 시 오류와 함께 종료.
"""
