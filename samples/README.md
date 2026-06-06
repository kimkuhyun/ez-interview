# EZ-Interview Sample Data

평가자가 `git clone` 직후 바로 면접 흐름을 검증해 볼 수 있도록 동봉한
공개·합성 샘플 파일입니다.

| 파일 | 용도 | 데이터 출처 / 라이선스 |
|---|---|---|
| `Richard Hendriks_이력서.pdf` | 후보자 이력서 업로드 (`POST /api/candidates/upload`) | [JSON Resume 공식 schema sample](https://github.com/jsonresume/resume-schema) — **MIT License**. Richard Hendriks 는 HBO Silicon Valley 시즌 1 의 패러디 **가상 인물** |
| `Sample_BackendEngineer_JD.pdf` | 채용 공고 업로드 (`POST /api/positions`) | 가상 회사 "Acme Tech" — **합성 데이터**, 실존 회사 무관 |
| `build_sample_resume.py` | 위 이력서 PDF 재빌드 (JSON Resume sample 을 fetch → reportlab) | — |
| `build_sample_jd.py` | 위 JD PDF 재빌드 | — |

## 재빌드

```bash
pip install reportlab requests
python samples/build_sample_resume.py   # JSON Resume 최신 sample 로 재빌드
python samples/build_sample_jd.py
```

## 업로드 파일명 규약

EZ-Interview 는 업로드 파일명에서 후보자 이름을 파싱하므로 패턴을 지켜야 합니다:

- `{이름}_이력서.{pdf|doc|docx}` — 이력서
- `{이름}_포트폴리오.{pdf|doc|docx}` — 포트폴리오

샘플 이력서 파일명이 `Richard Hendriks_이력서.pdf` 인 이유입니다.

## 자신의 이력서를 쓰고 싶다면

이 폴더에 같은 패턴으로 PDF 를 두고 업로드하면 됩니다. 개인정보는
fork 에 commit 하지 않도록 주의하세요.
