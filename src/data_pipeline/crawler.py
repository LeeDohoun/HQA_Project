# 파일: src/data_pipeline/crawler.py
"""
증권사 리포트 크롤러
- 네이버 금융에서 증권사 리포트 목록 수집
- PDF 다운로드 및 본문 추출
"""

import os
import requests
from bs4 import BeautifulSoup
from typing import List, Dict, Optional
from dataclasses import dataclass
from datetime import datetime


@dataclass
class Report:
    """리포트 데이터 클래스"""
    title: str              # 제목
    broker: str             # 증권사
    date: str               # 작성일
    link: str               # 상세페이지 URL
    pdf_url: Optional[str] = None  # PDF 다운로드 URL
    stock_code: str = ""    # 종목코드
    stock_name: str = ""    # 종목명
    
    def to_dict(self) -> Dict:
        return {
            "title": self.title,
            "broker": self.broker,
            "date": self.date,
            "link": self.link,
            "pdf_url": self.pdf_url,
            "stock_code": self.stock_code,
            "stock_name": self.stock_name
        }


class ReportCrawler:
    """증권사 리포트 크롤러"""
    
    def __init__(self, download_dir: str = "./data/reports"):
        self.base_url = "https://finance.naver.com/research/company_list.naver"
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer': 'https://finance.naver.com/research/'
        }
        self.download_dir = download_dir
        os.makedirs(download_dir, exist_ok=True)

    def fetch_latest_reports(
        self,
        stock_code: str,
        max_count: int = 5
    ) -> List[Report]:
        """
        특정 종목의 최신 리포트 목록 수집
        
        Args:
            stock_code: 종목코드
            max_count: 최대 수집 개수
            
        Returns:
            Report 리스트
        """
        params = {
            'searchType': 'itemCode',
            'itemCode': stock_code,
            'page': 1
        }
        
        try:
            print(f"   📥 {stock_code} 리포트 수집 중...")
            response = requests.get(self.base_url, headers=self.headers, params=params)
            response.encoding = 'euc-kr'
            
            if response.status_code != 200:
                print(f"   ❌ 서버 접속 실패 (상태코드: {response.status_code})")
                return []

            soup = BeautifulSoup(response.text, 'html.parser')
            table = soup.select_one('table.type_1')
            
            if not table:
                print("   ❌ 리포트 목록을 찾을 수 없습니다.")
                return []

            reports = []
            rows = table.find_all('tr')
            
            for row in rows:
                cols = row.find_all('td')
                
                if len(cols) >= 5:
                    title_tag = cols[1].find('a')
                    broker = cols[2].text.strip()
                    date = cols[4].text.strip()
                    
                    # PDF 링크 확인
                    pdf_tag = cols[3].find('a')
                    pdf_url = None
                    if pdf_tag and pdf_tag.get('href'):
                        pdf_url = pdf_tag.get('href')
                    
                    if title_tag:
                        reports.append(Report(
                            title=title_tag.text.strip(),
                            broker=broker,
                            date=date,
                            link="https://finance.naver.com/research/" + title_tag['href'],
                            pdf_url=pdf_url,
                            stock_code=stock_code
                        ))
                        
                        if len(reports) >= max_count:
                            break
            
            print(f"   ✅ {len(reports)}개 리포트 수집 완료")
            return reports

        except Exception as e:
            print(f"   ❌ 크롤링 오류: {e}")
            return []
    
    def download_pdf(self, report: Report) -> Optional[str]:
        """
        리포트 PDF 다운로드
        
        Args:
            report: Report 객체
            
        Returns:
            저장된 파일 경로 또는 None
        """
        if not report.pdf_url:
            print(f"   ⚠️ PDF URL 없음: {report.title}")
            return None
        
        try:
            response = requests.get(report.pdf_url, headers=self.headers)
            
            if response.status_code == 200:
                # 파일명 생성
                safe_title = "".join(c for c in report.title if c.isalnum() or c in (' ', '-', '_'))[:50]
                filename = f"{report.date}_{report.broker}_{safe_title}.pdf"
                filepath = os.path.join(self.download_dir, filename)
                
                with open(filepath, 'wb') as f:
                    f.write(response.content)
                
                print(f"   💾 PDF 저장: {filename}")
                return filepath
            else:
                print(f"   ❌ PDF 다운로드 실패: {response.status_code}")
                return None
                
        except Exception as e:
            print(f"   ❌ PDF 다운로드 오류: {e}")
            return None
    
    def fetch_and_download(
        self,
        stock_code: str,
        max_count: int = 3
    ) -> List[Dict]:
        """
        리포트 수집 및 PDF 다운로드 통합
        
        Args:
            stock_code: 종목코드
            max_count: 최대 수집 개수
            
        Returns:
            리포트 정보 + 파일경로 리스트
        """
        reports = self.fetch_latest_reports(stock_code, max_count)
        results = []
        
        for report in reports:
            filepath = self.download_pdf(report)
            result = report.to_dict()
            result['local_path'] = filepath
            results.append(result)
        
        return results


# 테스트
if __name__ == "__main__":
    crawler = ReportCrawler()
    reports = crawler.fetch_latest_reports("005930")  # 삼성전자
    for r in reports:
        print(f"- {r.title} ({r.broker}, {r.date})")

    print("--- 삼성전자(005930) ---")
    res = crawler.fetch_latest_reports("005930")
    print(res)