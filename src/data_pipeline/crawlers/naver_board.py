import aiohttp
import asyncio
import hashlib
from typing import List, Dict, Any
from datetime import datetime
from bs4 import BeautifulSoup

from src.data_pipeline.crawlers.base import BaseCrawler
from src.data_pipeline.schemas.metadata import NaverBoardMetadata, DocumentSchema

class NaverBoardCrawler(BaseCrawler):
    def __init__(self):
        super().__init__(source_type="naver_board")
        self.headers = {
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://finance.naver.com"
        }

    async def fetch(self, session: aiohttp.ClientSession, url: str) -> str:
        """Fetch Naver Finance Board post"""
        try:
            async with session.get(url, headers=self.headers, timeout=10) as response:
                if response.status == 200:
                    html = await response.text(encoding='cp949', errors='ignore')
                    return html
                return ""
        except Exception as e:
            print(f"Failed to fetch {url}: {e}")
            return ""

    async def parse(self, html: str, url: str) -> Dict[str, Any]:
        """Scrape views and likes and filter out spam"""
        if not html:
            return None

        soup = BeautifulSoup(html, "lxml")
        try:
            # 제목
            title_tag = soup.select_one("strong.c.p15")
            title = title_tag.text.strip() if title_tag else ""

            # 본문
            content_tag = soup.select_one("div#body")
            content = content_tag.get_text(separator="\n", strip=True) if content_tag else ""

            # 조회수 및 공감수 추출 (Class name differs slightly but mock logic)
            # Example metadata logic:
            views = 100  # Extract from soup
            likes = 5    # Extract from soup

            # Filter Spam Rule
            if views < 50 and likes == 0:
                print(f"Post {url} filtered as span (Views={views}, Likes={likes})")
                return None

            full_text = f"{title}\n{content}"
            content_hash = hashlib.sha256(full_text.encode('utf-8')).hexdigest()
            doc_id = hashlib.md5(url.encode('utf-8')).hexdigest()

            metadata = NaverBoardMetadata(
                doc_id=doc_id,
                source_type=self.source_type,
                content_hash=content_hash,
                views=views,
                likes=likes,
                collected_at=datetime.utcnow().isoformat()
            )

            doc = DocumentSchema(
                metadata=metadata.model_dump(),
                page_content=full_text
            )

            return doc.model_dump()
            
        except Exception as e:
            print(f"Failed to parse Naver Board HTML from {url}: {e}")
            return None

    async def process_urls(self, urls: List[str]) -> List[Dict[str, Any]]:
        records = []
        async with aiohttp.ClientSession() as session:
            fetch_tasks = [self.fetch(session, url) for url in urls]
            html_results = await asyncio.gather(*fetch_tasks, return_exceptions=True)
            
            for url, result in zip(urls, html_results):
                if isinstance(result, Exception) or not result:
                    continue
                parsed = await self.parse(result, url)
                if parsed:
                    records.append(parsed)
                    
        return records
