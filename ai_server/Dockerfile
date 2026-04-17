# ==========================================
# HQA AI Server - Dockerfile
# ==========================================
# AI 에이전트 & RAG 전용 서버 (CPU 집약적)
# ==========================================

FROM python:3.11-slim AS builder

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


FROM python:3.11-slim AS runtime

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    chromium \
    chromium-driver \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /install /usr/local

# 애플리케이션 코드 복사
COPY src/ ./src/
COPY ai_server/ ./ai_server/
COPY prompts/ ./prompts/
COPY config/ ./config/
COPY scripts/ ./scripts/

# 데이터/DB 디렉토리 생성
RUN mkdir -p /app/database /app/data/files /app/data/token /app/logs

# 비root 사용자
RUN groupadd -r hqa && useradd -r -g hqa -d /app hqa
RUN chown -R hqa:hqa /app
USER hqa

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    CHROME_BINARY=/usr/bin/chromium \
    CHROMEDRIVER=/usr/bin/chromedriver \
    ENV=production \
    PORT=8001

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8001/health || exit 1

EXPOSE 8001

CMD ["uvicorn", "ai_server.app:app", \
     "--host", "0.0.0.0", \
     "--port", "8001", \
     "--workers", "2"]
