# 파일: main.py

import time  # [필수] 시간 지연을 위해 추가
from src.data_pipeline.price_loader import PriceLoader
from src.data_pipeline.crawler import ReportCrawler
from src.database.vector_store import ReportVectorStore
from src.agents.analyst import AnalystAgent
from src.agents.quant import QuantAgent

def run_hqa_system():
    print("=== [HQA System] Start ===")
    
    # 1. 도구 초기화
    price_loader = PriceLoader()
    crawler = ReportCrawler()
    vector_store = ReportVectorStore()
    
    analyst_agent = AnalystAgent()
    quant_agent = QuantAgent()

    # 2. 타겟 종목
    target_stock = {"code": "000660", "name": "SK하이닉스"}
    
    print(f"\nPhase 1: {target_stock['name']} 데이터 수집 및 저장")
    print("-" * 50)
    
    # [Step 1] 기술적 필터링
    is_bullish, price, ma150 = price_loader.check_technical_status(target_stock['code'], target_stock['name'])
    
    if is_bullish:
        print(f"✅ 추세 확인: 상승세 (현재가 {price:,.0f}원 > 이평선 {ma150:,.0f}원)")
        
        # [Step 2 & 3] 크롤링 및 저장
        reports = crawler.fetch_latest_reports(target_stock['code'])
        if reports:
            vector_store.save_reports(reports, target_stock['code'])
        
        # ---------------------------------------------------------
        # [Phase 2] Analyst 실행
        # ---------------------------------------------------------
        print(f"\nPhase 2: AI Analyst (리포트 분석) 시작")
        print("-" * 50)
        report_result = analyst_agent.analyze_stock(target_stock['name'], target_stock['code'])
        
        print("\n" + "="*50)
        print("📜 [Analyst 보고서]")
        print("="*50)
        print(report_result)

        # ---------------------------------------------------------
        # [중요] RPM(분당 요청 제한) 회피를 위한 휴식
        # ---------------------------------------------------------
        print("\n⏳ [System] 구글 API 과부하 방지를 위해 60초간 대기합니다... (RPM 초기화)")
        for i in range(60, 0, -10):
            print(f"   ... {i}초 남음")
            time.sleep(10)
        print("✅ 대기 완료! 다음 단계 진행.")

        # ---------------------------------------------------------
        # [Phase 3] Quant 실행
        # ---------------------------------------------------------
        print(f"\nPhase 3: AI Quant (재무 분석) 시작")
        print("-" * 50)
        
        try:
            quant_result = quant_agent.analyze_fundamentals(target_stock['name'], target_stock['code'])
            print("\n" + "="*50)
            print("🔢 [Quant 보고서]")
            print("="*50)
            print(quant_result)
        except Exception as e:
            print(f"❌ Quant 분석 중 오류 발생: {e}")

    else:
        print(f"🔻 추세 하락으로 분석 중단.")

    print("\n=== [HQA System] Complete ===")

if __name__ == "__main__":
    run_hqa_system()