# 파일: src/rag/embeddings.py
"""
임베딩 모델 관리 모듈 (텍스트 전용)
- PaddleOCR-VL이 모든 문서를 텍스트로 변환하므로 텍스트 임베딩만 사용
- 기본 모델: dragonkue/snowflake-arctic-embed-l-v2.0-ko (한국어 SOTA)
"""

from typing import List, Dict, Optional, Callable
from abc import ABC, abstractmethod

from langchain_huggingface import HuggingFaceEmbeddings
from langchain_core.embeddings import Embeddings


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


class SnowflakeArcticEmbedding(BaseEmbedding):
    """
    Snowflake Arctic Embed 한국어 모델
    
    특징:
    - 한국어 검색 벤치마크 SOTA
    - 1024 차원 출력
    - 쿼리에 prompt_name="query" 사용
    - 금융/법률/의료/공공 문서에 최적화
    """
    
    def __init__(
        self,
        model_name: str = "dragonkue/snowflake-arctic-embed-l-v2.0-ko",
        device: Optional[str] = None
    ):
        """
        Args:
            model_name: HuggingFace 모델명
            device: 실행 디바이스 ("cuda", "cpu", None=자동)
        """
        print(f"⚙️ 한국어 임베딩 모델 로딩: {model_name}")
        
        try:
            from sentence_transformers import SentenceTransformer
            
            # SentenceTransformer 직접 사용 (prompt_name 지원)
            self.model = SentenceTransformer(model_name, device=device)
            self.model_name = model_name
            self._use_sentence_transformers = True
            print(f"✅ SentenceTransformer 모드로 로딩 완료 (1024 dim)")
            
        except ImportError:
            # fallback: HuggingFaceEmbeddings
            print("⚠️ sentence-transformers 미설치. HuggingFaceEmbeddings 사용")
            self.model = HuggingFaceEmbeddings(model_name=model_name)
            self.model_name = model_name
            self._use_sentence_transformers = False
    
    def embed_text(self, text: str) -> List[float]:
        """단일 텍스트 임베딩 (문서용)"""
        if self._use_sentence_transformers:
            return self.model.encode(text).tolist()
        return self.model.embed_query(text)
    
    def embed_texts(self, texts: List[str]) -> List[List[float]]:
        """다중 텍스트 임베딩 (문서용)"""
        if self._use_sentence_transformers:
            return self.model.encode(texts).tolist()
        return self.model.embed_documents(texts)
    
    def embed_query(self, query: str) -> List[float]:
        """쿼리 임베딩 (검색용) - prompt_name="query" 사용"""
        if self._use_sentence_transformers:
            return self.model.encode(query, prompt_name="query").tolist()
        return self.model.embed_query(query)
    
    def embed_queries(self, queries: List[str]) -> List[List[float]]:
        """다중 쿼리 임베딩"""
        if self._use_sentence_transformers:
            return self.model.encode(queries, prompt_name="query").tolist()
        return [self.embed_query(q) for q in queries]
    
    def get_langchain_embeddings(self) -> Embeddings:
        """LangChain 호환 임베딩 객체 반환"""
        return SnowflakeArcticLangChainWrapper(self)


class SnowflakeArcticLangChainWrapper(Embeddings):
    """LangChain 호환을 위한 래퍼 클래스"""
    
    def __init__(self, embedding: SnowflakeArcticEmbedding):
        self._embedding = embedding
    
    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        """문서 임베딩"""
        return self._embedding.embed_texts(texts)
    
    def embed_query(self, text: str) -> List[float]:
        """쿼리 임베딩 (prompt_name="query" 적용)"""
        return self._embedding.embed_query(text)


class TextEmbedding(BaseEmbedding):
    """일반 텍스트 임베딩 클래스 (레거시 호환)"""
    
    def __init__(self, model_name: str = "sentence-transformers/all-MiniLM-L6-v2"):
        """
        Args:
            model_name: HuggingFace 모델명
        """
        print(f"⚙️ 텍스트 임베딩 모델 로딩: {model_name}")
        self.model = HuggingFaceEmbeddings(model_name=model_name)
        self.model_name = model_name
    
    def embed_text(self, text: str) -> List[float]:
        """단일 텍스트 임베딩"""
        return self.model.embed_query(text)
    
    def embed_texts(self, texts: List[str]) -> List[List[float]]:
        """다중 텍스트 임베딩"""
        return self.model.embed_documents(texts)
    
    def embed_query(self, query: str) -> List[float]:
        """쿼리 임베딩"""
        return self.model.embed_query(query)
    
    def get_langchain_embeddings(self) -> HuggingFaceEmbeddings:
        """LangChain 호환 임베딩 객체 반환"""
        return self.model


class KoreanTextEmbedding(TextEmbedding):
    """한국어 특화 텍스트 임베딩 (레거시 호환)"""
    
    def __init__(self, model_name: str = "jhgan/ko-sbert-nli"):
        super().__init__(model_name)


class EmbeddingManager:
    """임베딩 모델 통합 관리 클래스"""
    
    # 사전 정의된 모델 목록
    MODELS = {
        # 기본값: 한국어 SOTA 모델
        "default": "dragonkue/snowflake-arctic-embed-l-v2.0-ko",
        "korean": "dragonkue/snowflake-arctic-embed-l-v2.0-ko",
        "snowflake-ko": "dragonkue/snowflake-arctic-embed-l-v2.0-ko",
        # 대안 모델들
        "bge-m3-ko": "dragonkue/BGE-m3-ko",
        "kure": "nlpai-lab/KURE-v1",
        "bge-m3": "BAAI/bge-m3",
        "multilingual": "intfloat/multilingual-e5-large",
        # 경량 모델
        "small": "sentence-transformers/all-MiniLM-L6-v2",
        "ko-sbert": "jhgan/ko-sbert-nli",
    }
    
    def __init__(
        self,
        model_type: str = "default",
        use_multimodal: bool = False,  # 레거시 호환성용 (무시됨)
        device: Optional[str] = None
    ):
        """
        Args:
            model_type: 모델 타입 ("default", "korean", "snowflake-ko", "bge-m3-ko", "kure" 등)
            use_multimodal: 레거시 호환성용 (무시됨)
            device: 실행 디바이스
        """
        self.model_type = model_type
        self.device = device
        
        model_name = self.MODELS.get(model_type, self.MODELS["default"])
        
        # Snowflake Arctic 모델인 경우 전용 클래스 사용
        if "snowflake-arctic" in model_name or "dragonkue" in model_name:
            self.text_embedding = SnowflakeArcticEmbedding(
                model_name=model_name,
                device=device
            )
        else:
            self.text_embedding = TextEmbedding(model_name=model_name)
    
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
