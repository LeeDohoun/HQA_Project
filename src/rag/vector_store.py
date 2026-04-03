# 파일: src/rag/vector_store.py
"""
벡터 저장소 관리 모듈 (텍스트 전용)
- ChromaDB 기반 벡터 저장
- PaddleOCR-VL이 모든 문서를 텍스트로 변환하므로 텍스트만 저장
"""

import hashlib
import json
import math
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from .dedupe import make_document_id
from .source_registry import is_document_source

try:
    from langchain_community.vectorstores import Chroma
    from langchain_core.documents import Document
    from .embeddings import EmbeddingManager
    from .document_loader import ProcessedDocument
    from .text_splitter import TextSplitter
    _VECTOR_BACKEND_AVAILABLE = True
except ImportError:
    Chroma = None
    Document = None
    EmbeddingManager = None
    ProcessedDocument = None
    TextSplitter = None
    _VECTOR_BACKEND_AVAILABLE = False


class VectorStoreManager:
    """벡터 저장소 관리 클래스 (텍스트 전용)"""
    
    def __init__(
        self,
        persist_dir: str = "./database/chroma_db",
        collection_name: str = "documents",
        embedding_type: str = "default",
        image_storage_dir: str = None,  # 레거시 호환성용 (무시됨)
        use_multimodal: bool = False,   # 레거시 호환성용 (무시됨)
        device: Optional[str] = None    # 레거시 호환성용 (무시됨)
    ):
        """
        Args:
            persist_dir: ChromaDB 저장 경로
            collection_name: 컬렉션 이름
            embedding_type: 임베딩 모델 타입 ("default", "korean", "multilingual", "large")
        """
        if not _VECTOR_BACKEND_AVAILABLE:
            raise ImportError(
                "VectorStoreManager를 사용하려면 langchain-community 및 RAG 의존성이 필요합니다."
            )

        print("⚙️ 벡터 저장소 초기화 중...")
        
        # 임베딩 관리자 초기화
        self.embedding_manager = EmbeddingManager(model_type=embedding_type)
        self.embeddings = self.embedding_manager.get_langchain_embeddings()
        
        # 텍스트 분할기 초기화
        self.text_splitter = TextSplitter(
            chunk_size=1000,
            chunk_overlap=200
        )
        
        # 저장소 설정
        self.persist_dir = persist_dir
        os.makedirs(persist_dir, exist_ok=True)
        
        # 텍스트용 벡터 저장소
        self.text_store = Chroma(
            collection_name=f"{collection_name}_text",
            embedding_function=self.embeddings,
            persist_directory=persist_dir
        )
        
        print(f"✅ 벡터 저장소 초기화 완료 (경로: {persist_dir})")
    
    def add_document(
        self,
        processed_doc: ProcessedDocument,
        doc_metadata: Optional[Dict] = None,
        chunk_text: bool = True
    ) -> Dict:
        """
        처리된 문서를 벡터 저장소에 추가
        
        Args:
            processed_doc: ProcessedDocument 객체
            doc_metadata: 문서 전체에 적용할 메타데이터
            chunk_text: 텍스트 청킹 여부
            
        Returns:
            저장 결과 요약
        """
        if doc_metadata is None:
            doc_metadata = {}
        
        text_docs = []
        
        for page in processed_doc.pages:
            base_metadata = {
                "source": processed_doc.source,
                "page_num": page.page_num,
                "content_type": page.content_type,
                **doc_metadata,
                **page.metadata
            }
            
            # 모든 페이지를 텍스트로 처리 (PaddleOCR가 텍스트로 변환함)
            content = page.content or page.text_fallback
            if content and content.strip():
                if chunk_text:
                    # 텍스트 청킹
                    chunks = self.text_splitter.split_text(
                        content, 
                        metadata=base_metadata
                    )
                    for chunk in chunks:
                        doc = Document(
                            page_content=chunk.content,
                            metadata={**base_metadata, **chunk.metadata}
                        )
                        text_docs.append(doc)
                else:
                    # 청킹 없이 전체 저장
                    doc = Document(
                        page_content=content,
                        metadata=base_metadata
                    )
                    text_docs.append(doc)
        
        # 저장
        if text_docs:
            self.text_store.add_documents(text_docs)
            print(f"💾 텍스트 청크 {len(text_docs)}개 저장 완료")
        
        return {
            "source": processed_doc.source,
            "total_pages": processed_doc.total_pages,
            "text_chunks_saved": len(text_docs)
        }
    
    def add_texts(
        self,
        texts: List[str],
        metadatas: Optional[List[Dict]] = None
    ) -> List[str]:
        """
        텍스트 리스트 직접 추가
        
        Args:
            texts: 텍스트 리스트
            metadatas: 메타데이터 리스트
            
        Returns:
            추가된 문서 ID 리스트
        """
        return self.text_store.add_texts(texts, metadatas=metadatas)
    
    def search_text(self, query: str, k: int = 5) -> List[Document]:
        """
        텍스트 저장소에서 검색
        
        Args:
            query: 검색 쿼리
            k: 반환할 결과 수
            
        Returns:
            Document 리스트
        """
        return self.text_store.similarity_search(query, k=k)
    
    # 레거시 호환성을 위한 별칭
    def search_images(self, query: str, k: int = 5) -> List[Document]:
        """레거시 호환성용 - search_text와 동일"""
        return self.search_text(query, k=k)
    
    def search_all(self, query: str, k: int = 5) -> Dict[str, List[Document]]:
        """
        검색 (레거시 호환성 유지)
        
        Args:
            query: 검색 쿼리
            k: 반환할 결과 수
            
        Returns:
            {"text_results": [...], "image_results": [...]}
        """
        results = self.search_text(query, k=k)
        return {
            "text_results": results,
            "image_results": []  # 더 이상 이미지 결과 없음
        }
    
    def search_with_scores(
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
            score_threshold: 점수 임계값 (이하만 반환)
            
        Returns:
            (Document, score) 튜플 리스트
        """
        results = self.text_store.similarity_search_with_score(query, k=k)
        
        if score_threshold is not None:
            results = [(doc, score) for doc, score in results if score <= score_threshold]
        
        return results
    
    def get_by_source(self, source: str) -> Dict:
        """
        소스 기준으로 문서 조회
        
        Args:
            source: 소스 경로/이름
            
        Returns:
            조회 결과
        """
        text_results = self.text_store.get(where={"source": source})
        return {"text_documents": text_results}
    
    def delete_by_source(self, source: str) -> bool:
        """
        소스 기준으로 문서 삭제
        
        Args:
            source: 소스 경로/이름
            
        Returns:
            삭제 성공 여부
        """
        try:
            text_docs = self.text_store.get(where={"source": source})
            if text_docs and text_docs['ids']:
                self.text_store.delete(ids=text_docs['ids'])
            return True
        except Exception as e:
            print(f"❌ 삭제 오류: {e}")
            return False
    
    def get_stats(self) -> Dict:
        """저장소 통계 반환"""
        return {
            "text_store_count": self.text_store._collection.count(),
            "persist_dir": self.persist_dir,
            "total_documents": self.text_store._collection.count()
        }
    
    def clear(self):
        """벡터 저장소 초기화 (모든 데이터 삭제)"""
        try:
            # ChromaDB 컬렉션 내 모든 문서 삭제
            collection = self.text_store._collection
            ids = collection.get()['ids']
            if ids:
                collection.delete(ids=ids)
            print(f"✅ {len(ids)}개 문서 삭제 완료")
        except Exception as e:
            print(f"❌ 초기화 오류: {e}")


@dataclass
class VectorRecord:
    """경량 JSON vector store 레코드"""

    text: str
    metadata: Dict
    vector: List[float]


class SimpleVectorStore:
    """rag-data-pipeline 호환용 경량 JSON vector store"""

    def __init__(self, dimension: int = 256):
        self.dimension = dimension
        self.records: List[VectorRecord] = []

    def add_texts(self, texts: Iterable[str], metadatas: Iterable[Dict]) -> int:
        count = 0
        for text, metadata in zip(texts, metadatas):
            self.records.append(
                VectorRecord(
                    text=text,
                    metadata=metadata,
                    vector=self._embed(text),
                )
            )
            count += 1
        return count

    def upsert_texts(self, texts: Iterable[str], metadatas: Iterable[Dict]) -> int:
        existing_ids = {
            self._make_doc_id(record.metadata, record.text)
            for record in self.records
        }

        added = 0
        for text, metadata in zip(texts, metadatas):
            doc_id = self._make_doc_id(metadata, text)
            if doc_id in existing_ids:
                continue
            self.records.append(
                VectorRecord(
                    text=text,
                    metadata=metadata,
                    vector=self._embed(text),
                )
            )
            existing_ids.add(doc_id)
            added += 1
        return added

    def remove_by_theme(self, theme_key: str) -> int:
        before = len(self.records)
        self.records = [
            record
            for record in self.records
            if (record.metadata or {}).get("theme_key", "") != theme_key
        ]
        return before - len(self.records)

    def search(self, query: str, top_k: int = 5) -> List[Tuple[Dict, float]]:
        query_vec = self._embed(query)
        scored: List[Tuple[Dict, float]] = []
        for record in self.records:
            score = self._cosine_similarity(query_vec, record.vector)
            scored.append(({"text": record.text, "metadata": record.metadata}, score))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]

    def save(self, output_path: str) -> int:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "dimension": self.dimension,
            "size": len(self.records),
            "records": [
                {
                    "text": record.text,
                    "metadata": record.metadata,
                    "vector": record.vector,
                }
                for record in self.records
            ],
        }
        with path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        return len(self.records)

    @classmethod
    def load(cls, input_path: str) -> "SimpleVectorStore":
        path = Path(input_path)
        store = cls()
        if not path.exists():
            return store

        with path.open("r", encoding="utf-8") as f:
            payload = json.load(f)

        store.dimension = int(payload.get("dimension", 256))
        store.records = [
            VectorRecord(
                text=row.get("text", ""),
                metadata=row.get("metadata", {}),
                vector=row.get("vector", []),
            )
            for row in payload.get("records", [])
        ]
        return store

    def _embed(self, text: str) -> List[float]:
        tokens = re.findall(r"[가-힣A-Za-z0-9]+", (text or "").lower())
        if not tokens:
            return [0.0] * self.dimension

        vector = [0.0] * self.dimension
        for token in tokens:
            token_hash = int(hashlib.md5(token.encode("utf-8")).hexdigest(), 16)
            vector[token_hash % self.dimension] += 1.0

        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0:
            return vector
        return [value / norm for value in vector]

    @staticmethod
    def _cosine_similarity(vec1: List[float], vec2: List[float]) -> float:
        dot = sum(a * b for a, b in zip(vec1, vec2))
        norm1 = math.sqrt(sum(a * a for a in vec1))
        norm2 = math.sqrt(sum(b * b for b in vec2))
        if norm1 == 0 or norm2 == 0:
            return 0.0
        return dot / (norm1 * norm2)

    @staticmethod
    def _make_doc_id(metadata: Dict, text: str) -> str:
        source_type = str((metadata or {}).get("source_type", "")).strip().lower()
        return make_document_id(source_type=source_type, metadata=metadata or {}, text=text or "")


class SourceRAGBuilder:
    """소스별 JSON vector store 빌더"""

    def __init__(self, dimension: int = 256):
        self.dimension = dimension

    def upsert_by_source(
        self,
        records: List[Dict],
        output_dir: str,
        mode: str = "append-new-stocks",
        theme_key: str = "",
    ) -> Dict[str, int]:
        grouped: Dict[str, List[Dict]] = {}
        for row in records:
            metadata = row.get("metadata", {}) or {}
            source_type = str(metadata.get("source_type", "")).strip().lower()
            if not is_document_source(source_type):
                continue
            grouped.setdefault(source_type, []).append(row)

        output_stats: Dict[str, int] = {}
        base = Path(output_dir)
        base.mkdir(parents=True, exist_ok=True)

        for source, source_rows in grouped.items():
            output_file = base / f"{source}_vector_store.json"
            store = SimpleVectorStore.load(str(output_file))
            store.dimension = self.dimension

            if mode == "overwrite" and theme_key:
                store.remove_by_theme(theme_key)

            store.upsert_texts(
                texts=[row.get("text", "") for row in source_rows],
                metadatas=[row.get("metadata", {}) for row in source_rows],
            )
            output_stats[source] = store.save(str(output_file))

        return output_stats
