import asyncio
import json
from src.core.redis_client import get_redis_client

async def start_worker(queue_name: str = "chunk_queue"):
    """
    Background worker to consume messages from Redis Queue
    and forward them to ChromaDB via LangChain embeddings.
    """
    print(f"Starting worker for queue: {queue_name}")
    redis = await get_redis_client()
    
    while True:
        try:
            # Block until a message is available in the queue
            result = await redis.brpop(queue_name, timeout=0)
            if result:
                _, message = result
                data = json.loads(message)
                file_path = data.get("file_path")
                
                print(f"Processing file: {file_path}")
                # TODO: Implement chunking logic on file_path
                # TODO: Implement vector embedding and ChromaDB upsert
                
                await asyncio.sleep(0.1)  # Simulate processing time
                
        except Exception as e:
            print(f"Worker Error: {e}")
            await asyncio.sleep(1)

if __name__ == "__main__":
    asyncio.run(start_worker())
