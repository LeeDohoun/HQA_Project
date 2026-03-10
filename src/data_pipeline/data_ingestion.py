# 파일: src/data_pipeline/data_ingestion.py
"""
데이터 수집 → 원본 저장 → RAG 벡터화 통합 파이프라인

아키텍처 (v0.3.0):
- OCR: PaddleOCR-VL-1.5 (텍스트 전용 변환)
- Embedding: Snowflake Arctic Korean (1024 dim)
- Reranker: Qwen3-Reranker-0.6B
- Vector DB: ChromaDB

흐름:
1. 데이터 수집 (크롤러, API)
2. 원본 DB 저장 (PostgreSQL) - 중복 체크
3. RAG 벡터화 (ChromaDB) - 미처리 데이터만
"""

import os
from typing import List, Dict, Optional, Tuple
from datetime import datetime
import logging

# 데이터 수집기
from .price_loader import PriceLoader
from .dart_collector import DARTCollector, Disclosure
from .news_crawler import NewsCrawler, NewsArticle
from .crawler import ReportCrawler, Report

# 원본 데이터 저장소
from src.database.raw_data_store import (
    RawDataStore,
    RawReport,
    RawNews,
    RawDisclosure,
    RawPriceData
)

# RAG 모듈 (텍스트 전용)
from src.rag import RAGRetriever

logger = logging.getLogger(__name__)


class DataIngestionPipeline:
    """
    데이터 수집 → 원본 저장 → RAG 벡터화 통합 파이프라인
    
    아키텍처:
    - 모든 문서는 PaddleOCR-VL-1.5로 텍스트 변환
    - Snowflake Arctic Korean 임베딩
    - Qwen3-Reranker로 검색 결과 리랭킹
    """
    
    def __init__(
        self,
        db_path: Optional[str] = None,
        files_dir: str = "./data/files",
        vector_persist_dir: str = "./database/chroma_db",
        collection_name: str = "stock_data",
        embedding_type: str = "default",  # Snowflake Arctic Korean
        # 리랭커 설정
        use_reranker: bool = True,
        retrieval_k: int = 20,
        rerank_top_k: int = 3
    ):
        """
        Args:
            db_path: (무시됨) 하위 호환성용
            files_dir: PDF 등 파일 저장 경로
            vector_persist_dir: 벡터 DB 저장 경로
            collection_name: 벡터 컬렉션 이름
            embedding_type: 임베딩 모델 타입
            use_reranker: 리랭커 사용 여부
            retrieval_k: 벡터 검색 후보 수
            rerank_top_k: 리랭킹 후 최종 반환 수
        """
        print("\n" + "="*60)
        print("🚀 데이터 수집 파이프라인 초기화")
        print("="*60)
        
        # 1. 데이터 수집기
        print("\n📦 [1/4] 데이터 수집기 초기화...")
        self.price_loader = PriceLoader()
        self.dart_collector = DARTCollector()
        self.news_crawler = NewsCrawler()
        self.report_crawler = ReportCrawler(download_dir=os.path.join(files_dir, "reports"))
        print("   ✅ 크롤러 초기화 완료")
        
        # 2. 원본 데이터 저장소 (PostgreSQL)
        print("\n💾 [2/4] 원본 데이터 저장소 초기화...")
        self.raw_store = RawDataStore(db_path=db_path, files_dir=files_dir)
        print(f"   ✅ PostgreSQL DB 연결 완료")
        
        # 3. RAG 검색기 (PaddleOCR + Snowflake Arctic + Qwen3 Reranker)
        print("\n🧠 [3/4] RAG 파이프라인 초기화...")
        self.retriever = RAGRetriever(
            persist_dir=vector_persist_dir,
            collection_name=collection_name,
            embedding_type=embedding_type,
            use_reranker=use_reranker,
            retrieval_k=retrieval_k,
            rerank_top_k=rerank_top_k,
            reranker_task_type="finance"
        )
        print(f"   ✅ 벡터 DB: {vector_persist_dir}")
        print(f"   ✅ 리랭커: {'활성화' if use_reranker else '비활성화'}")
        
        # 4. 설정 저장
        print("\n⚙️ [4/4] 설정 저장...")
        self.files_dir = files_dir
        self.embedding_type = embedding_type
        
        print("\n" + "="*60)
        print("✅ 파이프라인 초기화 완료!")
        print("   - OCR: PaddleOCR-VL-1.5")
        print("   - Embedding: Snowflake Arctic Korean")
        print(f"   - Reranker: Qwen3-Reranker-0.6B ({'ON' if use_reranker else 'OFF'})")
        print("="*60 + "\n")
    
    # ==================== 메인 수집 함수 ====================
    
    def ingest_stock_data(
        self,
        stock_code: str,
        stock_name: str,
        include_reports: bool = True,
        include_news: bool = True,
        include_dart: bool = True,
        include_price: bool = True,
        auto_embed: bool = True
    ) -> Dict:
        """
        특정 종목의 모든 데이터 수집 → 원본 저장 → RAG 벡터화
        
        Args:
            stock_code: 종목코드
            stock_name: 종목명
            include_reports: 증권사 리포트 포함 여부
            include_news: 뉴스 포함 여부
            include_dart: DART 공시 포함 여부
            include_price: 주가 데이터 포함 여부
            auto_embed: 수집 후 자동으로 RAG 임베딩 여부
            
        Returns:
            수집 결과 요약
        """
        print(f"\n{'='*60}")
        print(f"📊 [{stock_name}({stock_code})] 데이터 수집 시작")
        print(f"{'='*60}")
        
        results = {
            "stock_code": stock_code,
            "stock_name": stock_name,
            "timestamp": datetime.now().isoformat(),
            "collected": {"reports": 0, "news": 0, "disclosures": 0, "price": 0},
            "new_items": {"reports": 0, "news": 0, "disclosures": 0},
            "embedded": {"reports": 0, "news": 0, "disclosures": 0}
        }
        
        # 1. 주가 데이터 수집 및 저장
        if include_price:
            results["collected"]["price"] = self._collect_price(stock_code, stock_name)
        
        # 2. 증권사 리포트 수집 및 저장
        if include_reports:
            collected, new = self._collect_reports(stock_code, stock_name)
            results["collected"]["reports"] = collected
            results["new_items"]["reports"] = new
        
        # 3. 뉴스 수집 및 저장
        if include_news:
            collected, new = self._collect_news(stock_code, stock_name)
            results["collected"]["news"] = collected
            results["new_items"]["news"] = new
        
        # 4. DART 공시 수집 및 저장
        if include_dart:
            collected, new = self._collect_disclosures(stock_code, stock_name)
            results["collected"]["disclosures"] = collected
            results["new_items"]["disclosures"] = new
        
        # 5. 미임베딩 데이터 → RAG 벡터화
        if auto_embed:
            print(f"\n🔄 RAG 벡터화 시작...")
            embedded = self.embed_pending_data(stock_code)
            results["embedded"] = embedded
        
        # 결과 요약
        self._print_summary(results)
        
        return results
    
    # ==================== 개별 수집 함수 ====================
    
    def _collect_price(self, stock_code: str, stock_name: str) -> int:
        """주가 데이터 수집 및 저장"""
        print(f"\n📈 [1/4] 주가 데이터 수집...")
        
        try:
            df = self.price_loader.get_stock_data(stock_code, days=300)
            
            if len(df) < 150:
                print(f"   ⚠️ 데이터 부족 ({len(df)}일)")
                return 0
            
            # 150일 이평선 계산
            df['MA150'] = df['Close'].rolling(window=150).mean()
            
            # 최근 데이터만 저장 (최근 30일)
            recent_df = df.tail(30)
            count = 0
            
            for date, row in recent_df.iterrows():
                is_bullish = row['Close'] > row['MA150'] if row['MA150'] else None
                
                price_data = RawPriceData(
                    stock_code=stock_code,
                    stock_name=stock_name,
                    date=date.strftime("%Y-%m-%d"),
                    open_price=row['Open'],
                    high_price=row['High'],
                    low_price=row['Low'],
                    close_price=row['Close'],
                    volume=int(row['Volume']),
                    ma150=row['MA150'] if row['MA150'] else None,
                    is_bullish=is_bullish
                )
                self.raw_store.save_price_data(price_data)
                count += 1
            
            print(f"   ✅ {count}일치 주가 데이터 저장 완료")
            return count
            
        except Exception as e:
            print(f"   ❌ 주가 데이터 오류: {e}")
            logger.exception("주가 데이터 수집 오류")
            return 0
    
    def _collect_reports(self, stock_code: str, stock_name: str) -> Tuple[int, int]:
        """증권사 리포트 수집 및 저장"""
        print(f"\n📑 [2/4] 증권사 리포트 수집...")
        
        try:
            reports = self.report_crawler.fetch_and_download(stock_code, max_count=5)
            new_count = 0
            
            for report in reports:
                # 중복 체크
                if self.raw_store.is_report_exists(report['link']):
                    continue
                
                # 원본 저장
                raw_report = RawReport(
                    stock_code=stock_code,
                    stock_name=stock_name,
                    title=report['title'],
                    broker=report['broker'],
                    report_date=report['date'],
                    link=report['link'],
                    pdf_path=report.get('local_path')
                )
                self.raw_store.save_report(raw_report)
                new_count += 1
            
            print(f"   ✅ {len(reports)}개 수집, {new_count}개 신규 저장")
            return len(reports), new_count
            
        except Exception as e:
            print(f"   ❌ 리포트 수집 오류: {e}")
            logger.exception("리포트 수집 오류")
            return 0, 0
    
    def _collect_news(self, stock_code: str, stock_name: str) -> Tuple[int, int]:
        """뉴스 수집 및 저장"""
        print(f"\n📰 [3/4] 뉴스 수집...")
        
        try:
            articles = self.news_crawler.fetch_stock_news(stock_code, stock_name, max_count=10)
            new_count = 0
            
            for article in articles:
                # 중복 체크
                if self.raw_store.is_news_exists(article.url):
                    continue
                
                # 원본 저장
                raw_news = RawNews(
                    stock_code=stock_code,
                    stock_name=stock_name,
                    title=article.title,
                    summary=article.summary,
                    source=article.source,
                    url=article.url,
                    published_at=article.published_at
                )
                self.raw_store.save_news(raw_news)
                new_count += 1
            
            print(f"   ✅ {len(articles)}개 수집, {new_count}개 신규 저장")
            return len(articles), new_count
            
        except Exception as e:
            print(f"   ❌ 뉴스 수집 오류: {e}")
            logger.exception("뉴스 수집 오류")
            return 0, 0
    
    def _collect_disclosures(self, stock_code: str, stock_name: str) -> Tuple[int, int]:
        """DART 공시 수집 및 저장"""
        print(f"\n📋 [4/4] DART 공시 수집...")
        
        if not self.dart_collector.api_key:
            print("   ⚠️ DART API 키 미설정 - 건너뜀")
            return 0, 0
        
        try:
            disclosures = self.dart_collector.fetch_disclosures(
                stock_code=stock_code,
                max_count=10
            )
            new_count = 0
            
            for disc in disclosures:
                # 중복 체크 (receipt_no 기준)
                existing = self.raw_store.get_disclosures(stock_code)
                if any(d.receipt_no == disc.rcept_no for d in existing):
                    continue
                
                # 원본 저장
                raw_disc = RawDisclosure(
                    stock_code=stock_code,
                    stock_name=stock_name,
                    corp_code=disc.corp_code,
                    report_name=disc.report_nm,
                    receipt_no=disc.rcept_no,
                    receipt_date=disc.rcept_dt,
                    submitter=disc.flr_nm,
                    url=disc.url
                )
                self.raw_store.save_disclosure(raw_disc)
                new_count += 1
            
            print(f"   ✅ {len(disclosures)}개 수집, {new_count}개 신규 저장")
            return len(disclosures), new_count
            
        except Exception as e:
            print(f"   ❌ 공시 수집 오류: {e}")
            logger.exception("공시 수집 오류")
            return 0, 0
    
    # ==================== RAG 임베딩 ====================
    
    def embed_pending_data(self, stock_code: Optional[str] = None) -> Dict:
        """
        미임베딩 데이터를 RAG 벡터화
        
        처리 방식 (PaddleOCR-VL + Snowflake Arctic):
        - 리포트 PDF: PaddleOCR-VL로 텍스트 추출 → Snowflake Arctic 임베딩
        - 뉴스/공시: Snowflake Arctic 텍스트 임베딩
        - 주가 데이터: 임베딩 제외 (구조화 데이터로 별도 분석)
        
        Args:
            stock_code: 특정 종목만 처리 (None이면 전체)
            
        Returns:
            임베딩 결과
        """
        results = {"reports": 0, "news": 0, "disclosures": 0}
        
        print(f"\n{'='*50}")
        print(f"🧠 PaddleOCR-VL + Snowflake Arctic 임베딩 처리")
        print(f"{'='*50}")
        
        # 1. 미임베딩 리포트 처리 (PDF → OCR → 임베딩)
        reports = self.raw_store.get_reports(stock_code, not_embedded_only=True)
        print(f"\n📑 [1/3] 리포트 처리 ({len(reports)}건)")
        
        for report in reports:
            try:
                metadata = {
                    "stock_code": report.stock_code,
                    "stock_name": report.stock_name,
                    "data_type": "report",
                    "broker": report.broker,
                    "report_date": report.report_date,
                    "source_id": report.id,
                    "source_url": report.link
                }
                
                # PDF → PaddleOCR-VL → 텍스트 임베딩
                if report.pdf_path and os.path.exists(report.pdf_path):
                    print(f"   📄 OCR 처리 중: {report.title[:40]}...")
                    
                    # RAGRetriever의 index_document 사용 (내부에서 OCR 처리)
                    result = self.retriever.index_document(
                        file_path=report.pdf_path,
                        metadata=metadata,
                        chunk_text=True
                    )
                    
                    if result.get("success"):
                        self.raw_store.mark_as_embedded("reports", [report.id])
                        results["reports"] += 1
                        print(f"      ✅ {result.get('chunks_added', 0)}개 청크 임베딩")
                else:
                    # PDF 없으면 메타정보만 텍스트로 저장
                    print(f"   📝 메타정보만 저장: {report.title[:40]}...")
                    text = f"[증권사 리포트]\n제목: {report.title}\n증권사: {report.broker}\n날짜: {report.report_date}"
                    
                    result = self.retriever.index_text(
                        text=text,
                        metadata=metadata
                    )
                    
                    if result.get("success"):
                        self.raw_store.mark_as_embedded("reports", [report.id])
                        results["reports"] += 1
                        
            except Exception as e:
                print(f"   ⚠️ 리포트 임베딩 실패 ({report.title[:30]}): {e}")
                logger.exception(f"리포트 임베딩 오류: {report.id}")
        
        # 2. 미임베딩 뉴스 처리 (텍스트 → 임베딩)
        news_list = self.raw_store.get_news(stock_code, not_embedded_only=True)
        print(f"\n📰 [2/3] 뉴스 처리 ({len(news_list)}건)")
        
        news_ids = []
        for news in news_list:
            try:
                metadata = {
                    "stock_code": news.stock_code,
                    "stock_name": news.stock_name,
                    "data_type": "news",
                    "source": news.source,
                    "url": news.url,
                    "published_at": news.published_at,
                    "source_id": news.id
                }
                
                # 뉴스 텍스트 구성
                text = f"[뉴스] {news.title}\n출처: {news.source}\n\n{news.summary}"
                if news.content:
                    text += f"\n\n{news.content}"
                
                result = self.retriever.index_text(
                    text=text,
                    metadata=metadata
                )
                
                if result.get("success"):
                    news_ids.append(news.id)
                    
            except Exception as e:
                print(f"   ⚠️ 뉴스 임베딩 실패: {e}")
                logger.exception(f"뉴스 임베딩 오류: {news.id}")
        
        if news_ids:
            self.raw_store.mark_as_embedded("news", news_ids)
            results["news"] = len(news_ids)
            print(f"   ✅ {len(news_ids)}건 임베딩 완료")
        
        # 3. 미임베딩 공시 처리 (텍스트 → 임베딩)
        disclosures = self.raw_store.get_disclosures(stock_code, not_embedded_only=True)
        print(f"\n📋 [3/3] 공시 처리 ({len(disclosures)}건)")
        
        disc_ids = []
        for disc in disclosures:
            try:
                metadata = {
                    "stock_code": disc.stock_code,
                    "stock_name": disc.stock_name,
                    "data_type": "disclosure",
                    "report_name": disc.report_name,
                    "receipt_date": disc.receipt_date,
                    "url": disc.url,
                    "source_id": disc.id
                }
                
                # 공시 텍스트 구성
                text = f"[DART 공시] {disc.report_name}\n제출자: {disc.submitter}\n접수일: {disc.receipt_date}"
                if disc.content:
                    text += f"\n\n{disc.content}"
                
                result = self.retriever.index_text(
                    text=text,
                    metadata=metadata
                )
                
                if result.get("success"):
                    disc_ids.append(disc.id)
                    
            except Exception as e:
                print(f"   ⚠️ 공시 임베딩 실패: {e}")
                logger.exception(f"공시 임베딩 오류: {disc.id}")
        
        if disc_ids:
            self.raw_store.mark_as_embedded("disclosures", disc_ids)
            results["disclosures"] = len(disc_ids)
            print(f"   ✅ {len(disc_ids)}건 임베딩 완료")
        
        # 주가 데이터는 임베딩 제외 (구조화 데이터로 별도 분석)
        print(f"\n💰 주가 데이터: 임베딩 제외 (PostgreSQL에서 직접 조회)")
        
        print(f"\n{'='*50}")
        print(f"✅ 임베딩 완료")
        print(f"   - 리포트: {results['reports']}건 (PDF→OCR→텍스트)")
        print(f"   - 뉴스: {results['news']}건")
        print(f"   - 공시: {results['disclosures']}건")
        print(f"{'='*50}")
        
        return results
    
    # ==================== 직접 인덱싱 ====================
    
    def index_pdf(
        self,
        file_path: str,
        metadata: Optional[Dict] = None,
        chunk_text: bool = True
    ) -> Dict:
        """
        PDF 파일 직접 인덱싱 (DB 저장 없이)
        
        Args:
            file_path: PDF 파일 경로
            metadata: 추가 메타데이터
            chunk_text: 텍스트 청킹 여부
            
        Returns:
            인덱싱 결과
        """
        print(f"\n📄 PDF 직접 인덱싱: {os.path.basename(file_path)}")
        
        result = self.retriever.index_document(
            file_path=file_path,
            metadata=metadata,
            chunk_text=chunk_text
        )
        
        if result.get("success"):
            print(f"   ✅ {result.get('chunks_added', 0)}개 청크 인덱싱 완료")
        else:
            print(f"   ❌ 인덱싱 실패")
        
        return result
    
    def index_directory(
        self,
        directory: str,
        file_patterns: List[str] = None,
        metadata: Optional[Dict] = None,
        recursive: bool = True
    ) -> Dict:
        """
        디렉토리 내 파일 일괄 인덱싱
        
        Args:
            directory: 디렉토리 경로
            file_patterns: 파일 패턴 (예: ["*.pdf", "*.txt"])
            metadata: 공통 메타데이터
            recursive: 하위 디렉토리 포함 여부
            
        Returns:
            인덱싱 결과
        """
        import glob
        
        if file_patterns is None:
            file_patterns = ["*.pdf"]
        
        print(f"\n📁 디렉토리 인덱싱: {directory}")
        
        results = {
            "total_files": 0,
            "success": 0,
            "failed": 0,
            "total_chunks": 0
        }
        
        for pattern in file_patterns:
            if recursive:
                search_pattern = os.path.join(directory, "**", pattern)
                files = glob.glob(search_pattern, recursive=True)
            else:
                search_pattern = os.path.join(directory, pattern)
                files = glob.glob(search_pattern)
            
            results["total_files"] += len(files)
            
            for file_path in files:
                file_metadata = {
                    **(metadata or {}),
                    "source_directory": directory,
                    "filename": os.path.basename(file_path)
                }
                
                try:
                    result = self.retriever.index_document(
                        file_path=file_path,
                        metadata=file_metadata,
                        chunk_text=True
                    )
                    
                    if result.get("success"):
                        results["success"] += 1
                        results["total_chunks"] += result.get("chunks_added", 0)
                        print(f"   ✅ {os.path.basename(file_path)}")
                    else:
                        results["failed"] += 1
                        print(f"   ❌ {os.path.basename(file_path)}")
                        
                except Exception as e:
                    results["failed"] += 1
                    print(f"   ❌ {os.path.basename(file_path)}: {e}")
        
        print(f"\n📊 인덱싱 결과:")
        print(f"   - 전체: {results['total_files']}개")
        print(f"   - 성공: {results['success']}개")
        print(f"   - 실패: {results['failed']}개")
        print(f"   - 청크: {results['total_chunks']}개")
        
        return results
    
    # ==================== 검색 ====================
    
    def search(
        self,
        query: str,
        k: int = 5,
        use_reranker: bool = True,
        filter_metadata: Optional[Dict] = None
    ) -> List:
        """
        RAG 검색
        
        Args:
            query: 검색 쿼리
            k: 반환할 결과 수
            use_reranker: 리랭커 사용 여부
            filter_metadata: 메타데이터 필터
            
        Returns:
            검색 결과 리스트
        """
        result = self.retriever.retrieve(
            query=query,
            k=k,
            use_reranker=use_reranker
        )
        
        return result.text_results
    
    # ==================== 유틸리티 ====================
    
    def get_stats(self) -> Dict:
        """전체 통계"""
        raw_stats = self.raw_store.get_stats()
        vector_stats = self.retriever.get_stats()
        
        return {
            "raw_data": raw_stats,
            "vector_store": vector_stats
        }
    
    def _print_summary(self, results: Dict):
        """결과 요약 출력"""
        print(f"\n{'='*60}")
        print(f"📊 [{results['stock_name']}] 수집 완료!")
        print(f"{'='*60}")
        print(f"📥 수집: 리포트 {results['collected']['reports']}, "
              f"뉴스 {results['collected']['news']}, "
              f"공시 {results['collected']['disclosures']}, "
              f"주가 {results['collected']['price']}일")
        print(f"🆕 신규: 리포트 {results['new_items']['reports']}, "
              f"뉴스 {results['new_items']['news']}, "
              f"공시 {results['new_items']['disclosures']}")
        print(f"🔗 RAG: 리포트 {results['embedded']['reports']}, "
              f"뉴스 {results['embedded']['news']}, "
              f"공시 {results['embedded']['disclosures']}")
        print(f"{'='*60}")
    
    def clear_vector_store(self):
        """벡터 저장소 초기화 (주의!)"""
        print("⚠️ 벡터 저장소를 초기화합니다...")
        self.retriever.vector_store.clear()
        print("✅ 벡터 저장소 초기화 완료")
    
    def rebuild_embeddings(self, stock_code: Optional[str] = None):
        """
        임베딩 재구축 (모든 데이터 다시 임베딩)
        
        Args:
            stock_code: 특정 종목만 재구축 (None이면 전체)
        """
        print("🔄 임베딩 재구축 시작...")
        
        # is_embedded 플래그 초기화
        self.raw_store.reset_embedded_flags(stock_code)
        
        # 다시 임베딩
        results = self.embed_pending_data(stock_code)
        
        print(f"✅ 재구축 완료: {results}")
        return results


# 테스트
if __name__ == "__main__":
    # 파이프라인 초기화
    pipeline = DataIngestionPipeline(
        use_reranker=True,
        retrieval_k=20,
        rerank_top_k=3
    )
    
    # 삼성전자 데이터 수집
    results = pipeline.ingest_stock_data(
        stock_code="005930",
        stock_name="삼성전자",
        include_dart=False  # API 키 없으면 건너뜀
    )
    
    # 통계 확인
    print("\n📊 저장소 통계:")
    stats = pipeline.get_stats()
    print(f"   원본 DB: {stats['raw_data'].get('db_size_mb', 'N/A')}MB")
    
    # 검색 테스트
    print("\n🔍 검색 테스트: '삼성전자 실적'")
    docs = pipeline.search("삼성전자 실적", k=3)
    for doc in docs:
        print(f"- {doc.page_content[:100]}...")
