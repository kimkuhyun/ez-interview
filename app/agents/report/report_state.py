"""
ReportState (TypedDict spec)
필드:
  - stage: str
  - attempts: int              # repair 시도수
  - max_attempts: int          # 기본 1(최대 2)
  - ctx: dict                  # retrieve 결과
  - draft: dict                # 초안/보정안
  - final_report: dict         # 최종 산출
  - errors: list[str]
설명: 그래프 전 단계가 공유하는 최소 상태 모델.
"""
