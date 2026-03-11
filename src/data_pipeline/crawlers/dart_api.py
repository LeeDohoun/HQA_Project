import aiohttp
import asyncio
import hashlib
from typing import List, Dict, Any
from datetime import datetime
import json

from src.core.config import get_settings
from src.data_pipeline.crawlers.base import BaseCrawler
from src.data_pipeline.schemas.metadata import DartMetadata, DocumentSchema

settings = get_settings()

class DartCrawler(BaseCrawler):
    def __init__(self):
        super().__init__(source_type="dart")
        self.api_key = settings.DART_API_KEY
        self.base_url = "https://opendart.fss.or.kr/api"

    async def fetch(self, session: aiohttp.ClientSession, rcept_no: str) -> str:
        """Fetch report data via DART Open API. 
        Expects rcept_no as the 'url' arg
        """
        # For full text of a report, DART provides a document.xml in a ZIP file using /document.xml endpoint.
        # But for simplicity, we mock fetching the report sections using OpenDART if possible.
        # Real implementation would unzip and parse XBRL / XML.
        # As an MVP for this crawler layer, we'll demonstrate downloading the document zip
        doc_url = f"{self.base_url}/document.xml"
        params = {"crtfc_key": self.api_key, "rcept_no": rcept_no}
        
        try:
            async with session.get(doc_url, params=params, timeout=15) as response:
                if response.status == 200:
                    # In a real scenario, this response is a zip file containing XML.
                    # We pretend it's a string or we parse it later in processing layer
                    # Returning a string to keep the interface simple
                    return await response.read()
                return b""
        except Exception as e:
            print(f"Failed to fetch DART report {rcept_no}: {e}")
            return b""

    async def parse(self, raw_data: bytes, rcept_no: str) -> Dict[str, Any]:
        """Parse DART XML / Zip data"""
        if not raw_data:
            return None
            
        # [MOCK] We pretend to extract section text
        # Because full DART parsing requires `zipfile` and `xml.etree.ElementTree`
        extracted_text = "DART report parsed text mock. Section 1: Business Overview..."
        section_name = "Business Overview"
        report_type = "Quarterly"

        # Generate hashes
        content_hash = hashlib.sha256(extracted_text.encode('utf-8')).hexdigest()
        doc_id = f"DART_{rcept_no}"

        metadata = DartMetadata(
            doc_id=doc_id,
            source_type=self.source_type,
            content_hash=content_hash,
            report_type=report_type,
            section_name=section_name,
            collected_at=datetime.utcnow().isoformat()
        )

        doc = DocumentSchema(
            metadata=metadata.model_dump(),
            page_content=extracted_text
        )

        return doc.model_dump()

    async def process_rcept_nos(self, rcept_nos: List[str]) -> List[Dict[str, Any]]:
        records = []
        async with aiohttp.ClientSession() as session:
            fetch_tasks = [self.fetch(session, no) for no in rcept_nos]
            results = await asyncio.gather(*fetch_tasks, return_exceptions=True)
            
            for no, res in zip(rcept_nos, results):
                if isinstance(res, Exception) or not res:
                    continue
                parsed = await self.parse(res, no)
                if parsed:
                    records.append(parsed)
                    
        return records
