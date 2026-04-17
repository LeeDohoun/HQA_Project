from __future__ import annotations

# File role:
# - Extract usable text from report PDFs for raw report ingestion.

import re
from dataclasses import dataclass, field
from typing import List


@dataclass
class PDFTextExtractionResult:
    text: str
    page_count: int = 0
    extracted_pages: int = 0
    page_texts: List[str] = field(default_factory=list)
    error: str = ""

    @property
    def success(self) -> bool:
        return bool(self.text.strip()) and not self.error


class PDFTextExtractor:
    """PyMuPDF-backed text extractor for securities reports."""

    def __init__(self, max_pages: int = 80):
        self.max_pages = max_pages

    def extract(self, pdf_bytes: bytes) -> PDFTextExtractionResult:
        if not pdf_bytes:
            return PDFTextExtractionResult(text="", error="empty_pdf_bytes")

        try:
            import fitz
        except ImportError:
            return PDFTextExtractionResult(text="", error="pymupdf_not_installed")

        try:
            with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
                page_count = int(doc.page_count)
                limit = min(page_count, self.max_pages) if self.max_pages else page_count
                page_texts: List[str] = []
                for page_index in range(limit):
                    page = doc.load_page(page_index)
                    text = self._clean_text(page.get_text("text") or "")
                    if text:
                        page_texts.append(text)

                return PDFTextExtractionResult(
                    text=self._clean_text("\n\n".join(page_texts)),
                    page_count=page_count,
                    extracted_pages=len(page_texts),
                    page_texts=page_texts,
                )
        except Exception as exc:
            return PDFTextExtractionResult(text="", error=str(exc))

    @staticmethod
    def _clean_text(text: str) -> str:
        text = (text or "").replace("\x00", " ")
        text = re.sub(r"[ \t\r\f\v]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()
