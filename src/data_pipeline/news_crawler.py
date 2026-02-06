# 파일: src/data_pipeline/news_crawler.py
"""
뉴스 크롤러
- 네이버 뉴스, 구글 뉴스 등에서 종목 관련 뉴스 수집
- 키워드 기반 뉴스 검색
"""

import requests
from bs4 import BeautifulSoup
from typing import List, Dict, Optional
from datetime import datetime
from dataclasses import dataclass


@dataclass
class NewsArticle:
    """뉴스 기사 데이터 클래스"""
    title: str              # 제목
    summary: str            # 요약/본문 일부
    source: str             # 언론사
    url: str                # 기사 URL
    published_at: str       # 발행일
    keyword: str            # 검색 키워드
    
    def to_dict(self) -> Dict:
        return {
            "title": self.title,
            "summary": self.summary,
            "source": self.source,
            "url": self.url,
            "published_at": self.published_at,
            "keyword": self.keyword
        }


class NewsCrawler:
    """뉴스 크롤러"""
    
    def __init__(self):
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
    
    def fetch_naver_news(
        self,
        keyword: str,
        max_count: int = 10
    ) -> List[NewsArticle]:
        """
        네이버 뉴스 검색
        
        Args:
            keyword: 검색 키워드 (종목명 등)
            max_count: 최대 수집 개수
            
        Returns:
            NewsArticle 리스트
        """
        url = "https://search.naver.com/search.naver"
        params = {
            "where": "news",
            "query": keyword,
            "sort": "1"  # 최신순
        }
        
        try:
            response = requests.get(url, headers=self.headers, params=params)
            response.encoding = 'utf-8'
            soup = BeautifulSoup(response.text, 'html.parser')
            
            articles = []
            news_items = soup.select('div.news_area')[:max_count]
            
            for item in news_items:
                title_tag = item.select_one('a.news_tit')
                desc_tag = item.select_one('div.news_dsc')
                source_tag = item.select_one('a.info.press')
                date_tag = item.select_one('span.info')
                
                if title_tag:
                    articles.append(NewsArticle(
                        title=title_tag.get('title', title_tag.text.strip()),
                        summary=desc_tag.text.strip() if desc_tag else "",
                        source=source_tag.text.strip() if source_tag else "Unknown",
                        url=title_tag.get('href', ''),
                        published_at=date_tag.text.strip() if date_tag else "",
                        keyword=keyword
                    ))
            
            print(f"📰 '{keyword}' 뉴스 {len(articles)}건 수집 완료")
            return articles
            
        except Exception as e:
            print(f"❌ 뉴스 크롤링 오류: {e}")
            return []
    
    def fetch_stock_news(
        self,
        stock_code: str,
        stock_name: str,
        max_count: int = 10
    ) -> List[NewsArticle]:
        """
        종목 관련 뉴스 수집
        
        Args:
            stock_code: 종목코드
            stock_name: 종목명
            max_count: 최대 수집 개수
            
        Returns:
            NewsArticle 리스트
        """
        # 종목명으로 검색
        return self.fetch_naver_news(stock_name, max_count)
    
    def fetch_article_content(self, url: str) -> Optional[str]:
        """
        뉴스 기사 본문 수집
        
        Args:
            url: 기사 URL
            
        Returns:
            본문 텍스트
        """
        try:
            response = requests.get(url, headers=self.headers)
            response.encoding = 'utf-8'
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # 네이버 뉴스 본문 추출 (articleBody)
            content = soup.select_one('article#dic_area')
            if content:
                return content.get_text(strip=True)
            
            # 일반적인 본문 태그 시도
            for selector in ['article', '.article_body', '.news_content', '#content']:
                content = soup.select_one(selector)
                if content:
                    return content.get_text(strip=True)
            
            return None
            
        except Exception as e:
            print(f"⚠️ 본문 추출 실패: {e}")
            return None


# 테스트
if __name__ == "__main__":
    crawler = NewsCrawler()
    articles = crawler.fetch_naver_news("삼성전자", max_count=5)
    for article in articles:
        print(f"- {article.title} ({article.source})")
