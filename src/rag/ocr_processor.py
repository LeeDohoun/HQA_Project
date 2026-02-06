# 파일: src/rag/ocr_processor.py
"""
PaddleOCR-VL-1.5 기반 OCR 프로세서 모듈

특징:
- End-to-End VLM 기반 문서 파싱
- 표, 차트, 수식, 도장 인식
- 스캔/기울기/왜곡/조명 왜곡 강건
- Markdown 구조화 출력
- 0.9B 경량 모델로 빠른 추론

참조: https://huggingface.co/PaddlePaddle/PaddleOCR-VL-1.5
"""

import os
import io
import json
import tempfile
from typing import List, Dict, Optional, Union, Any
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

# PaddleOCR 선택적 임포트
_PADDLEOCR_AVAILABLE = False
try:
    from paddleocr import PaddleOCRVL
    _PADDLEOCR_AVAILABLE = True
except ImportError:
    PaddleOCRVL = None


def check_paddleocr_availability() -> Dict:
    """PaddleOCR-VL 사용 가능 여부 확인"""
    status = {
        "available": _PADDLEOCR_AVAILABLE,
        "install_cmd": (
            "# CUDA 12.6용 설치 (다른 버전은 공식 문서 참조)\n"
            "python -m pip install paddlepaddle-gpu==3.2.1 -i https://www.paddlepaddle.org.cn/packages/stable/cu126/\n"
            "python -m pip install -U 'paddleocr[doc-parser]'"
        ),
        "cpu_install_cmd": (
            "# CPU 전용 설치\n"
            "python -m pip install paddlepaddle==3.2.1\n"
            "python -m pip install -U 'paddleocr[doc-parser]'"
        )
    }
    
    if _PADDLEOCR_AVAILABLE:
        status["note"] = "PaddleOCR-VL 사용 가능"
    else:
        status["note"] = "PaddleOCR-VL 미설치. 위 명령어로 설치하세요."
    
    return status


@dataclass
class OCRPage:
    """OCR 처리된 페이지 데이터"""
    page_num: int
    markdown: str  # Markdown 형식 텍스트
    raw_text: str  # 순수 텍스트 (검색용)
    tables: List[str] = field(default_factory=list)  # 표 데이터 리스트
    formulas: List[str] = field(default_factory=list)  # 수식 리스트
    metadata: Dict = field(default_factory=dict)


@dataclass 
class OCRDocument:
    """OCR 처리된 문서 데이터"""
    source: str
    total_pages: int
    pages: List[OCRPage]
    full_markdown: str = ""  # 전체 문서 Markdown
    metadata: Dict = field(default_factory=dict)


class PaddleOCRProcessor:
    """
    PaddleOCR-VL-1.5 기반 문서 OCR 프로세서
    
    모든 문서를 이미지로 처리하여 VLM이 직접 텍스트/표/차트를 인식합니다.
    """
    
    # 지원하는 작업 타입
    TASK_TYPES = {
        "ocr": "OCR:",
        "table": "Table Recognition:",
        "formula": "Formula Recognition:",
        "chart": "Chart Recognition:",
        "spotting": "Spotting:",
        "seal": "Seal Recognition:"
    }
    
    def __init__(
        self,
        use_gpu: bool = True,
        use_vllm_server: bool = False,
        vllm_server_url: str = "http://127.0.0.1:8080/v1",
        output_dir: str = "./output/ocr"
    ):
        """
        Args:
            use_gpu: GPU 사용 여부
            use_vllm_server: vLLM 서버 사용 여부 (대량 처리 시 권장)
            vllm_server_url: vLLM 서버 URL
            output_dir: 출력 디렉토리 (JSON/Markdown 저장)
        """
        if not _PADDLEOCR_AVAILABLE:
            raise ImportError(
                "PaddleOCR-VL을 사용하려면 PaddlePaddle과 PaddleOCR을 설치하세요:\n"
                f"{check_paddleocr_availability()['install_cmd']}"
            )
        
        self.output_dir = output_dir
        os.makedirs(output_dir, exist_ok=True)
        
        # PaddleOCR-VL 파이프라인 초기화
        print("⚙️ PaddleOCR-VL-1.5 초기화 중...")
        
        if use_vllm_server:
            # vLLM 서버 모드 (대량 처리에 빠름)
            self.pipeline = PaddleOCRVL(
                vl_rec_backend="vllm-server",
                vl_rec_server_url=vllm_server_url
            )
            print(f"🚀 vLLM 서버 모드 활성화: {vllm_server_url}")
        else:
            # 로컬 모드
            self.pipeline = PaddleOCRVL()
            print("✅ PaddleOCR-VL-1.5 로컬 모드 초기화 완료")
    
    def process_file(
        self,
        file_path: str,
        save_outputs: bool = True
    ) -> OCRDocument:
        """
        파일을 OCR 처리
        
        Args:
            file_path: 입력 파일 경로 (PDF, 이미지)
            save_outputs: Markdown/JSON 파일 저장 여부
            
        Returns:
            OCRDocument 객체
        """
        print(f"📄 OCR 처리 시작: {file_path}")
        
        # PaddleOCR-VL 실행
        results = self.pipeline.predict(file_path)
        
        pages = []
        all_markdown = []
        
        for idx, result in enumerate(results):
            page_num = idx + 1
            
            # Markdown 추출
            markdown_content = ""
            if hasattr(result, 'save_to_markdown'):
                # 임시 파일로 저장 후 읽기
                with tempfile.TemporaryDirectory() as tmp_dir:
                    result.save_to_markdown(save_path=tmp_dir)
                    md_files = list(Path(tmp_dir).glob("*.md"))
                    if md_files:
                        markdown_content = md_files[0].read_text(encoding="utf-8")
            
            # 순수 텍스트 추출 (검색용)
            raw_text = self._extract_raw_text(result)
            
            # 표/수식 추출
            tables = self._extract_tables(result)
            formulas = self._extract_formulas(result)
            
            page = OCRPage(
                page_num=page_num,
                markdown=markdown_content,
                raw_text=raw_text,
                tables=tables,
                formulas=formulas,
                metadata={
                    "has_tables": len(tables) > 0,
                    "has_formulas": len(formulas) > 0,
                    "text_length": len(raw_text)
                }
            )
            pages.append(page)
            all_markdown.append(f"<!-- Page {page_num} -->\n{markdown_content}")
            
            print(f"   ✅ {page_num}페이지 완료 (텍스트: {len(raw_text)}자, 표: {len(tables)}개)")
        
        # 전체 Markdown 생성
        full_markdown = "\n\n---\n\n".join(all_markdown)
        
        # 출력 저장
        if save_outputs:
            base_name = Path(file_path).stem
            self._save_outputs(base_name, full_markdown, pages)
        
        doc = OCRDocument(
            source=file_path,
            total_pages=len(pages),
            pages=pages,
            full_markdown=full_markdown,
            metadata={
                "processor": "PaddleOCR-VL-1.5",
                "total_tables": sum(len(p.tables) for p in pages),
                "total_formulas": sum(len(p.formulas) for p in pages)
            }
        )
        
        print(f"✅ OCR 완료: {len(pages)}페이지")
        return doc
    
    def process_image(
        self,
        image: Union[str, Image.Image],
        task: str = "ocr"
    ) -> Dict:
        """
        단일 이미지 OCR 처리
        
        Args:
            image: 이미지 경로 또는 PIL Image
            task: 작업 타입 ("ocr", "table", "formula", "chart", "spotting", "seal")
            
        Returns:
            처리 결과 딕셔너리
        """
        if task not in self.TASK_TYPES:
            raise ValueError(f"지원하지 않는 작업: {task}. 지원 목록: {list(self.TASK_TYPES.keys())}")
        
        # PIL Image인 경우 임시 파일로 저장
        if isinstance(image, Image.Image):
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                image.save(tmp.name)
                image_path = tmp.name
        else:
            image_path = image
        
        try:
            results = self.pipeline.predict(image_path)
            result = results[0] if results else None
            
            return {
                "task": task,
                "raw_text": self._extract_raw_text(result) if result else "",
                "result": result
            }
        finally:
            # 임시 파일 삭제
            if isinstance(image, Image.Image) and os.path.exists(image_path):
                os.unlink(image_path)
    
    def process_bytes(
        self,
        data: bytes,
        filename: str = "document.pdf",
        save_outputs: bool = True
    ) -> OCRDocument:
        """
        바이트 데이터 OCR 처리
        
        Args:
            data: 파일 바이트 데이터
            filename: 파일명
            save_outputs: 출력 저장 여부
            
        Returns:
            OCRDocument 객체
        """
        # 임시 파일로 저장 후 처리
        ext = Path(filename).suffix or ".pdf"
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            tmp.write(data)
            tmp_path = tmp.name
        
        try:
            doc = self.process_file(tmp_path, save_outputs=save_outputs)
            doc.source = filename  # 원본 파일명으로 변경
            return doc
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
    
    def _extract_raw_text(self, result: Any) -> str:
        """결과에서 순수 텍스트 추출"""
        if result is None:
            return ""
        
        try:
            # PaddleOCR 결과 구조에 따라 텍스트 추출
            if hasattr(result, 'rec_text'):
                return result.rec_text
            elif hasattr(result, 'text'):
                return result.text
            elif hasattr(result, '__iter__'):
                texts = []
                for item in result:
                    if isinstance(item, str):
                        texts.append(item)
                    elif hasattr(item, 'text'):
                        texts.append(item.text)
                return "\n".join(texts)
        except Exception:
            pass
        
        return str(result) if result else ""
    
    def _extract_tables(self, result: Any) -> List[str]:
        """결과에서 표 데이터 추출"""
        tables = []
        try:
            if hasattr(result, 'tables'):
                for table in result.tables:
                    if hasattr(table, 'to_markdown'):
                        tables.append(table.to_markdown())
                    else:
                        tables.append(str(table))
        except Exception:
            pass
        return tables
    
    def _extract_formulas(self, result: Any) -> List[str]:
        """결과에서 수식 추출"""
        formulas = []
        try:
            if hasattr(result, 'formulas'):
                formulas = [str(f) for f in result.formulas]
        except Exception:
            pass
        return formulas
    
    def _save_outputs(
        self,
        base_name: str,
        markdown: str,
        pages: List[OCRPage]
    ):
        """출력 파일 저장"""
        # Markdown 저장
        md_path = os.path.join(self.output_dir, f"{base_name}.md")
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(markdown)
        print(f"   📝 Markdown 저장: {md_path}")
        
        # JSON 저장 (메타데이터)
        json_path = os.path.join(self.output_dir, f"{base_name}.json")
        json_data = {
            "source": base_name,
            "total_pages": len(pages),
            "pages": [
                {
                    "page_num": p.page_num,
                    "text_length": len(p.raw_text),
                    "has_tables": p.metadata.get("has_tables", False),
                    "has_formulas": p.metadata.get("has_formulas", False)
                }
                for p in pages
            ]
        }
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(json_data, f, ensure_ascii=False, indent=2)


class LegacyOCRProcessor:
    """
    PaddleOCR-VL 미설치 시 대체용 프로세서
    PyMuPDF 기반 텍스트 추출 (기존 방식)
    """
    
    def __init__(self, dpi: int = 150):
        """
        Args:
            dpi: 이미지 변환 해상도
        """
        import fitz  # PyMuPDF
        self.dpi = dpi
        print("⚠️ PaddleOCR-VL 미설치. 레거시 모드(PyMuPDF) 사용")
    
    def process_file(self, file_path: str, save_outputs: bool = False) -> OCRDocument:
        """PDF 파일 처리 (레거시)"""
        import fitz
        
        pages = []
        all_text = []
        
        doc = fitz.open(file_path)
        
        for page_num, page in enumerate(doc):
            text = page.get_text("text").strip()
            
            ocr_page = OCRPage(
                page_num=page_num + 1,
                markdown=text,
                raw_text=text,
                metadata={"legacy_mode": True}
            )
            pages.append(ocr_page)
            all_text.append(text)
        
        doc.close()
        
        return OCRDocument(
            source=file_path,
            total_pages=len(pages),
            pages=pages,
            full_markdown="\n\n---\n\n".join(all_text),
            metadata={"processor": "PyMuPDF (legacy)"}
        )
    
    def process_bytes(self, data: bytes, filename: str = "document.pdf", save_outputs: bool = False) -> OCRDocument:
        """바이트 데이터 처리 (레거시)"""
        import fitz
        
        pages = []
        all_text = []
        
        doc = fitz.open(stream=data, filetype="pdf")
        
        for page_num, page in enumerate(doc):
            text = page.get_text("text").strip()
            
            ocr_page = OCRPage(
                page_num=page_num + 1,
                markdown=text,
                raw_text=text,
                metadata={"legacy_mode": True}
            )
            pages.append(ocr_page)
            all_text.append(text)
        
        doc.close()
        
        return OCRDocument(
            source=filename,
            total_pages=len(pages),
            pages=pages,
            full_markdown="\n\n---\n\n".join(all_text),
            metadata={"processor": "PyMuPDF (legacy)"}
        )


def get_ocr_processor(
    use_gpu: bool = True,
    use_vllm_server: bool = False,
    vllm_server_url: str = "http://127.0.0.1:8080/v1",
    fallback_to_legacy: bool = True
) -> Union[PaddleOCRProcessor, LegacyOCRProcessor]:
    """
    OCR 프로세서 팩토리 함수
    
    Args:
        use_gpu: GPU 사용 여부
        use_vllm_server: vLLM 서버 사용 여부
        vllm_server_url: vLLM 서버 URL
        fallback_to_legacy: PaddleOCR 미설치 시 레거시 모드 사용
        
    Returns:
        OCR 프로세서 인스턴스
    """
    if _PADDLEOCR_AVAILABLE:
        return PaddleOCRProcessor(
            use_gpu=use_gpu,
            use_vllm_server=use_vllm_server,
            vllm_server_url=vllm_server_url
        )
    elif fallback_to_legacy:
        return LegacyOCRProcessor()
    else:
        raise ImportError(
            "PaddleOCR-VL이 설치되어 있지 않습니다.\n"
            f"{check_paddleocr_availability()['install_cmd']}"
        )
