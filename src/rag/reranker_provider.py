# 파일: src/rag/reranker_provider.py
"""
Reranker 프로바이더 추상화

로컬 Qwen3-Reranker와 외부 API(Cohere Rerank)를 동일한 인터페이스로 제공합니다.

사용법:
    provider = get_reranker_provider()
    results = provider.rerank(query, documents, top_k=5)
"""

from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Tuple

from src.rag.reranker import RerankResult

logger = logging.getLogger(__name__)


class RerankerProviderBase(ABC):
    """Reranker 프로바이더 추상 베이스"""

    @abstractmethod
    def rerank(
        self,
        query: str,
        documents: List[str],
        top_k: int = 5,
        instruction: Optional[str] = None,
    ) -> List[RerankResult]:
        """문서 재순위"""
        ...

    @abstractmethod
    def is_available(self) -> bool:
        """프로바이더 사용 가능 여부"""
        ...


class LocalQwen3RerankerProvider(RerankerProviderBase):
    """
    로컬 Qwen3-Reranker 프로바이더 (기존 로직 래핑)
    
    GPU 필요. 개발/연구 환경에 적합.
    """

    def __init__(self, **kwargs):
        self._reranker = None
        self._kwargs = kwargs

    def _get_reranker(self):
        if self._reranker is None:
            from src.rag.reranker import Qwen3Reranker
            self._reranker = Qwen3Reranker(**self._kwargs)
            self._reranker.load()
        return self._reranker

    def rerank(
        self,
        query: str,
        documents: List[str],
        top_k: int = 5,
        instruction: Optional[str] = None,
    ) -> List[RerankResult]:
        reranker = self._get_reranker()
        return reranker.rerank(
            query=query,
            documents=documents,
            top_k=top_k,
            instruction=instruction,
        )

    def is_available(self) -> bool:
        try:
            import torch
            return True
        except ImportError:
            return False


class CohereRerankerProvider(RerankerProviderBase):
    """
    Cohere Rerank API 프로바이더
    
    GPU 불필요. 프로덕션 환경에 적합.
    비용: Cohere API 과금 기준 적용 (1000 검색당 ~$1)
    
    참고: https://docs.cohere.com/reference/rerank
    """

    def __init__(
        self,
        api_key: str = "",
        model: str = "rerank-multilingual-v3.0",
    ):
        self.api_key = api_key or os.getenv("COHERE_API_KEY", "")
        self.model = model

    def rerank(
        self,
        query: str,
        documents: List[str],
        top_k: int = 5,
        instruction: Optional[str] = None,
    ) -> List[RerankResult]:
        """Cohere Rerank API 호출"""
        import requests

        if not self.api_key:
            raise ValueError("COHERE_API_KEY가 설정되지 않았습니다.")

        if not documents:
            return []

        logger.info(f"🔄 Cohere Rerank: {len(documents)}개 문서, top_k={top_k}")

        response = requests.post(
            "https://api.cohere.ai/v1/rerank",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.model,
                "query": query,
                "documents": documents,
                "top_n": top_k,
                "return_documents": False,
            },
        )

        if response.status_code != 200:
            raise RuntimeError(f"Cohere API 오류 ({response.status_code}): {response.text[:300]}")

        data = response.json()
        results = []

        for item in data.get("results", []):
            idx = item["index"]
            results.append(RerankResult(
                content=documents[idx],
                score=item["relevance_score"],
                original_index=idx,
                metadata={"provider": "cohere"},
            ))

        # 점수 내림차순 정렬
        results.sort(key=lambda x: x.score, reverse=True)
        return results[:top_k]

    def is_available(self) -> bool:
        return bool(self.api_key)


def get_reranker_provider(provider: Optional[str] = None, **kwargs) -> RerankerProviderBase:
    """
    설정에 따라 적절한 Reranker 프로바이더 반환
    
    Args:
        provider: "local" 또는 "cohere" (None이면 환경변수에서 결정)
        **kwargs: 프로바이더별 추가 인자
    """
    if provider is None:
        provider = os.getenv("RERANKER_PROVIDER", "local")

    if provider == "cohere":
        return CohereRerankerProvider(**kwargs)
    else:
        return LocalQwen3RerankerProvider(**kwargs)
