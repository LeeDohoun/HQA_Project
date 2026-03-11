# ==========================================
# HQA API - Production Dockerfile
# ==========================================
# Multi-stage build for minimal image size
# CPU-only (GPU 의존성 제거)
# ==========================================

# ── Stage 1: Dependencies ──
FROM python:3.11-slim AS builder

WORKDIR /app

# 시스템 패키지
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Python 패키지 설치
COPY requirements-prod.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements-prod.txt


# ── Stage 2: Runtime ──
FROM python:3.11-slim AS runtime

WORKDIR /app

# 런타임 시스템 패키지
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 빌드된 패키지 복사
COPY --from=builder /install /usr/local

# 애플리케이션 코드 복사
COPY . .

# 데이터/DB 디렉토리 생성
RUN mkdir -p /app/database /app/data/files /app/data/token /app/logs

# 비root 사용자
RUN groupadd -r hqa && useradd -r -g hqa -d /app hqa
RUN chown -R hqa:hqa /app
USER hqa

# 환경변수
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    ENV=production \
    PORT=8000

# 헬스체크
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:${PORT}/health || exit 1

# 포트 노출
EXPOSE ${PORT}

# 서버 실행
CMD ["uvicorn", "backend.app:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--workers", "4", \
     "--proxy-headers", \
     "--forwarded-allow-ips", "*"]
