# 파일: src/data_pipeline/crawler.py

import requests
from bs4 import BeautifulSoup

class ReportCrawler:
    def __init__(self):
        # 네이버 증권 리포트 게시판 URL (금융투자협회 등 다른 소스로 확장 가능)
        self.base_url = "https://finance.naver.com/research/company_list.naver?keyword="
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }

    def fetch_latest_reports(self, stock_code):
        """
        특정 종목의 최신 리포트 제목과 링크를 가져옵니다.
        """
        url = f"{self.base_url}{stock_code}"
        
        try:
            response = requests.get(url, headers=self.headers)
            # 한글 깨짐 방지
            response.encoding = 'euc-kr' 
            
            soup = BeautifulSoup(response.text, 'html.parser')
            
            reports = []
            
            # 테이블에서 리포트 목록 추출 (HTML 구조에 따라 변경될 수 있음)
            table = soup.find('table', {'class': 'type_1'})
            
            if not table:
                return []

            rows = table.find_all('tr')
            
            for row in rows:
                cols = row.find_all('td')
                # 데이터가 있는 행만 처리 (공백 행 제외)
                if len(cols) > 2:
                    # 제목 (HTML 구조 분석 결과: 2번째 td의 a 태그)
                    title_tag = cols[1].find('a')
                    # 증권사
                    broker = cols[2].text.strip()
                    # 작성일
                    date = cols[4].text.strip()
                    
                    if title_tag:
                        reports.append({
                            "title": title_tag.text.strip(),
                            "broker": broker,
                            "date": date,
                            "link": "https://finance.naver.com/research/" + title_tag['href']
                        })
                        
                        # 최근 3개만 가져오고 종료 (테스트용)
                        if len(reports) >= 3:
                            break
                            
            return reports

        except Exception as e:
            print(f"크롤링 중 에러 발생: {e}")
            return []

# 단독 실행 테스트
if __name__ == "__main__":
    crawler = ReportCrawler()
    # 삼성전자 코드 입력
    results = crawler.fetch_latest_reports("005930")
    
    print(f"--- 수집된 리포트 ({len(results)}건) ---")
    for r in results:
        print(f"[{r['date']}] {r['title']} ({r['broker']})")