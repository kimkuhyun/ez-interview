# 디베이트 피드백 루프 패턴 구현 완료 (v3.2)

## 핵심 기능

### 1. 디베이트 검증 후 조건부 라우팅

```
analysis (초기 실행: 4개 에이전트)
    ↓
debate_validation (3개 에이전트 검증)
    ↓
debate_router (라우팅 결정)
    ├─ 품질 통과 → final_recommendation
    └─ 품질 부족 → analysis (선택적 재실행: 실패한 에이전트만)
            ↓
        debate_validation (재검증)
```

### 2. 선택적 재실행 (단일 노드 방식)

**analysis_parallel_node가 조건부로 동작:**
- **retry_targets 없음 (초기 실행)**: 모든 4개 에이전트 실행
- **retry_targets 있음 (재실행)**: 실패한 에이전트만 실행

**재실행 대상:**
- 인터뷰 분석 재실행 (피드백 반영)
- 역량 평가 재실행 (피드백 반영)
- 증거 매핑 재실행 (피드백 반영)

**인터뷰 요약은 재실행 안 함** (통계 데이터이므로)

---

## 워크플로우 (단일 노드 재사용)

```
┌─────────────────────┐
│ Prompt Optimizer    │
└──────────┬──────────┘
           │
┌──────────▼──────────┐
│ Query Planner       │
└──────────┬──────────┘
           │
┌──────────▼──────────┐
│ Retriever           │
└──────────┬──────────┘
           │
┌──────────▼──────────┐
│ Analysis Parallel   │ ◄──┐
│ (조건부 실행)        │    │
│  - 초기: 4개 병렬    │    │
│  - 재실행: 선택적    │    │
└──────────┬──────────┘    │
           │               │
┌──────────▼──────────┐    │
│ Debate Validation
│ 3개 검증 병렬 실행    
└──────────┬──────────┘    │
           │               │
┌──────────▼──────────┐    │
│ Debate Router       │    │ 조건부 라우팅
└──┬──────────────┬───┘    │
   │              │        │
   │              │        │ 
   └──────────────┘        │
    통과  │                │
          │────────────────┘ 품질 부족 재실행 (최대 2회)
          │                  retry_targets + retry_feedbacks 설정
          │
┌─────────▼──────────┐
│Final Recommendation│
└─────────┬──────────┘
          │
┌─────────▼──────────┐
│ Validation         │
└─────────┬──────────┘
          │
         END
```

---

##  구현 세부사항

### 1. State 확장

```python
class ReportState(TypedDict):
    # 기존 필드...

    # 디베이트 재시도 관련 (추가)
    debate_retry_count: int  # 재시도 횟수 (0, 1, 2)
    retry_targets: Optional[List[str]]  # ["interview_analysis", "competency_eval", "evidence_mapping"]
    retry_feedbacks: Optional[Dict[str, List[str]]]  # {"competency_eval": ["점수 차별화 필요", ...]}
```

### 2. 피드백 기반 재실행 함수

```python
async def analyze_interview(
    log_ctx: str,
    portfolio_ctx: str,
    interview_query: str,
    feedback: Optional[List[str]] = None  # 추가
) -> InterviewAnalysisOut:
    # 피드백이 있으면 프롬프트에 추가
    if feedback:
        feedback_text = "\n\n**이전 시도 피드백:**\n" + "\n".join(f"- {fb}" for fb in feedback)
        interview_query = interview_query + feedback_text
    # ...
```

### 3. 디베이트 라우터

```python
def debate_router_node(state: ReportState) -> str:
    """검증 후 라우팅"""

    # 1. 최대 재시도 확인 (2회)
    if state.get("debate_retry_count", 0) >= 2:
        return "final_recommendation"

    # 2. 실패한 항목 필터링
    failed = [v for v in validation_results if not v["is_valid"]]

    if not failed:
        return "final_recommendation"

    # 3. 재실행 대상 및 피드백 수집
    retry_targets = []
    retry_feedbacks = {}

    for validation in failed:
        target = topic_to_target[validation["topic"]]
        retry_targets.append(target)
        retry_feedbacks[target] = validation["suggestions"]

    # 4. State 업데이트
    state["retry_targets"] = retry_targets
    state["retry_feedbacks"] = retry_feedbacks
    state["debate_retry_count"] += 1

    return "analysis"
```

### 4. 단일 노드 방식 (조건부 실행)

```python
def analysis_parallel_node(state: ReportState) -> ReportState:
    """초기 실행 + 선택적 재실행 통합"""

    # 재실행 여부 확인
    retry_targets = state.get("retry_targets") or []
    is_retry = len(retry_targets) > 0
    retry_feedbacks = state.get("retry_feedbacks") or {}

    tasks = []
    task_names = []

    # 인터뷰 분석: 초기 실행 OR retry_targets에 포함
    if not is_retry or "interview_analysis" in retry_targets:
        feedback = retry_feedbacks.get("interview_analysis")
        task = analyze_interview(log_ctx, portfolio_ctx, interview_query, feedback)
        tasks.append(task)
        task_names.append("interview_analysis")

    # 역량 평가: 초기 실행 OR retry_targets에 포함
    if not is_retry or "competency_eval" in retry_targets:
        feedback = retry_feedbacks.get("competency_eval")
        task = evaluate_competency(resume_ctx, portfolio_ctx, log_ctx, axes_keys, lens_perspective, feedback)
        tasks.append(task)
        task_names.append("competency_eval")

    # 증거 매핑: 초기 실행 OR retry_targets에 포함
    if not is_retry or "evidence_mapping" in retry_targets:
        feedback = retry_feedbacks.get("evidence_mapping")
        task = map_competency_evidence(axes_keys, resume_ctx, portfolio_ctx, log_ctx, eid_list, feedback)
        tasks.append(task)
        task_names.append("evidence_mapping")

    # 인터뷰 요약: 초기 실행만
    if not is_retry:
        task = summarize_interview(log_ctx, qa_pairs)
        tasks.append(task)
        task_names.append("interview_summary")

    # 병렬 실행
    results = await asyncio.gather(*tasks)

    # State 업데이트
    for name, result in zip(task_names, results):
        state[name] = result.model_dump()
```

---

## 실행 시나리오

### 시나리오 1: 첫 실행에서 통과

```
1. analysis_parallel 실행
   → 역량 평가: 30, 55, 75, 85, 90 (차별화 명확)

2. debate_validation 실행
   → 모두 is_valid=True

3. debate_router
   → "final_recommendation" 경로

4. 최종 리포트 생성
```

### 시나리오 2: 역량 평가만 재실행 (1회)

```
1. analysis (초기 실행, retry_targets=None)
   → 모든 4개 에이전트 실행
   → 역량 평가: 60, 62, 65, 68, 70 (차별화 부족)

2. debate_validation 실행
   → 역량 평가 검증: is_valid=False
   → suggestions: ["역량 간 최소 20점 편차 필요", "근거 부족 시 과감하게 낮은 점수"]

3. debate_router
   → retry_targets = ["competency_eval"]
   → retry_feedbacks = {"competency_eval": ["역량 간 최소 20점 편차 필요", ...]}
   → "analysis" 경로 (재실행)

4. analysis (재실행, retry_targets=["competency_eval"])
   → competency_eval만 실행 (피드백 포함)
   → 결과: 30, 50, 75, 85, 90 (개선됨)
   → interview_summary는 실행 안 함 (기존 값 유지)

5. debate_validation 재실행
   → is_valid=True

6. debate_router
   → "final_recommendation" 경로
```

### 시나리오 3: 2회 재시도 후 강제 통과

```
1. 첫 실행 → 실패 (debate_retry_count=0)
2. 재실행 1회 → 실패 (debate_retry_count=1)
3. 재실행 2회 → 실패 (debate_retry_count=2)
4. debate_router
   → 최대 재시도 도달 → "final_recommendation" (강제 통과)
```

---

##  제어 파라미터

### 코드 내 설정

```python
# debate_router_node
if debate_retry_count >= 2:  # 최대 2회
    return "final_recommendation"
```

---

##  에이전트 로그 예시

```
[디베이트 검증 에이전트] 분석 결과 디베이트 검증 시작...
[디베이트 검증 - competency_eval] ⚠️ 개선 필요 (품질: 0.65)
   이슈: 2개, 제안: 3개
[디베이트 검증 에이전트] ⚠️ 일부 개선 필요
   평균 품질: 0.75
   검증 항목: 3개
[디베이트 라우터] ⚠️ 품질 개선 필요 → 재실행 (1/2)
   대상: competency_eval

[재실행 에이전트] 품질 개선을 위한 선택적 재실행 시작: competency_eval
[역량 평가 재실행] 피드백 반영 중: 역량 간 최소 20점 편차 필요, 근거 부족 시 과감하게 낮은 점수...
[역량 평가 재실행] ✅ 재실행 완료 - 문제해결능력: 30점, 커뮤니케이션: 50점, ...
[재실행 에이전트] ✅ 선택적 재실행 완료 (1개 항목)

[디베이트 검증 에이전트] 분석 결과 디베이트 검증 시작... (2차)
[디베이트 검증 - competency_eval] ✅ 통과 (품질: 0.82)
[디베이트 라우터] ✅ 모든 검증 통과 → 최종 권고 생성으로 진행
```

---

##  검증 기준

### 인터뷰 분석 검증

- 모순도/깊이/신뢰도 점수가 로그 내용과 일치하는지
- positive/negative aspects가 구체적이고 균형있는지
- final_comment가 근거 기반인지

### 역량 평가 검증

- 점수가 과도하게 높거나 낮지 않은지
- 근거 부족 시 낮은 점수를 주었는지 (10점 이하도 가능)
- 가중치 합계가 100인지
- 역량 간 차별화가 명확한지 (**최소 20점 편차**)

### 증거 매핑 검증

- 모든 역량이 최소 1개 이상의 EID를 가지는지
- 증거가 해당 역량에 실제로 관련있는지 (**단순 키워드 매칭 금지**)
- 충족도 평가가 증거 강도와 일치하는지

---

## 활용 효과

### Before (v2)
```
analysis → debate_validation → final_recommendation
           (검증만 함, 재실행 없음)
```
- 품질 부족해도 그냥 통과
- 검증 로그만 기록

### After (v3.2) - 단일 노드 재사용 방식
```
analysis → debate_validation → debate_router
   ▲                              ├─ 통과 → final_recommendation
   │                              └─ 실패 ──┘ (analysis로 돌아감)
   └────────────────────────────────┘
```
- 품질 부족하면 재실행
- 피드백 반영으로 개선
- 최대 2회 재시도
- **코드 중복 제거** (단일 노드로 통합)

---

## 사용법

```python
from app.agents.report_agent_v3 import create_report_async

async for event in create_report_async(
    session_id="test_session",
    candidate_name="홍길동",
    axes_keys=["문제해결능력", "커뮤니케이션", "학습능력", "협업능력", "전문성"],
    user_prompt="역량 점수 차별화를 명확하게 해주세요",
    has_portfolio=True,
):
    if event["type"] == "log":
        # 일반 로그 (재실행 로그 포함)
        print(f"[{event['agent']}] {event['message']}")

        # 재실행 관련 로그 예시:
        # [디베이트 라우터] ⚠️ 품질 개선 필요 → 재실행 (1/2)
        # [재실행 에이전트] 품질 개선을 위한 선택적 재실행 시작: competency_eval
        # [역량 평가 재실행] ✅ 재실행 완료 - ...
```

---

## 완료 항목

- [x] State에 재시도 관련 필드 추가 (`debate_retry_count`, `retry_targets`, `retry_feedbacks`)
- [x] 분석 함수에 피드백 파라미터 추가 (`analyze_interview`, `evaluate_competency`, `map_competency_evidence`)
- [x] 디베이트 라우터 노드 구현 (`debate_router_node`)
- [x] ~~선택적 재실행 노드 구현 (`retry_analysis_node`)~~ → **삭제됨 (v3.2)**
- [x] **analysis_parallel_node에 선택적 재실행 기능 통합** (v3.2)
- [x] LangGraph 워크플로우에 재실행 루프 추가 (`analysis` → `debate_validation` → `debate_router` → `analysis`)
- [x] 에이전트 로그 시스템에 재실행 로그 통합
- [x] 문법 검증 완료
- [x] **코드 중복 제거 및 단일 노드 재사용 방식으로 리팩토링** (v3.2)

---

## 참고

- 재실행은 **병렬로** 수행 (속도 최적화)
- 피드백은 **프롬프트에 직접 추가** (LLM이 자연어로 이해)
- 최대 2회 재시도 후 **강제 통과** (무한 루프 방지)
- 인터뷰 요약은 **재실행 안 함** (통계 데이터)
- **단일 노드 재사용** (`analysis_parallel_node`가 초기 실행과 재실행 모두 처리)
- **코드 중복 제거** (`retry_analysis_node` 삭제)

---

작성일: 2025-11-15
버전: 3.2.0 (단일 노드 재사용 방식)
