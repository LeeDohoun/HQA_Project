from abc import ABC, abstractmethod
import aiohttp
import asyncio
from typing import List, Dict, Any

class BaseCrawler(ABC):
    def __init__(self, source_type: str):
        self.source_type = source_type
        
    @abstractmethod
    async def fetch(self, session: aiohttp.ClientSession, url: str) -> Dict[str, Any]:
        """Fetch data from a specific URL"""
        pass
        
    @abstractmethod
    async def parse(self, html: str) -> List[Dict[str, Any]]:
        """Parse the fetched HTML/JSON into structured metadata"""
        pass

    async def run(self, urls: List[str]):
        """General runner to execute crawling over a list of URLs concurrently"""
        async with aiohttp.ClientSession() as session:
            tasks = [self.fetch(session, url) for url in urls]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            return results
