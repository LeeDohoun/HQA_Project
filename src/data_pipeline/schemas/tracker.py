import enum
from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, Enum, Text
from sqlalchemy.orm import declarative_base

Base = declarative_base()

class CrawlStatus(enum.Enum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    DONE = "DONE"
    FAILED = "FAILED"

class CrawlTracker(Base):
    __tablename__ = "crawl_tracker"

    id = Column(Integer, primary_key=True, index=True)
    url = Column(String, unique=True, index=True, nullable=False)
    source_type = Column(String, index=True, nullable=False) # naver_news, dart, naver_board
    status = Column(Enum(CrawlStatus), default=CrawlStatus.PENDING, index=True)
    retry_count = Column(Integer, default=0)
    last_error_message = Column(Text, nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
