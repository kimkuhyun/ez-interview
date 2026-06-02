# Python 3.11 베이스 이미지
FROM python:3.11-slim

# 작업 디렉토리 설정
WORKDIR /app

# 시스템 패키지 업데이트 및 필요한 패키지 설치
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    libpq-dev \
    portaudio19-dev \
    # Playwright 의존성
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    # 폰트 (한글 렌더링 + PDF 생성용)
    fonts-noto-cjk \
    fonts-noto-cjk-extra \
    fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

# requirements.txt 복사 및 의존성 설치
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Playwright 브라우저 설치 및 환경변수 설정
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN playwright install chromium

# 애플리케이션 코드 복사
COPY . .

# 업로드 디렉토리 생성
RUN mkdir -p app/uploads/jds app/uploads/portfolio app/uploads/resumes temp_uploads

# 환경변수 설정 (기본값, .env로 오버라이드 가능)
ENV PYTHONUNBUFFERED=1
ENV FLASK_APP=app.main

# 포트 노출
EXPOSE 5000

# 애플리케이션 실행 (gunicorn + eventlet for SocketIO)
CMD ["gunicorn", "--worker-class", "eventlet", "-w", "1", "--bind", "0.0.0.0:5000", "app.main:app"]
