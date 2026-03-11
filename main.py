from contextlib import asynccontextmanager
from fastapi import FastAPI
import uvicorn
import logging

from src.core.config import get_settings
from src.core.redis_client import redis_manager
from src.api.routes import pipeline

settings = get_settings()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifecycle manager for FastAPI app.
    Startup: Connect to Redis, DB, etc.
    Shutdown: Let go of connections gracefully.
    """
    logger.info("Initializing system dependencies...")
    await redis_manager.connect()
    
    yield
    
    logger.info("Shutting down gracefully...")
    await redis_manager.disconnect()

# Initializes FastAPI App
app = FastAPI(
    title=settings.PROJECT_NAME,
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    lifespan=lifespan
)

# Include Sub-Routers
app.include_router(pipeline.router, prefix=settings.API_V1_STR + "/pipeline", tags=["pipeline"])


@app.get("/")
async def root():
    return {
        "message": f"Welcome to {settings.PROJECT_NAME} API Base",
        "docs_url": "/docs"
    }

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)