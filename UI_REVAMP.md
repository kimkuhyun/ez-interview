# EZ-Interview · UI 리뉴얼 & LangGraph 고도화

피드백 대응: ① UI가 심심하다/안 보인다 ② LangGraph가 너무 단순하다

---

## 1. 디자인 시스템 (청록 teal/cyan 라이트)

- `app/static/css/ezi-theme.css` — 토큰(청록 그라데이션·글래스·글로우·소프트섀도) + 컴포넌트 + 애니메이션.
- 메인 컬러 **teal `#14B8A6` → cyan `#06B6D4`** 그라데이션, 화이트 카드 + 청록 메시 배경. 폰트 Pretendard.

### 실제 적용된 화면
- **홈 랜딩** `app/templates/admin/tabs/landing.html` — 새 청록 히어로로 **전면 교체 완료**(다크 그라데이션·기능 칩·통계·orbit 모티프). 앱 실행 시 `/`에서 바로 보임.
- **리뉴얼 화면 목업 + 스크린샷** `app/static/design_preview/` — 대시보드 / 실시간 면접 / 평가 파이프라인 / v4o→v5 다이어그램 / 평가 리포트(추천·보류). 각 템플릿 이식용 레퍼런스(데이터 구조는 실제 ReportOut·파이프라인에 맞춤).

> 나머지 템플릿(`report_view.html` 등)은 `design_preview/mockups`의 마크업을 Jinja 바인딩으로 옮기면 적용됨. 기존 동작을 깨지 않도록 점진 이식 권장.

---

## 2. LangGraph 고도화 — 실제 적용됨

### 문제 (v4o)
`report_agent_v4o.py`는 각 분석 에이전트가 **자기 결과를 스스로 채점(self-scoring)**. 제3자 검증 주체 없는 "가짜 디베이트".

### 해결 (v5) — `app/agents/report_agent_v5.py`
```
START → optimize → query_plan → retrieve ◄────────────┐(데이터 재검색)
   → parallel(역량·요약·품질) ◄──────────────────────┐│(선택적 재실행)
   → evidence_crosscheck   [신규] 환각·약한근거 차단   ││
   → debate_critic         [신규] Critic 3종 적대적 반박 ││
   → debate_router         [신규] 실패 항목만 재실행 ───┴┘
       └(통과/최대2R) → final_agent → assemble → END
```
1. **증거 교차검증** — 역량 근거(EID)가 면접 로그·JD에 트레이스되는지 확인 → 환각 차단.
2. **적대적 Critic 3종** — 점수 타당성 / 증거-발언 일치 / 모순·환각 렌즈로 결과를 "반박".
3. **디베이트 라우터** — 실패 분석만 Critic 피드백 주입해 선택적 재실행(최대 2R).

신규 프롬프트: `report_prompt/critic_agent.md`, `crosscheck_agent.md`.

### 적용(중요)
`app/routes/report_routes.py` 의 import 를 **v5 로 교체**:
```python
from app.agents.report_agent_v5 import create_report, create_report_async  # line 20
```
v5 에 v4o 와 동일 시그니처·이벤트 계약(debate/report/done)의 공개 API를 추가했으므로 라우트·프론트 수정 없이 동작. v4o 는 보존(롤백 가능).
> 의존성·DB 가 있어야 런타임 검증 가능. `py_compile` 문법 검증 통과.

---

## 3. 변경 파일
```
app/static/css/ezi-theme.css                   (신규) 청록 디자인 시스템
app/static/design_preview/                     (신규) 리뉴얼 화면 목업+스크린샷
app/templates/admin/tabs/landing.html          (교체) 홈 청록 히어로
app/agents/report_agent_v5.py                  (신규) 적대적 디베이트 그래프 + 공개 API
app/agents/report_prompt/critic_agent.md       (신규)
app/agents/report_prompt/crosscheck_agent.md   (신규)
app/routes/report_routes.py                    (수정) report 생성 → v5 적용
```
