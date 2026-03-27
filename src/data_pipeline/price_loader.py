# 파일 위치: src/data_pipeline/price_loader.py

import FinanceDataReader as fdr
import pandas as pd
from datetime import datetime, timedelta

class PriceLoader:
    def __init__(self):
        pass

    def get_stock_data(self, code, days=300):
        """
        특정 종목의 최근 N일치 주가 데이터를 가져옵니다.
        150일 이평선 계산을 위해 넉넉하게 300일 데이터를 요청합니다.
        """
        # 오늘 날짜와 N일 전 날짜 계산
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days)
        
        # 데이터 수집 (FinanceDataReader 사용)
        df = fdr.DataReader(code, start_date, end_date)
        return df

    def check_technical_status(self, code, name="Unknown"):
        """
        [기획안 핵심 로직]
        주가가 150일(30주) 이동평균선 위에 있는지 판단합니다.
        Return: (통과여부(bool), 현재가, 150일이평선)
        """
        df = self.get_stock_data(code)
        
        if len(df) < 150:
            print(f"[{name}] 데이터 부족으로 분석 불가 (상장 150일 미만)")
            return False, 0, 0

        # 150일 단순 이동평균선(SMA) 계산
        df['MA150'] = df['Close'].rolling(window=150).mean()
        
        # 최신 데이터 추출
        latest = df.iloc[-1]
        current_price = latest['Close']
        ma150 = latest['MA150']
        
        # [조건] 주가 > 150일 이평선
        is_bullish = current_price > ma150
        
        return is_bullish, current_price, ma150

# 테스트용 실행 코드 (이 파일을 직접 실행할 때만 동작)
if __name__ == "__main__":
    loader = PriceLoader()
    # 삼성전자(005930) 테스트
    result, price, ma = loader.check_technical_status("005930", "삼성전자")
    print(f"삼성전자 분석 결과: {'✅ 통과' if result else '❌ 탈락'}")
    print(f"현재가: {price}원 vs 150일 이평선: {ma:.0f}원")