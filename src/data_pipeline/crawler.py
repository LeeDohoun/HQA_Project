# 파일: src/data_pipeline/crawler.py

import requests
from bs4 import BeautifulSoup
import re

class ReportCrawler:
    def __init__(self):
        # [변경] 종목코드로 검색하는 '전용 링크' 사용 (가장 정확함)
        self.base_url = "https://finance.naver.com/research/company_list.naver"
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36',
            'Referer': 'https://finance.naver.com/research/'
        }

    def fetch_latest_reports(self, stock_code):
        # 쿼리 파라미터로 종목코드(itemCode)를 직접 지정
        params = {
            'searchType': 'itemCode',
            'itemCode': stock_code,
            'page': 1
        }
        
        try:
            print(f"   [Debug] 접속 시도: {stock_code} 리포트 페이지...")
            response = requests.get(self.base_url, headers=self.headers, params=params)
            response.encoding = 'euc-kr'
            
            if response.status_code != 200:
                print(f"   [Error] 서버 접속 실패 (상태코드: {response.status_code})")
                return []

            soup = BeautifulSoup(response.text, 'html.parser')
            
            # 테이블 찾기
            table = soup.select_one('table.type_1')
            if not table:
                print("   [Error] 리포트 목록 테이블(table.type_1)을 못 찾았습니다.")
                # 디버깅을 위해 HTML 일부 출력 (너무 길면 잘라서)
                # print(soup.text[:200]) 
                return []

            reports = []
            rows = table.find_all('tr')
            
            # print(f"   [Debug] 찾은 행 개수: {len(rows)}") # 디버깅용
            
            for row in rows:
                cols = row.find_all('td')
                
                # 데이터가 있는 행은 보통 5칸 이상 (제목, 증권사, 첨부, 날짜, 조회수)
                if len(cols) >= 5:
                    title_tag = cols[1].find('a') # 두 번째 칸: 제목
                    broker = cols[2].text.strip() # 세 번째 칸: 증권사
                    date = cols[4].text.strip()   # 다섯 번째 칸: 날짜
                    
                    if title_tag:
                        reports.append({
                            "title": title_tag.text.strip(),
                            "broker": broker,
                            "date": date,
                            "link": "https://finance.naver.com/research/" + title_tag['href']
                        })
                        
                        if len(reports) >= 3:
                            break
            
            if len(reports) == 0:
                 print("   [Info] 테이블은 찾았으나, 조건에 맞는 리포트가 없습니다.")
                 
            return reports

        except Exception as e:
            print(f"   [Error] 크롤링 중 예외 발생: {e}")
            return []

# 테스트 실행
if __name__ == "__main__":
    crawler = ReportCrawler()
    # 삼성전자 테스트
    print("--- 삼성전자(005930) ---")
    res = crawler.fetch_latest_reports("005930")
    print(res)