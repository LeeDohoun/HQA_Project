import redis.asyncio as redis
from typing import Optional
from src.core.config import get_settings

settings = get_settings()

class RedisClientManager:
    def __init__(self):
        self.redis: Optional[redis.Redis] = None

    async def connect(self):
        if self.redis is None:
            self.redis = redis.from_url(
                settings.redis_url, 
                encoding="utf-8", 
                decode_responses=True
            )
            # Tries to ping to ensure connection
            await self.redis.ping()
            print("Connected to Redis")

    async def disconnect(self):
        if self.redis is not None:
            await self.redis.close()
            self.redis = None
            print("Disconnected from Redis")

    async def publish(self, channel: str, message: str):
        if self.redis is None:
            raise Exception("Redis is not connected")
        await self.redis.publish(channel, message)

    def pubsub(self):
        if self.redis is None:
            raise Exception("Redis is not connected")
        return self.redis.pubsub()

    async def lpush(self, queue_name: str, message: str):
        """Push a message to a list (queue)"""
        if self.redis is None:
            raise Exception("Redis is not connected")
        await self.redis.lpush(queue_name, message)

    async def brpop(self, queue_name: str, timeout: int = 0):
        """Blocking pop from a list (queue)"""
        if self.redis is None:
            raise Exception("Redis is not connected")
        return await self.redis.brpop(queue_name, timeout=timeout)

redis_manager = RedisClientManager()

async def get_redis_client() -> redis.Redis:
    if redis_manager.redis is None:
        await redis_manager.connect()
    return redis_manager.redis
