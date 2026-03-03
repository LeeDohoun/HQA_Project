# 파일: src/rag/embeddings.py
"""
임베딩 모델 관리 모듈 (Ollama 기반)
- Ollama 서버의 임베딩 모델을 사용
- 기본 모델: nomic-embed-text (다국어/한국어 지원)
- 설정: .env 파일의 OLLAMA_BASE_URL, OLLAMA_EMBED_MODEL
"""

import os
from typing import List, Dict, Optional, Callable
from abc import ABC, abstractmethod

from langchain_ollama import OllamaEmbeddings
from langchain_core.embeddings import Embeddings
from dotenv import load_dotenv

load_dotenv()


class BaseEmbedding(ABC):
    """임베딩 기본 인터페이스"""
    
    @abstractmethod
    def embed_text(self, text: str) -> List[float]:
        """단일 텍스트 임베딩"""
        pass
    
    @abstractmethod
    def embed_texts(self, texts: List[str]) -> List[List[float]]:
        """다중 텍스트 임베딩"""
        pass
    
    @abstractmethod
    def embed_query(self, query: str) -> List[float]:
        """쿼리 임베딩 (검색용)"""
        pass


class OllamaEmbedding(BaseEmbedding):
    """
    Ollama 기반 임베딩 모델
    
    특징:
    - Ollama 서버에서 로컬 실행
    - nomic-embed-text: 768차원, 다국어 지원
    - snowflake-arctic-embed: 1024차원 (Ollama에서 사용 가능 시)
    - bge-m3: 1024차원, 다국어 (Ollama에서 사용 가능 시)
    """
    
    def __init__(
        self,
        model_name: str = "nomic-embed-text",
        base_url: Optional[str] = None
    ):
        """
        Args:
            model_name: Ollama 임베딩 모델명
            base_url: Ollama 서버 URL (None이면 환경변수 또는 기본값 사용)
        """
        self.base_url = base_url or os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
        self.model_name = model_name
        
        print(f"⚙️ Ollama 임베딩 모델 로딩: {model_name} ({self.base_url})")
        
        self.model = OllamaEmbeddings(
            model=model_name,
            base_url=self.base_url,
        )
        
        print(f"✅ Ollama 임베딩 모델 준비 완료: {model_name}")
    
    def embed_text(self, text: str) -> List[float]:
        """단일 텍스트 임베딩 (문서용)"""
        return self.model.embed_query(text)
    
    def embed_texts(self, texts: List[str]) -> List[List[float]]:
        """다중 텍스트 임베딩 (문서용)"""
        return self.model.embed_documents(texts)
    
    def embed_query(self, query: str) -> List[float]:
        """쿼리 임베딩 (검색용)"""
        return self.model.embed_query(query)
    
    def embed_queries(self, queries: List[str]) -> List[List[float]]:
        """다중 쿼리 임베딩"""
        return [self.model.embed_query(q) for q in queries]
    
    def get_langchain_embeddings(self) -> Embeddings:
        """LangChain 호환 임베딩 객체 반환"""
        return self.model


# 하위 호환성을 위한 별칭
SnowflakeArcticEmbedding = OllamaEmbedding
TextEmbedding = OllamaEmbedding
KoreanTextEmbedding = OllamaEmbedding


class EmbeddingManager:
    """임베딩 모델 통합 관리 클래스 (Ollama 기반)"""
    
    # 사전 정의된 Ollama 임베딩 모델 목록
    MODELS = {
        # 기본값: 다국어/한국어 지원 모델
        "default": "nomic-embed-text",
        "korean": "nomic-embed-text",
        # Ollama에서 사용 가능한 임베딩 모델들
        "nomic": "nomic-embed-text",
        "snowflake-ko": "snowflake-arctic-embed",
        "bge-m3": "bge-m3",
        "mxbai": "mxbai-embed-large",
        "all-minilm": "all-minilm",
        # 경량 모델
        "small": "nomic-embed-text",
    }
    
    def __init__(
        self,
        model_type: str = "default",
        use_multimodal: bool = False,  # 레거시 호환성용 (무시됨)
        device: Optional[str] = None,  # 레거시 호환성용 (무시됨)
        base_url: Optional[str] = None
    ):
        """
        Args:
            model_type: 모델 타입 ("default", "korean", "nomic", "bge-m3" 등)
            use_multimodal: 레거시 호환성용 (무시됨)
            device: 레거시 호환성용 (무시됨)
            base_url: Ollama 서버 URL
        """
        self.model_type = model_type
        self.base_url = base_url
        
        model_name = os.getenv("OLLAMA_EMBED_MODEL") or self.MODELS.get(model_type, self.MODELS["default"])
        
        self.text_embedding = OllamaEmbedding(
            model_name=model_name,
            base_url=base_url
        )
    
    def get_text_embedding(self):
        """텍스트 임베딩 객체 반환"""
        return self.text_embedding
    
    def get_langchain_embeddings(self) -> Embeddings:
        """LangChain 호환 임베딩 객체 반환"""
        return self.text_embedding.get_langchain_embeddings()
    
    def embed_text(self, text: str) -> List[float]:
        """텍스트 임베딩"""
        return self.text_embedding.embed_text(text)
    
    def embed_texts(self, texts: List[str]) -> List[List[float]]:
        """다중 텍스트 임베딩"""
        return self.text_embedding.embed_texts(texts)
    
    def embed_query(self, query: str) -> List[float]:
        """쿼리 임베딩 (검색 최적화)"""
        return self.text_embedding.embed_query(query)
    
    @property
    def is_multimodal(self) -> bool:
        """멀티모달 모드 여부 (항상 False)"""
        return False
    
    @classmethod
    def available_models(cls) -> Dict:
        """사용 가능한 모델 목록 반환"""
        return cls.MODELS.copy()
