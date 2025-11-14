import os
import time
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.runnables import RunnableLambda
from app.utils.rag_retriever import search_similar_chunks
from app.utils.state import InterviewState


class QuestionAgent:
    """
    이 Agent는 RAG DB에서 문서 내용을 검색해 질문 및 평가 지표를 생성하는 역할을 수행
    (LangGraph에서 Runnable 형태로 바로 연결 가능)
    """

    def __init__(self):
        api_key = os.getenv("OPENAI_API_KEY")

        # LLM 초기화
        self.llm = ChatOpenAI(
            # model="gpt-4o-mini",
            model="gpt-3.5-turbo",
            temperature=0.5,
            openai_api_key=api_key,
        )

        # JSON 파서 (출력 구조를 명시)
        self.parser = JsonOutputParser()

        # 프롬프트 템플릿 정의
        self.prompt = ChatPromptTemplate.from_template(
            "너는 대면 기술면접을 위한 질문과 평가 기준을 생성하는 AI야.\n\n"
            "[필수] 출력 형식 - 반드시 이 정확한 구조로 JSON을 생성하라:\n"
            "{{\n"
            '  "interview_questions": {{\n'
            '    "technical_questions": [\n'
            '      {{"question": "질문내용", "evaluation_criteria": "평가기준"}},\n'
            '      ...(총 5개)\n'
            '    ],\n'
            '    "personality_questions": [\n'
            '      {{"question": "질문내용", "evaluation_criteria": "평가기준"}},\n'
            '      ...(총 3개)\n'
            '    ]\n'
            '  }},\n'
            '  "evaluation_criteria": [\n'
            '    {{"criteria": "평가지표명", "description": "설명"}},\n'
            '    ...(총 10개)\n'
            '  ]\n'
            "}}\n\n"
            "=============================================================\n"
            "[지원자 이력서 내용]\n{resume_context}\n\n"
            "[회사 채용공고(JD) 내용]\n{jd_context}\n"
            "=============================================================\n\n"
            "[면접 질문 생성 가이드]\n"
            "면접의 핵심 목적은 '검증'이다:\n"
            "1. 이력서 진실성 검증: 지원자가 이력서에 작성한 프로젝트, 기술스택, 성과가 진짜인지 확인\n"
            "   - 예: '○○ 프로젝트에서 어떤 기술적 어려움이 있었나요?'\n"
            "   - 예: '△△ 기술을 사용하셨는데, 내부 동작 원리를 설명해주실 수 있나요?'\n\n"
            "2. 기술 이해도 검증: 단순히 사용만 한 게 아니라 제대로 이해하고 있는지 확인\n"
            "   - 이력서의 구체적인 키워드(기술명, 프로젝트명 등)를 직접 언급하며 질문\n"
            "   - '왜 그 기술을 선택했는지', '어떤 trade-off를 고려했는지' 등 깊이 있는 질문\n\n"
            "3. 회사 기술환경 연결: JD의 기술스택을 자연스럽게 물어봄\n"
            "   - 예: '○○ 기술은 사용해보셨나요?'\n"
            "   - 예: '△△ 환경에서 개발해보신 경험이 있나요?'\n"
            "   - 🚫 금지: 'JD에 언급된', 'JD에서 봤는데', '채용공고에' 같은 메타 표현 사용 금지\n\n"
            "💡 질문 생성 시 반드시:\n"
            "   - 이력서의 '구체적인 내용'을 직접 언급 (프로젝트명, 기술명, 회사명, 활동명 등)\n"
            "   - 'Yes/No'로 답할 수 없는 개방형 질문으로 작성\n"
            "   - 단순 암기가 아닌 '이해도'와 '경험의 진실성'을 검증할 수 있는 질문\n"
            "   - 면접관이 이미 이력서와 JD를 읽었다는 전제로 자연스럽게 질문\n\n"
            "[기술 질문 5개 작성 기준]\n"
            "- 주 참조: 이력서 (80%) + JD (20%)\n"
            "- 이력서에 작성된 프로젝트/기술/성과의 진실성과 깊이를 검증\n"
            "- JD 기술스택을 자연스럽게 물어보는 질문 1~2개 포함 (메타 언급 없이)\n\n"
            "[인성 질문 3개 작성 기준]\n"
            "- 이력서의 '구체적인 경험/활동'을 반드시 언급하며 질문\n"
            "- 팀워크/의사소통 1개:\n"
            "  * 예: 이력서에 '버스킹 경험' → '버스킹 활동 하시는 걸 보면 성격이 내성적이지는 않을 것 같은데, 그런 부분이 다른 사람과 소통할 때 도움이 됐나요?'\n"
            "  * 예: '○○ 동아리 활동' → '동아리에서 의견 충돌이 있을 때 어떻게 해결하셨나요?'\n"
            "  * 🚫 금지: '협업 경험 중 기억에 남는 게 있나요?' 같은 나이브한 질문\n\n"
            "- 책임감/주도성 1개:\n"
            "  * 이력서의 '리더십/주도적 경험'을 구체적으로 언급\n"
            "  * 예: '○○ 프로젝트에서 팀장을 맡으셨는데, 팀원들의 동기부여는 어떻게 하셨나요?'\n\n"
            "- 적응력/학습태도 1개:\n"
            "  * 이력서의 '새로운 기술 학습 경험'이나 'JD의 생소한 기술'을 연결\n"
            "  * 예: '○○ 기술은 처음 접하시는 것 같은데, 새로운 기술 배울 때 주로 어떤 방식으로 학습하시나요?'\n\n"
            "=============================================================\n"
            "[평가 지표 생성 가이드]\n"
            "평가 지표는 '회사가 원하는 인재상'을 반영해야 한다:\n\n"
            "⚠️ 평가지표 작성 시 절대 금지 사항:\n"
            "   🚫 '팀워크 및 문제 해결 태도' ❌\n"
            "   🚫 '의사소통 능력 및 활동 경험 연결' ❌\n"
            "   🚫 '학습 태도 및 기술 적응력' ❌\n"
            "   🚫 'A 및 B', 'A와 B', 'A/B' 형태 모두 금지\n"
            "   ✅ '팀워크' ✅\n"
            "   ✅ '문제 해결 태도' ✅\n"
            "   ✅ '의사소통 능력' ✅\n"
            "   ✅ '학습 태도' ✅\n"
            "   → 하나의 평가지표는 반드시 하나의 단일 역량만 명시할 것\n\n"
            "1. JD 중심 분석 (70%): 채용공고에서 요구하는 핵심 역량 추출\n"
            "   - JD의 '자격요건', '우대사항', '주요업무' 섹션을 집중 분석\n"
            "   - 예: JD에 'MSA 아키텍처 경험' → 평가지표 '마이크로서비스 설계 역량' (단일)\n"
            "   - 예: JD에 'CI/CD 파이프라인 구축' → 평가지표 'DevOps 실무 능력' (단일)\n\n"
            "2. 기술 역량 지표 (6~7개):\n"
            "   - JD에 명시된 필수 기술스택 관련 평가지표\n"
            "   - JD의 '주요 업무'를 수행하기 위해 필요한 기술 역량\n"
            "   - 각 지표는 간결하게 2~5단어로 작성 (예: 'React 개발 능력', 'API 설계 역량')\n\n"
            "3. 인성/태도 지표 (3~4개):\n"
            "   - 각각을 별도 지표로 분리: '팀워크', '의사소통 능력', '문제 해결 태도', '학습 의지', '책임감', '적응력'\n"
            "   - 절대 두 개를 합치지 말 것\n\n"
            "💡 평가지표 생성 체크리스트:\n"
            "   ✅ JD의 구체적인 키워드 활용\n"
            "   ✅ 명확하고 채점 가능한 지표\n"
            "   ✅ 2~3단어의 간결한 표현\n"
            "   ✅ 하나의 지표 = 하나의 역량\n"
            "   🚫 '및', '와', '/', '그리고' 등 연결어 사용 금지\n"
            "   🚫 복합 역량 표현 금지\n\n"
            "=============================================================\n"
            "[JSON 형식 준수 사항]\n"
            "1. 정확히 이 키 이름들만 사용: interview_questions, technical_questions, personality_questions, evaluation_criteria, question, criteria, description\n"
            "2. 질문 개수: 기술 5개 + 인성 3개 = 총 8개\n"
            "3. 평가지표 개수: 정확히 10개\n"
            "4. 모든 텍스트는 한국어로 작성\n"
            "5. JSON 형식이 유효해야 함 (따옴표, 쉼표, 중괄호 정확히)\n"
            "6. 추가 필드나 다른 키 이름 사용 금지"
        )

        # LLM Runnable 체인 (prompt → llm → parser)
        self.chain = self.prompt | self.llm | self.parser

    # 메인 로직
    def generate_questions_and_metrics(self, query_text: str, num_questions: int = 10, num_metrics: int = 10, session_id: str = None) -> InterviewState:
        """
        State 객체의 전체 텍스트를 사용하여 면접 질문 및 평가지표를 생성
        (RAG 대신 전체 문서 컨텍스트 사용으로 정보 손실 방지)
        
        Args:
            query_text: 검색 쿼리 (사용 안 함, 하위 호환성 유지)
            num_questions: 생성할 질문 개수 (기본값: 10)
            num_metrics: 생성할 평가지표 개수 (기본값: 10)
            session_id: 세션 UUID (State 조회용)
        """
        
        total_start = time.time()

        print("\n" + "="*80)
        print("🤖 [QuestionAgent] 시작")
        print("="*80)
        
        # 1️⃣ State에서 전체 텍스트 가져오기
        context_start = time.time()
        print(f"\n📝 [Step 1] State에서 문서 컨텍스트 조회")
        print(f"   - Session ID: {session_id}")
        
        try:
            from app.routes.state_routes import GLOBAL_STATE
            
            resume_text = GLOBAL_STATE.resume_text or ""
            jd_text = GLOBAL_STATE.jd_text or ""
            
            context_time = time.time() - context_start
            print(f"✅ [Step 1] 문서 컨텍스트 조회 완료 (⏱️  {context_time:.2f}초)")
            print(f"   - Resume 길이: {len(resume_text)}자")
            print(f"   - JD 길이: {len(jd_text)}자")
            
            if not resume_text and not jd_text:
                print("⚠️  [Step 1] State에 문서가 없습니다!")
            else:
                print(f"   - Resume 미리보기: {resume_text[:100]}...")
                print(f"   - JD 미리보기: {jd_text[:100]}...")
            
        except Exception as e:
            context_time = time.time() - context_start
            print(f"❌ [Step 1] State 조회 실패 (⏱️  {context_time:.2f}초): {e}")
            import traceback
            traceback.print_exc()
            resume_text = ""
            jd_text = ""

        # 2️⃣ LLM 호출
        llm_start = time.time()
        print(f"\n📡 [Step 2] LLM 호출 시작")
        print(f"   - 요청 질문 수: {num_questions}")
        print(f"   - 요청 평가지표 수: {num_metrics}")
        
        try:
            print(f"   - Prompt 작성 중...")
            
            # 전체 텍스트 사용 (토큰 제한 고려하여 적절히 자름)
            invoke_params = {
                "resume_context": resume_text[:8000],  # 넉넉하게 8000자
                "jd_context": jd_text[:2000],          # JD는 짧으므로 2000자
            }
            print(f"   - Resume context: {len(invoke_params['resume_context'])}자")
            print(f"   - JD context: {len(invoke_params['jd_context'])}자")
            print(f"   - Resume (첫 100자): {invoke_params['resume_context'][:100]}...")
            print(f"   - JD (첫 100자): {invoke_params['jd_context'][:100]}...")
            
            response = self.chain.invoke(invoke_params)
            llm_time = time.time() - llm_start
            print(f"✅ [Step 2] LLM 호출 완료 (⏱️  {llm_time:.2f}초)")
            print(f"   - 응답 타입: {type(response)}")
            print(f"   - 응답 내용: {str(response)[:200]}...")
            
        except Exception as e:
            llm_time = time.time() - llm_start
            print(f"❌ [Step 2] LLM 호출 실패 (⏱️  {llm_time:.2f}초): {e}")
            import traceback
            traceback.print_exc()
            response = {"questions": [], "metrics": []}

        # 3️⃣ 결과 파싱
        parse_start = time.time()
        print(f"\n📊 [Step 3] 결과 파싱")
        
        try:
            print(f"   - 응답 구조 분석")
            print(f"     응답 키: {response.keys()}")
            
            questions = []
            metrics = []
            
            # 지정된 형식: interview_questions + evaluation_criteria
            if "interview_questions" in response and "evaluation_criteria" in response:
                print(f"   ✅ 지정된 형식 감지 (표준 스키마)")
                
                # 1. 질문 추출
                interview_data = response.get("interview_questions", {})
                
                if isinstance(interview_data, dict):
                    print(f"      📋 interview_questions 분석:")
                    print(f"         구조: dict | 키: {interview_data.keys()}")
                    
                    # 기술 관련 질문 (5개)
                    tech_questions = interview_data.get("technical_questions", [])
                    print(f"         - technical_questions: {len(tech_questions)}개")
                    for idx, item in enumerate(tech_questions):
                        if isinstance(item, dict) and "question" in item:
                            questions.append(item["question"])
                            if idx < 1:
                                print(f"           [{idx+1}] {item['question'][:60]}...")
                    
                    # 인성 및 태도 관련 질문 (3개)
                    personality_questions = interview_data.get("personality_questions", [])
                    print(f"         - personality_questions: {len(personality_questions)}개")
                    for idx, item in enumerate(personality_questions):
                        if isinstance(item, dict) and "question" in item:
                            questions.append(item["question"])
                            if idx < 1:
                                print(f"           [{idx+1}] {item['question'][:60]}...")
                
                # 2. 평가지표 추출 (10개)
                evaluation_criteria_list = response.get("evaluation_criteria", [])
                print(f"      📊 evaluation_criteria: {len(evaluation_criteria_list)}개")
                
                for idx, item in enumerate(evaluation_criteria_list):
                    if isinstance(item, dict) and "criteria" in item:
                        metrics.append(item["criteria"])
                        if idx < 3:
                            print(f"         [{idx+1}] {item['criteria']}")
                
                print(f"      ✅ 추출 완료: 질문 {len(questions)}개, 지표 {len(metrics)}개")
            
            else:
                print(f"   ❌ 예상 형식과 다릅니다!")
                print(f"      응답 키: {response.keys()}")
                print(f"      필수 키: 'interview_questions', 'evaluation_criteria'")
                print(f"      전체 응답: {str(response)[:300]}...")
            
            print(f"   - 생성된 질문 수: {len(questions)}")
            if questions:
                for idx, q in enumerate(questions[:3]):
                    q_text = q if isinstance(q, str) else str(q)[:80]
                    print(f"     [{idx+1}] {q_text}...")
            
            print(f"   - \생성된 평가지표 수: {len(metrics)}")
            if metrics:
                for idx, m in enumerate(metrics[:5]):
                    print(f"     [{idx+1}] {m}")
            
            # 평가지표가 부족하면 한국어 기본값으로 채우기
            default_metrics = [
                "문제 해결력", "기술 이해도", "의사 소통 능력", "코드 작성 능력", "분석력",
                "창의성", "팀워크", "학습 의지", "자신감", "준비도", "세부 사항 파악", "시스템 설계",
                "트러블 슈팅", "성능 최적화", "코드 리뷰 능력"
            ]
            
            if not metrics:
                metrics = default_metrics[:num_metrics]
                print(f"   ℹ️  평가지표 기본값 설정: {metrics}")
            elif len(metrics) < num_metrics:
                # 부족한 개수만큼 기본값 추가
                shortage = num_metrics - len(metrics)
                additional = [m for m in default_metrics if m not in metrics][:shortage]
                metrics.extend(additional)
                print(f"   ℹ️  평가지표 추가 설정 (기본값): {additional}")
            
            result = InterviewState(
                questions=questions[:num_questions],
                metrics=metrics[:num_metrics],
            )
            parse_time = time.time() - parse_start
            print(f"✅ [Step 3] 파싱 완료 (⏱️  {parse_time:.2f}초)")
            print(f"   - 최종 질문: {len(result.questions)}개")
            print(f"   - 최종 지표: {len(result.metrics)}개")
            
            total_time = time.time() - total_start
            print("\n" + "="*80)
            print("✅ [QuestionAgent] 완료!")
            print(f"📊 종합 소요 시간: {total_time:.2f}초")
            print(f"   - State 조회: {context_time:.2f}초 ({context_time/total_time*100:.1f}%)")
            print(f"   - LLM 호출: {llm_time:.2f}초 ({llm_time/total_time*100:.1f}%)")
            print(f"   - 파싱:     {parse_time:.2f}초 ({parse_time/total_time*100:.1f}%)")
            print("="*80 + "\n")
            
            return result
            
        except Exception as e:
            total_time = time.time() - total_start
            parse_time = time.time() - parse_start
            print(f"❌ [Step 3] 파싱 실패 (⏱️  {parse_time:.2f}초): {e}")
            import traceback
            traceback.print_exc()
            
            print("\n" + "="*80)
            print("❌ [QuestionAgent] 실패!")
            print("="*80 + "\n")
            
            return InterviewState(questions=[], metrics=[])


    # LangGraph 연결용 Runnable
    def as_runnable(self):
        """
        LangGraph 연결용 Runnable
        입력: { "query_text": str, "num_questions": int, "num_metrics": int, "session_id": str }
        출력: { "questions": [...], "metrics": [...] }
        """
        return RunnableLambda(
            lambda inputs: self.generate_questions_and_metrics(
                query_text=inputs.get("query_text", ""),
                num_questions=inputs.get("num_questions", 10),
                num_metrics=inputs.get("num_metrics", 10),
                session_id=inputs.get("session_id", None)
            )
        )