from flask import Blueprint, request, jsonify, render_template
from app.agents.embedding_agent import EmbeddingAgent
from app.agents.question_agent import QuestionAgent
from app.utils.state import InterviewState
import time

question_bp = Blueprint("question", __name__)

@question_bp.route("/panel/question")
def question_panel():
    return render_template("agents/question.html")

# -------------------------------
# 1️⃣ 문서 임베딩 단계
# -------------------------------
@question_bp.route("/api/interview-embed", methods=["POST"])
def embed_docs():
    print("\n" + "="*80)
    print("📚 [API] /api/interview-embed 호출")
    print("="*80)
    
    start_time = time.perf_counter()

    try:
        # 1️⃣ 파일 읽기
        print("1️⃣ 파일 읽기")
        candidate_name = request.form.get("candidate_name")
        resume_file = request.files.get("resume")
        jd_file = request.files.get("jd")
        portfolio_file = request.files.get("portfolio")  # 선택
        
        if not candidate_name:
            raise ValueError("면접자 이름이 필요합니다")
        
        if not resume_file or not jd_file:
            raise ValueError("resume과 jd 파일이 필요합니다")
        
        print(f"   ✅ 면접자 이름: {candidate_name}")
        print(f"   ✅ resume 파일: {resume_file.filename}")
        print(f"   ✅ jd 파일: {jd_file.filename}")
        if portfolio_file:
            print(f"   ✅ portfolio 파일: {portfolio_file.filename}")
        else:
            print(f"   ℹ️  portfolio: 없음")

        # 2️⃣ EmbeddingAgent 실행
        print("\n2️⃣ EmbeddingAgent 실행")
        agent = EmbeddingAgent()
        embed_result = agent.run(resume_file, jd_file, portfolio_file)
        
        print(f"   ✅ 임베딩 완료")
        print(f"      - session_id: {embed_result['session_id']}")
        print(f"      - resume_id: {embed_result['resume_id']}")
        print(f"      - jd_id: {embed_result['jd_id']}")
        print(f"      - portfolio_id: {embed_result.get('portfolio_id')}")
        print(f"      - resume_len: {embed_result['resume_len']} 자")
        print(f"      - jd_len: {embed_result['jd_len']} 자")
        print(f"      - portfolio_len: {embed_result.get('portfolio_len', 0)} 자")

        # 3️⃣ State 객체 생성
        print("\n3️⃣ State 객체 생성")
        state = InterviewState(
            session_id=embed_result["session_id"],
            candidate_name=candidate_name,
            resume_id=embed_result["resume_id"],
            jd_id=embed_result["jd_id"],
            portfolio_id=embed_result.get("portfolio_id"),
            resume_len=embed_result["resume_len"],
            jd_len=embed_result["jd_len"],
            portfolio_len=embed_result.get("portfolio_len", 0),
            resume_text=embed_result.get("resume_text"),
            jd_text=embed_result.get("jd_text"),
            portfolio_text=embed_result.get("portfolio_text")
        )
        print(f"   ✅ State 객체 생성 완료")
        
        # 4️⃣ GLOBAL_STATE에도 저장
        print("\n4️⃣ GLOBAL_STATE 업데이트")
        from app.routes.state_routes import GLOBAL_STATE
        GLOBAL_STATE.session_id = state.session_id
        GLOBAL_STATE.candidate_name = state.candidate_name
        GLOBAL_STATE.resume_id = state.resume_id
        GLOBAL_STATE.jd_id = state.jd_id
        GLOBAL_STATE.portfolio_id = state.portfolio_id
        GLOBAL_STATE.resume_len = state.resume_len
        GLOBAL_STATE.jd_len = state.jd_len
        GLOBAL_STATE.portfolio_len = state.portfolio_len
        GLOBAL_STATE.resume_text = state.resume_text
        GLOBAL_STATE.jd_text = state.jd_text
        GLOBAL_STATE.portfolio_text = state.portfolio_text
        print(f"   ✅ GLOBAL_STATE 업데이트 완료")

        end_time = time.perf_counter()
        print(f"\n✅ [API] /api/interview-embed 완료 ({end_time - start_time:.2f} sec)")
        print("="*80 + "\n")

        return jsonify({
            "status": "ok",
            "message": "임베딩 완료",
            "state": state.model_dump(),
        })
        
    except Exception as e:
        print(f"\n❌ [API] 에러 발생: {e}")
        import traceback
        traceback.print_exc()
        
        end_time = time.perf_counter()
        print(f"\n❌ [API] /api/interview-embed 실패 ({end_time - start_time:.2f} sec)")
        print("="*80 + "\n")
        
        return jsonify({
            "status": "error",
            "error": str(e),
        }), 500

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
        session_id = data.get("session_id", None)
        
        # session_id가 None이면 GLOBAL_STATE에서 가져오기
        if session_id is None:
            session_id = GLOBAL_STATE.session_id
            print(f"   ℹ️  session_id가 요청에 없음 → GLOBAL_STATE에서 조회")
        
        print(f"   ✅ query_text: {query_text[:100]}...")
        print(f"   ✅ num_questions: {num_questions}")
        print(f"   ✅ num_metrics: {num_metrics}")
        print(f"   ✅ session_id: {session_id}")
        
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
            session_id=session_id
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
        print(f"   - resume_id: {GLOBAL_STATE.resume_id}")
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
