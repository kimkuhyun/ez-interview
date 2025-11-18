from flask import Blueprint, request, jsonify
from app.utils.state import InterviewState

state_bp = Blueprint("state", __name__)

# 전역 state (간단 버전)
GLOBAL_STATE = InterviewState()

@state_bp.route("/api/state", methods=["POST"])
def update_state():
    """
    State 객체 업데이트
    
    지원하는 필드:
    - questions: List[str] - 면접 질문 (UI에서 수정된 질문들)
    - selected_metrics: List[str] - 사용자가 선택한 평가지표
    - all_metrics: List[str] - 전체 평가지표 (참고용)
    - resume_id, jd_id: 문서 임베딩 단계에서
    """
    try:
        print("\n📍 [API] /api/state POST 호출")
        print(f"   - Content-Type: {request.content_type}")
        print(f"   - Request body size: {len(request.data)} bytes")
        
        # JSON 파싱 시도 (force=True로 Content-Type 무시)
        try:
            data = request.get_json(force=True, silent=False)
        except Exception as json_error:
            print(f"   ❌ JSON 파싱 에러: {json_error}")
            return jsonify({
                "status": "error",
                "message": f"JSON 파싱 실패: {str(json_error)}"
            }), 400
        
        if not data:
            print(f"   ⚠️  데이터가 비어있음")
            return jsonify({
                "status": "error",
                "message": "요청 데이터가 없습니다"
            }), 400
        
        print("\n📍 [API] /api/state 호출")
        print(f"   - 수신 데이터 키: {data.keys()}")
        
        # 1. 질문 저장 (UI에서 수정된 질문들)
        if "questions" in data:
            questions_data = data.get("questions", [])
            GLOBAL_STATE.questions = questions_data
            print(f"   ✅ 질문 저장: {len(GLOBAL_STATE.questions)}개")
            for idx, q in enumerate(GLOBAL_STATE.questions, 1):
                print(f"      [{idx}] {q[:60]}{'...' if len(q) > 60 else ''}")
        
        # 1-1. session_id 업데이트 (면접 진행 시 전달)
        if "session_id" in data:
            GLOBAL_STATE.session_id = data.get("session_id")
            print(f"   ✅ session_id 업데이트: {GLOBAL_STATE.session_id}")
        
        # 2. 선택된 평가지표 저장 (최종 선택 지표: 5개)
        if "selected_metrics" in data:
            selected_metrics_data = data.get("selected_metrics", [])
            
            # 5개 검증
            if len(selected_metrics_data) != 5:
                print(f"   ⚠️  선택된 평가지표 개수 검증 실패: {len(selected_metrics_data)}개 (필요: 5개)")
                return jsonify({
                    "status": "error",
                    "message": f"평가지표는 정확히 5개를 선택해야 합니다 (현재: {len(selected_metrics_data)}개)"
                }), 400
            
            # metrics 필드에 저장
            GLOBAL_STATE.metrics = selected_metrics_data
            print(f"   ✅ 선택된 평가지표 저장: {len(GLOBAL_STATE.metrics)}개")
            for idx, m in enumerate(GLOBAL_STATE.metrics, 1):
                print(f"      [{idx}] {m}")
        
        # 3. 전체 평가지표 저장 (참고용, 선택된 지표가 없을 때만)
        if "all_metrics" in data and not data.get("selected_metrics"):
            all_metrics = data.get("all_metrics", [])
            print(f"   ℹ️  전체 평가지표 저장 (선택된 지표 없음): {len(all_metrics)}개")
            if not GLOBAL_STATE.metrics:
                GLOBAL_STATE.metrics = all_metrics
        
        # 4. 문서 임베딩 정보 저장
        if "resume_id" in data:
            GLOBAL_STATE.resume_id = data.get("resume_id")
            print(f"   ✅ resume_id: {GLOBAL_STATE.resume_id}")
        
        if "jd_id" in data:
            GLOBAL_STATE.jd_id = data.get("jd_id")
            print(f"   ✅ jd_id: {GLOBAL_STATE.jd_id}")
        
        if "resume_len" in data:
            GLOBAL_STATE.resume_len = data.get("resume_len")
            print(f"   ✅ resume_len: {GLOBAL_STATE.resume_len}")
        
        if "jd_len" in data:
            GLOBAL_STATE.jd_len = data.get("jd_len")
            print(f"   ✅ jd_len: {GLOBAL_STATE.jd_len}")
        
        print(f"\n   📊 최종 State:")
        print(f"      - session_id: {GLOBAL_STATE.session_id}")
        print(f"      - questions: {len(GLOBAL_STATE.questions or [])}개")
        print(f"      - metrics: {len(GLOBAL_STATE.metrics or [])}개")
        print(f"      - resume_id: {GLOBAL_STATE.resume_id}")
        print(f"      - jd_id: {GLOBAL_STATE.jd_id}\n")
        
        return jsonify({
            "status": "ok",
            "state": GLOBAL_STATE.model_dump(),
            "message": "State 업데이트 완료"
        })
    
    except ValueError as ve:
        print(f"\n❌ [API] /api/state ValueError: {ve}")
        import traceback
        traceback.print_exc()
        
        return jsonify({
            "status": "error",
            "message": f"값 검증 실패: {str(ve)}"
        }), 400
    
    except TypeError as te:
        print(f"\n❌ [API] /api/state TypeError: {te}")
        import traceback
        traceback.print_exc()
        
        return jsonify({
            "status": "error",
            "message": f"타입 에러: {str(te)}"
        }), 400
    
    except Exception as e:
        print(f"\n❌ [API] /api/state 예기치 않은 에러: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        
        return jsonify({
            "status": "error",
            "message": f"State 업데이트 실패: {str(e)}"
        }), 500

@state_bp.route("/api/state", methods=["GET"])
def get_state():
    """
    현재 State 조회
    """
    print("\n📍 [API] /api/state (GET) 호출")
    state_data = GLOBAL_STATE.model_dump()
    print(f"   - 현재 State: {state_data}\n")
    
    # State 필드들을 직접 반환 (프론트에서 최상위 레벨로 접근 가능)
    return jsonify(state_data)
