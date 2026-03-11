import aiohttp
import asyncio
import hashlib
from bs4 import BeautifulSoup
from typing import List, Dict, Any
from datetime import datetime

from src.data_pipeline.crawlers.base import BaseCrawler
from src.data_pipeline.schemas.metadata import NaverNewsMetadata, DocumentSchema

class NaverNewsCrawler(BaseCrawler):
    def __init__(self):
        super().__init__(source_type="naver_news")
        # User-Agent to prevent bot blocking
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }

    async def fetch(self, session: aiohttp.ClientSession, url: str) -> str:
        """Fetch raw HTML of a single news article"""
        try:
            async with session.get(url, headers=self.headers, timeout=10) as response:
                response.raise_for_status()
                html = await response.text()
                return html
        except Exception as e:
            print(f"Failed to fetch {url}: {e}")
            return ""

    async def parse(self, html: str, url: str) -> Dict[str, Any]:
        """Parse the Naver News HTML into DocumentSchema format"""
        if not html:
            return None

        soup = BeautifulSoup(html, "lxml")
        
        try:
            # 제목 (Title)
            title_tag = soup.select_one("h2#title_area span")
            title = title_tag.text.strip() if title_tag else "No Title"

            # 본문 (Content)
            content_tag = soup.select_one("article#dic_area")
            # Strip out images and scripts if needed before extracting text
            if content_tag:
                content = content_tag.get_text(separator="\n", strip=True)
            else:
                content = ""

            # 언론사 (Publisher)
            publisher_tag = soup.select_one(".media_end_head_top_logo img")
            publisher = publisher_tag["title"] if publisher_tag and "title" in publisher_tag.attrs else "Unknown"

            # 발행일 (Published Date)
            date_tag = soup.select_one(".media_end_head_info_datestamp_time")
            published_at = date_tag["data-date-time"] if date_tag and "data-date-time" in date_tag.attrs else datetime.utcnow().isoformat()

            if not content:
                print(f"Could not parse content from {url}")
                return None

            # Generate Doc ID and Hash
            raw_text = title + content
            content_hash = hashlib.sha256(raw_text.encode('utf-8')).hexdigest()
            doc_id = hashlib.md5(url.encode('utf-8')).hexdigest()

            # Metadata Validation
            metadata = NaverNewsMetadata(
                doc_id=doc_id,
                source_type=self.source_type,
                content_hash=content_hash,
                published_at=published_at,
                publisher=publisher,
                url=url,
                collected_at=datetime.utcnow().isoformat()
            )

            doc = DocumentSchema(
                metadata=metadata.model_dump(),
                page_content=content
            )

            return doc.model_dump()
            
        except Exception as e:
            print(f"Failed to parse Naver News HTML from {url}: {e}")
            return None

    async def process_urls(self, urls: List[str]) -> List[Dict[str, Any]]:
        """Fetch and parse multiple URLs concurrently"""
        records = []
        async with aiohttp.ClientSession() as session:
            # 1. Fetch HTMLs concurrently
            fetch_tasks = [self.fetch(session, url) for url in urls]
            html_results = await asyncio.gather(*fetch_tasks, return_exceptions=True)
            
            # 2. Parse HTMLs
            for url, result in zip(urls, html_results):
                if isinstance(result, Exception) or not result:
                    continue
                parsed = await self.parse(result, url)
                if parsed:
                    records.append(parsed)
                    
        return records
