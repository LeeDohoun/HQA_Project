# 파일: main.py

from src.data_pipeline.price_loader import PriceLoader
from src.data_pipeline.crawler import ReportCrawler
from src.database.vector_store import ReportVectorStore
from src.agents.analyst import AnalystAgent # 에이전트 추가

def run_hqa_system():
    print("=== [HQA System] Start ===")
    
    # 1. 도구 초기화
    price_loader = PriceLoader()
    crawler = ReportCrawler()
    vector_store = ReportVectorStore()
    analyst_agent = AnalystAgent() # 에이전트 소환

    # 2. 타겟 종목 (테스트용: SK하이닉스)
    target_stock = {"code": "000660", "name": "SK하이닉스"}
    
    print(f"\nPhase 1: {target_stock['name']} 데이터 수집 및 저장")
    print("-" * 50)
    
    # [Step 1] 기술적 필터링
    is_bullish, price, ma150 = price_loader.check_technical_status(target_stock['code'], target_stock['name'])
    
    if is_bullish:
        print(f"✅ 추세 확인: 상승세 (현재가 {price:,.0f}원 > 이평선 {ma150:,.0f}원)")
        
        # [Step 2] 크롤링 (이미 데이터가 있어도 최신화를 위해 수행)
        reports = crawler.fetch_latest_reports(target_stock['code'])
        
        # [Step 3] DB 저장
        if reports:
            vector_store.save_reports(reports, target_stock['code'])
            
            print(f"\nPhase 2: AI Analyst 분석 시작")
            print("-" * 50)
            
            # [Step 4] 에이전트 분석 실행 (여기가 핵심!)
            result = analyst_agent.analyze_stock(target_stock['name'], target_stock['code'])
            
            print("\n" + "="*50)
            print("📜 [최종 분석 보고서]")
            print("="*50)
            print(result)
            
        else:
            print("❌ 리포트를 찾을 수 없어 분석을 중단합니다.")
            
    else:
        print(f"🔻 추세 확인: 하락세 (현재가 {price:,.0f}원 < 이평선 {ma150:,.0f}원)")
        print("   -> 매수 대상이 아니므로 분석을 건너뜁니다.")

    print("\n=== [HQA System] Complete ===")

if __name__ == "__main__":
    run_hqa_system()