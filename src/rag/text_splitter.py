# 파일: src/rag/text_splitter.py
"""
텍스트 청킹(Chunking) 모듈
- 문서를 적절한 크기의 청크로 분할
- 오버랩 지원으로 컨텍스트 유지
"""

from typing import List, Dict, Optional
from dataclasses import dataclass, field


@dataclass
class TextChunk:
    """텍스트 청크 데이터 클래스"""
    content: str
    chunk_index: int
    start_char: int
    end_char: int
    metadata: Dict = field(default_factory=dict)


class TextSplitter:
    """텍스트를 청크로 분할하는 클래스"""
    
    def __init__(
        self,
        chunk_size: int = 1000,
        chunk_overlap: int = 200,
        separators: Optional[List[str]] = None
    ):
        """
        Args:
            chunk_size: 청크 최대 문자 수 (기본값: 1000)
            chunk_overlap: 청크 간 오버랩 문자 수 (기본값: 200)
            separators: 분할 기준 문자열 리스트 (기본값: ["\n\n", "\n", ". ", " "])
        """
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.separators = separators or ["\n\n", "\n", ". ", " "]
    
    def split_text(self, text: str, metadata: Optional[Dict] = None) -> List[TextChunk]:
        """
        텍스트를 청크로 분할
        
        Args:
            text: 분할할 텍스트
            metadata: 모든 청크에 적용할 메타데이터
            
        Returns:
            TextChunk 리스트
        """
        if metadata is None:
            metadata = {}
        
        if not text.strip():
            return []
        
        # 재귀적으로 분할
        chunks = self._split_recursive(text, self.separators)
        
        # 청크 병합 (너무 작은 청크 방지)
        merged_chunks = self._merge_chunks(chunks)
        
        # TextChunk 객체로 변환
        result = []
        current_pos = 0
        
        for i, chunk_text in enumerate(merged_chunks):
            start_pos = text.find(chunk_text, current_pos)
            if start_pos == -1:
                start_pos = current_pos
            
            result.append(TextChunk(
                content=chunk_text,
                chunk_index=i,
                start_char=start_pos,
                end_char=start_pos + len(chunk_text),
                metadata={
                    **metadata,
                    "chunk_index": i,
                    "total_chunks": len(merged_chunks)
                }
            ))
            current_pos = start_pos + len(chunk_text) - self.chunk_overlap
        
        return result
    
    def _split_recursive(self, text: str, separators: List[str]) -> List[str]:
        """재귀적으로 텍스트 분할"""
        if not separators:
            # 더 이상 분할 기준이 없으면 강제로 분할
            return self._split_by_length(text)
        
        separator = separators[0]
        remaining_separators = separators[1:]
        
        # 현재 구분자로 분할
        parts = text.split(separator)
        
        chunks = []
        for part in parts:
            if len(part) <= self.chunk_size:
                if part.strip():
                    chunks.append(part)
            else:
                # 청크 크기를 초과하면 다음 구분자로 재귀 분할
                sub_chunks = self._split_recursive(part, remaining_separators)
                chunks.extend(sub_chunks)
        
        return chunks
    
    def _split_by_length(self, text: str) -> List[str]:
        """길이 기준으로 강제 분할"""
        chunks = []
        start = 0
        
        while start < len(text):
            end = min(start + self.chunk_size, len(text))
            chunks.append(text[start:end])
            start = end - self.chunk_overlap if end < len(text) else end
        
        return chunks
    
    def _merge_chunks(self, chunks: List[str]) -> List[str]:
        """
        작은 청크들을 병합하여 적절한 크기로 조정
        """
        if not chunks:
            return []
        
        merged = []
        current_chunk = chunks[0]
        
        for chunk in chunks[1:]:
            # 현재 청크와 다음 청크를 합쳐도 크기 제한 내라면 병합
            if len(current_chunk) + len(chunk) + 1 <= self.chunk_size:
                current_chunk = current_chunk + "\n" + chunk
            else:
                if current_chunk.strip():
                    merged.append(current_chunk.strip())
                current_chunk = chunk
        
        # 마지막 청크 추가
        if current_chunk.strip():
            merged.append(current_chunk.strip())
        
        return merged


class SemanticTextSplitter(TextSplitter):
    """의미 단위로 텍스트를 분할하는 클래스 (문장/문단 기반)"""
    
    def __init__(
        self,
        chunk_size: int = 1000,
        chunk_overlap: int = 200
    ):
        # 한국어/영어 문장 구분자 포함
        separators = [
            "\n\n",  # 문단
            "\n",    # 줄바꿈
            "。",    # 일본어/중국어 마침표
            ". ",    # 영어 마침표
            "! ",    # 느낌표
            "? ",    # 물음표
            "다. ",  # 한국어 종결어미
            "요. ",  # 한국어 종결어미
            ", ",    # 쉼표
            " "      # 공백
        ]
        super().__init__(chunk_size, chunk_overlap, separators)
