# 파일: src/tools/realtime_tool.py
"""
한국투자증권 Open API 기반 실시간 시세 도구

공식 API 문서:
https://apiportal.koreainvestment.com

기능:
- 현재가 조회 (inquire_price)
- 호가 조회 (inquire_asking_price)
- 체결 내역 조회 (inquire_ccnl)
- 일/주/월봉 조회 (inquire_daily_price)
- 분봉 조회 (inquire_time_itemchartprice)
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
import sys
from pathlib import Path

# 상위 디렉토리 import 경로 추가
sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.kis_auth import call_api, is_api_available, KISConfig


# ==========================================
# 데이터 모델
# ==========================================
@dataclass
class StockPrice:
    """현재가 정보"""
    code: str                       # 종목코드
    name: str                       # 종목명
    current_price: int              # 현재가
    change: int                     # 전일대비
    change_rate: float              # 등락률 (%)
    open_price: int                 # 시가
    high_price: int                 # 고가
    low_price: int                  # 저가
    volume: int                     # 거래량
    volume_amount: int              # 거래대금 (백만원)
    prev_close: int                 # 전일종가
    market_cap: int                 # 시가총액 (억원)
    per: float                      # PER
    pbr: float                      # PBR
    eps: int                        # EPS
    bps: int                        # BPS
    high_52w: int                   # 52주 최고가
    low_52w: int                    # 52주 최저가
    timestamp: datetime = field(default_factory=datetime.now)
    
    @property
    def change_sign(self) -> str:
        """등락 부호"""
        if self.change > 0:
            return "▲"
        elif self.change < 0:
            return "▼"
        return "-"
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "name": self.name,
            "current_price": self.current_price,
            "change": self.change,
            "change_rate": self.change_rate,
            "open_price": self.open_price,
            "high_price": self.high_price,
            "low_price": self.low_price,
            "volume": self.volume,
            "prev_close": self.prev_close,
            "per": self.per,
            "pbr": self.pbr,
        }


@dataclass
class OrderBookEntry:
    """호가 항목"""
    price: int          # 호가 가격
    volume: int         # 잔량
    change: int = 0     # 직전 대비 잔량 변화


@dataclass
class OrderBook:
    """호가창 정보"""
    code: str
    ask_prices: List[OrderBookEntry] = field(default_factory=list)  # 매도호가 (10개)
    bid_prices: List[OrderBookEntry] = field(default_factory=list)  # 매수호가 (10개)
    total_ask_volume: int = 0   # 총 매도잔량
    total_bid_volume: int = 0   # 총 매수잔량
    timestamp: datetime = field(default_factory=datetime.now)
    
    @property
    def spread(self) -> int:
        """스프레드 (매도1호가 - 매수1호가)"""
        if self.ask_prices and self.bid_prices:
            return self.ask_prices[0].price - self.bid_prices[0].price
        return 0
    
    @property
    def imbalance_ratio(self) -> float:
        """매수/매도 불균형 비율 (-1 ~ 1, 양수면 매수 우위)"""
        total = self.total_ask_volume + self.total_bid_volume
        if total == 0:
            return 0
        return (self.total_bid_volume - self.total_ask_volume) / total


@dataclass
class OHLCV:
    """일/분봉 캔들 데이터"""
    date: str           # 날짜 (YYYYMMDD)
    time: str           # 시간 (HHMMSS, 분봉일 때만)
    open: int           # 시가
    high: int           # 고가
    low: int            # 저가
    close: int          # 종가
    volume: int         # 거래량
    amount: int = 0     # 거래대금


@dataclass
class TradeRecord:
    """체결 내역"""
    time: str           # 체결시간 (HHMMSS)
    price: int          # 체결가
    change: int         # 전일대비
    volume: int         # 체결수량
    cum_volume: int     # 누적거래량
    side: str           # 매수/매도 구분


# ==========================================
# API 함수 - 현재가 조회
# ==========================================
def inquire_price(
    stock_code: str,
    market: str = "J",
    paper: bool = False,
) -> Optional[StockPrice]:
    """
    주식 현재가 시세 조회
    
    API: FHKST01010100
    경로: /uapi/domestic-stock/v1/quotations/inquire-price
    
    Args:
        stock_code: 종목코드 (6자리)
        market: 시장구분 (J: 주식, ETF, ETN)
        paper: 모의투자 여부
        
    Returns:
        StockPrice 객체 또는 None
    """
    if not is_api_available():
        print("⚠️ KIS API 키가 설정되지 않았습니다.")
        return None
    
    path = "/uapi/domestic-stock/v1/quotations/inquire-price"
    tr_id = "FHKST01010100"
    
    params = {
        "FID_COND_MRKT_DIV_CODE": market,
        "FID_INPUT_ISCD": stock_code,
    }
    
    resp = call_api("GET", path, tr_id, params=params, paper=paper)
    
    if resp.get("rt_cd") != "0":
        print(f"❌ 현재가 조회 실패: {resp.get('msg1', 'Unknown error')}")
        return None
    
    output = resp.get("output", {})
    
    return StockPrice(
        code=stock_code,
        name=output.get("hts_kor_isnm", ""),
        current_price=int(output.get("stck_prpr", 0)),
        change=int(output.get("prdy_vrss", 0)),
        change_rate=float(output.get("prdy_ctrt", 0)),
        open_price=int(output.get("stck_oprc", 0)),
        high_price=int(output.get("stck_hgpr", 0)),
        low_price=int(output.get("stck_lwpr", 0)),
        volume=int(output.get("acml_vol", 0)),
        volume_amount=int(output.get("acml_tr_pbmn", 0)) // 1_000_000,
        prev_close=int(output.get("stck_prdy_clpr", 0)),
        market_cap=int(output.get("hts_avls", 0)),
        per=float(output.get("per", 0)),
        pbr=float(output.get("pbr", 0)),
        eps=int(output.get("eps", 0)),
        bps=int(output.get("bps", 0)),
        high_52w=int(output.get("stck_dryy_hgpr", 0)),
        low_52w=int(output.get("stck_dryy_lwpr", 0)),
    )


# ==========================================
# API 함수 - 호가 조회
# ==========================================
def inquire_asking_price(
    stock_code: str,
    market: str = "J",
    paper: bool = False,
) -> Optional[OrderBook]:
    """
    주식 호가 조회
    
    API: FHKST01010200
    경로: /uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn
    
    Args:
        stock_code: 종목코드
        market: 시장구분
        paper: 모의투자 여부
        
    Returns:
        OrderBook 객체 또는 None
    """
    if not is_api_available():
        return None
    
    path = "/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn"
    tr_id = "FHKST01010200"
    
    params = {
        "FID_COND_MRKT_DIV_CODE": market,
        "FID_INPUT_ISCD": stock_code,
    }
    
    resp = call_api("GET", path, tr_id, params=params, paper=paper)
    
    if resp.get("rt_cd") != "0":
        print(f"❌ 호가 조회 실패: {resp.get('msg1')}")
        return None
    
    output = resp.get("output1", {})
    
    # 매도호가 (1~10)
    ask_prices = []
    for i in range(1, 11):
        price = int(output.get(f"askp{i}", 0))
        volume = int(output.get(f"askp_rsqn{i}", 0))
        if price > 0:
            ask_prices.append(OrderBookEntry(price=price, volume=volume))
    
    # 매수호가 (1~10)
    bid_prices = []
    for i in range(1, 11):
        price = int(output.get(f"bidp{i}", 0))
        volume = int(output.get(f"bidp_rsqn{i}", 0))
        if price > 0:
            bid_prices.append(OrderBookEntry(price=price, volume=volume))
    
    return OrderBook(
        code=stock_code,
        ask_prices=ask_prices,
        bid_prices=bid_prices,
        total_ask_volume=int(output.get("total_askp_rsqn", 0)),
        total_bid_volume=int(output.get("total_bidp_rsqn", 0)),
    )


# ==========================================
# API 함수 - 일/주/월봉 조회
# ==========================================
def inquire_daily_price(
    stock_code: str,
    period: str = "D",
    adj_price: bool = True,
    count: int = 100,
    paper: bool = False,
) -> List[OHLCV]:
    """
    주식 일/주/월봉 조회
    
    API: FHKST01010400
    경로: /uapi/domestic-stock/v1/quotations/inquire-daily-price
    
    Args:
        stock_code: 종목코드
        period: 기간구분 (D: 일, W: 주, M: 월)
        adj_price: 수정주가 여부
        count: 조회 건수 (최대 100)
        paper: 모의투자 여부
        
    Returns:
        OHLCV 리스트
    """
    if not is_api_available():
        return []
    
    path = "/uapi/domestic-stock/v1/quotations/inquire-daily-price"
    tr_id = "FHKST01010400"
    
    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": stock_code,
        "FID_PERIOD_DIV_CODE": period,
        "FID_ORG_ADJ_PRC": "0" if adj_price else "1",  # 0: 수정주가, 1: 원주가
    }
    
    resp = call_api("GET", path, tr_id, params=params, paper=paper)
    
    if resp.get("rt_cd") != "0":
        print(f"❌ 일봉 조회 실패: {resp.get('msg1')}")
        return []
    
    output = resp.get("output", [])
    
    result = []
    for item in output[:count]:
        result.append(OHLCV(
            date=item.get("stck_bsop_date", ""),
            time="",
            open=int(item.get("stck_oprc", 0)),
            high=int(item.get("stck_hgpr", 0)),
            low=int(item.get("stck_lwpr", 0)),
            close=int(item.get("stck_clpr", 0)),
            volume=int(item.get("acml_vol", 0)),
            amount=int(item.get("acml_tr_pbmn", 0)),
        ))
    
    return result


# ==========================================
# API 함수 - 분봉 조회
# ==========================================
def inquire_time_itemchartprice(
    stock_code: str,
    time_unit: int = 1,
    count: int = 120,
    paper: bool = False,
) -> List[OHLCV]:
    """
    주식 분봉 조회
    
    API: FHKST03010200
    경로: /uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice
    
    Args:
        stock_code: 종목코드
        time_unit: 분 단위 (1, 3, 5, 10, 15, 30, 60)
        count: 조회 건수 (최대 120)
        paper: 모의투자 여부
        
    Returns:
        OHLCV 리스트
    """
    if not is_api_available():
        return []
    
    path = "/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice"
    tr_id = "FHKST03010200"
    
    # 현재 시간 (장중이면 현재, 아니면 어제 15:30)
    now = datetime.now()
    if now.hour < 9:
        # 장 시작 전이면 전일
        query_time = "153000"
    else:
        query_time = now.strftime("%H%M%S")
    
    params = {
        "FID_ETC_CLS_CODE": "",
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": stock_code,
        "FID_INPUT_HOUR_1": query_time,
        "FID_PW_DATA_INCU_YN": "N",
    }
    
    resp = call_api("GET", path, tr_id, params=params, paper=paper)
    
    if resp.get("rt_cd") != "0":
        print(f"❌ 분봉 조회 실패: {resp.get('msg1')}")
        return []
    
    output = resp.get("output2", [])
    
    result = []
    for item in output[:count]:
        result.append(OHLCV(
            date=item.get("stck_bsop_date", ""),
            time=item.get("stck_cntg_hour", ""),
            open=int(item.get("stck_oprc", 0)),
            high=int(item.get("stck_hgpr", 0)),
            low=int(item.get("stck_lwpr", 0)),
            close=int(item.get("stck_prpr", 0)),
            volume=int(item.get("cntg_vol", 0)),
        ))
    
    return result


# ==========================================
# API 함수 - 체결 내역 조회
# ==========================================
def inquire_ccnl(
    stock_code: str,
    count: int = 30,
    paper: bool = False,
) -> List[TradeRecord]:
    """
    주식 체결 내역 조회 (틱 데이터)
    
    API: FHKST01010300
    경로: /uapi/domestic-stock/v1/quotations/inquire-ccnl
    
    Args:
        stock_code: 종목코드
        count: 조회 건수 (최대 30)
        paper: 모의투자 여부
        
    Returns:
        TradeRecord 리스트
    """
    if not is_api_available():
        return []
    
    path = "/uapi/domestic-stock/v1/quotations/inquire-ccnl"
    tr_id = "FHKST01010300"
    
    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": stock_code,
    }
    
    resp = call_api("GET", path, tr_id, params=params, paper=paper)
    
    if resp.get("rt_cd") != "0":
        print(f"❌ 체결 조회 실패: {resp.get('msg1')}")
        return []
    
    output = resp.get("output", [])
    
    result = []
    for item in output[:count]:
        # 체결강도 기준 매수/매도 구분
        ccld_dvsn = item.get("ccld_dvsn", "")
        side = "매수" if ccld_dvsn == "1" else "매도" if ccld_dvsn == "2" else ""
        
        result.append(TradeRecord(
            time=item.get("stck_cntg_hour", ""),
            price=int(item.get("stck_prpr", 0)),
            change=int(item.get("prdy_vrss", 0)),
            volume=int(item.get("cntg_vol", 0)),
            cum_volume=int(item.get("acml_vol", 0)),
            side=side,
        ))
    
    return result


# ==========================================
# 통합 인터페이스 클래스
# ==========================================
class KISRealtimeTool:
    """
    한국투자증권 실시간 시세 도구
    
    공식 API 직접 호출 방식으로 구현
    
    Features:
    - 실시간 현재가 조회
    - 호가창 (10호가) 조회
    - 체결 내역 조회
    - 일/주/월봉 데이터
    - 분봉 데이터
    
    Example:
        tool = KISRealtimeTool()
        price = tool.get_current_price("005930")  # 삼성전자
        print(f"현재가: {price.current_price:,}원")
    """
    
    def __init__(self, paper: bool = False):
        """
        Args:
            paper: True면 모의투자, False면 실전투자
        """
        self.paper = paper
        self._available = is_api_available()
        
        if not self._available:
            print("⚠️ KIS API 키가 설정되지 않았습니다.")
            print("   .env 파일에 다음 값을 설정하세요:")
            print("   - KIS_APP_KEY")
            print("   - KIS_APP_SECRET")
            print("   - KIS_ACCOUNT_NO (선택)")
    
    @property
    def is_available(self) -> bool:
        return self._available
    
    def get_current_price(self, stock_code: str) -> Optional[StockPrice]:
        """현재가 조회"""
        return inquire_price(stock_code, paper=self.paper)
    
    def get_orderbook(self, stock_code: str) -> Optional[OrderBook]:
        """호가창 조회"""
        return inquire_asking_price(stock_code, paper=self.paper)
    
    def get_daily_ohlcv(
        self,
        stock_code: str,
        period: str = "D",
        count: int = 100,
    ) -> List[OHLCV]:
        """일/주/월봉 조회"""
        return inquire_daily_price(stock_code, period, count=count, paper=self.paper)
    
    def get_minute_ohlcv(
        self,
        stock_code: str,
        time_unit: int = 1,
        count: int = 120,
    ) -> List[OHLCV]:
        """분봉 조회"""
        return inquire_time_itemchartprice(stock_code, time_unit, count, paper=self.paper)
    
    def get_trade_records(self, stock_code: str, count: int = 30) -> List[TradeRecord]:
        """체결 내역 조회"""
        return inquire_ccnl(stock_code, count, paper=self.paper)
    
    def get_quote_summary(self, stock_code: str) -> str:
        """
        종합 시세 요약 (LLM 친화적 텍스트 반환)
        
        Args:
            stock_code: 종목코드
            
        Returns:
            자연어 형태의 시세 요약
        """
        price = self.get_current_price(stock_code)
        
        if not price:
            return f"종목코드 {stock_code}의 시세를 조회할 수 없습니다."
        
        # 등락 표시
        sign = "상승" if price.change > 0 else "하락" if price.change < 0 else "보합"
        
        summary = f"""
## {price.name} ({price.code}) 현재가 정보

**현재가**: {price.current_price:,}원 ({sign} {abs(price.change):,}원, {price.change_rate:+.2f}%)

| 항목 | 값 |
|------|-----|
| 시가 | {price.open_price:,}원 |
| 고가 | {price.high_price:,}원 |
| 저가 | {price.low_price:,}원 |
| 전일종가 | {price.prev_close:,}원 |
| 거래량 | {price.volume:,}주 |
| 거래대금 | {price.volume_amount:,}백만원 |
| 시가총액 | {price.market_cap:,}억원 |
| PER | {price.per:.2f} |
| PBR | {price.pbr:.2f} |
| 52주 최고 | {price.high_52w:,}원 |
| 52주 최저 | {price.low_52w:,}원 |

조회시간: {price.timestamp.strftime('%Y-%m-%d %H:%M:%S')}
""".strip()
        
        return summary


# ==========================================
# 하위 호환용 별칭 (기존 코드 호환)
# ==========================================
# 기존 RealtimeQuote 사용하던 코드를 위한 별칭
RealtimeQuote = StockPrice


# ==========================================
# CrewAI 도구 래퍼
# ==========================================
def create_realtime_tools():
    """CrewAI용 실시간 시세 도구 생성"""
    from crewai.tools import tool
    
    kis_tool = KISRealtimeTool()
    
    @tool("실시간 현재가 조회")
    def get_realtime_price(stock_code: str) -> str:
        """
        종목의 실시간 현재가를 조회합니다.
        한국투자증권 공식 API를 통해 정확한 실시간 데이터를 제공합니다.
        
        Args:
            stock_code: 종목코드 (예: "005930" 삼성전자)
            
        Returns:
            현재가, 등락률, 거래량 등 실시간 시세 정보
        """
        return kis_tool.get_quote_summary(stock_code)
    
    @tool("호가창 조회")
    def get_orderbook_info(stock_code: str) -> str:
        """
        종목의 호가창(10호가)을 조회합니다.
        매수/매도 세력의 힘을 파악하는 데 유용합니다.
        
        Args:
            stock_code: 종목코드
            
        Returns:
            10단계 매수/매도 호가 및 잔량
        """
        orderbook = kis_tool.get_orderbook(stock_code)
        
        if orderbook is None:
            return f"❌ {stock_code} 호가 조회 실패"
        
        lines = [f"📋 {stock_code} 호가창", "━" * 50]
        lines.append(f"{'매도잔량':>12} {'매도호가':>12} | {'매수호가':<12} {'매수잔량':<12}")
        lines.append("-" * 50)
        
        # 매도호가 (역순: 10호가 → 1호가)
        for i in range(min(10, len(orderbook.ask_prices)) - 1, -1, -1):
            entry = orderbook.ask_prices[i]
            lines.append(f"{entry.volume:>12,} {entry.price:>12,} |")
        
        lines.append("-" * 50)
        
        # 매수호가 (1호가 → 10호가)
        for i in range(min(10, len(orderbook.bid_prices))):
            entry = orderbook.bid_prices[i]
            lines.append(f"{'':>12} {'':>12} | {entry.price:<12,} {entry.volume:<12,}")
        
        lines.append("-" * 50)
        lines.append(f"총 매도잔량: {orderbook.total_ask_volume:,} | 총 매수잔량: {orderbook.total_bid_volume:,}")
        lines.append(f"불균형 비율: {orderbook.imbalance_ratio:+.2%} {'(매수 우세)' if orderbook.imbalance_ratio > 0 else '(매도 우세)'}")
        
        return "\n".join(lines)
    
    @tool("일봉 데이터 조회")
    def get_daily_chart(stock_code: str, days: int = 20) -> str:
        """
        종목의 일봉 데이터를 조회합니다.
        
        Args:
            stock_code: 종목코드
            days: 조회 일수 (기본 20일)
            
        Returns:
            일봉 OHLCV 데이터
        """
        candles = kis_tool.get_daily_ohlcv(stock_code, count=days)
        
        if not candles:
            return f"❌ {stock_code} 일봉 조회 실패"
        
        lines = [f"📈 {stock_code} 일봉 (최근 {len(candles)}일)", "━" * 60]
        lines.append(f"{'날짜':<10} {'시가':>10} {'고가':>10} {'저가':>10} {'종가':>10} {'거래량':>12}")
        lines.append("-" * 60)
        
        for c in candles[:days]:
            date_str = f"{c.date[:4]}-{c.date[4:6]}-{c.date[6:]}"
            lines.append(f"{date_str:<10} {c.open:>10,} {c.high:>10,} {c.low:>10,} {c.close:>10,} {c.volume:>12,}")
        
        return "\n".join(lines)
    
    return [get_realtime_price, get_orderbook_info, get_daily_chart]


# ==========================================
# 단독 테스트
# ==========================================
if __name__ == "__main__":
    print("=== KIS 실시간 시세 도구 테스트 ===\n")
    
    tool = KISRealtimeTool(paper=False)
    
    if not tool.is_available:
        print("API 키가 설정되지 않아 테스트를 건너뜁니다.")
        print("\n테스트하려면 .env 파일에 다음을 설정하세요:")
        print("  KIS_APP_KEY=your_api_key")
        print("  KIS_APP_SECRET=your_api_secret")
    else:
        # 삼성전자 현재가 조회
        test_code = "005930"
        
        print(f"[현재가 조회] {test_code}")
        price = tool.get_current_price(test_code)
        if price:
            print(f"  {price.name}: {price.current_price:,}원 ({price.change_rate:+.2f}%)")
        
        print(f"\n[호가 조회] {test_code}")
        orderbook = tool.get_orderbook(test_code)
        if orderbook:
            print(f"  매수잔량: {orderbook.total_bid_volume:,}")
            print(f"  매도잔량: {orderbook.total_ask_volume:,}")
            print(f"  불균형: {orderbook.imbalance_ratio:.2%}")
        
        print(f"\n[일봉 조회] {test_code} (최근 5일)")
        candles = tool.get_daily_ohlcv(test_code, count=5)
        for c in candles[:5]:
            print(f"  {c.date}: 시{c.open:,} 고{c.high:,} 저{c.low:,} 종{c.close:,}")
        
        print("\n[요약 정보]")
        print(tool.get_quote_summary(test_code))
