from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from src.core.config import get_settings

settings = get_settings()

engine = create_async_engine(
    settings.async_database_url, 
    echo=False, 
    pool_pre_ping=True
)

AsyncSessionLocal = async_sessionmaker(
    engine, 
    class_=AsyncSession, 
    expire_on_commit=False
)

async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
