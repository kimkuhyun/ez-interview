from flask import Blueprint, request, jsonify, render_template
from app.agents.question_agent import QuestionAgent
from app.utils.state import InterviewState
import time

question_bp = Blueprint("question", __name__)

@question_bp.route("/panel/question")
def question_page():
    return render_template("agents/question.html")

@question_bp.route("/panel/interview")
def interview_page():
    """면접 페이지 - URL 파라미터를 GLOBAL_STATE에 저장"""
    from app.routes.state_routes import GLOBAL_STATE
    
    # URL 파라미터 가져오기
    session_id = request.args.get('session_id')
    name = request.args.get('name')
    jd_id = request.args.get('jd_id')
    position = request.args.get('position')
    
    print("\n" + "="*80)
    print("🎯 [INTERVIEW PAGE] 면접 페이지 로드")
    print("="*80)
    print(f"📥 URL 파라미터:")
    print(f"   - session_id: {session_id}")
    print(f"   - name: {name}")
    print(f"   - jd_id: {jd_id}")
    print(f"   - position: {position}")
    
    # GLOBAL_STATE에 저장
    if session_id:
        GLOBAL_STATE.session_id = session_id
        print(f"   ✅ GLOBAL_STATE.session_id = {session_id}")
    
    if name:
        GLOBAL_STATE.candidate_name = name
        print(f"   ✅ GLOBAL_STATE.candidate_name = {name}")
    
    if jd_id:
        GLOBAL_STATE.jd_id = jd_id
        print(f"   ✅ GLOBAL_STATE.jd_id = {jd_id}")
    
    if position:
        GLOBAL_STATE.position = position
        print(f"   ✅ GLOBAL_STATE.position = {position}")
    
    print("="*80 + "\n")
    
    return render_template("interview.html")



@question_bp.route("/api/interview-question", methods=["POST"])
def generate_questions():
    print("\n" + "="*80)
    print("📋 [API] /api/interview-question 호출")
    print("="*80)
    
    start_time = time.perf_counter()
    
    try:
        # 1️⃣ 요청 데이터 파싱
        print("1️⃣ 요청 데이터 파싱")
        data = request.get_json()
        print(f"   - 전체 요청: {data}")
        
        from app.routes.state_routes import GLOBAL_STATE
        
        query_text = data.get("query_text", "지원자 이력서 및 JD를 기반으로 면접 질문을 생성해라.")
        num_questions = data.get("num_questions", 10)
        num_metrics = data.get("num_metrics", 10)
        
        # session_id, jd_id, position은 GLOBAL_STATE에서만 가져오기 (새로 생성하지 않음)
        session_id = GLOBAL_STATE.session_id
        jd_id = GLOBAL_STATE.jd_id
        position = GLOBAL_STATE.position
        
        print(f"   ✅ query_text: {query_text[:100]}...")
        print(f"   ✅ num_questions: {num_questions}")
        print(f"   ✅ num_metrics: {num_metrics}")
        print(f"   ✅ session_id (GLOBAL_STATE): {session_id}")
        print(f"   ✅ jd_id (GLOBAL_STATE): {jd_id}")
        print(f"   ✅ position (GLOBAL_STATE): {position}")
        
        # 2️⃣ QuestionAgent 초기화 및 실행
        print("\n2️⃣ QuestionAgent 초기화")
        step2_start = time.perf_counter()
        agent = QuestionAgent()
        step2_elapsed = time.perf_counter() - step2_start
        print(f"   ✅ Agent 초기화 완료 ({step2_elapsed:.2f}초)")
        
        print("\n3️⃣ Agent.generate_questions_and_metrics() 호출 시작")
        step3_start = time.perf_counter()
        result = agent.generate_questions_and_metrics(
            query_text=query_text, 
            num_questions=num_questions,
            num_metrics=num_metrics,
            session_id=session_id,
            jd_id=jd_id
        )
        step3_elapsed = time.perf_counter() - step3_start
        print(f"   ✅ Agent 실행 완료 ({step3_elapsed:.2f}초)")
        
        # 3️⃣ 결과 검증
        print("\n4️⃣ 결과 검증")
        print(f"   - result 타입: {type(result)}")
        print(f"   - result: {result}")
        
        if hasattr(result, 'questions'):
            questions = result.questions
            print(f"   ✅ questions 속성 존재: {len(questions)}개")
            if questions:
                for idx, q in enumerate(questions[:2]):
                    print(f"      [{idx+1}] {q[:80]}...")
        else:
            print(f"   ❌ questions 속성 없음!")
            questions = []
        
        if hasattr(result, 'metrics'):
            metrics = result.metrics
            print(f"   ✅ metrics 속성 존재: {len(metrics)}개")
            if metrics:
                for idx, m in enumerate(metrics[:3]):
                    print(f"      [{idx+1}] {m}")
        else:
            print(f"   ❌ metrics 속성 없음!")
            metrics = []
        
        # 4️⃣ State에 저장
        print("\n5️⃣ State에 질문 및 평가지표 저장")
        
        # 고정 질문 추가 (맨 앞과 맨 뒤)
        FIXED_FIRST = "자신에 대해 간단히 소개해 주세요."
        FIXED_LAST = "마지막으로 저희 회사에 하고 싶은 말씀이 있으신가요?"
        
        questions_with_fixed = [FIXED_FIRST] + questions + [FIXED_LAST]
        
        GLOBAL_STATE.questions = questions_with_fixed
        GLOBAL_STATE.metrics = metrics
        
        print(f"   ✅ State 업데이트 완료 (고정 질문 2개 추가)")
        print(f"      - questions: {len(questions_with_fixed)}개 (고정 2개 + 생성 {len(questions)}개)")
        print(f"      - metrics: {len(metrics)}개")
        
        # 5️⃣ 응답 생성
        print("\n6️⃣ 응답 생성")
        response_data = {
            "status": "ok",
            "questions": questions_with_fixed,
            "metrics": metrics,
        }
        print(f"   - 응답 객체 생성 완료: {len(str(response_data))} 바이트")
        
        end_time = time.perf_counter()
        total_elapsed = end_time - start_time
        print(f"\n✅ [API] /api/interview-question 완료")
        print(f"   - 총 시간: {total_elapsed:.2f}초")
        print(f"   - 초기화: {step2_elapsed:.2f}초")
        print(f"   - 처리: {step3_elapsed:.2f}초")
        
        # 📊 현재 State 출력
        print(f"\n📊 [현재 State 상태]")
        print(f"   - session_id: {GLOBAL_STATE.session_id}")
        print(f"   - jd_id: {GLOBAL_STATE.jd_id}")
        print(f"   - questions: {len(GLOBAL_STATE.questions or [])}개")
        print(f"   - metrics: {len(GLOBAL_STATE.metrics or [])}개")
        print(f"   - resume_len: {GLOBAL_STATE.resume_len}")
        print(f"   - jd_len: {GLOBAL_STATE.jd_len}")
        print("="*80 + "\n")
        
        return jsonify(response_data)
        
    except Exception as e:
        print(f"\n❌ [API] 에러 발생: {e}")
        import traceback
        traceback.print_exc()
        
        end_time = time.perf_counter()
        print(f"\n❌ [API] /api/interview-question 실패 ({end_time - start_time:.2f} sec)")
        print("="*80 + "\n")
        
        return jsonify({
            "status": "error",
            "error": str(e),
            "questions": [],
            "metrics": [],
        }), 500

# -------------------------------
# 개별 질문 재생성 API (2번 기능)
# -------------------------------
@question_bp.route("/api/interview-question-single", methods=["POST"])
def generate_single_question():
    print("\n" + "="*80)
    print("🔄 [API] /api/interview-question-single 호출 (개별 질문 재생성)")
    print("="*80)
    
    start_time = time.perf_counter()
    
    try:
        data = request.get_json()
        session_id = data.get("session_id")
        existing_questions = data.get("existing_questions", [])
        
        print(f"   ✅ session_id: {session_id}")
        print(f"   ✅ 기존 질문 수: {len(existing_questions)}개")
        
        from app.routes.state_routes import GLOBAL_STATE
        
        if not session_id:
            session_id = GLOBAL_STATE.session_id
        
        # QuestionAgent로 1개 질문 생성 (기존 질문 제외 명시)
        agent = QuestionAgent()
        result = agent.generate_questions_and_metrics(
            query_text="기존 질문과 겹치지 않는 새로운 면접 질문을 1개만 생성해라.",
            num_questions=1,
            num_metrics=0,
            session_id=session_id,
            existing_questions=existing_questions  # 기존 질문 제외
        )
        
        if not hasattr(result, 'questions') or not result.questions:
            raise ValueError("질문 생성 실패")
        
        new_question = result.questions[0]
        
        # 기존 질문과 중복 체크 (간단한 텍스트 비교)
        attempts = 0
        while new_question in existing_questions and attempts < 3:
            print(f"   ⚠️  중복 질문 감지, 재생성 시도 {attempts + 1}/3")
            result = agent.generate_questions_and_metrics(
                query_text="완전히 새로운 면접 질문을 1개만 생성해라.",
                num_questions=1,
                num_metrics=0,
                session_id=session_id
            )
            if hasattr(result, 'questions') and result.questions:
                new_question = result.questions[0]
            attempts += 1
        
        end_time = time.perf_counter()
        print(f"✅ [API] /api/interview-question-single 완료 ({end_time - start_time:.2f}초)")
        print(f"   - 생성된 질문: {new_question[:80]}...")
        print("="*80 + "\n")
        
        return jsonify({
            "status": "ok",
            "question": new_question
        })
        
    except Exception as e:
        print(f"❌ [API] 에러 발생: {e}")
        import traceback
        traceback.print_exc()
        
        end_time = time.perf_counter()
        print(f"❌ [API] /api/interview-question-single 실패 ({end_time - start_time:.2f}초)")
        print("="*80 + "\n")
        
        return jsonify({
            "status": "error",
            "error": str(e)
        }), 500
