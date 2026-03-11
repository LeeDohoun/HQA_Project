# 파일: backend/config.py
"""
프로덕션 설정 관리

환경변수 기반 설정 (Pydantic Settings)
- 로컬 개발: .env 파일
- 프로덕션: 환경변수 또는 AWS Secrets Manager
"""

from __future__ import annotations

import os
from enum import Enum
from functools import lru_cache
from typing import List, Optional

from pydantic_settings import BaseSettings
from pydantic import Field, field_validator


class Environment(str, Enum):
    LOCAL = "local"
    DEV = "dev"
    STAGING = "staging"
    PRODUCTION = "production"


class Settings(BaseSettings):
    """애플리케이션 설정"""

    # ── 환경 ──
    ENV: Environment = Environment.LOCAL
    DEBUG: bool = False
    APP_NAME: str = "HQA API"
    APP_VERSION: str = "1.0.0"

    # ── 서버 ──
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    WORKERS: int = 4
    CORS_ORIGINS: List[str] = Field(default=["http://localhost:3000", "http://localhost:8501"])

    # ── API 키 (필수) ──
    GOOGLE_API_KEY: str = ""

    # ── API 키 (선택) ──
    DART_API_KEY: str = ""
    TAVILY_API_KEY: str = ""
    KIS_APP_KEY: str = ""
    KIS_APP_SECRET: str = ""
    KIS_ACCOUNT_NO: str = ""

    # ── OCR 프로바이더 ──
    # "local" = PaddleOCR 로컬, "upstage" = Upstage Document AI API
    OCR_PROVIDER: str = "local"
    UPSTAGE_API_KEY: str = ""
    UPSTAGE_OCR_URL: str = "https://api.upstage.ai/v1/document-ai/ocr"

    # ── Reranker 프로바이더 ──
    # "local" = Qwen3 로컬, "cohere" = Cohere Rerank API
    RERANKER_PROVIDER: str = "local"
    COHERE_API_KEY: str = ""

    # ── 데이터베이스 ──
    # SQLite (로컬) 또는 PostgreSQL (프로덕션)
    DATABASE_URL: str = "sqlite+aiosqlite:///./database/hqa.db"

    # ── Redis (Task Queue & Cache) ──
    REDIS_URL: str = "redis://localhost:6379/0"

    # ── 벡터 DB ──
    # "chroma" = 로컬 ChromaDB, "pinecone" = Pinecone 관리형
    VECTOR_DB_PROVIDER: str = "chroma"
    CHROMA_PERSIST_DIR: str = "./database/chroma_db"
    PINECONE_API_KEY: str = ""
    PINECONE_INDEX_NAME: str = "hqa-stocks"

    # ── LangSmith 모니터링 ──
    LANGCHAIN_TRACING_V2: bool = False
    LANGCHAIN_API_KEY: str = ""
    LANGCHAIN_PROJECT: str = "hqa-production"

    # ── 보안 ──
    SECRET_KEY: str = "change-me-in-production"
    API_KEY_HEADER: str = "X-API-Key"
    RATE_LIMIT_PER_MINUTE: int = 30

    # ── AWS Secrets Manager (프로덕션) ──
    USE_AWS_SECRETS: bool = False
    AWS_SECRET_NAME: str = "hqa/api-keys"
    AWS_REGION: str = "ap-northeast-2"

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def parse_cors_origins(cls, v):
        if isinstance(v, str):
            return [origin.strip() for origin in v.split(",")]
        return v

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": True,
    }


@lru_cache()
def get_settings() -> Settings:
    """싱글턴 설정 인스턴스 반환"""
    settings = Settings()

    # AWS Secrets Manager 사용 시 API 키 오버라이드
    if settings.USE_AWS_SECRETS:
        _load_aws_secrets(settings)

    # LangSmith 환경변수 설정
    if settings.LANGCHAIN_TRACING_V2:
        os.environ["LANGCHAIN_TRACING_V2"] = "true"
        os.environ["LANGCHAIN_API_KEY"] = settings.LANGCHAIN_API_KEY
        os.environ["LANGCHAIN_PROJECT"] = settings.LANGCHAIN_PROJECT

    return settings


def _load_aws_secrets(settings: Settings) -> None:
    """AWS Secrets Manager에서 비밀값 로드"""
    try:
        import boto3
        import json

        client = boto3.client("secretsmanager", region_name=settings.AWS_REGION)
        response = client.get_secret_value(SecretId=settings.AWS_SECRET_NAME)
        secrets = json.loads(response["SecretString"])

        # 키 매핑
        key_mapping = {
            "GOOGLE_API_KEY": "GOOGLE_API_KEY",
            "DART_API_KEY": "DART_API_KEY",
            "TAVILY_API_KEY": "TAVILY_API_KEY",
            "KIS_APP_KEY": "KIS_APP_KEY",
            "KIS_APP_SECRET": "KIS_APP_SECRET",
            "UPSTAGE_API_KEY": "UPSTAGE_API_KEY",
            "COHERE_API_KEY": "COHERE_API_KEY",
            "PINECONE_API_KEY": "PINECONE_API_KEY",
        }

        for env_key, secret_key in key_mapping.items():
            if secret_key in secrets:
                setattr(settings, env_key, secrets[secret_key])
                os.environ[env_key] = secrets[secret_key]

    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"AWS Secrets Manager 로드 실패: {e}")
