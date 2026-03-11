# 파일: src/rag/bm25_index.py
"""
BM25 키워드 검색 인덱스 관리 모듈

벡터 검색의 약점(정확한 금융 용어, 숫자, 약어 매칭)을 보완하기 위해
BM25 키워드 검색을 추가합니다.

- 벡터 검색: 의미적 유사도 (semantic) — "수익성이 좋은 기업" ↔ "ROE 높은 종목"
- BM25 검색: 키워드 매칭 (lexical)   — "PER", "EBITDA", "YOY 30%" 정확 매칭

두 검색을 Reciprocal Rank Fusion (RRF)으로 합산한 후 리랭커에 전달합니다.
"""

import json
import os
import re
import logging
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field

from langchain_core.documents import Document

logger = logging.getLogger(__name__)

# BM25 라이브러리 (선택적)
try:
    from rank_bm25 import BM25Okapi
    _BM25_AVAILABLE = True
except ImportError:
    _BM25_AVAILABLE = False
    logger.warning("rank_bm25 미설치 → BM25 키워드 검색 비활성화 (pip install rank-bm25)")


def _tokenize_korean(text: str) -> List[str]:
    """
    한국어 + 금융 용어 토크나이저
    
    형태소 분석기 없이 동작하는 경량 토크나이저.
    금융 약어(PER, ROE 등), 숫자+단위, 한글 형태소를 분리합니다.
    """
    if not text:
        return []
    
    # 소문자 변환 (영어 약어 통일)
    text = text.lower()
    
    # 금융 용어 보호 (분리되지 않도록)
    # 예: "per 12.5배" → "per", "12.5배"
    protected_terms = [
        "ebitda", "ebit", "per", "pbr", "psr", "pcr", "peg",
        "roe", "roa", "roic", "roce", "eps", "bps", "dps",
        "yoy", "qoq", "mom", "cagr", "wacc", "dcf", "fcf",
        "ev/ebitda", "ev/sales", "ev/fcf",
        "m&a", "ipo", "etf", "kospi", "kosdaq",
        "macd", "rsi", "bollinger", "stochastic",
    ]
    
    # 특수 패턴 추출
    tokens = []
    
    # 1. 숫자+단위 패턴: "12.5배", "3.2%", "1,200억", "50조"
    number_patterns = re.findall(
        r'[\-+]?\d[\d,]*\.?\d*\s*(?:배|%|억|조|만|원|주|달러|위안|엔|점|bp|bps)',
        text
    )
    tokens.extend([p.strip() for p in number_patterns])
    
    # 2. 영어 약어 / 금융 용어
    english_words = re.findall(r'[a-z][a-z/&]+[a-z]', text)
    tokens.extend(english_words)
    
    # 3. 한글 단어 (2글자 이상)
    korean_words = re.findall(r'[가-힣]{2,}', text)
    tokens.extend(korean_words)
    
    # 4. 단독 숫자 (4자리 이상 = 종목코드 등)
    standalone_numbers = re.findall(r'\b\d{4,}\b', text)
    tokens.extend(standalone_numbers)
    
    return tokens


@dataclass
class BM25Document:
    """BM25 인덱스용 문서 메타데이터"""
    doc_id: str              # 고유 ID (ChromaDB ID와 매칭)
    page_content: str        # 원본 텍스트
    metadata: Dict           # 메타데이터
    tokens: List[str] = field(default_factory=list)  # 토큰화된 텍스트


class BM25IndexManager:
    """
    BM25 키워드 검색 인덱스
    
    ChromaDB와 병행하여 키워드 기반 검색을 수행합니다.
    인덱스를 JSON 파일로 영속화하여 재시작 시에도 유지됩니다.
    
    Example:
        bm25 = BM25IndexManager(persist_path="./database/bm25_index.json")
        
        # 문서 추가
        bm25.add_documents([doc1, doc2, doc3])
        
        # 검색
        results = bm25.search("삼성전자 PER", k=10)
    """
    
    def __init__(
        self,
        persist_path: str = "./database/bm25_index.json",
        auto_save: bool = True,
        save_interval: int = 50,  # N개 추가마다 자동 저장
    ):
        """
        Args:
            persist_path: BM25 인덱스 저장 경로
            auto_save: 자동 저장 여부
            save_interval: 자동 저장 간격 (문서 수)
        """
        self.persist_path = persist_path
        self.auto_save = auto_save
        self.save_interval = save_interval
        
        # 코퍼스 데이터
        self._corpus: List[BM25Document] = []
        self._tokenized_corpus: List[List[str]] = []
        self._bm25: Optional['BM25Okapi'] = None
        
        # 중복 검사용 ID 세트
        self._indexed_ids: set = set()
        
        # 변경 카운터 (자동 저장용)
        self._changes_since_save = 0
        
        # 기존 인덱스 로드
        self._load_index()
    
    @property
    def is_available(self) -> bool:
        """BM25 사용 가능 여부"""
        return _BM25_AVAILABLE
    
    @property
    def corpus_size(self) -> int:
        """코퍼스 크기"""
        return len(self._corpus)
    
    def add_documents(self, documents: List[Document]) -> int:
        """
        Document 리스트를 BM25 인덱스에 추가
        
        Args:
            documents: LangChain Document 리스트
            
        Returns:
            실제 추가된 문서 수
        """
        if not _BM25_AVAILABLE:
            return 0
        
        added = 0
        for doc in documents:
            # 고유 ID 생성 (source + page_num + content hash)
            doc_id = self._generate_id(doc)
            
            if doc_id in self._indexed_ids:
                continue
            
            # 토큰화
            tokens = _tokenize_korean(doc.page_content)
            if not tokens:
                continue
            
            bm25_doc = BM25Document(
                doc_id=doc_id,
                page_content=doc.page_content,
                metadata=dict(doc.metadata) if doc.metadata else {},
                tokens=tokens,
            )
            
            self._corpus.append(bm25_doc)
            self._tokenized_corpus.append(tokens)
            self._indexed_ids.add(doc_id)
            added += 1
        
        if added > 0:
            # BM25 인덱스 재구성
            self._rebuild_bm25()
            self._changes_since_save += added
            
            # 자동 저장
            if self.auto_save and self._changes_since_save >= self.save_interval:
                self.save_index()
            
            logger.info(f"BM25 인덱스에 {added}개 문서 추가 (총 {self.corpus_size}개)")
        
        return added
    
    def add_texts(
        self,
        texts: List[str],
        metadatas: Optional[List[Dict]] = None
    ) -> int:
        """
        텍스트 리스트를 BM25 인덱스에 추가
        
        Args:
            texts: 텍스트 리스트
            metadatas: 메타데이터 리스트
            
        Returns:
            추가된 문서 수
        """
        docs = []
        for i, text in enumerate(texts):
            meta = metadatas[i] if metadatas and i < len(metadatas) else {}
            docs.append(Document(page_content=text, metadata=meta))
        
        return self.add_documents(docs)
    
    def search(
        self,
        query: str,
        k: int = 20,
    ) -> List[Tuple[Document, float]]:
        """
        BM25 키워드 검색
        
        Args:
            query: 검색 쿼리
            k: 반환할 결과 수
            
        Returns:
            (Document, score) 튜플 리스트 (점수 높은 순)
        """
        if not _BM25_AVAILABLE or self._bm25 is None or self.corpus_size == 0:
            return []
        
        # 쿼리 토큰화
        query_tokens = _tokenize_korean(query)
        if not query_tokens:
            return []
        
        # BM25 점수 계산
        scores = self._bm25.get_scores(query_tokens)
        
        # 상위 k개 추출
        top_indices = sorted(
            range(len(scores)),
            key=lambda i: scores[i],
            reverse=True
        )[:k]
        
        results = []
        for idx in top_indices:
            if scores[idx] <= 0:
                break  # 관련 없는 문서는 제외
            
            bm25_doc = self._corpus[idx]
            doc = Document(
                page_content=bm25_doc.page_content,
                metadata=bm25_doc.metadata,
            )
            results.append((doc, float(scores[idx])))
        
        return results
    
    def delete_by_source(self, source: str) -> int:
        """
        소스 기준으로 인덱스에서 문서 삭제
        
        Args:
            source: 소스 경로/이름
            
        Returns:
            삭제된 문서 수
        """
        if not _BM25_AVAILABLE:
            return 0
        
        indices_to_remove = [
            i for i, doc in enumerate(self._corpus)
            if doc.metadata.get("source") == source
        ]
        
        if not indices_to_remove:
            return 0
        
        # 역순으로 삭제 (인덱스 밀림 방지)
        for idx in sorted(indices_to_remove, reverse=True):
            removed = self._corpus.pop(idx)
            self._tokenized_corpus.pop(idx)
            self._indexed_ids.discard(removed.doc_id)
        
        # BM25 인덱스 재구성
        if self._corpus:
            self._rebuild_bm25()
        else:
            self._bm25 = None
        
        deleted = len(indices_to_remove)
        logger.info(f"BM25 인덱스에서 {deleted}개 문서 삭제 (source={source})")
        
        if self.auto_save:
            self.save_index()
        
        return deleted
    
    def clear(self):
        """전체 인덱스 초기화"""
        self._corpus.clear()
        self._tokenized_corpus.clear()
        self._indexed_ids.clear()
        self._bm25 = None
        self._changes_since_save = 0
        
        # 파일도 삭제
        if os.path.exists(self.persist_path):
            os.remove(self.persist_path)
        
        logger.info("BM25 인덱스 전체 초기화")
    
    def save_index(self):
        """인덱스를 파일로 저장"""
        if not self._corpus:
            return
        
        os.makedirs(os.path.dirname(self.persist_path) or ".", exist_ok=True)
        
        data = {
            "version": "1.0",
            "corpus_size": len(self._corpus),
            "documents": [
                {
                    "doc_id": doc.doc_id,
                    "page_content": doc.page_content,
                    "metadata": doc.metadata,
                }
                for doc in self._corpus
            ]
        }
        
        with open(self.persist_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=None)
        
        self._changes_since_save = 0
        logger.info(f"BM25 인덱스 저장 완료: {self.persist_path} ({len(self._corpus)}개 문서)")
    
    def _load_index(self):
        """파일에서 인덱스 로드"""
        if not os.path.exists(self.persist_path):
            logger.info("BM25 인덱스 파일 없음 → 새로 생성")
            return
        
        if not _BM25_AVAILABLE:
            logger.warning("rank_bm25 미설치 → 기존 인덱스 무시")
            return
        
        try:
            with open(self.persist_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            documents = data.get("documents", [])
            
            for doc_data in documents:
                tokens = _tokenize_korean(doc_data["page_content"])
                if not tokens:
                    continue
                
                bm25_doc = BM25Document(
                    doc_id=doc_data["doc_id"],
                    page_content=doc_data["page_content"],
                    metadata=doc_data.get("metadata", {}),
                    tokens=tokens,
                )
                
                self._corpus.append(bm25_doc)
                self._tokenized_corpus.append(tokens)
                self._indexed_ids.add(bm25_doc.doc_id)
            
            if self._corpus:
                self._rebuild_bm25()
            
            logger.info(
                f"BM25 인덱스 로드 완료: {self.persist_path} "
                f"({len(self._corpus)}개 문서)"
            )
            
        except Exception as e:
            logger.error(f"BM25 인덱스 로드 오류: {e}")
            self._corpus.clear()
            self._tokenized_corpus.clear()
            self._indexed_ids.clear()
    
    def _rebuild_bm25(self):
        """BM25 인덱스 재구성"""
        if not _BM25_AVAILABLE or not self._tokenized_corpus:
            self._bm25 = None
            return
        
        self._bm25 = BM25Okapi(self._tokenized_corpus)
    
    def _generate_id(self, doc: Document) -> str:
        """문서 고유 ID 생성"""
        import hashlib
        
        source = doc.metadata.get("source", "unknown")
        page = doc.metadata.get("page_num", 0)
        content_hash = hashlib.md5(
            doc.page_content[:200].encode("utf-8", errors="ignore")
        ).hexdigest()[:8]
        
        return f"{source}_{page}_{content_hash}"
    
    def get_stats(self) -> Dict:
        """통계 반환"""
        return {
            "bm25_available": _BM25_AVAILABLE,
            "corpus_size": self.corpus_size,
            "persist_path": self.persist_path,
            "index_built": self._bm25 is not None,
        }


def reciprocal_rank_fusion(
    vector_results: List[Tuple[Document, float]],
    bm25_results: List[Tuple[Document, float]],
    k: int = 60,
    vector_weight: float = 1.0,
    bm25_weight: float = 1.0,
) -> List[Tuple[Document, float]]:
    """
    Reciprocal Rank Fusion (RRF)으로 벡터 + BM25 결과 병합
    
    RRF 공식: score(d) = Σ  weight / (k + rank(d))
    
    Args:
        vector_results: 벡터 검색 결과 (Document, score) — score는 거리(낮을수록 좋음)
        bm25_results: BM25 검색 결과 (Document, score) — score는 관련도(높을수록 좋음)
        k: RRF 상수 (기본 60, 논문 권장값)
        vector_weight: 벡터 검색 가중치
        bm25_weight: BM25 검색 가중치
        
    Returns:
        병합된 (Document, rrf_score) 리스트 (점수 높은 순)
    """
    # 문서를 page_content로 식별 (동일 문서 병합용)
    doc_scores: Dict[str, Tuple[Document, float]] = {}
    
    # 벡터 결과 — rank 기반 점수 부여
    for rank, (doc, _score) in enumerate(vector_results, 1):
        content_key = doc.page_content[:200]  # 앞 200자로 식별
        rrf_score = vector_weight / (k + rank)
        
        if content_key in doc_scores:
            existing_doc, existing_score = doc_scores[content_key]
            doc_scores[content_key] = (existing_doc, existing_score + rrf_score)
        else:
            doc_scores[content_key] = (doc, rrf_score)
    
    # BM25 결과 — rank 기반 점수 부여
    for rank, (doc, _score) in enumerate(bm25_results, 1):
        content_key = doc.page_content[:200]
        rrf_score = bm25_weight / (k + rank)
        
        if content_key in doc_scores:
            existing_doc, existing_score = doc_scores[content_key]
            doc_scores[content_key] = (existing_doc, existing_score + rrf_score)
        else:
            doc_scores[content_key] = (doc, rrf_score)
    
    # RRF 점수 기준 정렬
    merged = sorted(
        doc_scores.values(),
        key=lambda x: x[1],
        reverse=True
    )
    
    return merged
