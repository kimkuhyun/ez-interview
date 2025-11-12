from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnableLambda, RunnableSequence
from app.config.config import Config
from app.utils.rag_retriever import search_similar_chunks


class StreamAgent:
    """
    면접 스트림 AI 에이전트 (LCEL 기반)
    
    역할: 면접자의 답변을 받아 후속 질문 3개를 자동 생성
    구조: 전처리 → LLM 호출 → 파싱 (3단계 파이프라인)
    """
    
    def __init__(self):
        # ========================================
        # LangChain ChatOpenAI 모델 초기화
        # ========================================
        self.llm = ChatOpenAI(
            model="gpt-4",              # 사용할 모델
            temperature=0.7,            # 창의성 (0=결정적, 1=창의적)
            openai_api_key=Config.OPENAI_API_KEY,
        )
        
        # ========================================
        # 프롬프트 템플릿 정의
        # ========================================
        # ChatPromptTemplate: system 메시지 + user 메시지로 구성
        # {변수명}으로 동적 값 주입 가능
        self.prompt = ChatPromptTemplate.from_messages([
            # System: AI의 역할 정의
            ("system", "너는 면접관 AI야. 면접자의 답변에 기반해서 후속 질문을 자연스럽게 만들어줘."),
            
            # User: 실제 프롬프트 (변수 포함)
            ("user", """
아래는 지금까지의 대화야.
면접자의 최신 답변을 참고해서 후속 질문 3개를 자연스럽게 만들어줘.

{rag_context}

[이전 대화 기록]
{history_text}

[면접자의 최신 답변]
{interviewee_answer}

요구사항:
- 각 질문은 한 줄씩 출력
- 번호 없이 순수 질문만 3줄
- 인사말·요약문·설명문은 쓰지 마
- 이력서와 JD 내용을 참고하여 맥락에 맞는 질문을 생성해
""")
        ])
        
        # ========================================
        # 출력 파서 (LLM 응답을 문자열로 추출)
        # ========================================
        self.parser = StrOutputParser()
        
        # ========================================
        # LCEL Sequence 정의 (핵심 파이프라인)
        # ========================================
        # RunnableSequence: 여러 단계를 순차적으로 실행
        # 각 단계의 출력이 다음 단계의 입력이 됨
        # LangSmith에서 단계별 추적 가능 (디버깅/성능 모니터링)
        self.chain = RunnableSequence(
            RunnableLambda(self._preprocess_input),    # 1단계: 입력 전처리
            RunnableLambda(self._generate_questions),   # 2단계: LLM 호출
            RunnableLambda(self._parse_output),         # 3단계: 출력 파싱
        )
    
    # ========================================
    # 단계별 처리 함수
    # ========================================
    
    def _preprocess_input(self, inputs: dict) -> dict:
        """
        1️⃣ 입력 데이터 전처리 + RAG 검색
        
        목적: 
        - history 리스트를 LLM이 이해할 수 있는 문자열로 변환
        - VectorDB에서 이력서/JD 검색하여 프롬프트 보강
        
        입력 예시:
          {
            "text": "저는 Django로 프로젝트를...",
            "history": [
              {"role": "면접관", "content": "자기소개를 해주세요."},
              {"role": "면접자", "content": "저는 백엔드 개발자입니다."}
            ],
            "session_id": "uuid-1234-5678"  # GLOBAL_STATE에서 전달
          }
        
        출력 예시:
          {
            ...원본 데이터,
            "history_text": "면접관: 자기소개를 해주세요.\n면접자: 저는 백엔드 개발자입니다.",
            "interviewee_answer": "저는 Django로 프로젝트를...",
            "rag_context": "[이력서]\n...\n\n[모집공고]\n..."
          }
        """
        history = inputs.get("history")
        
        # 이전 대화 기록이 리스트(딕셔너리)로 넘어오면 문자열로 변환
        if history and isinstance(history, (list, tuple)):
            # 각 메시지를 "역할: 내용" 형태로 변환 후 줄바꿈으로 연결
            history_text = "\n".join(
                f"{m.get('role','')}: {m.get('content','')}" for m in history
            )
        else:
            # history가 없거나 이미 문자열이면 그대로 사용
            history_text = history if history else '(이전 대화 없음)'
        
        # ========================================
        # RAG 검색: 이력서 + JD 가져오기
        # ========================================
        session_id = inputs.get("session_id")
        interviewee_answer = inputs.get("text", "")
        
        rag_context = ""
        
        if session_id and interviewee_answer:
            try:
                # 면접자 답변을 쿼리로 사용하여 관련 문서 검색
                # top_k=3: 이력서 1~2개 + JD 1~2개 정도 가져옴
                results = search_similar_chunks(
                    query=interviewee_answer,
                    session_id=session_id,
                    top_k=3
                )
                
                if results:
                    resume_chunks = []
                    jd_chunks = []
                    
                    for content, doc_type, score in results:
                        if doc_type == 'resume':
                            resume_chunks.append(content)
                        elif doc_type == 'jd':
                            jd_chunks.append(content)
                    
                    # 이력서와 JD를 구분하여 컨텍스트 구성
                    if resume_chunks:
                        rag_context += f"[이력서 관련 정보]\n{' '.join(resume_chunks)}\n\n"
                    
                    if jd_chunks:
                        rag_context += f"[모집공고 관련 정보]\n{' '.join(jd_chunks)}\n\n"
                    
                    print(f"✅ RAG 검색 완료: 이력서 {len(resume_chunks)}개, JD {len(jd_chunks)}개")
                else:
                    print("⚠️  RAG 검색 결과 없음")
                    
            except Exception as e:
                print(f"❌ RAG 검색 실패: {e}")
                # RAG 실패 시에도 질문 생성은 계속 진행
        
        # RAG 컨텍스트가 없으면 안내 메시지
        if not rag_context:
            rag_context = "[참고: 이력서 및 모집공고 정보 없음]\n"
        
        # 원본 inputs에 변환된 데이터 추가
        inputs["history_text"] = history_text
        inputs["interviewee_answer"] = interviewee_answer
        inputs["rag_context"] = rag_context
        
        # 다음 단계로 전달 (dict 전체 반환)
        return inputs
    
    def _generate_questions(self, inputs: dict) -> dict:
        """
        2️⃣ LLM 호출하여 질문 생성
        
        목적: 프롬프트에 변수를 주입하고 GPT-4에 요청
        
        서브 체인 구조:
          prompt (변수 주입) 
            → llm (GPT-4 호출) 
            → parser (응답 문자열 추출)
        
        입력: {"history_text": "...", "interviewee_answer": "...", "rag_context": "..."}
        출력: {...원본 데이터, "ai_reply": "질문1\n질문2\n질문3"}
        """
        # 서브 체인 실행: | 연산자로 컴포넌트 연결 (Unix 파이프와 유사)
        # self.prompt: 프롬프트 템플릿에 변수 주입
        # self.llm: GPT-4 호출
        # self.parser: 응답에서 텍스트만 추출
        ai_reply = (self.prompt | self.llm | self.parser).invoke({
            "history_text": inputs["history_text"],
            "interviewee_answer": inputs["interviewee_answer"],
            "rag_context": inputs.get("rag_context", ""),  # RAG 컨텍스트 추가
        })
        
        # AI 응답을 inputs에 추가하여 다음 단계로 전달
        inputs["ai_reply"] = ai_reply
        return inputs
    
    def _parse_output(self, inputs: dict) -> list:
        """
        3️⃣ AI 응답을 질문 리스트로 파싱
        
        목적: AI가 생성한 텍스트를 클라이언트가 사용할 수 있는 리스트로 변환
        
        처리 로직:
          1. 줄바꿈으로 split
          2. 각 줄에서 "- ", "• " 같은 기호 제거
          3. 빈 줄 제거
          4. 최대 3개만 선택
        
        입력: {"ai_reply": "- 질문1\n- 질문2\n- 질문3"}
        출력: ["질문1", "질문2", "질문3"]
        """
        ai_reply = inputs["ai_reply"]
        
        # 한 줄씩 split하고 번호/기호 제거
        questions = [
            q.strip("-• ").strip()  # "- ", "• " 제거 후 공백 제거
            for q in ai_reply.split("\n")  # 줄바꿈으로 분리
            if q.strip()  # 빈 줄 제외
        ]
        
        # 최대 3개만 반환 (AI가 더 많이 생성해도 3개로 제한)
        return questions[:3]
    
    # ========================================
    # 외부 인터페이스 (Flask Route에서 호출)
    # ========================================
    
    def generate_followups(self, text, question_id, history=None, session_id=None, regen=False):
        """
        후속 질문 3개 생성 (기존 Flask API와 호환)
        
        Args:
            text (str): 면접자의 최신 답변
            question_id (str): 현재 질문 ID (q1, q2, ...)
            history (list or str, optional): 이전 대화 기록
            session_id (str, optional): 세션 ID (RAG 검색용)
            regen (bool, optional): 재생성 여부 (현재 미사용)
        
        Returns:
            list[str]: 후속 질문 리스트 (최대 3개)
        
        사용 예시:
            questions = stream_agent.generate_followups(
                text="저는 Django로 RESTful API를 개발했습니다.",
                question_id="q1",
                history=[{"role": "면접관", "content": "자기소개를 해주세요."}],
                session_id="uuid-1234-5678"
            )
            # 반환: ["Django에서 가장 어려웠던 점은?", ...]
        """
        try:
            # LCEL 체인 실행
            # invoke(): 동기 방식으로 체인 실행 (입력 → 전처리 → LLM → 파싱 → 출력)
            questions = self.chain.invoke({
                "text": text,
                "history": history,
                "session_id": session_id,  # RAG 검색을 위한 session_id 전달
            })
            
            return questions
            
        except Exception as e:
            # LLM 호출 실패 시 fallback 메시지 반환
            print(f"❌ LangChain AI 오류: {e}")
            import traceback
            traceback.print_exc()
            return [
                "죄송합니다. AI 질문 생성 중 오류가 발생했습니다.",
                "잠시 후 다시 시도해주세요.",
                "또는 직접 질문을 입력해주세요."
            ]
    
    def as_runnable(self):
        """
        Runnable 체인 반환 (다른 에이전트와 조합 가능)
        
        용도: 여러 에이전트를 연결하여 복잡한 파이프라인 구성
        
        사용 예시:
            # 여러 에이전트를 연결
            combined_chain = RunnableSequence(
                parse_agent.as_runnable(),
                jd_agent.as_runnable(),
                stream_agent.as_runnable(),
            )
            combined_chain.invoke({...})
        """
        return self.chain
    
    def analyze_answer(self, text, criteria=None):
        """답변 분석 (추후 구현)
        
        Args:
            text (str): 면접자 답변
            criteria (dict, optional): 평가 기준
        
        Returns:
            dict: 분석 결과
        """
        # TODO: 답변 분석 로직 구현
        # LCEL 체인으로 구성 가능
        pass
