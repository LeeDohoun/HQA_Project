# 파일: src/rag/document_loader.py
"""
문서 로딩 및 전처리 모듈 (PaddleOCR-VL-1.5 기반)

변경사항 (v0.2.0):
- PaddleOCR-VL-1.5를 기본 OCR 엔진으로 사용
- 모든 문서를 텍스트로 통합 처리
- 이미지 관련 코드 제거
- Markdown 구조화 출력 지원
"""

import os
from typing import List, Dict, Optional, TYPE_CHECKING
from dataclasses import dataclass, field

if TYPE_CHECKING:
    from PIL import Image

# OCR 프로세서 임포트
from .ocr_processor import (
    get_ocr_processor,
    OCRDocument,
    OCRPage,
    check_paddleocr_availability
)


@dataclass
class ProcessedPage:
    """처리된 페이지 데이터 클래스"""
    page_num: int
    content_type: str  # 항상 "text" (PaddleOCR가 모두 텍스트로 변환)
    content: str  # 텍스트 내용
    text_fallback: str = ""  # 레거시 호환용 (content와 동일)
    metadata: Dict = field(default_factory=dict)
    
    # PaddleOCR-VL 전용 필드
    markdown: str = ""  # Markdown 형식
    tables: List[str] = field(default_factory=list)
    formulas: List[str] = field(default_factory=list)


@dataclass
class ProcessedDocument:
    """처리된 문서 데이터 클래스"""
    source: str
    total_pages: int
    pages: List[ProcessedPage]
    text_page_count: int = 0
    
    # PaddleOCR-VL 전용 필드
    full_markdown: str = ""
    processor: str = "PaddleOCR-VL-1.5"


def _convert_ocr_to_processed(ocr_doc: OCRDocument) -> ProcessedDocument:
    """OCRDocument를 ProcessedDocument로 변환 (하위 호환성)"""
    pages = []
    
    for ocr_page in ocr_doc.pages:
        # Markdown이 있으면 사용, 없으면 raw_text 사용
        content = ocr_page.markdown if ocr_page.markdown else ocr_page.raw_text
        
        page = ProcessedPage(
            page_num=ocr_page.page_num,
            content_type="text",  # PaddleOCR는 모두 텍스트로 변환
            content=content,
            text_fallback=ocr_page.raw_text,
            markdown=ocr_page.markdown,
            tables=ocr_page.tables,
            formulas=ocr_page.formulas,
            metadata={
                **ocr_page.metadata,
                "ocr_processed": True
            }
        )
        pages.append(page)
    
    return ProcessedDocument(
        source=ocr_doc.source,
        total_pages=ocr_doc.total_pages,
        pages=pages,
        text_page_count=ocr_doc.total_pages,  # 모두 텍스트로 처리됨
        full_markdown=ocr_doc.full_markdown,
        processor=ocr_doc.metadata.get("processor", "PaddleOCR-VL-1.5")
    )


class PDFProcessor:
    """
    PDF 문서 처리기 (PaddleOCR-VL-1.5 기반)
    
    이전 버전과의 하위 호환성을 유지하면서 PaddleOCR-VL을 사용합니다.
    """
    
    def __init__(
        self,
        dpi: int = 150,  # 레거시 호환성용 (PaddleOCR에서는 무시)
        use_gpu: bool = True,
        use_vllm_server: bool = False,
        vllm_server_url: str = "http://127.0.0.1:8080/v1"
    ):
        """
        Args:
            dpi: 이미지 해상도 (레거시 호환성, PaddleOCR에서는 무시)
            use_gpu: GPU 사용 여부
            use_vllm_server: vLLM 서버 사용 여부
            vllm_server_url: vLLM 서버 URL
        """
        self.dpi = dpi
        self.ocr_processor = get_ocr_processor(
            use_gpu=use_gpu,
            use_vllm_server=use_vllm_server,
            vllm_server_url=vllm_server_url,
            fallback_to_legacy=True
        )
    
    def process_pdf(self, pdf_path: str) -> ProcessedDocument:
        """
        PDF 파일 처리
        
        Args:
            pdf_path: PDF 파일 경로
            
        Returns:
            ProcessedDocument 객체
        """
        ocr_doc = self.ocr_processor.process_file(pdf_path, save_outputs=False)
        return _convert_ocr_to_processed(ocr_doc)
    
    def process_pdf_bytes(self, pdf_bytes: bytes, filename: str = "document.pdf") -> ProcessedDocument:
        """
        바이트 형태의 PDF 처리
        
        Args:
            pdf_bytes: PDF 바이트 데이터
            filename: 파일명
            
        Returns:
            ProcessedDocument 객체
        """
        ocr_doc = self.ocr_processor.process_bytes(pdf_bytes, filename, save_outputs=False)
        return _convert_ocr_to_processed(ocr_doc)
    
    # 레거시 메서드 (하위 호환성)
    def analyze_page(self, page) -> Dict:
        """레거시 메서드 - PaddleOCR에서는 사용 안 함"""
        return {"has_visual": False, "text": "", "deprecated": True}
    
    def page_to_image(self, page) -> "Image.Image":
        """레거시 메서드 - PaddleOCR에서는 사용 안 함"""
        raise NotImplementedError("PaddleOCR-VL 모드에서는 page_to_image를 사용하지 않습니다")


class DocumentLoader:
    """다양한 문서 형식을 로딩하는 통합 클래스"""
    
    def __init__(
        self,
        dpi: int = 150,
        use_gpu: bool = True,
        use_vllm_server: bool = False,
        vllm_server_url: str = "http://127.0.0.1:8080/v1"
    ):
        """
        Args:
            dpi: 이미지 해상도 (레거시 호환성)
            use_gpu: GPU 사용 여부
            use_vllm_server: vLLM 서버 사용 여부
            vllm_server_url: vLLM 서버 URL
        """
        self.pdf_processor = PDFProcessor(
            dpi=dpi,
            use_gpu=use_gpu,
            use_vllm_server=use_vllm_server,
            vllm_server_url=vllm_server_url
        )
        
        # 직접 OCR 프로세서 접근용
        self.ocr_processor = self.pdf_processor.ocr_processor
    
    def load(self, file_path: str) -> ProcessedDocument:
        """
        파일 확장자에 따라 적절한 로더 선택
        
        Args:
            file_path: 파일 경로
            
        Returns:
            ProcessedDocument 객체
        """
        ext = os.path.splitext(file_path)[1].lower()
        
        # PDF 및 이미지 파일은 OCR 처리
        if ext in [".pdf", ".png", ".jpg", ".jpeg", ".tiff", ".bmp"]:
            ocr_doc = self.ocr_processor.process_file(file_path, save_outputs=False)
            return _convert_ocr_to_processed(ocr_doc)
        elif ext == ".txt":
            return self._load_text(file_path)
        elif ext == ".md":
            return self._load_markdown(file_path)
        else:
            raise ValueError(f"지원하지 않는 파일 형식: {ext}")
    
    def load_bytes(self, data: bytes, filename: str) -> ProcessedDocument:
        """
        바이트 데이터 로딩
        
        Args:
            data: 파일 바이트 데이터
            filename: 파일명
            
        Returns:
            ProcessedDocument 객체
        """
        ext = os.path.splitext(filename)[1].lower()
        
        if ext in [".pdf", ".png", ".jpg", ".jpeg", ".tiff", ".bmp"]:
            ocr_doc = self.ocr_processor.process_bytes(data, filename, save_outputs=False)
            return _convert_ocr_to_processed(ocr_doc)
        else:
            raise ValueError(f"지원하지 않는 파일 형식: {ext}")
    
    def load_with_markdown(self, file_path: str, save_markdown: bool = True) -> tuple:
        """
        파일 로드 + Markdown 출력
        
        Args:
            file_path: 파일 경로
            save_markdown: Markdown 파일 저장 여부
            
        Returns:
            (ProcessedDocument, markdown_str) 튜플
        """
        ocr_doc = self.ocr_processor.process_file(file_path, save_outputs=save_markdown)
        processed = _convert_ocr_to_processed(ocr_doc)
        return processed, ocr_doc.full_markdown
    
    def _load_text(self, file_path: str) -> ProcessedDocument:
        """텍스트 파일 로딩"""
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
        
        return ProcessedDocument(
            source=file_path,
            total_pages=1,
            pages=[ProcessedPage(
                page_num=1,
                content_type="text",
                content=content,
                text_fallback=content,
                metadata={"text_length": len(content)}
            )],
            text_page_count=1,
            image_page_count=0,
            processor="text_loader"
        )
    
    def _load_markdown(self, file_path: str) -> ProcessedDocument:
        """Markdown 파일 로딩"""
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
        
        return ProcessedDocument(
            source=file_path,
            total_pages=1,
            pages=[ProcessedPage(
                page_num=1,
                content_type="text",
                content=content,
                text_fallback=content,
                markdown=content,
                metadata={"text_length": len(content), "format": "markdown"}
            )],
            text_page_count=1,
            image_page_count=0,
            full_markdown=content,
            processor="markdown_loader"
        )
