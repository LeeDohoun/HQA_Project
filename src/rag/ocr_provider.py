# 파일: src/rag/ocr_provider.py
"""
OCR 프로바이더 추상화

로컬 PaddleOCR과 외부 API(Upstage Document AI)를 동일한 인터페이스로 제공합니다.

사용법:
    provider = get_ocr_provider()  # 설정에 따라 적절한 프로바이더 반환
    result = provider.process_file("report.pdf")
"""

from __future__ import annotations

import io
import json
import logging
import os
import base64
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, List, Optional

from src.rag.ocr_processor import OCRDocument, OCRPage

logger = logging.getLogger(__name__)


class OCRProviderBase(ABC):
    """OCR 프로바이더 추상 베이스"""

    @abstractmethod
    def process_file(self, file_path: str, save_outputs: bool = False) -> OCRDocument:
        """파일을 OCR 처리하여 OCRDocument 반환"""
        ...

    @abstractmethod
    def is_available(self) -> bool:
        """프로바이더 사용 가능 여부"""
        ...


class LocalPaddleOCRProvider(OCRProviderBase):
    """
    로컬 PaddleOCR-VL 프로바이더 (기존 로직 래핑)
    
    GPU 필요. 개발/연구 환경에 적합.
    """

    def __init__(self, **kwargs):
        self._processor = None
        self._kwargs = kwargs

    def _get_processor(self):
        if self._processor is None:
            from src.rag.ocr_processor import PaddleOCRProcessor
            self._processor = PaddleOCRProcessor(**self._kwargs)
        return self._processor

    def process_file(self, file_path: str, save_outputs: bool = False) -> OCRDocument:
        processor = self._get_processor()
        return processor.process_file(file_path, save_outputs=save_outputs)

    def is_available(self) -> bool:
        try:
            from src.rag.ocr_processor import _PADDLEOCR_AVAILABLE
            return _PADDLEOCR_AVAILABLE
        except Exception:
            return False


class UpstageOCRProvider(OCRProviderBase):
    """
    Upstage Document AI API 프로바이더
    
    GPU 불필요. 프로덕션 환경에 적합.
    비용: Document AI API 과금 기준 적용
    
    참고: https://developers.upstage.ai/docs/apis/document-ai
    """

    def __init__(
        self,
        api_key: str = "",
        api_url: str = "https://api.upstage.ai/v1/document-ai/ocr",
    ):
        self.api_key = api_key or os.getenv("UPSTAGE_API_KEY", "")
        self.api_url = api_url

    def process_file(self, file_path: str, save_outputs: bool = False) -> OCRDocument:
        """Upstage Document AI API로 OCR 처리"""
        import requests

        if not self.api_key:
            raise ValueError("UPSTAGE_API_KEY가 설정되지 않았습니다.")

        file_path = Path(file_path)
        if not file_path.exists():
            raise FileNotFoundError(f"파일을 찾을 수 없습니다: {file_path}")

        logger.info(f"📄 Upstage OCR 처리: {file_path.name}")

        with open(file_path, "rb") as f:
            response = requests.post(
                self.api_url,
                headers={"Authorization": f"Bearer {self.api_key}"},
                files={"document": (file_path.name, f)},
                data={"output_formats": '["text", "html"]'},
            )

        if response.status_code != 200:
            raise RuntimeError(f"Upstage API 오류 ({response.status_code}): {response.text[:300]}")

        data = response.json()
        pages = []

        # API 응답 파싱
        page_texts = data.get("pages", [])
        if not page_texts and "text" in data:
            # 단일 결과인 경우
            page_texts = [{"text": data["text"]}]

        for idx, page_data in enumerate(page_texts):
            text = page_data.get("text", "")
            pages.append(OCRPage(
                page_num=idx + 1,
                markdown=text,
                raw_text=text,
                tables=[],
                formulas=[],
                metadata={"provider": "upstage"},
            ))

        full_markdown = "\n\n---\n\n".join(p.markdown for p in pages)

        return OCRDocument(
            source=str(file_path),
            total_pages=len(pages),
            pages=pages,
            full_markdown=full_markdown,
            metadata={"provider": "upstage", "api_response_keys": list(data.keys())},
        )

    def is_available(self) -> bool:
        return bool(self.api_key)


def get_ocr_provider(provider: Optional[str] = None, **kwargs) -> OCRProviderBase:
    """
    설정에 따라 적절한 OCR 프로바이더 반환
    
    Args:
        provider: "local" 또는 "upstage" (None이면 환경변수에서 결정)
        **kwargs: 프로바이더별 추가 인자
    """
    if provider is None:
        provider = os.getenv("OCR_PROVIDER", "local")

    if provider == "upstage":
        return UpstageOCRProvider(**kwargs)
    else:
        return LocalPaddleOCRProvider(**kwargs)
