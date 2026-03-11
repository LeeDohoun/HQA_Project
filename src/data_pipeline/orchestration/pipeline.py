import asyncio
import json
from typing import List

from src.data_pipeline.crawlers.naver_news import NaverNewsCrawler
from src.data_pipeline.crawlers.dart_api import DartCrawler
from src.data_pipeline.crawlers.naver_board import NaverBoardCrawler
from src.data_pipeline.storage.tier1_datalake import DatalakeStorage
from src.core.redis_client import get_redis_client

async def run_crawling_pipeline():
    """
    Main orchestration function to scrape data, save it to Tier 1 Data Lake (JSONL),
    and publish the file path onto the Redis Message Queue for further processing.
    """
    print("🚀 Starting Data Collection Pipeline (Layer 1)")
    
    datalake = DatalakeStorage()
    redis = await get_redis_client()
    
    # 1. Initialize Crawlers
    news_crawler = NaverNewsCrawler()
    dart_crawler = DartCrawler()
    
    # Example Target URLs/IDs
    news_urls = [
        "https://n.news.naver.com/mnews/article/015/0004940001",
        "https://n.news.naver.com/mnews/article/015/0004940002"
    ]
    rcept_nos = ["20240311000101", "20240311000102"]
    
    # 2. Run Crawlers Concurrently
    print("📥 Crawling Naver News...")
    news_records = await news_crawler.process_urls(news_urls)
    
    print("📥 Crawling DART API...")
    dart_records = await dart_crawler.process_rcept_nos(rcept_nos)
    
    # 3. Save to Tier 1 Data Lake (JSONL)
    print("💾 Saving to Tier 1 Data Lake...")
    news_file_path = await datalake.save_jsonl(news_crawler.source_type, news_records)
    dart_file_path = await datalake.save_jsonl(dart_crawler.source_type, dart_records)
    
    # 4. Publish Event to Redis Queue for Tier 2 Embedding Workers
    print("📤 Publishing events to Redis Queue...")
    queue_name = "chunk_queue"
    
    if news_file_path:
        event = json.dumps({"source": "naver_news", "file_path": news_file_path})
        await redis.lpush(queue_name, event)
        print(f"Published: {news_file_path}")
        
    if dart_file_path:
        event = json.dumps({"source": "dart", "file_path": dart_file_path})
        await redis.lpush(queue_name, event)
        print(f"Published: {dart_file_path}")
        
    print("✅ Pipeline Completed!")

if __name__ == "__main__":
    asyncio.run(run_crawling_pipeline())
