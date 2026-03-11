from pydantic_settings import BaseSettings
from functools import lru_cache

class Settings(BaseSettings):
    # App Settings
    PROJECT_NAME: str = "HQA RAG Pipeline"
    API_V1_STR: str = "/api/v1"
    
    # DB (PostgreSQL)
    POSTGRES_SERVER: str = "localhost"
    POSTGRES_USER: str = "postgres"
    POSTGRES_PASSWORD: str = "postgres"
    POSTGRES_DB: str = "hqa_db"
    POSTGRES_PORT: str = "5432"

    @property
    def sync_database_url(self) -> str:
        return f"postgresql://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@{self.POSTGRES_SERVER}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        
    @property
    def async_database_url(self) -> str:
        return f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@{self.POSTGRES_SERVER}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"

    # Redis (Message Queue & Cache)
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0
    
    @property
    def redis_url(self) -> str:
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"
        
    # ChromaDB (Vector Store)
    CHROMA_PERSIST_DIRECTORY: str = "./chroma_db"
    
    # External APIs
    NAVER_CLIENT_ID: str = ""
    NAVER_CLIENT_SECRET: str = ""
    DART_API_KEY: str = ""
    OPENAI_API_KEY: str = ""

    class Config:
        env_file = ".env"
        case_sensitive = True

@lru_cache()
def get_settings():
    return Settings()
