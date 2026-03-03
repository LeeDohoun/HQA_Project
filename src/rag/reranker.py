# 파일: src/rag/reranker.py
"""
Sentence-Transformers CrossEncoder 기반 Reranker 모듈
- CrossEncoder를 사용한 Query-Document 관련성 점수 계산
- 검색 결과 재순위 지정
- 기본 모델: cross-encoder/ms-marco-MiniLM-L-6-v2 (빠르고 정확)
"""

import os
from typing import List, Dict, Tuple, Optional, Any
from dataclasses import dataclass
import logging

logger = logging.getLogger(__name__)


@dataclass
class RerankResult:
    """리랭킹 결과 데이터 클래스"""
    content: str
    score: float
    original_index: int
    metadata: Dict = None
    
    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


class CrossEncoderReranker:
    """
    Sentence-Transformers CrossEncoder 기반 문서 재순위 클래스
    
    특징:
    - CrossEncoder로 Query-Document 쌍의 관련성 점수 직접 계산
    - LLM 프롬프트 대비 빠르고 일관된 점수 산출
    - 다국어/한국어 지원 모델 선택 가능
    - 배치 처리 지원
    """
    
    # 사전 정의된 CrossEncoder 모델
    MODELS = {
        "default": "cross-encoder/ms-marco-MiniLM-L-6-v2",
        "small": "cross-encoder/ms-marco-MiniLM-L-6-v2",
        "medium": "cross-encoder/ms-marco-MiniLM-L-12-v2",
        "large": "cross-encoder/ms-marco-TinyBERT-L-2-v2",
        "multilingual": "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1",
        "korean": "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1",
    }
    
    # 작업별 기본 Instruction (레거시 호환성, CrossEncoder에서는 무시됨)
    DEFAULT_INSTRUCTIONS = {
        "retrieval": "Given a web search query, retrieve relevant passages that answer the query",
        "qa": "Given a question, retrieve passages that contain the answer to the question",
        "finance": "Given a financial query, retrieve relevant financial documents, reports, or news that answer the query",
        "code": "Given a code-related query, retrieve relevant code snippets or documentation",
        "semantic": "Given a query, retrieve semantically similar passages",
    }
    
    def __init__(
        self,
        model_name: str = "default",
        device: Optional[str] = None,
        max_length: int = 512,
        batch_size: int = 32,
        **kwargs,  # 레거시 호환성 (base_url, use_fp16 등 무시)
    ):
        """
        Args:
            model_name: 모델 이름 또는 키 (default, small, medium, large, multilingual, korean)
                        또는 HuggingFace 모델 경로 직접 지정 가능
            device: 장치 (cuda, cpu, None=자동)
            max_length: 최대 토큰 길이 (기본값: 512)
            batch_size: 배치 크기 (기본값: 32)
        """
        self.model_id = os.getenv("RERANKER_MODEL") or self.MODELS.get(model_name, model_name)
        self.max_length = max_length
        self.default_batch_size = batch_size
        self.device = device
        
        self.model = None
        self._is_loaded = False
    
    def load(self):
        """모델 로드 (지연 로딩)"""
        if self._is_loaded:
            return
        
        try:
            from sentence_transformers import CrossEncoder
            
            logger.info(f"Loading CrossEncoder Reranker: {self.model_id}")
            print(f"⚙️ CrossEncoder 리랭커 로딩: {self.model_id}")
            
            self.model = CrossEncoder(
                self.model_id,
                max_length=self.max_length,
                device=self.device,
            )
            
            self._is_loaded = True
            logger.info(f"CrossEncoder Reranker loaded: {self.model_id}")
            print(f"✅ CrossEncoder 리랭커 로딩 완료: {self.model_id}")
            
        except ImportError:
            raise ImportError(
                "sentence-transformers가 설치되지 않았습니다.\n"
                "설치: pip install sentence-transformers"
            )
        except Exception as e:
            logger.error(f"Failed to load CrossEncoder Reranker: {e}")
            raise
    
    def rerank(
        self,
        query: str,
        documents: List[str],
        instruction: Optional[str] = None,
        task_type: str = "retrieval",
        top_k: Optional[int] = None,
        return_scores: bool = True,
        batch_size: Optional[int] = None,
    ) -> List[RerankResult]:
        """
        문서 리랭킹 수행
        
        Args:
            query: 검색 쿼리
            documents: 문서 리스트
            instruction: 레거시 호환성 (CrossEncoder에서는 무시됨)
            task_type: 레거시 호환성 (CrossEncoder에서는 무시됨)
            top_k: 반환할 상위 결과 수 (None이면 전체 반환)
            return_scores: 점수 포함 여부
            batch_size: 배치 크기 (None이면 기본값 사용)
            
        Returns:
            RerankResult 리스트 (점수 내림차순 정렬)
        """
        self.load()
        
        if not documents:
            return []
        
        batch_size = batch_size or self.default_batch_size
        
        # Query-Document 쌍 생성
        pairs = [[query, doc] for doc in documents]
        
        # CrossEncoder로 점수 계산 (배치 처리)
        scores = self.model.predict(
            pairs,
            batch_size=batch_size,
            show_progress_bar=False,
        )
        
        # float 리스트로 변환
        all_scores = [float(s) for s in scores]
        
        # 결과 생성
        results = [
            RerankResult(
                content=doc,
                score=score,
                original_index=idx,
            )
            for idx, (doc, score) in enumerate(zip(documents, all_scores))
        ]
        
        # 점수 기준 정렬
        results.sort(key=lambda x: x.score, reverse=True)
        
        # top_k 적용
        if top_k is not None:
            results = results[:top_k]
        
        return results
    
    def rerank_with_metadata(
        self,
        query: str,
        documents: List[Dict[str, Any]],
        content_key: str = "content",
        instruction: Optional[str] = None,
        task_type: str = "retrieval",
        top_k: Optional[int] = None,
        batch_size: Optional[int] = None,
    ) -> List[RerankResult]:
        """
        메타데이터를 포함한 문서 리랭킹
        
        Args:
            query: 검색 쿼리
            documents: 문서 딕셔너리 리스트 (content_key 필드 필요)
            content_key: 문서 내용 키
            instruction: 레거시 호환성 (무시됨)
            task_type: 레거시 호환성 (무시됨)
            top_k: 반환할 상위 결과 수
            batch_size: 배치 크기
            
        Returns:
            RerankResult 리스트 (메타데이터 포함)
        """
        self.load()
        
        if not documents:
            return []
        
        # 문서 내용 추출
        contents = [doc.get(content_key, "") for doc in documents]
        
        # 리랭킹 수행
        results = self.rerank(
            query=query,
            documents=contents,
            top_k=None,  # 일단 전체 결과
            batch_size=batch_size,
        )
        
        # 메타데이터 추가
        for result in results:
            original_doc = documents[result.original_index]
            result.metadata = {
                k: v for k, v in original_doc.items()
                if k != content_key
            }
        
        # top_k 적용
        if top_k is not None:
            results = results[:top_k]
        
        return results
    
    def compute_score(
        self,
        query: str,
        document: str,
        instruction: Optional[str] = None,
        task_type: str = "retrieval",
    ) -> float:
        """
        단일 Query-Document 쌍의 관련성 점수 계산
        
        Args:
            query: 검색 쿼리
            document: 문서
            instruction: 레거시 호환성 (무시됨)
            task_type: 레거시 호환성 (무시됨)
            
        Returns:
            관련성 점수
        """
        results = self.rerank(
            query=query,
            documents=[document],
        )
        
        return results[0].score if results else 0.0


# 하위 호환성을 위한 별칭
OllamaReranker = CrossEncoderReranker
Qwen3Reranker = CrossEncoderReranker


class RerankerManager:
    """리랭커 관리 클래스 (싱글톤 패턴)"""
    
    _instance = None
    _reranker = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    @classmethod
    def get_reranker(
        cls,
        model_name: str = "default",
        **kwargs
    ) -> CrossEncoderReranker:
        """
        리랭커 인스턴스 반환
        
        Args:
            model_name: 모델 이름
            **kwargs: CrossEncoderReranker 초기화 인자
            
        Returns:
            CrossEncoderReranker 인스턴스
        """
        if cls._reranker is None:
            cls._reranker = CrossEncoderReranker(model_name=model_name, **kwargs)
        return cls._reranker
    
    @classmethod
    def reset(cls):
        """리랭커 인스턴스 초기화"""
        if cls._reranker is not None:
            del cls._reranker
            cls._reranker = None


def rerank_documents(
    query: str,
    documents: List[str],
    top_k: int = 10,
    task_type: str = "finance",
    model_name: str = "default",
) -> List[RerankResult]:
    """
    문서 리랭킹 편의 함수
    
    Args:
        query: 검색 쿼리
        documents: 문서 리스트
        top_k: 반환할 상위 결과 수
        task_type: 작업 유형 (레거시 호환성)
        model_name: 모델 이름
        
    Returns:
        RerankResult 리스트
        
    Example:
        >>> docs = ["삼성전자 주가 분석...", "애플 실적 발표...", ...]
        >>> results = rerank_documents("삼성전자 실적", docs, top_k=5)
        >>> for r in results:
        ...     print(f"[{r.score:.3f}] {r.content[:50]}...")
    """
    reranker = RerankerManager.get_reranker(model_name=model_name)
    return reranker.rerank(
        query=query,
        documents=documents,
        task_type=task_type,
        top_k=top_k,
    )
