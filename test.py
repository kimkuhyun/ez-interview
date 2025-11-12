"""
interview_store 테스트 스크립트
샘플 면접 로그를 DB에 저장
"""
from app.utils.interview_store import (
    save_interview_logs, 
    get_session_logs,
    retrieve_interview_context,
    search_similar_messages
)

# 샘플 면접 로그
sample_interview_logs = [
    {
        "question_id": "q1",
        "followups": [
            {
                "role": "면접관",
                "content": "안지석님, 간단히 자기소개 부탁드립니다.",
                "offset_sec": 0
            },
            {
                "role": "면접자",
                "content": "안녕하세요. 비전공자로 시작해 뉴로코어에서 약 2년간 개발과 인프라 관련 업무를 담당했습니다. SaaS 전환 과제에서 Docker와 NCP를 활용한 DB 테넌트 자동생성 기능을 개발하며 데이터베이스 엔지니어로 성장하고자 지원했습니다.",
                "offset_sec": 12
            },
            {
                "role": "면접관",
                "content": "비전공자로서 어려웠던 점과 학습 과정이 궁금합니다.",
                "offset_sec": 28
            },
            {
                "role": "면접자",
                "content": "처음엔 Python만 다뤘지만, 실무 중 필요에 따라 Java와 SQL, Docker를 익혔습니다. 업무 중 문제를 직접 해결하며 배운 내용을 문서화해두는 습관을 유지했습니다.",
                "offset_sec": 38
            }
        ]
    },
    {
        "question_id": "q2",
        "followups": [
            {
                "role": "면접관",
                "content": "DB 관련 실무 경험을 구체적으로 말씀해 주세요.",
                "offset_sec": 60
            },
            {
                "role": "면접자",
                "content": "고객사 환경에 맞춰 Oracle, MySQL, MSSQL을 배포하고 설정했습니다. 폐쇄망 환경에서도 서비스가 실행되도록 DB 설정을 조정했고, 백업·복구 절차를 문서화해 데이터 무결성을 확보했습니다.",
                "offset_sec": 68
            },
            {
                "role": "면접관",
                "content": "성능 개선이나 쿼리 튜닝 경험이 있을까요?",
                "offset_sec": 84
            },
            {
                "role": "면접자",
                "content": "Full Scan이 자주 발생하던 쿼리를 인덱스 재설계로 최적화했습니다. 실행 계획을 분석하고 Join 구조를 단순화해 약 40%의 성능 향상을 얻었습니다.",
                "offset_sec": 96
            }
        ]
    },
    {
        "question_id": "q3",
        "followups": [
            {
                "role": "면접관",
                "content": "SaaS 전환 과제에 대해 자세히 말씀해 주시겠어요?",
                "offset_sec": 120
            },
            {
                "role": "면접자",
                "content": "NCP API와 Docker를 활용해 테넌트 생성 자동화를 구현했습니다. DB 스키마, 계정, WAS 컨테이너, DNS까지 일괄 배포되도록 구성했고, 배포 시간을 2분 이내로 단축했습니다.",
                "offset_sec": 128
            },
            {
                "role": "면접관",
                "content": "가장 어려웠던 기술적 문제와 해결 과정은요?",
                "offset_sec": 145
            },
            {
                "role": "면접자",
                "content": "환경별 인증 토큰 이슈가 많아 환경변수 기반 토큰 관리 모듈을 추가했고, 네트워크 차이로 인한 배포 실패를 막기 위해 공통 스크립트를 재작성했습니다.",
                "offset_sec": 158
            }
        ]
    },
    {
        "question_id": "q4",
        "followups": [
            {
                "role": "면접관",
                "content": "DB 엔지니어로서 중요하다고 생각하는 역량은 무엇인가요?",
                "offset_sec": 180
            },
            {
                "role": "면접자",
                "content": "안정성과 복구력이라고 생각합니다. 장애가 발생해도 데이터 손실 없이 복구할 수 있는 체계를 구축하는 게 핵심이라 생각합니다. 백업·리커버리, Undo 관리, 로그 분석을 꾸준히 공부 중입니다.",
                "offset_sec": 192
            },
            {
                "role": "면접관",
                "content": "최근 관심 있는 기술이나 학습 중인 영역은요?",
                "offset_sec": 205
            },
            {
                "role": "면접자",
                "content": "Oracle RAC와 Linux 기반 고가용성 환경 구축을 학습 중입니다. 향후 PostgreSQL과 Oracle을 모두 다룰 수 있는 멀티 DB 엔지니어로 성장하고 싶습니다.",
                "offset_sec": 216
            }
        ]
    },
    {
        "question_id": "q5",
        "followups": [
            {
                "role": "면접관",
                "content": "데이터브릿지에 지원하게 된 이유가 궁금합니다.",
                "offset_sec": 240
            },
            {
                "role": "면접자",
                "content": "귀사가 중소기업 고객을 대상으로 클라우드 DB 관리 솔루션을 제공한다는 점이 인상적이었습니다. 다양한 환경에서의 DB 배포 경험을 활용해 안정적인 운영을 지원하고 싶습니다.",
                "offset_sec": 250
            },
            {
                "role": "면접관",
                "content": "입사 후 어떤 목표를 가지고 계신가요?",
                "offset_sec": 264
            },
            {
                "role": "면접자",
                "content": "단기적으로는 장애 대응 및 복구 자동화를 고도화하고, 장기적으로는 DB 성능 모니터링과 튜닝 자동화 시스템을 개발해보고 싶습니다.",
                "offset_sec": 276
            }
        ]
    }
]


# ==================== 테스트 함수들 ====================

def test_save_and_retrieve():
    """테스트 1: 면접 로그 저장 및 조회"""
    print("\n" + "="*80)
    print("� 테스트 1: 면접 로그 저장 및 조회")
    print("="*80 + "\n")
    
    try:
        # 1. DB에 저장
        session_id = save_interview_logs(sample_interview_logs)
        print(f"\n✅ 저장 성공! Session ID: {session_id}\n")
        
        # 2. 저장된 로그 조회
        print("📖 저장된 로그 조회 중...\n")
        logs = get_session_logs(session_id)
        
        print(f"총 {len(logs)}개 메시지 저장됨:\n")
        for log in logs:
            print(f"  [{log['question_id']}] {log['role']}: {log['message'][:50]}...")
        
        return session_id
        
    except Exception as e:
        print(f"❌ 테스트 실패: {e}\n")
        import traceback
        traceback.print_exc()
        return None


def test_retrieve_context(session_id: str):
    """테스트 2: 리포트용 컨텍스트 추출"""
    print("\n" + "="*80)
    print("📝 테스트 2: 리포트용 인터뷰 컨텍스트 추출")
    print("="*80 + "\n")
    
    try:
        context = retrieve_interview_context(session_id)
        
        if context:
            print("✅ 컨텍스트 추출 성공!\n")
            print("--- 추출된 컨텍스트 (처음 500자) ---")
            print(context[:500])
            print("...\n")
            print(f"전체 길이: {len(context)}자\n")
        else:
            print("❌ 컨텍스트 추출 실패 (빈 문자열 반환)\n")
        
    except Exception as e:
        print(f"❌ 테스트 실패: {e}\n")
        import traceback
        traceback.print_exc()


def test_similarity_search_problem_solving(session_id: str):
    """테스트 3: 유사도 검색 - 문제해결 역량"""
    print("\n" + "="*80)
    print("🔍 테스트 3: 유사도 검색 - 문제해결 역량")
    print("="*80 + "\n")
    
    try:
        print("🎯 검색 쿼리: '문제 해결, 디버깅, 최적화, 성능 개선'\n")
        results = search_similar_messages(
            query_text="문제 해결, 디버깅, 최적화, 성능 개선",
            session_id=session_id,
            role="면접자",
            top_k=3
        )
        
        if results:
            print(f"✅ {len(results)}개 유사 답변 발견:\n")
            for i, result in enumerate(results, 1):
                print(f"{i}. [{result['question_id']}] 유사도: {result['similarity']:.3f}")
                print(f"   답변: {result['msg'][:80]}...")
                print()
        else:
            print("❌ 검색 결과 없음\n")
        
    except Exception as e:
        print(f"❌ 테스트 실패: {e}\n")
        import traceback
        traceback.print_exc()


def test_similarity_search_communication(session_id: str):
    """테스트 4: 유사도 검색 - 협업/커뮤니케이션"""
    print("\n" + "="*80)
    print("🔍 테스트 4: 유사도 검색 - 협업/커뮤니케이션")
    print("="*80 + "\n")
    
    try:
        print("🎯 검색 쿼리: '협업, 소통, 문서화, 공유'\n")
        results = search_similar_messages(
            query_text="협업, 소통, 문서화, 공유",
            session_id=session_id,
            role="면접자",
            top_k=3
        )
        
        if results:
            print(f"✅ {len(results)}개 유사 답변 발견:\n")
            for i, result in enumerate(results, 1):
                print(f"{i}. [{result['question_id']}] 유사도: {result['similarity']:.3f}")
                print(f"   답변: {result['msg'][:80]}...")
                print()
        else:
            print("❌ 검색 결과 없음\n")
        
    except Exception as e:
        print(f"❌ 테스트 실패: {e}\n")
        import traceback
        traceback.print_exc()


# ==================== 메인 실행 ====================

if __name__ == "__main__":
    print("\n🚀 interview_store 테스트 시작\n")
    
    session_id = "interview_1762911165"
    test_retrieve_context(session_id) # 컨텍스트 추출
    test_similarity_search_problem_solving(session_id) # 유사도 검색 - 문제해결
    test_similarity_search_communication(session_id) # 유사도 검색 - 협업
