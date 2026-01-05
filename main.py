# 파일: main.py

from src.data_pipeline.price_loader import PriceLoader
from src.data_pipeline.crawler import ReportCrawler # 크롤러 추가

def run_phase_1_test():
    print("=== [HQA System] Phase 1 Integration Test ===")
    
    # 1. 도구 준비
    price_loader = PriceLoader()
    crawler = ReportCrawler()
    
    # 2. 테스트 종목
    test_stocks = [
        {"code": "000660", "name": "SK하이닉스"}, # 상승 추세 예상
        {"code": "005930", "name": "삼성전자"}    # 하락 추세 예상
    ]
    
    for stock in test_stocks:
        print(f"\nAnalyzing... {stock['name']} ({stock['code']})")
        
        # [Step 1] 기술적 필터링 (Quant)
        is_bullish, price, ma150 = price_loader.check_technical_status(stock['code'], stock['name'])
        
        if is_bullish:
            print(f"✅ 기술적 분석 통과! (현재가 {price} > 이평선 {ma150:.0f})")
            print("   -> 🔎 최신 리포트를 검색합니다...")
            
            # [Step 2] 리포트 수집 (Mental)
            reports = crawler.fetch_latest_reports(stock['code'])
            
            if reports:
                for idx, r in enumerate(reports, 1):
                    print(f"      {idx}. [{r['date']}] {r['title']} - {r['broker']}")
            else:
                print("      (최근 등록된 리포트가 없습니다.)")
                
        else:
            print(f"🔻 기술적 분석 탈락 (현재가 {price} < 이평선 {ma150:.0f})")
            print("   -> 리포트 수집을 건너뜁니다.")

    print("\n=== [HQA System] Test Complete ===")

if __name__ == "__main__":
    run_phase_1_test()