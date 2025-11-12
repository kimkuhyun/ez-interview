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
            "너는 기술면접 질문과 평가 기준을 함께 생성하는 AI야.\n"
            "아래의 지원자 이력서 및 JD 관련 문서 내용을 참고해서 "
            "면접 질문과 평가 지표를 정확하게 JSON 형태로 출력해라.\n\n"
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
            "[검색된 문서 내용]\n{context}\n\n"
            "[중요 지시사항]\n"
            "1. 면접 질문은 총 8개 (기술 5개 + 인성 3개):\n"
            "   - 기술관련질문 5개: 이력서와 JD 기반 기술 역량 평가\n"
            "   - 인성및태도관련질문 3개:\n"
            "     * 팀워크/의사소통 1개\n"
            "     * 책임감/주도성 1개\n"
            "     * 적응력/학습태도 1개\n\n"
            "2. 평가 지표는 정확히 10개를 생성한다.\n"
            "3. 모든 질문과 지표는 한국어로 작성한다.\n"
            "4. JSON 형식은 유효해야 하며, 위의 정확한 키 이름을 사용한다:\n"
            "   - interview_questions (필수)\n"
            "   - technical_questions (필수)\n"
            "   - personality_questions (필수)\n"
            "   - evaluation_criteria (필수)\n"
            "   - question (필수)\n"
            "   - criteria (필수)\n"
            "   - description (필수)\n"
            "5. 추가 필드나 다른 키 이름을 사용하지 마라."
        )

        # LLM Runnable 체인 (prompt → llm → parser)
        self.chain = self.prompt | self.llm | self.parser

    # 메인 로직
    def generate_questions_and_metrics(self, query_text: str, num_questions: int = 10, num_metrics: int = 10, session_id: str = None) -> InterviewState:
        """
        입력된 JD/이력서 기반 검색 질의(query_text)를 받아,
        RAG 기반으로 문맥을 검색해 면접 질문 및 평가지표를 함께 생성
        
        Args:
            query_text: 검색 쿼리
            num_questions: 생성할 질문 개수 (기본값: 10)
            num_metrics: 생성할 평가지표 개수 (기본값: 10)
            session_id: 세션 UUID (같은 세션 내의 문서만 검색)
        """
        
        total_start = time.time()

        print("\n" + "="*80)
        print("🤖 [QuestionAgent] 시작")
        print("="*80)
        
        # 1️⃣ RAG DB 검색
        rag_start = time.time()
        print(f"\n📝 [Step 1] RAG 검색 시작")
        print(f"   - Query: {query_text[:100]}...")
        print(f"   - Session ID: {session_id}")
        print(f"   - Top-K: 5")
        
        try:
            rag_results = search_similar_chunks(query_text, session_id=session_id, top_k=5)
            rag_time = time.time() - rag_start
            print(f"✅ [Step 1] RAG 검색 완료: {len(rag_results)}개 문서 검색됨 (⏱️  {rag_time:.2f}초)")
            
            if rag_results:
                for idx, result in enumerate(rag_results):
                    content, doc_type, score = result
                    print(f"   [{idx+1}] 타입: {doc_type}, 길이: {len(content)}자, 유사도: {score:.4f}")
                    print(f"       내용: {content[:80]}...")
            else:
                print("⚠️  [Step 1] RAG 검색 결과가 없습니다!")
            
            context_text = "\n".join([r[0] for r in rag_results]) if rag_results else ""
            print(f"   - 최종 Context 길이: {len(context_text)}자")
            
        except Exception as e:
            rag_time = time.time() - rag_start
            print(f"❌ [Step 1] RAG 검색 실패 (⏱️  {rag_time:.2f}초): {e}")
            import traceback
            traceback.print_exc()
            context_text = ""

        # 2️⃣ LLM 호출
        llm_start = time.time()
        print(f"\n📡 [Step 2] LLM 호출 시작")
        print(f"   - 요청 질문 수: {num_questions}")
        print(f"   - 요청 평가지표 수: {num_metrics}")
        
        try:
            print(f"   - Prompt 작성 중...")
            invoke_params = {
                "context": context_text[:3000],  # prompt overflow 방지
            }
            print(f"   - Context (첫 200자): {invoke_params['context'][:200]}...")
            
            response = self.chain.invoke(invoke_params)
            print(f"✅ [Step 2] LLM 호출 완료")
            print(f"   - 응답 타입: {type(response)}")
            print(f"   - 응답 내용: {str(response)[:200]}...")
            
        except Exception as e:
            print(f"❌ [Step 2] LLM 호출 실패: {e}")
            import traceback
            traceback.print_exc()
            response = {"questions": [], "metrics": []}

        llm_time = time.time() - llm_start

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
                "문제 해결력",
                "기술 이해도",
                "의사 소통 능력",
                "코드 작성 능력",
                "분석력",
                "창의성",
                "팀워크",
                "학습 의지",
                "자신감",
                "준비도",
                "세부 사항 파악",
                "시스템 설계",
                "트러블 슈팅",
                "성능 최적화",
                "코드 리뷰 능력"
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
            print(f"   - RAG 검색: {rag_time:.2f}초 ({rag_time/total_time*100:.1f}%)")
            print(f"   - LLM 호출: {llm_time:.2f}초 ({llm_time/total_time*100:.1f}%)")
            print(f"   - 파싱:     {parse_time:.2f}초 ({parse_time/total_time*100:.1f}%)")
            print("="*80 + "\n")
            
            return result
            
        except Exception as e:
            total_time = time.time() - total_start
            print(f"❌ [Step 3] 파싱 실패 (⏱️  {total_time:.2f}초 경과): {e}")
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