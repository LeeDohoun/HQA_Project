# 파일: src/data_pipeline/data_ingestion.py
"""
데이터 수집 → 원본 저장 → RAG 벡터화 통합 파이프라인

흐름:
1. 데이터 수집 (크롤러, API)
2. 원본 DB 저장 (SQLite) - 중복 체크
3. RAG 벡터화 (ChromaDB) - 미처리 데이터만
"""

import os
from typing import List, Dict, Optional
from datetime import datetime

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

# RAG 모듈
from src.rag import (
    DocumentLoader,
    VectorStoreManager
)


class DataIngestionPipeline:
    """데이터 수집 → 원본 저장 → RAG 벡터화 통합 파이프라인"""
    
    def __init__(
        self,
        db_path: str = "./database/raw_data.db",
        files_dir: str = "./data/files",
        vector_persist_dir: str = "./database/chroma_db",
        collection_name: str = "stock_data",
        use_multimodal: bool = True,  # 기본값 True → Qwen3-VL 사용
        embedding_model: str = "multimodal-2b"  # 2B 또는 8B
    ):
        """
        Args:
            db_path: SQLite DB 경로
            files_dir: PDF 등 파일 저장 경로
            vector_persist_dir: 벡터 DB 저장 경로
            collection_name: 벡터 컬렉션 이름
            use_multimodal: Qwen3-VL 멀티모달 임베딩 사용 여부
            embedding_model: 임베딩 모델 ("multimodal-2b", "multimodal-8b")
        """
        print("🚀 데이터 수집 파이프라인 초기화...")
        
        # 1. 데이터 수집기
        self.price_loader = PriceLoader()
        self.dart_collector = DARTCollector()
        self.news_crawler = NewsCrawler()
        self.report_crawler = ReportCrawler(download_dir=os.path.join(files_dir, "reports"))
        
        # 2. 원본 데이터 저장소 (SQLite)
        self.raw_store = RawDataStore(db_path=db_path, files_dir=files_dir)
        
        # 3. RAG 벡터 저장소 (ChromaDB + Qwen3-VL)
        self.use_multimodal = use_multimodal
        self.vector_store = VectorStoreManager(
            persist_dir=vector_persist_dir,
            collection_name=collection_name,
            embedding_type=embedding_model if use_multimodal else "korean",
            use_multimodal=use_multimodal
        )
        
        # 4. PDF 로더
        self.doc_loader = DocumentLoader()
        
        if use_multimodal:
            print(f"🧠 Qwen3-VL 멀티모달 임베딩 활성화 ({embedding_model})")
            print("   - 텍스트 데이터 → Qwen3-VL 텍스트 임베딩")
            print("   - 이미지 데이터 → Qwen3-VL 이미지 임베딩")
        
        print("✅ 파이프라인 초기화 완료")
    
    # ==================== 메인 수집 함수 ====================
    
    def ingest_stock_data(
        self,
        stock_code: str,
        stock_name: str,
        include_reports: bool = True,
        include_news: bool = True,
        include_dart: bool = True,
        include_price: bool = True,
        auto_embed: bool = True  # 수집 후 자동 임베딩
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
            "embedded": {"reports": 0, "news": 0, "disclosures": 0, "price": 0}
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
            return 0
    
    def _collect_reports(self, stock_code: str, stock_name: str) -> tuple:
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
            return 0, 0
    
    def _collect_news(self, stock_code: str, stock_name: str) -> tuple:
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
            return 0, 0
    
    def _collect_disclosures(self, stock_code: str, stock_name: str) -> tuple:
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
            return 0, 0
    
    # ==================== RAG 임베딩 ====================
    
    def embed_pending_data(self, stock_code: Optional[str] = None) -> Dict:
        """
        미임베딩 데이터를 RAG 벡터화 (Qwen3-VL 사용)
        
        처리 방식:
        - 리포트 PDF: 이미지로 변환 후 Qwen3-VL 이미지 임베딩
        - 뉴스/공시: Qwen3-VL 텍스트 임베딩
        - 주가 데이터: 임베딩 제외 (구조화 데이터로 별도 분석)
        
        Args:
            stock_code: 특정 종목만 처리 (None이면 전체)
            
        Returns:
            임베딩 결과
        """
        results = {"reports": 0, "news": 0, "disclosures": 0}
        
        print(f"\n{'='*50}")
        print(f"🧠 Qwen3-VL 임베딩 처리 시작")
        print(f"{'='*50}")
        
        # 1. 미임베딩 리포트 처리 (PDF → 이미지 임베딩)
        reports = self.raw_store.get_reports(stock_code, not_embedded_only=True)
        print(f"\n📑 [1/3] 리포트 처리 ({len(reports)}건) - 이미지 임베딩")
        
        for report in reports:
            try:
                metadata = {
                    "stock_code": report.stock_code,
                    "stock_name": report.stock_name,
                    "data_type": "report",
                    "broker": report.broker,
                    "report_date": report.report_date,
                    "source_id": report.id
                }
                
                # PDF → 이미지로 변환 후 Qwen3-VL 이미지 임베딩
                if report.pdf_path and os.path.exists(report.pdf_path):
                    print(f"   🖼️ {report.title[:30]}...")
                    processed = self.doc_loader.load(report.pdf_path)
                    self.vector_store.add_document(processed, doc_metadata=metadata)
                else:
                    # PDF 없으면 메타정보만 텍스트로 저장
                    print(f"   📝 {report.title[:30]}... (PDF 없음)")
                    text = f"[증권사 리포트] {report.title}\n증권사: {report.broker}\n날짜: {report.report_date}"
                    self.vector_store.add_texts([text], metadatas=[metadata])
                
                self.raw_store.mark_as_embedded("reports", [report.id])
                results["reports"] += 1
            except Exception as e:
                print(f"   ⚠️ 리포트 임베딩 실패: {e}")
        
        # 2. 미임베딩 뉴스 처리 (텍스트 전용 → Qwen3-VL 텍스트 임베딩)
        news_list = self.raw_store.get_news(stock_code, not_embedded_only=True)
        print(f"\n📰 [2/3] 뉴스 처리 ({len(news_list)}건) - 텍스트 전용")
        
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
                
                text = f"[뉴스] {news.title}\n{news.summary}"
                if news.content:
                    text += f"\n\n{news.content}"
                
                self.vector_store.add_texts([text], metadatas=[metadata])
                news_ids.append(news.id)
            except Exception as e:
                print(f"   ⚠️ 뉴스 임베딩 실패: {e}")
        
        if news_ids:
            self.raw_store.mark_as_embedded("news", news_ids)
            results["news"] = len(news_ids)
        
        # 3. 미임베딩 공시 처리 (텍스트 전용 → Qwen3-VL 텍스트 임베딩)
        disclosures = self.raw_store.get_disclosures(stock_code, not_embedded_only=True)
        print(f"\n📋 [3/3] 공시 처리 ({len(disclosures)}건) - 텍스트 전용")
        
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
                
                text = f"[공시] {disc.report_name}\n제출자: {disc.submitter}\n접수일: {disc.receipt_date}"
                if disc.content:
                    text += f"\n\n{disc.content}"
                
                self.vector_store.add_texts([text], metadatas=[metadata])
                disc_ids.append(disc.id)
            except Exception as e:
                print(f"   ⚠️ 공시 임베딩 실패: {e}")
        
        if disc_ids:
            self.raw_store.mark_as_embedded("disclosures", disc_ids)
            results["disclosures"] = len(disc_ids)
        
        # 주가 데이터는 임베딩 제외 (구조화 데이터로 별도 분석)
        print(f"\n💰 주가 데이터: 임베딩 제외 (SQLite에서 직접 조회)")
        
        print(f"\n{'='*50}")
        print(f"✅ 임베딩 완료 (Qwen3-VL 모델 사용)")
        print(f"   - 리포트: {results['reports']}건 (PDF → 이미지)")
        print(f"   - 뉴스: {results['news']}건 (텍스트 전용)")
        print(f"   - 공시: {results['disclosures']}건 (텍스트 전용)")
        print(f"{'='*50}")
        
        return results
    
    # ==================== 유틸리티 ====================
    
    def search(self, query: str, k: int = 5) -> List:
        """RAG 검색"""
        return self.vector_store.search_text(query, k=k)
    
    def get_stats(self) -> Dict:
        """전체 통계"""
        raw_stats = self.raw_store.get_stats()
        vector_stats = self.vector_store.get_stats()
        
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
              f"공시 {results['embedded']['disclosures']}, "
              f"주가 {results['embedded']['price']}")
        print(f"{'='*60}")


# 테스트
if __name__ == "__main__":
    pipeline = DataIngestionPipeline()
    
    # 삼성전자 데이터 수집
    results = pipeline.ingest_stock_data(
        stock_code="005930",
        stock_name="삼성전자",
        include_dart=False  # API 키 없으면 건너뜀
    )
    
    # 통계 확인
    print("\n📊 저장소 통계:")
    stats = pipeline.get_stats()
    print(f"   원본 DB: {stats['raw_data']['db_size_mb']}MB")
    print(f"   파일: {stats['raw_data']['files_size_mb']}MB")
    
    # 검색 테스트
    print("\n🔍 검색 테스트: '삼성전자 실적'")
    docs = pipeline.search("삼성전자 실적", k=3)
    for doc in docs:
        print(f"- {doc.page_content[:100]}...")
