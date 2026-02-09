# 파일: src/rag/retriever.py
"""
RAG 검색기 모듈 (텍스트 전용 + Qwen3 리랭커)
- 벡터 검색 + 리랭킹 파이프라인
- PaddleOCR-VL이 모든 문서를 텍스트로 변환하므로 텍스트 검색만 수행
"""

import os
from typing import List, Dict, Optional, Any
from dataclasses import dataclass, field
import logging

from langchain_core.documents import Document

from .vector_store import VectorStoreManager
from .document_loader import DocumentLoader, ProcessedDocument
from .reranker import Qwen3Reranker, RerankerManager
from .bm25_index import BM25IndexManager, reciprocal_rank_fusion

logger = logging.getLogger(__name__)


@dataclass
class RetrievalResult:
    """검색 결과 데이터 클래스"""
    query: str
    text_results: List[Document] = field(default_factory=list)
    combined_context: str = ""
    is_reranked: bool = False  # 리랭킹 거쳤는지 여부
    scores: List[float] = field(default_factory=list)  # 각 결과의 점수
    
    @property
    def total_results(self) -> int:
        return len(self.text_results)
    
    # 레거시 호환성
    @property
    def image_results(self) -> List[Document]:
        return []
    
    @property
    def has_images(self) -> bool:
        return False


class RAGRetriever:
    """RAG 검색기 - 문서 검색 및 컨텍스트 구성 (텍스트 전용 + 리랭킹)"""
    
    def __init__(
        self,
        persist_dir: str = "./database/chroma_db",
        collection_name: str = "documents",
        embedding_type: str = "default",
        # 검색 설정
        retrieval_k: int = 20,       # 벡터 검색 후보 수
        rerank_top_k: int = 3,       # 리랭킹 후 최종 반환 수
        use_reranker: bool = True,   # 리랭커 사용 여부
        # 리랭커 설정
        reranker_model: str = "default",
        reranker_task_type: str = "finance",
        reranker_instruction: Optional[str] = None,
        # Hybrid Search 설정
        use_hybrid: bool = True,     # BM25 하이브리드 검색 사용 여부
        bm25_weight: float = 1.0,    # BM25 가중치 (RRF)
        vector_weight: float = 1.0,  # 벡터 검색 가중치 (RRF)
        bm25_persist_path: Optional[str] = None,  # BM25 인덱스 저장 경로
    ):
        """
        Args:
            persist_dir: 벡터 저장소 경로
            collection_name: 컬렉션 이름
            embedding_type: 임베딩 모델 타입
            retrieval_k: 벡터 검색으로 가져올 후보 수
            rerank_top_k: 리랭킹 후 최종 반환 수
            use_reranker: 리랭커 사용 여부
            reranker_model: 리랭커 모델 (default, small, medium, large)
            reranker_task_type: 리랭커 작업 유형 (finance, retrieval, qa, code, semantic)
            reranker_instruction: 커스텀 리랭커 Instruction
            use_hybrid: BM25 하이브리드 검색 사용 여부
            bm25_weight: BM25 검색 RRF 가중치
            vector_weight: 벡터 검색 RRF 가중치
            bm25_persist_path: BM25 인덱스 파일 경로
        """
        # 문서 로더
        self.document_loader = DocumentLoader()
        
        # 벡터 저장소 관리자
        self.vector_store = VectorStoreManager(
            persist_dir=persist_dir,
            collection_name=collection_name,
            embedding_type=embedding_type
        )
        
        # 검색 설정
        self.retrieval_k = retrieval_k
        self.rerank_top_k = rerank_top_k
        self.use_reranker = use_reranker
        
        # 리랭커 설정
        self.reranker_model = reranker_model
        self.reranker_task_type = reranker_task_type
        self.reranker_instruction = reranker_instruction
        
        # 리랭커 (지연 로딩)
        self._reranker: Optional[Qwen3Reranker] = None
        
        # ── Hybrid Search: BM25 인덱스 ──
        self.use_hybrid = use_hybrid
        self.bm25_weight = bm25_weight
        self.vector_weight = vector_weight
        
        _bm25_path = bm25_persist_path or os.path.join(
            persist_dir, f"{collection_name}_bm25_index.json"
        )
        self.bm25_index = BM25IndexManager(persist_path=_bm25_path)
        
        if self.use_hybrid and self.bm25_index.is_available:
            logger.info(
                f"Hybrid Search 활성화: BM25(w={bm25_weight}) + Vector(w={vector_weight})"
            )
        elif self.use_hybrid and not self.bm25_index.is_available:
            logger.warning("rank_bm25 미설치 → Hybrid Search 비활성화, 벡터 검색만 사용")
            self.use_hybrid = False
        
        logger.info(
            f"RAGRetriever 초기화: retrieval_k={retrieval_k}, "
            f"rerank_top_k={rerank_top_k}, use_reranker={use_reranker}, "
            f"hybrid={self.use_hybrid}"
        )
    
    def _get_reranker(self) -> Qwen3Reranker:
        """리랭커 인스턴스 반환 (지연 로딩)"""
        if self._reranker is None:
            self._reranker = RerankerManager.get_reranker(
                model_name=self.reranker_model
            )
        return self._reranker
    
    def index_document(
        self,
        file_path: str,
        metadata: Optional[Dict] = None,
        chunk_text: bool = True
    ) -> Dict:
        """
        문서를 인덱싱 (로드 + 저장)
        
        Args:
            file_path: 파일 경로
            metadata: 추가 메타데이터
            chunk_text: 텍스트 청킹 여부
            
        Returns:
            인덱싱 결과
        """
        # 문서 로드 (PaddleOCR로 텍스트 변환)
        processed_doc = self.document_loader.load(file_path)
        
        # 벡터 저장소에 추가
        result = self.vector_store.add_document(
            processed_doc,
            doc_metadata=metadata,
            chunk_text=chunk_text
        )
        
        # BM25 인덱스에도 추가
        if self.use_hybrid:
            bm25_docs = []
            for page in processed_doc.pages:
                content = page.content or page.text_fallback
                if content and content.strip():
                    bm25_docs.append(Document(
                        page_content=content,
                        metadata={
                            "source": processed_doc.source,
                            "page_num": page.page_num,
                            **(metadata or {}),
                        }
                    ))
            if bm25_docs:
                self.bm25_index.add_documents(bm25_docs)
        
        return result
    
    def index_bytes(
        self,
        data: bytes,
        filename: str,
        metadata: Optional[Dict] = None,
        chunk_text: bool = True
    ) -> Dict:
        """
        바이트 데이터를 인덱싱
        
        Args:
            data: 파일 바이트 데이터
            filename: 파일명
            metadata: 추가 메타데이터
            chunk_text: 텍스트 청킹 여부
            
        Returns:
            인덱싱 결과
        """
        # 문서 로드
        processed_doc = self.document_loader.load_bytes(data, filename)
        
        # 벡터 저장소에 추가
        result = self.vector_store.add_document(
            processed_doc,
            doc_metadata=metadata,
            chunk_text=chunk_text
        )
        
        # BM25 인덱스에도 추가
        if self.use_hybrid:
            bm25_docs = []
            for page in processed_doc.pages:
                content = page.content or page.text_fallback
                if content and content.strip():
                    bm25_docs.append(Document(
                        page_content=content,
                        metadata={
                            "source": processed_doc.source,
                            "page_num": page.page_num,
                            **(metadata or {}),
                        }
                    ))
            if bm25_docs:
                self.bm25_index.add_documents(bm25_docs)
        
        return result
    
    def index_text(
        self,
        text: str,
        metadata: Optional[Dict] = None,
        chunk_text: bool = True
    ) -> Dict:
        """
        텍스트를 직접 인덱싱
        
        Args:
            text: 인덱싱할 텍스트
            metadata: 추가 메타데이터
            chunk_text: 텍스트 청킹 여부
            
        Returns:
            인덱싱 결과
        """
        try:
            if chunk_text:
                # 텍스트 분할
                from .text_splitter import TextSplitter
                splitter = TextSplitter(chunk_size=1000, chunk_overlap=200)
                chunks = splitter.split_text(text, metadata=metadata or {})
                
                # 청크 추가
                texts = [chunk["text"] for chunk in chunks]
                metadatas = [chunk["metadata"] for chunk in chunks]
                
                self.vector_store.add_texts(texts, metadatas=metadatas)
                
                # BM25 인덱스에도 추가
                if self.use_hybrid:
                    self.bm25_index.add_texts(texts, metadatas=metadatas)
                
                return {
                    "success": True,
                    "chunks_added": len(texts)
                }
            else:
                # 청킹 없이 전체 텍스트 추가
                self.vector_store.add_texts([text], metadatas=[metadata or {}])
                
                # BM25 인덱스에도 추가
                if self.use_hybrid:
                    self.bm25_index.add_texts([text], metadatas=[metadata or {}])
                
                return {
                    "success": True,
                    "chunks_added": 1
                }
        except Exception as e:
            logger.exception("텍스트 인덱싱 오류")
            return {
                "success": False,
                "error": str(e),
                "chunks_added": 0
            }
    
    def retrieve(
        self,
        query: str,
        k: Optional[int] = None,
        include_images: bool = False,  # 레거시 호환성용 (무시됨)
        use_reranker: Optional[bool] = None,
        task_type: Optional[str] = None,
        instruction: Optional[str] = None,
    ) -> RetrievalResult:
        """
        쿼리에 대한 관련 문서 검색 (벡터 검색 + 리랭킹)
        
        Args:
            query: 검색 쿼리
            k: 최종 반환할 결과 수 (None이면 rerank_top_k 사용)
            include_images: 레거시 호환성용 (무시됨)
            use_reranker: 리랭커 사용 여부 (None이면 인스턴스 설정 사용)
            task_type: 리랭커 작업 유형 (None이면 인스턴스 설정 사용)
            instruction: 커스텀 리랭커 Instruction
            
        Returns:
            RetrievalResult 객체
        """
        final_k = k if k is not None else self.rerank_top_k
        should_rerank = use_reranker if use_reranker is not None else self.use_reranker
        task = task_type if task_type is not None else self.reranker_task_type
        inst = instruction if instruction is not None else self.reranker_instruction
        
        # 1. 벡터 검색 (후보 추출)
        search_k = self.retrieval_k if should_rerank else final_k
        
        logger.info(f"벡터 검색 중: query='{query[:50]}...', k={search_k}")
        
        vector_results = self.vector_store.search_with_scores(
            query=query,
            k=search_k
        )
        
        logger.info(f"벡터 검색 결과: {len(vector_results)}개")
        
        # 2. BM25 Hybrid Search (선택적)
        if self.use_hybrid and self.bm25_index.is_available and self.bm25_index.corpus_size > 0:
            logger.info(f"BM25 키워드 검색 중: query='{query[:50]}...'")
            bm25_results = self.bm25_index.search(query, k=search_k)
            logger.info(f"BM25 검색 결과: {len(bm25_results)}개")
            
            if bm25_results:
                # RRF로 두 결과 병합
                merged_results = reciprocal_rank_fusion(
                    vector_results=vector_results,
                    bm25_results=bm25_results,
                    vector_weight=self.vector_weight,
                    bm25_weight=self.bm25_weight,
                )
                logger.info(
                    f"Hybrid 병합 완료: Vector({len(vector_results)}) + "
                    f"BM25({len(bm25_results)}) → {len(merged_results)}개 (RRF)"
                )
                
                # 병합된 결과를 vector_results 형식으로 변환
                # RRF 점수는 순위만 반영하므로 거리 점수 대신 사용
                vector_results = [
                    (doc, 1.0 - min(rrf_score * 100, 1.0))  # RRF → 거리 스케일 변환
                    for doc, rrf_score in merged_results[:search_k]
                ]
        
        if not vector_results:
            logger.warning("검색 결과 없음 (벡터 + BM25)")
            return RetrievalResult(
                query=query,
                text_results=[],
                combined_context="관련 문서를 찾지 못했습니다.",
                is_reranked=False,
                scores=[]
            )
        
        # 2. 리랭킹 (선택적)
        if should_rerank and len(vector_results) > 0:
            logger.info(f"리랭킹 중: {len(vector_results)}개 → top {final_k}")
            
            # 문서 내용 추출
            documents = [doc.page_content for doc, _ in vector_results]
            original_docs = [doc for doc, _ in vector_results]
            
            # 리랭킹 수행
            reranker = self._get_reranker()
            rerank_results = reranker.rerank(
                query=query,
                documents=documents,
                instruction=inst,
                task_type=task,
                top_k=final_k,
            )
            
            # 리랭킹된 순서로 Document 재구성
            reranked_docs = []
            reranked_scores = []
            for rr in rerank_results:
                original_doc = original_docs[rr.original_index]
                reranked_docs.append(original_doc)
                reranked_scores.append(rr.score)
            
            # 컨텍스트 구성
            combined_context = self._build_context_with_scores(reranked_docs, reranked_scores)
            
            logger.info(f"리랭킹 완료: {len(reranked_docs)}개 반환")
            
            return RetrievalResult(
                query=query,
                text_results=reranked_docs,
                combined_context=combined_context,
                is_reranked=True,
                scores=reranked_scores
            )
        
        else:
            # 리랭킹 없이 벡터 검색 결과 반환
            text_results = [doc for doc, _ in vector_results[:final_k]]
            scores = [1.0 - score for _, score in vector_results[:final_k]]  # 거리 → 유사도
            
            # 컨텍스트 구성
            combined_context = self._build_context(text_results)
            
            return RetrievalResult(
                query=query,
                text_results=text_results,
                combined_context=combined_context,
                is_reranked=False,
                scores=scores
            )
    
    def retrieve_with_scores(
        self,
        query: str,
        k: int = 5,
        score_threshold: Optional[float] = None
    ) -> List[tuple]:
        """
        점수와 함께 검색
        
        Args:
            query: 검색 쿼리
            k: 반환할 결과 수
            score_threshold: 점수 임계값
            
        Returns:
            (Document, score) 튜플 리스트
        """
        return self.vector_store.search_with_scores(
            query, k=k, score_threshold=score_threshold
        )
    
    def _build_context(self, text_results: List[Document]) -> str:
        """
        검색 결과로부터 컨텍스트 문자열 구성
        
        Args:
            text_results: 텍스트 검색 결과
            
        Returns:
            구성된 컨텍스트 문자열
        """
        context_parts = []
        
        if text_results:
            context_parts.append("=== 검색된 문서 ===")
            for i, doc in enumerate(text_results, 1):
                source = doc.metadata.get("source", "unknown")
                page = doc.metadata.get("page_num", "?")
                context_parts.append(f"\n[문서 {i}] (출처: {source}, 페이지: {page})")
                context_parts.append(doc.page_content)
        
        return "\n".join(context_parts)
    
    def _build_context_with_scores(
        self,
        text_results: List[Document],
        scores: List[float]
    ) -> str:
        """
        점수와 함께 컨텍스트 문자열 구성
        
        Args:
            text_results: 텍스트 검색 결과
            scores: 각 결과의 점수 (리랭킹 점수)
            
        Returns:
            구성된 컨텍스트 문자열
        """
        context_parts = []
        
        if text_results:
            context_parts.append("=== 검색된 문서 (리랭킹 적용) ===")
            for i, (doc, score) in enumerate(zip(text_results, scores), 1):
                source = doc.metadata.get("source", "unknown")
                page = doc.metadata.get("page_num", "?")
                context_parts.append(f"\n[문서 {i}] (출처: {source}, 페이지: {page}, 관련도: {score:.3f})")
                context_parts.append(doc.page_content)
        
        return "\n".join(context_parts)
    
    def retrieve_without_rerank(
        self,
        query: str,
        k: int = 5,
    ) -> RetrievalResult:
        """리랭킹 없이 벡터 검색만 수행"""
        return self.retrieve(query=query, k=k, use_reranker=False)
    
    def get_retrieval_config(self) -> Dict[str, Any]:
        """현재 검색 설정 반환"""
        return {
            "retrieval_k": self.retrieval_k,
            "rerank_top_k": self.rerank_top_k,
            "use_reranker": self.use_reranker,
            "reranker_model": self.reranker_model,
            "reranker_task_type": self.reranker_task_type,
            "reranker_instruction": self.reranker_instruction,
            "use_hybrid": self.use_hybrid,
            "bm25_weight": self.bm25_weight,
            "vector_weight": self.vector_weight,
            "bm25_corpus_size": self.bm25_index.corpus_size,
        }
    
    def set_retrieval_config(
        self,
        retrieval_k: Optional[int] = None,
        rerank_top_k: Optional[int] = None,
        use_reranker: Optional[bool] = None,
        reranker_task_type: Optional[str] = None,
        reranker_instruction: Optional[str] = None,
        use_hybrid: Optional[bool] = None,
        bm25_weight: Optional[float] = None,
        vector_weight: Optional[float] = None,
    ):
        """검색 설정 업데이트"""
        if retrieval_k is not None:
            self.retrieval_k = retrieval_k
        if rerank_top_k is not None:
            self.rerank_top_k = rerank_top_k
        if use_reranker is not None:
            self.use_reranker = use_reranker
        if reranker_task_type is not None:
            self.reranker_task_type = reranker_task_type
        if reranker_instruction is not None:
            self.reranker_instruction = reranker_instruction
        if use_hybrid is not None:
            self.use_hybrid = use_hybrid
        if bm25_weight is not None:
            self.bm25_weight = bm25_weight
        if vector_weight is not None:
            self.vector_weight = vector_weight
    
    def delete_document(self, source: str) -> bool:
        """
        문서 삭제
        
        Args:
            source: 소스 경로/이름
            
        Returns:
            삭제 성공 여부
        """
        # BM25 인덱스에서도 삭제
        if self.use_hybrid:
            self.bm25_index.delete_by_source(source)
        
        return self.vector_store.delete_by_source(source)
    
    def get_stats(self) -> Dict:
        """저장소 통계 반환"""
        stats = self.vector_store.get_stats()
        stats["hybrid_search"] = {
            "enabled": self.use_hybrid,
            **self.bm25_index.get_stats(),
        }
        return stats


# 하위 호환성을 위한 기존 인터페이스 유지
class ReportVectorStore:
    """기존 ReportVectorStore 호환 클래스"""
    
    def __init__(self, use_multimodal: bool = False):  # use_multimodal 무시됨
        self._retriever = RAGRetriever(
            persist_dir="./database/chroma_db",
            collection_name="stock_reports",
            embedding_type="default"
        )
        
        # 기존 인터페이스 호환
        self.embedding_model = self._retriever.vector_store.embeddings
        self.text_embedding_model = self.embedding_model
        self.vector_store = self._retriever.vector_store.text_store
        self.pdf_processor = self._retriever.document_loader.pdf_processor
    
    def save_reports(self, reports: List[Dict], stock_code: str):
        """기존 리포트 저장 메서드"""
        for report in reports:
            existing_docs = self.vector_store.get(
                where={"source": report['link']}
            )
            
            if existing_docs and len(existing_docs['ids']) > 0:
                print(f"   (중복) 이미 저장된 리포트: {report['title']}")
                continue
            
            content = f"[{report['date']}] {report['title']} - {report['broker']}"
            metadata = {
                "stock_code": stock_code,
                "date": report['date'],
                "source": report['link']
            }
            
            self.vector_store.add_texts([content], metadatas=[metadata])
            print(f"💾 리포트 저장: {report['title']}")
    
    def save_pdf_report(
        self,
        pdf_path: str,
        stock_code: str,
        report_metadata: Optional[Dict] = None
    ) -> Dict:
        """PDF 리포트 저장"""
        metadata = {"stock_code": stock_code, **(report_metadata or {})}
        return self._retriever.index_document(pdf_path, metadata=metadata)
    
    def save_pdf_bytes(
        self,
        pdf_bytes: bytes,
        stock_code: str,
        filename: str = "document.pdf",
        report_metadata: Optional[Dict] = None
    ) -> Dict:
        """PDF 바이트 저장"""
        metadata = {"stock_code": stock_code, **(report_metadata or {})}
        return self._retriever.index_bytes(pdf_bytes, filename, metadata=metadata)
    
    def search_similar_reports(self, query: str, k: int = 3) -> List[Document]:
        """기존 검색 메서드"""
        return self._retriever.vector_store.search_text(query, k=k)
    
    def search_with_images(self, query: str, k: int = 3) -> Dict:
        """레거시 호환성 - 이미지 결과는 빈 리스트"""
        return self._retriever.vector_store.search_all(query, k=k)
