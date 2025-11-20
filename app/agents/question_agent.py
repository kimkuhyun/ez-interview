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
            model="gpt-4o-mini",
            # model="gpt-3.5-turbo",
            temperature=0.5,
            openai_api_key=api_key,
        )

        # JSON 파서 (출력 구조를 명시)
        self.parser = JsonOutputParser()

        # 프롬프트 템플릿 정의
        self.prompt = ChatPromptTemplate.from_template(
            "당신은 면접관입니다. 제공된 문서를 바탕으로 면접 질문과 평가지표를 생성하세요.\n\n"
            "=== 참고 문서 ===\n"
            "[JD 요구사항]\n{jd_context}\n\n"
            "[지원자 이력서]\n{resume_context}\n\n"
            "[지원자 포트폴리오]\n{portfolio_context}\n\n"
            "{existing_questions_context}"
            "=== 질문 생성 규칙 ===\n"
            "**총 8개 질문을 생성하세요 (기술 5개 + 인성 3개)**\n\n"
            "**[기술 질문 5개]**\n"
            "- JD 기반 2개: JD의 필수 역량/우대 사항을 확인하는 질문\n"
            "- 이력서 기반 2개: 이력서의 구체적인 경력/프로젝트를 검증하는 질문 (프로젝트명, 기술명 직접 언급)\n"
            "- 포트폴리오 기반 1개: 포트폴리오의 기술적 구현/문제 해결을 확인하는 질문 (포트폴리오 없으면 이력서 기반으로 추가)\n\n"
            "**[인성 질문 3개]**\n"
            "- 의사소통, 팀워크, 학습 의지, 문제 해결 태도 등을 확인하는 질문\n"
            "- ⚠️ 반드시 실무 상황 기반 질문 (이력서/JD 맥락 활용)\n"
            "- 지원자의 실제 경험(회사명/프로젝트명)과 JD 요구사항을 연결\n\n"
            "⚠️ 질문 형식 (중요):\n"
            "- 모든 질문은 '~을/를 설명해주세요' 패턴으로 통일\n"
            "- 한 문장으로 간결하게 (40-60자 권장)\n"
            "- 구체적 기술/프로젝트명 반드시 명시\n"
            "⚠️ 금지사항:\n"
            "- 'JD에 언급된', 'JD에서 봤는데', 'JD와', 'JD를' 같은 메타 표현 금지\n"
            "- Yes/No 질문 금지 (개방형 질문만)\n"
            "- 막연한 질문 금지 (구체적인 내용 언급 필수)\n"
            "- 두 문장 이상의 긴 질문 금지\n"
            "- 기존 질문과 비슷한 주제나 표현 사용 금지 (완전히 새로운 관점 필수)\n"
            "- 기존 질문에서 다룬 기술/경험은 절대 재사용 금지 (예: PostgreSQL/MySQL 이미 물어봤으면 다른 기술로)\n\n"
            "💡 다양성 강화 (필수):\n"
            "- 이력서/포트폴리오의 **아직 다루지 않은** 프로젝트, 기술, 경험을 발굴\n"
            "- JD의 **아직 확인하지 않은** 요구사항이나 업무 영역 선택\n"
            "- 같은 기술이라도 완전히 다른 각도로 질문 (예: 사용 경험 → 트러블슈팅, 설계, 최적화 등)\n"
            "- 문서에 명시되지 않은 암묵적 역량 검증 (예: 코드 리뷰 경험, 장애 대응, 기술 선택 근거 등)\n\n"
            "=== 평가지표 생성 규칙 ===\n"
            "JD를 중심으로 10개 평가지표를 생성하세요 (카테고리별 통합):\n\n"
            "**[기술 역량 카테고리 6~7개]**\n"
            "- JD 필수 기술 영역을 대분류로 묶어서 표현\n"
            "- 개별 기술이 아닌 기술 영역/도메인 중심으로 통합\n\n"
            "**[인성/태도 카테고리 3~4개]**\n"
            "- 팀워크, 의사소통, 학습의지, 문제해결 등\n"
            "- 단순 추상적 표현보다 실무 맥락 포함 권장\n"
            "- 예: '팀워크' → '협업 및 의사소통', '학습의지' → '기술 학습 및 성장 의지'\n\n"
            "⚠️ 중요:\n"
            "- 질문과 1:1 대응 금지 (평가지표는 여러 질문에서 공통 평가)\n"
            "- 하나의 지표 = 하나의 역량만 (복합 표현 금지)\n"
            "- '및', '와', '/', '그리고' 사용 금지 (단, '협업 및 의사소통'처럼 불가분 관계는 예외)\n"
            "- 예: 'SQL 튜닝 능력' (O), 'SQL 튜닝 및 최적화' (X)\n\n"
            "=== 출력 형식 (JSON) ===\n"
            "{{\n"
            '  "interview_questions": {{\n'
            '    "technical_questions": [\n'
            '      {{"question": "질문", "evaluation_criteria": "평가기준"}},\n'
            '      ... (정확히 5개)\n'
            '    ],\n'
            '    "personality_questions": [\n'
            '      {{"question": "질문", "evaluation_criteria": "평가기준"}},\n'
            '      ... (정확히 3개)\n'
            '    ]\n'
            '  }},\n'
            '  "evaluation_criteria": [\n'
            '    {{"criteria": "평가지표", "description": "설명"}},\n'
            '    ... (10개)\n'
            '  ]\n'
            "}}"
        )

        # LLM Runnable 체인 (prompt → llm → parser)
        self.chain = self.prompt | self.llm | self.parser

    # 메인 로직
    def generate_questions_and_metrics(self, query_text: str, num_questions: int = 10, num_metrics: int = 10, session_id: str = None, jd_id: str = None, existing_questions: list = None) -> InterviewState:
        """
        RAG로 필요한 문서 부분만 검색하여 질문 및 평가지표 생성
        
        Args:
            query_text: 검색 쿼리 (기본값 사용)
            num_questions: 생성할 질문 개수 (기본값: 10)
            num_metrics: 생성할 평가지표 개수 (기본값: 10)
            session_id: 세션 UUID (이력서/포트폴리오 검색용)
            jd_id: JD ID (JD 검색용)
            existing_questions: 기존 질문 리스트 (중복 방지용)
        """
        
        total_start = time.time()

        print("\n" + "="*80)
        print("🤖 [QuestionAgent] 시작")
        print("="*80)
        
        # GLOBAL_STATE에서 portfolio_len 확인
        from app.routes.state_routes import GLOBAL_STATE
        has_portfolio = GLOBAL_STATE.portfolio_len and GLOBAL_STATE.portfolio_len > 0
        
        # 🆕 Priority 1: 구조화 데이터 추출 (RAG 우회)
        print(f"\n📄 [Step 0] 구조화 데이터 추출")
        structured_resume = GLOBAL_STATE.structured_resume
        structured_jd = GLOBAL_STATE.structured_jd
        
        # 구조화 데이터에서 핵심 정보 추출
        resume_tech_stack = []
        resume_projects = []
        resume_companies = []
        
        if structured_resume:
            print(f"   ✅ Resume 구조화 데이터 발견")
            
            # dict로 접근 (Pydantic 객체가 아님!)
            skills = structured_resume.get('skills', {})
            if skills:
                resume_tech_stack.extend(skills.get('technical', []))
                resume_tech_stack.extend(skills.get('languages', []))
                resume_tech_stack.extend(skills.get('frameworks', []))
                resume_tech_stack.extend(skills.get('tools', []))
            
            # 프로젝트 정보
            projects = structured_resume.get('projects', [])
            for proj in projects:
                resume_projects.append({
                    "name": proj.get('name', ''),
                    "tech": proj.get('tech_stack', []),
                    "description": proj.get('description', '')[:100]
                })
            
            # 경력 정보
            experience = structured_resume.get('experience', [])
            for exp in experience:
                resume_companies.append({
                    "company": exp.get('company', ''),
                    "role": exp.get('role', ''),
                    "tech": exp.get('tech_stack', [])
                })
                if exp.get('tech_stack'):
                    resume_tech_stack.extend(exp['tech_stack'])
            
            resume_tech_stack = list(set(resume_tech_stack))  # 중복 제거
            print(f"      - 기술 스택: {len(resume_tech_stack)}개 ({', '.join(resume_tech_stack[:5])}...)")
            print(f"      - 프로젝트: {len(resume_projects)}개")
            print(f"      - 경력: {len(resume_companies)}개")
        else:
            print(f"   ⚠️  Resume 구조화 데이터 없음")
        
        jd_required_skills = []
        jd_preferred_skills = []
        jd_tech_stack = []
        jd_responsibilities = []
        
        if structured_jd:
            print(f"   ✅ JD 구조화 데이터 발견")
            
            # dict로 접근
            requirements = structured_jd.get('requirements', {})
            if requirements:
                jd_required_skills = requirements.get('required_skills', [])
                jd_preferred_skills = requirements.get('preferred_skills', [])
            
            jd_tech_stack = structured_jd.get('tech_stack', [])
            jd_responsibilities = structured_jd.get('responsibilities', [])[:5]  # 상위 5개만
            
            print(f"      - 필수 기술: {len(jd_required_skills)}개 ({', '.join(jd_required_skills[:3])}...)")
            print(f"      - 우대 기술: {len(jd_preferred_skills)}개")
            print(f"      - 기술 스택: {len(jd_tech_stack)}개")
            print(f"      - 주요 업무: {len(jd_responsibilities)}개")
        else:
            print(f"   ⚠️  JD 구조화 데이터 없음")
        
        # 1️⃣ RAG 검색 (문서별 필요한 부분만 검색)
        rag_start = time.time()
        print(f"\n🔍 [Step 1] RAG 검색 (메타데이터 필터링 적용)")
        print(f"   - Session ID: {session_id}")
        print(f"   - JD ID: {jd_id}")
        print(f"   - Portfolio 제출 여부: {has_portfolio}")
        
        try:
            # JD 중심 검색 (top 3) - 필수, jd_id 사용
            jd_query = "JD 필수 역량, 우대 사항, 주요 업무"
            jd_chunks = search_similar_chunks(
                query=jd_query,
                session_id=session_id,
                jd_id=jd_id,
                doc_type="jd",
                top_k=3,
                metadata_filter=None  # JD는 필터 없음
            )
            jd_context = "\n".join([chunk.get("content", "") for chunk in jd_chunks])
            print(f"   ✅ JD 검색: {len(jd_chunks)}개 청크, {len(jd_context)}자")
            print(f"\n   📄 [JD Chunks]")
            for i, chunk in enumerate(jd_chunks, 1):
                score = chunk.get('score', 'N/A')
                content = chunk.get('content', '')[:150]
                print(f"      {i}. [유사도: {score:.3f}] {content}...")
            
            # 이력서 중심 검색 (top 5) - 필수, 🆕 experience/projects 섬션 우선
            resume_query = "경력, 프로젝트 경험, 기술 스택, 주요 성과"
            resume_chunks = search_similar_chunks(
                query=resume_query,
                session_id=session_id,
                doc_type="resume",
                top_k=5,
                metadata_filter={"sections": ["experience", "projects"]}  # 🆕 Priority 2
            )
            resume_context = "\n".join([chunk.get("content", "") for chunk in resume_chunks])
            print(f"   ✅ 이력서 검색: {len(resume_chunks)}개 청크, {len(resume_context)}자")
            print(f"\n   📄 [Resume Chunks]")
            for i, chunk in enumerate(resume_chunks, 1):
                score = chunk.get('score', 'N/A')
                content = chunk.get('content', '')[:150]
                print(f"      {i}. [유사도: {score:.3f}] {content}...")
            
            # 포트폴리오 검색 (top 3) - 선택 (제출한 경우에만)
            if has_portfolio:
                print(f"\n   🔍 포트폴리오 검색 시작...")
                portfolio_query = "프로젝트 상세, 기술적 구현, 문제 해결"
                portfolio_chunks = search_similar_chunks(
                    query=portfolio_query,
                    session_id=session_id,
                    doc_type="portfolio",
                    top_k=3,
                    metadata_filter={"sections": ["projects"]}  # 🆕 Priority 2
                )
                
                if portfolio_chunks:
                    portfolio_context = "\n".join([chunk.get("content", "") for chunk in portfolio_chunks])
                    print(f"   ✅ 포트폴리오 검색: {len(portfolio_chunks)}개 청크, {len(portfolio_context)}자")
                    print(f"\n   📄 [Portfolio Chunks]")
                    for i, chunk in enumerate(portfolio_chunks, 1):
                        score = chunk.get('score', 'N/A')
                        content = chunk.get('content', '')[:150]
                        print(f"      {i}. [유사도: {score:.3f}] {content}...")
                else:
                    portfolio_context = "포트폴리오 없음"
                    print(f"   ⚠️  포트폴리오 검색 결과 0개 (DB에 데이터 없음)")
            else:
                portfolio_context = "포트폴리오 없음"
                print(f"   ℹ️  포트폴리오 미제출 → 검색 스킵")
            
            rag_time = time.time() - rag_start
            print(f"✅ [Step 1] RAG 검색 완료 (⏱️  {rag_time:.2f}초)")
            print(f"   - 총 컨텍스트: {len(jd_context) + len(resume_context) + len(portfolio_context)}자")
            
        except Exception as e:
            rag_time = time.time() - rag_start
            print(f"❌ [Step 1] RAG 검색 실패 (⏱️  {rag_time:.2f}초): {e}")
            import traceback
            traceback.print_exc()
            jd_context = ""
            resume_context = ""
            portfolio_context = ""

        # 2️⃣ LLM 호출
        llm_start = time.time()
        print(f"\n📡 [Step 2] LLM 호출")
        
        # 기존 질문이 있으면 프롬프트에 추가
        existing_questions_context = ""
        if existing_questions and len(existing_questions) > 0:
            questions_list = "\n".join([f"  - {q}" for q in existing_questions])
            existing_questions_context = (
                "=== ⚠️ 중복 금지 질문 목록 ===\n"
                "아래 질문들과 주제, 의도, 표현이 겹치지 않는 완전히 새로운 질문을 생성하세요:\n"
                f"{questions_list}\n\n"
            )
            print(f"   ⚠️ 기존 질문 {len(existing_questions)}개 제외 (무작위성 강화 모드)")
        
        try:
            invoke_params = {
                "jd_context": jd_context or "정보 없음",
                "resume_context": resume_context or "정보 없음",
                "portfolio_context": portfolio_context or "정보 없음",
                "existing_questions_context": existing_questions_context,
            }
            
            # 🆕 Priority 1: 구조화 데이터를 프롬프트에 직접 추가
            if structured_resume or structured_jd:
                structured_context = "\n=== 📄 구조화 데이터 (정확한 정보) ===\n"
                
                if structured_resume:
                    structured_context += "\n[지원자 기술 스택]\n"
                    if resume_tech_stack:
                        structured_context += f"{', '.join(resume_tech_stack[:20])}\n"  # 상위 20개
                    
                    structured_context += "\n[지원자 프로젝트]\n"
                    for proj in resume_projects[:5]:  # 상위 5개
                        structured_context += f"- {proj['name']}: {', '.join(proj['tech'][:5])}\n"
                    
                    structured_context += "\n[지원자 경력]\n"
                    for exp in resume_companies[:3]:  # 상위 3개
                        structured_context += f"- {exp['company']} ({exp['role']}): {', '.join(exp['tech'][:5])}\n"
                
                if structured_jd:
                    structured_context += "\n[JD 필수 기술]\n"
                    if jd_required_skills:
                        structured_context += f"{', '.join(jd_required_skills)}\n"
                    
                    structured_context += "\n[JD 우대 기술]\n"
                    if jd_preferred_skills:
                        structured_context += f"{', '.join(jd_preferred_skills)}\n"
                    
                    structured_context += "\n[JD 기술 스택]\n"
                    if jd_tech_stack:
                        structured_context += f"{', '.join(jd_tech_stack)}\n"
                    
                    structured_context += "\n[JD 주요 업무]\n"
                    for idx, resp in enumerate(jd_responsibilities, 1):
                        structured_context += f"{idx}. {resp}\n"
                
                structured_context += "\n⚠️  위 구조화 데이터를 우선적으로 활용하여 질문을 생성하세요.\n"
                
                # 프롬프트에 추가
                invoke_params["resume_context"] = structured_context + "\n" + invoke_params["resume_context"]
                
                print(f"   🆕 구조화 데이터 프롬프트에 주입 완료")
            
            # 개별 질문 재생성 시 temperature 높이기 (다양성 증가)
            llm = self.llm
            if existing_questions and len(existing_questions) > 0:
                from langchain_openai import ChatOpenAI
                llm = ChatOpenAI(
                    model="gpt-3.5-turbo",
                    temperature=0.9,  # 0.5 → 0.9 (무작위성 증가)
                    openai_api_key=os.getenv("OPENAI_API_KEY"),
                )
                chain = self.prompt | llm | self.parser
                response = chain.invoke(invoke_params)
            else:
                response = self.chain.invoke(invoke_params)
            llm_time = time.time() - llm_start
            print(f"✅ [Step 2] LLM 호출 완료 (⏱️  {llm_time:.2f}초)")
            
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
            questions = []
            metrics = []
            
            if "interview_questions" in response and "evaluation_criteria" in response:
                interview_data = response.get("interview_questions", {})
                
                # 기술 질문
                tech_questions = interview_data.get("technical_questions", [])
                for item in tech_questions:
                    if isinstance(item, dict) and "question" in item:
                        questions.append(item["question"])
                
                # 인성 질문
                personality_questions = interview_data.get("personality_questions", [])
                for item in personality_questions:
                    if isinstance(item, dict) and "question" in item:
                        questions.append(item["question"])
                
                # 평가지표
                evaluation_criteria_list = response.get("evaluation_criteria", [])
                for item in evaluation_criteria_list:
                    if isinstance(item, dict) and "criteria" in item:
                        metrics.append(item["criteria"])
                
                print(f"   ✅ 추출 완료: 질문 {len(questions)}개, 지표 {len(metrics)}개")
            
            # 기본값 보충
            if not metrics:
                default_metrics = [
                    "문제 해결력", "기술 이해도", "의사소통", "코드 작성", "분석력",
                    "창의성", "팀워크", "학습 의지", "책임감", "적응력"
                ]
                metrics = default_metrics[:num_metrics]
            
            result = InterviewState(
                questions=questions[:num_questions],
                metrics=metrics[:num_metrics],
            )
            
            parse_time = time.time() - parse_start
            print(f"✅ [Step 3] 파싱 완료 (⏱️  {parse_time:.2f}초)")
            
            total_time = time.time() - total_start
            print("\n" + "="*80)
            print(f"✅ [QuestionAgent] 완료 ({total_time:.2f}초)")
            print(f"   - 질문: {len(result.questions)}개")
            print(f"   - 지표: {len(result.metrics)}개")
            print("="*80 + "\n")
            
            return result
            
        except Exception as e:
            parse_time = time.time() - parse_start
            print(f"❌ [Step 3] 파싱 실패 (⏱️  {parse_time:.2f}초): {e}")
            import traceback
            traceback.print_exc()
            
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