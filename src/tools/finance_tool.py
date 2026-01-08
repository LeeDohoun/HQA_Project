# 파일: src/tools/finance_tool.py

import yfinance as yf
from crewai.tools import BaseTool

class FinancialAnalysisTool(BaseTool):
    name: str = "Financial Data Search"
    description: str = (
        "Search for fundamental financial data (PER, PBR, ROE, Revenue, Net Income) "
        "of a specific stock using its ticker code. "
        "Useful for quantitative analysis and valuation."
    )

    def _run(self, ticker: str) -> str:
        """
        특정 종목의 재무 데이터를 조회합니다.
        Input: 종목코드 (예: '000660') -> 자동으로 .KS를 붙여 조회함
        """
        # 한국 코스피 종목은 뒤에 .KS가 필요함 (이미 있으면 그대로 둠)
        if not ticker.endswith(".KS") and not ticker.endswith(".KQ"):
            yf_ticker = f"{ticker}.KS"
        else:
            yf_ticker = ticker
            
        stock = yf.Ticker(yf_ticker)
        info = stock.info
        
        # 데이터가 없는 경우 방어 코드
        if not info or 'regularMarketPrice' not in info:
             return f"Error: '{ticker}'에 대한 재무 데이터를 찾을 수 없습니다."

        # 주요 지표 추출 (없으면 'N/A' 처리)
        currency = info.get('currency', 'KRW')
        price = info.get('currentPrice', info.get('regularMarketPrice', 0))
        market_cap = info.get('marketCap', 0)
        
        per = info.get('trailingPE', 'N/A') # PER
        pbr = info.get('priceToBook', 'N/A') # PBR
        roe = info.get('returnOnEquity', 'N/A') # ROE
        
        # 결과 텍스트 포맷팅
        result = f"""
        [재무 분석 데이터: {ticker}]
        1. 현재 주가: {price:,} {currency}
        2. 시가총액: {market_cap:,} {currency}
        3. 밸류에이션 지표:
           - PER (주가수익비율): {per}
           - PBR (주가순자산비율): {pbr}
           - ROE (자기자본이익률): {roe}
        4. 의견: 이 데이터만 보고 고평가/저평가 여부를 판단하세요.
        """
        return result