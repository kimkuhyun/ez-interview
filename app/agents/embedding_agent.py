from app.utils.rag_indexer import insert_resume_jd_embeddings
from app.utils.file_utils import extract_text, extract_portfolio_multimodal
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

        # 2️⃣ RAG 인덱싱 (DB 저장)
        print("\n2️⃣ RAG 인덱싱 (DB 저장)")
        try:
            print("   📝 Resume 문서 임베딩 중...")
            resume_id = insert_resume_jd_embeddings(session_id, "resume_input", "resume", resume_text)
            print(f"   ✅ 이력서 임베딩 완료: doc_id={resume_id}")
            
            print("   📝 JD 문서 임베딩 중...")
            jd_id = insert_resume_jd_embeddings(session_id, "jd_input", "jd", jd_text)
            print(f"   ✅ JD 임베딩 완료: doc_id={jd_id}")
            
            # 포트폴리오 임베딩 (선택)
            if portfolio_text:
                print("   📝 Portfolio 문서 임베딩 중...")
                portfolio_id = insert_resume_jd_embeddings(session_id, "portfolio_input", "portfolio", portfolio_text)
                print(f"   ✅ 포트폴리오 임베딩 완료: doc_id={portfolio_id}")
            else:
                print(f"   ℹ️  포트폴리오 임베딩 skip")
                
        except Exception as e:
            print(f"   ❌ 임베딩 저장 실패: {e}")
            import traceback
            traceback.print_exc()
            raise

        # 3️⃣ 반환 (state 저장용)
        print("\n3️⃣ 결과 반환")
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
            "portfolio_text": portfolio_text if portfolio_text else None
        }
        print(f"   ✅ [EmbeddingAgent] 완료")
        print(f"      - session_id: {session_id}")
        print(f"      - resume_id (doc_id): {resume_id}")
        print(f"      - jd_id (doc_id): {jd_id}")
        print(f"      - portfolio_id (doc_id): {portfolio_id}")
        print(f"      - resume_len: {len(resume_text)} 자")
        print(f"      - jd_len: {len(jd_text)} 자")
        print(f"      - portfolio_len: {len(portfolio_text) if portfolio_text else 0} 자")
        print()
        
        return result


