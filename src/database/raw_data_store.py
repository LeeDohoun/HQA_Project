# 파일: src/database/raw_data_store.py
"""
원본 데이터 저장소 (PostgreSQL)
- 수집한 원본 데이터 보존
- 중복 체크
- 나중에 재처리/재임베딩 가능
"""

import os
import psycopg2
import psycopg2.extras
from typing import List, Dict, Optional
from datetime import datetime
from dataclasses import dataclass
from pathlib import Path

from src.config.settings import get_data_dir


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
    pdf_path: Optional[str] = None
    content_text: Optional[str] = None
    created_at: Optional[str] = None
    is_embedded: bool = False


@dataclass
class RawNews:
    """뉴스 원본 데이터"""
    id: Optional[int] = None
    stock_code: str = ""
    stock_name: str = ""
    title: str = ""
    summary: str = ""
    content: Optional[str] = None
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
    content: Optional[str] = None
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


def _get_default_dsn() -> str:
    """환경변수에서 DATABASE_URL을 가져와 psycopg2 형식으로 변환"""
    url = os.getenv("DATABASE_URL", "postgresql://postgres:password@localhost:5432/hqa")
    url = url.replace("postgresql+asyncpg://", "postgresql://")
    return url


class RawDataStore:
    """원본 데이터 PostgreSQL 저장소"""

    def __init__(
        self,
        db_url: Optional[str] = None,
        files_dir: Optional[str] = None,
        # legacy parameter - ignored, kept for backward compatibility
        db_path: Optional[str] = None,
    ):
        """
        Args:
            db_url: PostgreSQL 연결 URL
            files_dir: PDF 등 파일 저장 디렉토리
            db_path: (무시됨) 하위 호환성용
        """
        self.db_url = db_url or _get_default_dsn()
        self.files_dir = Path(files_dir) if files_dir else get_data_dir() / "files"

        # 디렉토리 생성
        self.files_dir.mkdir(parents=True, exist_ok=True)

        # DB 초기화
        self._init_db()
        print(f"📦 원본 데이터 저장소 초기화 (PostgreSQL)")

    def _get_conn(self):
        """PostgreSQL 연결 반환"""
        return psycopg2.connect(self.db_url)

    def _init_db(self):
        """데이터베이스 테이블 생성"""
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS reports (
                id SERIAL PRIMARY KEY,
                stock_code TEXT NOT NULL,
                stock_name TEXT,
                title TEXT NOT NULL,
                broker TEXT,
                report_date TEXT,
                link TEXT UNIQUE,
                pdf_path TEXT,
                content_text TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_embedded BOOLEAN DEFAULT FALSE
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS news (
                id SERIAL PRIMARY KEY,
                stock_code TEXT NOT NULL,
                stock_name TEXT,
                title TEXT NOT NULL,
                summary TEXT,
                content TEXT,
                source TEXT,
                url TEXT UNIQUE,
                published_at TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_embedded BOOLEAN DEFAULT FALSE
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS disclosures (
                id SERIAL PRIMARY KEY,
                stock_code TEXT NOT NULL,
                stock_name TEXT,
                corp_code TEXT,
                report_name TEXT NOT NULL,
                receipt_no TEXT UNIQUE,
                receipt_date TEXT,
                submitter TEXT,
                content TEXT,
                url TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_embedded BOOLEAN DEFAULT FALSE
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS price_data (
                id SERIAL PRIMARY KEY,
                stock_code TEXT NOT NULL,
                stock_name TEXT,
                date TEXT NOT NULL,
                open_price REAL,
                high_price REAL,
                low_price REAL,
                close_price REAL,
                volume BIGINT,
                ma150 REAL,
                is_bullish BOOLEAN,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(stock_code, date)
            )
        """)

        cursor.execute("CREATE INDEX IF NOT EXISTS idx_reports_stock ON reports(stock_code)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_news_stock ON news(stock_code)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_disclosures_stock ON disclosures(stock_code)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_price_stock_date ON price_data(stock_code, date)")

        conn.commit()
        conn.close()

    # ==================== 리포트 ====================

    def save_report(self, report: RawReport) -> int:
        """리포트 저장 (중복 시 무시)"""
        conn = self._get_conn()
        cursor = conn.cursor()

        try:
            cursor.execute("""
                INSERT INTO reports
                (stock_code, stock_name, title, broker, report_date, link, pdf_path, content_text)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (link) DO NOTHING
                RETURNING id
            """, (
                report.stock_code, report.stock_name, report.title,
                report.broker, report.report_date, report.link,
                report.pdf_path, report.content_text
            ))
            conn.commit()
            row = cursor.fetchone()
            return row[0] if row else 0
        finally:
            conn.close()

    def get_reports(
        self,
        stock_code: Optional[str] = None,
        not_embedded_only: bool = False,
        limit: int = 100
    ) -> List[RawReport]:
        """리포트 조회"""
        conn = self._get_conn()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        query = "SELECT * FROM reports WHERE TRUE"
        params: list = []

        if stock_code:
            query += " AND stock_code = %s"
            params.append(stock_code)
        if not_embedded_only:
            query += " AND is_embedded = FALSE"

        query += " ORDER BY created_at DESC LIMIT %s"
        params.append(limit)

        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()

        return [RawReport(**dict(row)) for row in rows]

    def is_report_exists(self, link: str) -> bool:
        """리포트 중복 체크"""
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM reports WHERE link = %s", (link,))
        exists = cursor.fetchone() is not None
        conn.close()
        return exists

    # ==================== 뉴스 ====================

    def save_news(self, news: RawNews) -> int:
        """뉴스 저장 (중복 시 무시)"""
        conn = self._get_conn()
        cursor = conn.cursor()

        try:
            cursor.execute("""
                INSERT INTO news
                (stock_code, stock_name, title, summary, content, source, url, published_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (url) DO NOTHING
                RETURNING id
            """, (
                news.stock_code, news.stock_name, news.title,
                news.summary, news.content, news.source,
                news.url, news.published_at
            ))
            conn.commit()
            row = cursor.fetchone()
            return row[0] if row else 0
        finally:
            conn.close()

    def get_news(
        self,
        stock_code: Optional[str] = None,
        not_embedded_only: bool = False,
        limit: int = 100
    ) -> List[RawNews]:
        """뉴스 조회"""
        conn = self._get_conn()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        query = "SELECT * FROM news WHERE TRUE"
        params: list = []

        if stock_code:
            query += " AND stock_code = %s"
            params.append(stock_code)
        if not_embedded_only:
            query += " AND is_embedded = FALSE"

        query += " ORDER BY created_at DESC LIMIT %s"
        params.append(limit)

        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()

        return [RawNews(**dict(row)) for row in rows]

    def is_news_exists(self, url: str) -> bool:
        """뉴스 중복 체크"""
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM news WHERE url = %s", (url,))
        exists = cursor.fetchone() is not None
        conn.close()
        return exists

    # ==================== 공시 ====================

    def save_disclosure(self, disclosure: RawDisclosure) -> int:
        """공시 저장 (중복 시 무시)"""
        conn = self._get_conn()
        cursor = conn.cursor()

        try:
            cursor.execute("""
                INSERT INTO disclosures
                (stock_code, stock_name, corp_code, report_name, receipt_no,
                 receipt_date, submitter, content, url)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (receipt_no) DO NOTHING
                RETURNING id
            """, (
                disclosure.stock_code, disclosure.stock_name, disclosure.corp_code,
                disclosure.report_name, disclosure.receipt_no, disclosure.receipt_date,
                disclosure.submitter, disclosure.content, disclosure.url
            ))
            conn.commit()
            row = cursor.fetchone()
            return row[0] if row else 0
        finally:
            conn.close()

    def get_disclosures(
        self,
        stock_code: Optional[str] = None,
        not_embedded_only: bool = False,
        limit: int = 100
    ) -> List[RawDisclosure]:
        """공시 조회"""
        conn = self._get_conn()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        query = "SELECT * FROM disclosures WHERE TRUE"
        params: list = []

        if stock_code:
            query += " AND stock_code = %s"
            params.append(stock_code)
        if not_embedded_only:
            query += " AND is_embedded = FALSE"

        query += " ORDER BY created_at DESC LIMIT %s"
        params.append(limit)

        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()

        return [RawDisclosure(**dict(row)) for row in rows]

    # ==================== 주가 ====================

    def save_price_data(self, price: RawPriceData) -> int:
        """주가 데이터 저장 (중복 시 업데이트)"""
        conn = self._get_conn()
        cursor = conn.cursor()

        try:
            cursor.execute("""
                INSERT INTO price_data
                (stock_code, stock_name, date, open_price, high_price,
                 low_price, close_price, volume, ma150, is_bullish)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (stock_code, date) DO UPDATE SET
                    open_price = EXCLUDED.open_price,
                    high_price = EXCLUDED.high_price,
                    low_price = EXCLUDED.low_price,
                    close_price = EXCLUDED.close_price,
                    volume = EXCLUDED.volume,
                    ma150 = EXCLUDED.ma150,
                    is_bullish = EXCLUDED.is_bullish
                RETURNING id
            """, (
                price.stock_code, price.stock_name, price.date,
                price.open_price, price.high_price, price.low_price,
                price.close_price, price.volume, price.ma150, price.is_bullish
            ))
            conn.commit()
            row = cursor.fetchone()
            return row[0] if row else 0
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
        conn = self._get_conn()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        query = "SELECT * FROM price_data WHERE stock_code = %s"
        params: list = [stock_code]

        if start_date:
            query += " AND date >= %s"
            params.append(start_date)
        if end_date:
            query += " AND date <= %s"
            params.append(end_date)

        query += " ORDER BY date DESC LIMIT %s"
        params.append(limit)

        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()

        return [RawPriceData(**dict(row)) for row in rows]

    # ==================== 임베딩 상태 관리 ====================

    def mark_as_embedded(self, table: str, ids: List[int]):
        """임베딩 완료 표시"""
        if not ids:
            return

        conn = self._get_conn()
        cursor = conn.cursor()

        placeholders = ",".join(["%s"] * len(ids))
        cursor.execute(f"UPDATE {table} SET is_embedded = TRUE WHERE id IN ({placeholders})", ids)

        conn.commit()
        conn.close()

    # ==================== 통계 ====================

    def get_stats(self) -> Dict:
        """저장소 통계"""
        conn = self._get_conn()
        cursor = conn.cursor()

        stats = {}
        for table in ['reports', 'news', 'disclosures', 'price_data']:
            cursor.execute(f"SELECT COUNT(*) FROM {table}")
            total = cursor.fetchone()[0]

            if table != 'price_data':
                cursor.execute(f"SELECT COUNT(*) FROM {table} WHERE is_embedded = TRUE")
                embedded = cursor.fetchone()[0]
                stats[table] = {"total": total, "embedded": embedded}
            else:
                stats[table] = {"total": total}

        conn.close()

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

    report = RawReport(
        stock_code="005930",
        stock_name="삼성전자",
        title="테스트 리포트",
        broker="테스트증권",
        report_date="2025-01-01",
        link="https://example.com/test"
    )
    store.save_report(report)

    print(store.get_stats())
