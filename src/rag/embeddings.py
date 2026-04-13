# 파일: src/rag/embeddings.py
"""
임베딩 모델 관리 모듈
- 기본 모델: Jina Embeddings v5 (jinaai/jina-embeddings-v5-text-small) - Hugging Face
- 레거시 지원: Ollama (nomic-embed-text 등)
"""

import os
from typing import List, Dict, Optional, Callable
from abc import ABC, abstractmethod

from langchain_core.embeddings import Embeddings
from src.config.settings import load_project_env

load_project_env()


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
    
    @abstractmethod
    def get_langchain_embeddings(self) -> Embeddings:
        """LangChain 호환 임베딩 객체 반환"""
        pass


class JinaEmbedding(BaseEmbedding):
    """
    Jina Embeddings v5 (Hugging Face)
    
    특징:
    - PyTorch 로컬 구동 (Hugging Face)
    - Jina v5 아키텍처 (long-context, 다국어/한국어 우수)
    - 기본 모델: jinaai/jina-embeddings-v5-text-small (매우 가볍고 빠름)
    """
    
    def __init__(self, model_name: str = "jinaai/jina-embeddings-v5-text-small"):
        from langchain_huggingface import HuggingFaceEmbeddings
        print(f"⚙️ Jina 임베딩 모델 로딩: {model_name} (Hugging Face)")
        
        # Jina v5는 trust_remote_code=True가 필수입니다
        model_kwargs = {'trust_remote_code': True}
        encode_kwargs = {'normalize_embeddings': True}
        
        self.model = HuggingFaceEmbeddings(
            model_name=model_name,
            model_kwargs=model_kwargs,
            encode_kwargs=encode_kwargs
        )
        print(f"✅ Jina 임베딩 모델 준비 완료")
        
    def embed_text(self, text: str) -> List[float]:
        return self.model.embed_query(text)
    
    def embed_texts(self, texts: List[str]) -> List[List[float]]:
        return self.model.embed_documents(texts)
    
    def embed_query(self, query: str) -> List[float]:
        return self.model.embed_query(query)
        
    def get_langchain_embeddings(self) -> Embeddings:
        return self.model


class OllamaEmbedding(BaseEmbedding):
    """Ollama 기반 레거시 호환 임베딩 (nomic-embed-text)"""
    
    def __init__(self, model_name: str = "nomic-embed-text", base_url: Optional[str] = None):
        from langchain_ollama import OllamaEmbeddings
        self.base_url = base_url or os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
        self.model_name = model_name
        print(f"⚙️ Ollama 임베딩 모델 로딩: {model_name} ({self.base_url})")
        self.model = OllamaEmbeddings(model=model_name, base_url=self.base_url)
        print(f"✅ Ollama 임베딩 준비 완료")
    
    def embed_text(self, text: str) -> List[float]:
        return self.model.embed_query(text)
    
    def embed_texts(self, texts: List[str]) -> List[List[float]]:
        return self.model.embed_documents(texts)
    
    def embed_query(self, query: str) -> List[float]:
        return self.model.embed_query(query)
        
    def get_langchain_embeddings(self) -> Embeddings:
        return self.model


# 하위 호환성을 위한 별칭
SnowflakeArcticEmbedding = JinaEmbedding
TextEmbedding = JinaEmbedding
KoreanTextEmbedding = JinaEmbedding


class EmbeddingManager:
    """임베딩 모델 통합 관리 클래스"""
    
    MODELS = {
        # 기본값을 Jina v5로 변경
        "default": "jinaai/jina-embeddings-v5-text-small",
        "korean": "jinaai/jina-embeddings-v5-text-small",
        "jina-small": "jinaai/jina-embeddings-v5-text-small",
        # 기존 Ollama 레거시 지원
        "nomic": "nomic-embed-text",
        "snowflake-ko": "snowflake-arctic-embed",
    }
    
    def __init__(
        self,
        model_type: str = "default",
        use_multimodal: bool = False,
        device: Optional[str] = None,
        base_url: Optional[str] = None
    ):
        self.model_type = model_type
        
        model_name = os.getenv("EMBED_MODEL") or self.MODELS.get(model_type, self.MODELS["default"])
        
        if model_name.startswith("jinaai/"):
            self.text_embedding = JinaEmbedding(model_name=model_name)
        else:
            self.text_embedding = OllamaEmbedding(model_name=model_name, base_url=base_url)
    
    def get_text_embedding(self):
        return self.text_embedding
    
    def get_langchain_embeddings(self) -> Embeddings:
        return self.text_embedding.get_langchain_embeddings()
    
    def embed_text(self, text: str) -> List[float]:
        return self.text_embedding.embed_text(text)
    
    def embed_texts(self, texts: List[str]) -> List[List[float]]:
        return self.text_embedding.embed_texts(texts)
    
    def embed_query(self, query: str) -> List[float]:
        return self.text_embedding.embed_query(query)
    
    @property
    def is_multimodal(self) -> bool:
        return False
    
    @classmethod
    def available_models(cls) -> Dict:
        return cls.MODELS.copy()
