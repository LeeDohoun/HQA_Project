import datetime
from pydantic import BaseModel, Field
from typing import Optional, Any, Dict

class BaseMetadata(BaseModel):
    """
    Tier 1 Data Lake (JSONL) 공통 메타데이터 원칙
    """
    schema_version: str = Field(default="1.0.0", description="Schema versioning")
    doc_id: str = Field(..., description="Unique document ID (e.g. hash of URL or ID)")
    source_type: str = Field(..., description="Source of data: naver_news, dart, naver_board")
    collected_at: str = Field(default_factory=lambda: datetime.datetime.utcnow().isoformat())
    ticker_code: Optional[str] = Field(None, description="Stock ticker code if applicable")
    company_name: Optional[str] = Field(None, description="Company Name if applicable")
    content_hash: str = Field(..., description="SHA-256 hash of the content to prevent duplication")

class NaverNewsMetadata(BaseMetadata):
    published_at: str = Field(..., description="Publish date of the news")
    publisher: str = Field(..., description="Name of the news publisher")
    url: str = Field(..., description="Original URL of the news")

class DartMetadata(BaseMetadata):
    report_type: str = Field(..., description="Type of the report (e.g. quarterly, annual)")
    section_name: str = Field(..., description="Table of contents section name")

class NaverBoardMetadata(BaseMetadata):
    views: int = Field(0, description="Number of views")
    likes: int = Field(0, description="Number of likes")

class DocumentSchema(BaseModel):
    metadata: Dict[str, Any]
    page_content: str
