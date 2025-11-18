from app.utils.rag_indexer import insert_resume_jd_embeddings
from app.utils.file_utils import extract_text, extract_portfolio_multimodal
from app.utils.document_structurer import structure_resume, structure_jd
from app.utils.semantic_chunker import chunk_structured_resume, chunk_structured_jd, chunks_to_text_with_metadata
import uuid

class EmbeddingAgent:
    """
    PDF 파일을 읽어 텍스트 추출 → RAG DB에 임베딩 저장하는 Agent
    """

    def run(self, resume_file, jd_file, portfolio_file=None, session_id: str = None):
        print("\n📚 [EmbeddingAgent] 시작")
        
        # Session ID 생성 (없으면 새로 생성)
        if not session_id:
            session_id = str(uuid.uuid4())
        print(f"   📌 Session ID: {session_id}")
        
        # 1️⃣ PDF 텍스트 추출
        print("\n1️⃣ PDF 텍스트 추출")
        try:
            resume_text = extract_text(resume_file)
            print(f"   ✅ 이력서 추출 완료: {len(resume_text)} 자")
            if resume_text:
                print(f"      샘플: {resume_text[:100]}...")
            else:
                print(f"      ⚠️  추출된 텍스트가 없습니다!")
        except Exception as e:
            print(f"   ❌ 이력서 추출 실패: {e}")
            raise
        
        try:
            jd_text = extract_text(jd_file)
            print(f"   ✅ JD 추출 완료: {len(jd_text)} 자")
            if jd_text:
                print(f"      샘플: {jd_text[:100]}...")
            else:
                print(f"      ⚠️  추출된 텍스트가 없습니다!")
        except Exception as e:
            print(f"   ❌ JD 추출 실패: {e}")
            raise
        
        # 포트폴리오 처리 (선택)
        portfolio_text = ""
        portfolio_id = None
        if portfolio_file:
            try:
                # 포트폴리오는 멀티모달 추출 사용
                print("   🎨 포트폴리오 멀티모달 분석 중...")
                portfolio_text = extract_portfolio_multimodal(portfolio_file)
                print(f"   ✅ 포트폴리오 분석 완료: {len(portfolio_text)} 자")
                if portfolio_text:
                    print(f"      샘플: {portfolio_text[:150]}...")
                else:
                    print(f"      ⚠️  분석 결과가 없습니다!")
            except Exception as e:
                print(f"   ⚠️  포트폴리오 분석 실패: {e}")
                portfolio_text = ""
        else:
            print(f"   ℹ️  포트폴리오 없음 (skip)")

        # 🆕 2️⃣ 문서 구조화 (OpenAI API)
        print("\n2️⃣ 문서 구조화 (OpenAI API)")
        structured_resume = None
        structured_jd = None
        resume_chunks_with_metadata = None
        jd_chunks_with_metadata = None
        
        try:
            # Resume 구조화
            structured_resume = structure_resume(resume_text)
            print(f"   ✅ Resume 구조화 완료")
            print(f"      - 이름: {structured_resume.basic_info.name if structured_resume.basic_info else 'N/A'}")
            print(f"      - 경력: {len(structured_resume.experience)}개")
            print(f"      - 프로젝트: {len(structured_resume.projects)}개")
            
            # Resume 시맨틱 청킹
            resume_chunks = chunk_structured_resume(structured_resume)
            resume_chunks_with_metadata = chunks_to_text_with_metadata(resume_chunks)
            print(f"   ✅ Resume 시맨틱 청킹 완료: {len(resume_chunks_with_metadata)}개 청크")
            
        except Exception as e:
            print(f"   ⚠️  Resume 구조화 실패: {e}")
            print(f"      → 기존 방식으로 폴백")
        
        try:
            # JD 구조화
            structured_jd = structure_jd(jd_text)
            print(f"   ✅ JD 구조화 완료")
            print(f"      - 포지션: {structured_jd.position}")
            print(f"      - 회사: {structured_jd.company or 'N/A'}")
            print(f"      - 필수 스킬: {len(structured_jd.requirements.required_skills)}개")
            
            # JD 시맨틱 청킹
            jd_chunks = chunk_structured_jd(structured_jd)
            jd_chunks_with_metadata = chunks_to_text_with_metadata(jd_chunks)
            print(f"   ✅ JD 시맨틱 청킹 완료: {len(jd_chunks_with_metadata)}개 청크")
            
        except Exception as e:
            print(f"   ⚠️  JD 구조화 실패: {e}")
            print(f"      → 기존 방식으로 폴백")

        # 3️⃣ RAG 인덱싱 (DB 저장)
        print("\n3️⃣ RAG 인덱싱 (DB 저장)")
        try:
            print("   📝 Resume 문서 임베딩 중...")
            if resume_chunks_with_metadata:
                # 🆕 시맨틱 청킹 사용
                resume_id = insert_resume_jd_embeddings(
                    session_id, "resume_input", "resume", 
                    chunks_with_metadata=resume_chunks_with_metadata
                )
            else:
                # 기존 방식 폴백
                resume_id = insert_resume_jd_embeddings(
                    session_id, "resume_input", "resume", text=resume_text
                )
            print(f"   ✅ 이력서 임베딩 완료: doc_id={resume_id}")
            
            print("   📝 JD 문서 임베딩 중...")
            if jd_chunks_with_metadata:
                # 🆕 시맨틱 청킹 사용
                jd_id = insert_resume_jd_embeddings(
                    session_id, "jd_input", "jd",
                    chunks_with_metadata=jd_chunks_with_metadata
                )
            else:
                # 기존 방식 폴백
                jd_id = insert_resume_jd_embeddings(
                    session_id, "jd_input", "jd", text=jd_text
                )
            print(f"   ✅ JD 임베딩 완료: doc_id={jd_id}")
            
            # 포트폴리오 임베딩 (선택, 기존 방식 유지)
            if portfolio_text:
                print("   📝 Portfolio 문서 임베딩 중...")
                portfolio_id = insert_resume_jd_embeddings(
                    session_id, "portfolio_input", "portfolio", text=portfolio_text
                )
                print(f"   ✅ 포트폴리오 임베딩 완료: doc_id={portfolio_id}")
            else:
                print(f"   ℹ️  포트폴리오 임베딩 skip")
                
        except Exception as e:
            print(f"   ❌ 임베딩 저장 실패: {e}")
            import traceback
            traceback.print_exc()
            raise

        # 4️⃣ 반환 (state 저장용)
        print("\n4️⃣ 결과 반환")
        result = {
            "session_id": session_id,
            "resume_id": resume_id,
            "jd_id": jd_id,
            "portfolio_id": portfolio_id,
            "resume_len": len(resume_text),
            "jd_len": len(jd_text),
            "portfolio_len": len(portfolio_text) if portfolio_text else 0,
            "resume_text": resume_text,
            "jd_text": jd_text,
            "portfolio_text": portfolio_text if portfolio_text else None,
            # 🆕 구조화된 데이터 추가
            "structured_resume": structured_resume.model_dump() if structured_resume else None,
            "structured_jd": structured_jd.model_dump() if structured_jd else None
        }
        print(f"   ✅ [EmbeddingAgent] 완료")
        print(f"      - session_id: {session_id}")
        print(f"      - resume_id (doc_id): {resume_id}")
        print(f"      - jd_id (doc_id): {jd_id}")
        print(f"      - portfolio_id (doc_id): {portfolio_id}")
        print(f"      - resume_len: {len(resume_text)} 자")
        print(f"      - jd_len: {len(jd_text)} 자")
        print(f"      - portfolio_len: {len(portfolio_text) if portfolio_text else 0} 자")
        print(f"      - structured_resume: {'✅' if structured_resume else '❌'}")
        print(f"      - structured_jd: {'✅' if structured_jd else '❌'}")
        print()
        
        return result


