# 파일: src/database/raw_data_store.py
"""
원본 데이터 저장소 (SQLite)
- 수집한 원본 데이터 보존
- 중복 체크
- 나중에 재처리/재임베딩 가능
"""

import os
import sqlite3
import json
from typing import List, Dict, Optional, Any
from datetime import datetime
from dataclasses import dataclass, asdict
from pathlib import Path


@dataclass
class RawReport:
    """증권사 리포트 원본 데이터"""
    id: Optional[int] = None
    stock_code: str = ""
    stock_name: str = ""
    title: str = ""
    broker: str = ""
    report_date: str = ""
    link: str = ""
    pdf_path: Optional[str] = None  # 로컬 PDF 파일 경로
    content_text: Optional[str] = None  # 추출된 텍스트
    created_at: Optional[str] = None
    is_embedded: bool = False  # RAG 임베딩 완료 여부


@dataclass
class RawNews:
    """뉴스 원본 데이터"""
    id: Optional[int] = None
    stock_code: str = ""
    stock_name: str = ""
    title: str = ""
    summary: str = ""
    content: Optional[str] = None  # 본문 전체
    source: str = ""
    url: str = ""
    published_at: str = ""
    created_at: Optional[str] = None
    is_embedded: bool = False


@dataclass
class RawDisclosure:
    """DART 공시 원본 데이터"""
    id: Optional[int] = None
    stock_code: str = ""
    stock_name: str = ""
    corp_code: str = ""
    report_name: str = ""
    receipt_no: str = ""
    receipt_date: str = ""
    submitter: str = ""
    content: Optional[str] = None  # 공시 본문
    url: str = ""
    created_at: Optional[str] = None
    is_embedded: bool = False


@dataclass
class RawPriceData:
    """주가 데이터"""
    id: Optional[int] = None
    stock_code: str = ""
    stock_name: str = ""
    date: str = ""
    open_price: float = 0
    high_price: float = 0
    low_price: float = 0
    close_price: float = 0
    volume: int = 0
    ma150: Optional[float] = None
    is_bullish: Optional[bool] = None
    created_at: Optional[str] = None


class RawDataStore:
    """원본 데이터 SQLite 저장소"""
    
    def __init__(
        self,
        db_path: str = "./database/raw_data.db",
        files_dir: str = "./data/files"
    ):
        """
        Args:
            db_path: SQLite DB 파일 경로
            files_dir: PDF 등 파일 저장 디렉토리
        """
        self.db_path = db_path
        self.files_dir = Path(files_dir)
        
        # 디렉토리 생성
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.files_dir.mkdir(parents=True, exist_ok=True)
        
        # DB 초기화
        self._init_db()
        print(f"📦 원본 데이터 저장소 초기화: {db_path}")
    
    def _init_db(self):
        """데이터베이스 테이블 생성"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # 증권사 리포트 테이블
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                stock_code TEXT NOT NULL,
                stock_name TEXT,
                title TEXT NOT NULL,
                broker TEXT,
                report_date TEXT,
                link TEXT UNIQUE,
                pdf_path TEXT,
                content_text TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                is_embedded INTEGER DEFAULT 0
            )
        """)
        
        # 뉴스 테이블
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS news (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                stock_code TEXT NOT NULL,
                stock_name TEXT,
                title TEXT NOT NULL,
                summary TEXT,
                content TEXT,
                source TEXT,
                url TEXT UNIQUE,
                published_at TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                is_embedded INTEGER DEFAULT 0
            )
        """)
        
        # DART 공시 테이블
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS disclosures (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                stock_code TEXT NOT NULL,
                stock_name TEXT,
                corp_code TEXT,
                report_name TEXT NOT NULL,
                receipt_no TEXT UNIQUE,
                receipt_date TEXT,
                submitter TEXT,
                content TEXT,
                url TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                is_embedded INTEGER DEFAULT 0
            )
        """)
        
        # 주가 데이터 테이블
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS price_data (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                stock_code TEXT NOT NULL,
                stock_name TEXT,
                date TEXT NOT NULL,
                open_price REAL,
                high_price REAL,
                low_price REAL,
                close_price REAL,
                volume INTEGER,
                ma150 REAL,
                is_bullish INTEGER,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(stock_code, date)
            )
        """)
        
        # 인덱스 생성
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_reports_stock ON reports(stock_code)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_news_stock ON news(stock_code)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_disclosures_stock ON disclosures(stock_code)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_price_stock_date ON price_data(stock_code, date)")
        
        conn.commit()
        conn.close()
    
    # ==================== 리포트 ====================
    
    def save_report(self, report: RawReport) -> int:
        """리포트 저장 (중복 시 무시)"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            cursor.execute("""
                INSERT OR IGNORE INTO reports 
                (stock_code, stock_name, title, broker, report_date, link, pdf_path, content_text)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                report.stock_code, report.stock_name, report.title,
                report.broker, report.report_date, report.link,
                report.pdf_path, report.content_text
            ))
            conn.commit()
            return cursor.lastrowid or 0
        finally:
            conn.close()
    
    def get_reports(
        self,
        stock_code: Optional[str] = None,
        not_embedded_only: bool = False,
        limit: int = 100
    ) -> List[RawReport]:
        """리포트 조회"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        query = "SELECT * FROM reports WHERE 1=1"
        params = []
        
        if stock_code:
            query += " AND stock_code = ?"
            params.append(stock_code)
        if not_embedded_only:
            query += " AND is_embedded = 0"
        
        query += f" ORDER BY created_at DESC LIMIT {limit}"
        
        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()
        
        return [RawReport(**dict(row)) for row in rows]
    
    def is_report_exists(self, link: str) -> bool:
        """리포트 중복 체크"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM reports WHERE link = ?", (link,))
        exists = cursor.fetchone() is not None
        conn.close()
        return exists
    
    # ==================== 뉴스 ====================
    
    def save_news(self, news: RawNews) -> int:
        """뉴스 저장 (중복 시 무시)"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            cursor.execute("""
                INSERT OR IGNORE INTO news 
                (stock_code, stock_name, title, summary, content, source, url, published_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                news.stock_code, news.stock_name, news.title,
                news.summary, news.content, news.source,
                news.url, news.published_at
            ))
            conn.commit()
            return cursor.lastrowid or 0
        finally:
            conn.close()
    
    def get_news(
        self,
        stock_code: Optional[str] = None,
        not_embedded_only: bool = False,
        limit: int = 100
    ) -> List[RawNews]:
        """뉴스 조회"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        query = "SELECT * FROM news WHERE 1=1"
        params = []
        
        if stock_code:
            query += " AND stock_code = ?"
            params.append(stock_code)
        if not_embedded_only:
            query += " AND is_embedded = 0"
        
        query += f" ORDER BY created_at DESC LIMIT {limit}"
        
        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()
        
        return [RawNews(**dict(row)) for row in rows]
    
    def is_news_exists(self, url: str) -> bool:
        """뉴스 중복 체크"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM news WHERE url = ?", (url,))
        exists = cursor.fetchone() is not None
        conn.close()
        return exists
    
    # ==================== 공시 ====================
    
    def save_disclosure(self, disclosure: RawDisclosure) -> int:
        """공시 저장 (중복 시 무시)"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            cursor.execute("""
                INSERT OR IGNORE INTO disclosures 
                (stock_code, stock_name, corp_code, report_name, receipt_no, 
                 receipt_date, submitter, content, url)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                disclosure.stock_code, disclosure.stock_name, disclosure.corp_code,
                disclosure.report_name, disclosure.receipt_no, disclosure.receipt_date,
                disclosure.submitter, disclosure.content, disclosure.url
            ))
            conn.commit()
            return cursor.lastrowid or 0
        finally:
            conn.close()
    
    def get_disclosures(
        self,
        stock_code: Optional[str] = None,
        not_embedded_only: bool = False,
        limit: int = 100
    ) -> List[RawDisclosure]:
        """공시 조회"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        query = "SELECT * FROM disclosures WHERE 1=1"
        params = []
        
        if stock_code:
            query += " AND stock_code = ?"
            params.append(stock_code)
        if not_embedded_only:
            query += " AND is_embedded = 0"
        
        query += f" ORDER BY created_at DESC LIMIT {limit}"
        
        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()
        
        return [RawDisclosure(**dict(row)) for row in rows]
    
    # ==================== 주가 ====================
    
    def save_price_data(self, price: RawPriceData) -> int:
        """주가 데이터 저장 (중복 시 업데이트)"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            cursor.execute("""
                INSERT OR REPLACE INTO price_data 
                (stock_code, stock_name, date, open_price, high_price, 
                 low_price, close_price, volume, ma150, is_bullish)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                price.stock_code, price.stock_name, price.date,
                price.open_price, price.high_price, price.low_price,
                price.close_price, price.volume, price.ma150, price.is_bullish
            ))
            conn.commit()
            return cursor.lastrowid or 0
        finally:
            conn.close()
    
    def get_price_data(
        self,
        stock_code: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        limit: int = 365
    ) -> List[RawPriceData]:
        """주가 데이터 조회"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        query = "SELECT * FROM price_data WHERE stock_code = ?"
        params = [stock_code]
        
        if start_date:
            query += " AND date >= ?"
            params.append(start_date)
        if end_date:
            query += " AND date <= ?"
            params.append(end_date)
        
        query += f" ORDER BY date DESC LIMIT {limit}"
        
        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()
        
        return [RawPriceData(**dict(row)) for row in rows]
    
    # ==================== 임베딩 상태 관리 ====================
    
    def mark_as_embedded(self, table: str, ids: List[int]):
        """임베딩 완료 표시"""
        if not ids:
            return
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        placeholders = ",".join("?" * len(ids))
        cursor.execute(f"UPDATE {table} SET is_embedded = 1 WHERE id IN ({placeholders})", ids)
        
        conn.commit()
        conn.close()
    
    # ==================== 통계 ====================
    
    def get_stats(self) -> Dict:
        """저장소 통계"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        stats = {}
        for table in ['reports', 'news', 'disclosures', 'price_data']:
            cursor.execute(f"SELECT COUNT(*) FROM {table}")
            total = cursor.fetchone()[0]
            
            if table != 'price_data':
                cursor.execute(f"SELECT COUNT(*) FROM {table} WHERE is_embedded = 1")
                embedded = cursor.fetchone()[0]
                stats[table] = {"total": total, "embedded": embedded}
            else:
                stats[table] = {"total": total}
        
        conn.close()
        
        # DB 파일 크기
        db_size = os.path.getsize(self.db_path) if os.path.exists(self.db_path) else 0
        stats["db_size_mb"] = round(db_size / (1024 * 1024), 2)
        
        # 파일 저장소 크기
        files_size = sum(f.stat().st_size for f in self.files_dir.rglob("*") if f.is_file())
        stats["files_size_mb"] = round(files_size / (1024 * 1024), 2)
        
        return stats
    
    def get_file_path(self, category: str, filename: str) -> Path:
        """파일 저장 경로 생성"""
        category_dir = self.files_dir / category
        category_dir.mkdir(parents=True, exist_ok=True)
        return category_dir / filename


# 테스트
if __name__ == "__main__":
    store = RawDataStore()
    
    # 테스트 데이터 저장
    report = RawReport(
        stock_code="005930",
        stock_name="삼성전자",
        title="테스트 리포트",
        broker="테스트증권",
        report_date="2025-01-01",
        link="https://example.com/test"
    )
    store.save_report(report)
    
    # 통계 확인
    print(store.get_stats())
