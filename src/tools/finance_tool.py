# 파일: src/tools/finance_tool.py
"""
정량적 분석 도구 (Quantitative Analysis Tools)

데이터 소스:
- 네이버 금융: PER, PBR, ROE 등 재무 지표 (한국 주식)

분석 항목:
1. 밸류에이션 (Valuation): PER, PBR, PSR
2. 수익성 (Profitability): ROE, ROA, 영업이익률
3. 성장성 (Growth): 매출/이익 성장률
4. 재무 건전성 (Financial Health): 부채비율, 유동비율
"""

import re
import requests
from bs4 import BeautifulSoup
import json
from pathlib import Path
from typing import Any, ClassVar, Dict, Iterable, Optional, Tuple
from dataclasses import dataclass, field

from src.config.settings import get_data_dir

# yfinance 제거됨 - 한국 주식은 네이버 금융 사용
# 해외 주식 필요 시 별도 모듈에서 처리

# CrewAI Tool
try:
    from crewai.tools import BaseTool
    HAS_CREWAI = True
except ImportError:
    HAS_CREWAI = False
    BaseTool = object


# ============================================================
# 네이버 금융 크롤러
# ============================================================

class NaverFinanceCrawler:
    """네이버 금융 데이터 크롤러"""

    BASE_URL = "https://finance.naver.com/item"

    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(self.HEADERS)
    
    def get_stock_info(self, stock_code: str) -> Dict:
        """
        종목의 기본 정보 및 투자 지표 조회
        
        Args:
            stock_code: 종목 코드 (예: "005930")
            
        Returns:
            종목 정보 딕셔너리
        """
        url = f"{self.BASE_URL}/main.naver?code={stock_code}"
        
        try:
            response = self.session.get(url, timeout=10)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            
            result = {
                "stock_code": stock_code,
                "stock_name": self._get_stock_name(soup),
                "current_price": self._get_current_price(soup),
                "market_cap": self._get_market_cap(soup),
            }
            
            # 투자 지표 (PER, PBR 등)
            result.update(self._get_investment_indicators(soup))
            
            return result
            
        except Exception as e:
            print(f"⚠️ 네이버 금융 크롤링 오류: {e}")
            return {"error": str(e), "stock_code": stock_code}
    
    def get_financial_summary(self, stock_code: str) -> Dict:
        """
        재무 정보 요약 조회 (매출, 영업이익, 순이익 등)
        
        Args:
            stock_code: 종목 코드
            
        Returns:
            재무 정보 딕셔너리
        """
        url = f"{self.BASE_URL}/main.naver?code={stock_code}"
        
        try:
            response = self.session.get(url, timeout=10)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            
            return self._get_financial_data(soup, stock_code)
            
        except Exception as e:
            print(f"⚠️ 재무 정보 크롤링 오류: {e}")
            return {"error": str(e)}
    
    def _get_stock_name(self, soup: BeautifulSoup) -> str:
        """종목명 추출"""
        try:
            name_tag = soup.select_one("div.wrap_company h2 a")
            if name_tag:
                return name_tag.text.strip()
            return "Unknown"
        except:
            return "Unknown"
    
    def _get_current_price(self, soup: BeautifulSoup) -> float:
        """현재가 추출"""
        try:
            price_tag = soup.select_one("p.no_today span.blind")
            if price_tag:
                price_text = price_tag.text.replace(",", "")
                return float(price_text)
            return 0.0
        except:
            return 0.0
    
    def _get_market_cap(self, soup: BeautifulSoup) -> str:
        """시가총액 추출"""
        try:
            # 시가총액 테이블에서 추출
            table = soup.select_one("table.no_info")
            if table:
                rows = table.select("tr")
                for row in rows:
                    th = row.select_one("th")
                    td = row.select_one("td")
                    if th and td and "시가총액" in th.text:
                        return td.text.strip()
            return "N/A"
        except:
            return "N/A"
    
    def _get_investment_indicators(self, soup: BeautifulSoup) -> Dict:
        """투자 지표 (PER, PBR, ROE 등) 추출"""
        result = {
            "per": None,
            "eps": None,
            "pbr": None,
            "bps": None,
            "dividend_yield": None,
        }
        
        try:
            # 투자지표 테이블
            table = soup.select_one("table.per_table")
            if not table:
                # 대체 방법: no_info 테이블에서 찾기
                tables = soup.select("table.no_info")
                for t in tables:
                    text = t.text
                    if "PER" in text or "EPS" in text:
                        table = t
                        break
            
            if table:
                rows = table.select("tr")
                for row in rows:
                    cells = row.select("td, th")
                    for i, cell in enumerate(cells):
                        text = cell.text.strip()
                        
                        if "PER" in text and i + 1 < len(cells):
                            result["per"] = self._parse_number(cells[i + 1].text)
                        elif "EPS" in text and i + 1 < len(cells):
                            result["eps"] = self._parse_number(cells[i + 1].text)
                        elif "PBR" in text and i + 1 < len(cells):
                            result["pbr"] = self._parse_number(cells[i + 1].text)
                        elif "BPS" in text and i + 1 < len(cells):
                            result["bps"] = self._parse_number(cells[i + 1].text)
            
            # 배당수익률 별도 추출
            div_tag = soup.find(string=re.compile("배당수익률"))
            if div_tag:
                parent = div_tag.find_parent("tr")
                if parent:
                    td = parent.select_one("td")
                    if td:
                        result["dividend_yield"] = self._parse_number(td.text)
            
        except Exception as e:
            print(f"⚠️ 투자지표 파싱 오류: {e}")
        
        return result
    
    def _get_financial_data(self, soup: BeautifulSoup, stock_code: str) -> Dict:
        """재무 데이터 추출"""
        result = {
            "stock_code": stock_code,
            "revenue": None,          # 매출액
            "operating_profit": None, # 영업이익
            "net_income": None,       # 순이익
            "roe": None,              # ROE
            "roa": None,              # ROA  
            "debt_ratio": None,       # 부채비율
            "operating_margin": None, # 영업이익률
            "net_margin": None,       # 순이익률
        }
        
        try:
            # 기업실적분석 테이블 찾기
            tables = soup.select("table.tb_type1")
            primary_tables = [table for table in tables if self._is_primary_financial_table(table)]
            candidate_tables = primary_tables or [
                table for table in tables if self._is_fallback_financial_table(table)
            ]
            
            for table in candidate_tables:
                rows = table.select("tr")
                for row in rows:
                    th = row.select_one("th")
                    tds = row.select("td")

                    if not th or not tds:
                        continue

                    label = th.get_text(" ", strip=True)
                    value = self._preferred_financial_number(table, tds)

                    if "ROE" in label:
                        result["roe"] = value
                    elif "ROA" in label:
                        result["roa"] = value
                    elif "부채비율" in label:
                        result["debt_ratio"] = value
                    elif "영업이익률" in label:
                        result["operating_margin"] = value
                    elif "순이익률" in label:
                        result["net_margin"] = value
                    elif "매출액" in label:
                        result["revenue"] = value
                    elif "영업이익" in label and "률" not in label:
                        result["operating_profit"] = value
                    elif "당기순이익" in label:
                        result["net_income"] = value
            
        except Exception as e:
            print(f"⚠️ 재무데이터 파싱 오류: {e}")
        
        return result

    def _is_primary_financial_table(self, table) -> bool:
        caption = table.select_one("caption")
        caption_text = caption.get_text(" ", strip=True) if caption else ""
        classes = set(table.get("class") or [])
        return (
            "기업실적분석" in caption_text
            or "tb_type1_ifrs" in classes
            or "수익성" in caption_text
            or "기업개요" in caption_text
        )

    def _is_fallback_financial_table(self, table) -> bool:
        table_text = table.get_text(" ", strip=True)
        return (
            "ROE" in table_text
            and "부채비율" in table_text
            and ("영업이익률" in table_text or "순이익률" in table_text)
        )

    def _latest_number(self, cells) -> Optional[float]:
        """오른쪽에서부터 비어 있지 않은 최신 숫자를 선택한다."""
        for cell in reversed(cells):
            value = self._parse_number(cell.get_text(" ", strip=True))
            if value is not None:
                return value
        return None

    def _preferred_financial_number(self, table, cells) -> Optional[float]:
        """네이버 기업실적 표에서 최신 연간 실제값을 우선 선택한다."""
        annual_indices = self._annual_actual_indices(table)
        for index in reversed(annual_indices):
            if index >= len(cells):
                continue
            value = self._parse_number(cells[index].get_text(" ", strip=True))
            if value is not None:
                return value
        return self._latest_number(cells)

    def _annual_actual_indices(self, table) -> list[int]:
        header_rows = table.select("tr")
        annual_count = 0
        date_labels: list[str] = []

        for row in header_rows[:3]:
            headers = row.select("th")
            for header in headers:
                text = header.get_text(" ", strip=True)
                if "최근 연간 실적" in text:
                    annual_count = int(header.get("colspan") or 0)
            labels = [header.get_text(" ", strip=True) for header in headers]
            if labels and any(re.search(r"\d{4}\.\d{2}", label) for label in labels):
                date_labels = labels

        if annual_count <= 0:
            return []

        indices = []
        for index in range(min(annual_count, len(date_labels) or annual_count)):
            label = date_labels[index] if index < len(date_labels) else ""
            if "(E)" not in label and "E)" not in label:
                indices.append(index)
        return indices
    
    def _parse_number(self, text: str) -> Optional[float]:
        """텍스트에서 숫자 추출"""
        try:
            # 쉼표, 공백 제거
            cleaned = text.replace(",", "").replace(" ", "").strip()
            # 숫자와 소수점, 마이너스만 추출
            match = re.search(r"-?[\d.]+", cleaned)
            if match:
                return float(match.group())
            return None
        except:
            return None


# ============================================================
# 정량적 분석 결과 데이터 클래스
# ============================================================

@dataclass
class QuantitativeAnalysis:
    """정량적 분석 결과"""
    stock_code: str
    stock_name: str
    current_price: float
    market_cap: str
    
    # 밸류에이션
    per: Optional[float]
    pbr: Optional[float]
    eps: Optional[float]
    bps: Optional[float]
    
    # 수익성
    roe: Optional[float]
    roa: Optional[float]
    operating_margin: Optional[float]
    net_margin: Optional[float]
    
    # 재무 건전성
    debt_ratio: Optional[float]
    current_ratio: Optional[float] = None
    current_assets: Optional[float] = None
    current_liabilities: Optional[float] = None

    # 손익 규모
    revenue: Optional[float] = None
    operating_profit: Optional[float] = None
    net_income: Optional[float] = None
    revenue_yoy_change: Optional[float] = None
    operating_profit_yoy_change: Optional[float] = None
    revenue_growth_3y: Optional[float] = None
    operating_profit_growth_3y: Optional[float] = None
    operating_margin_trend: Optional[float] = None
    net_margin_trend: Optional[float] = None
    financial_history_years: list[str] = field(default_factory=list)
    
    # 배당
    dividend_yield: Optional[float] = None
    
    # 점수
    valuation_score: int = 0      # 밸류에이션 점수 (25점)
    profitability_score: int = 0  # 수익성 점수 (25점)
    growth_score: int = 0         # 성장성 점수 (25점)
    stability_score: int = 0      # 안정성 점수 (25점)
    total_score: int = 0          # 총점 (100점)
    financial_source: str = "naver_finance"

    CORE_FINANCIAL_METRICS: ClassVar[Tuple[str, ...]] = ("roe", "operating_margin", "debt_ratio")

    def missing_core_metrics(self) -> list[str]:
        """퀀트 점수 신뢰도에 필요한 핵심 재무 지표 누락 목록."""
        return [
            field_name
            for field_name in self.CORE_FINANCIAL_METRICS
            if getattr(self, field_name) is None
        ]

    def has_sufficient_financial_data(self) -> bool:
        """핵심 수익성/안정성 지표와 최소 밸류에이션 지표가 있는지 확인."""
        core_present = len(self.CORE_FINANCIAL_METRICS) - len(self.missing_core_metrics())
        valuation_present = self.per is not None or self.pbr is not None
        return valuation_present and core_present >= 2
    
    def calculate_scores(self):
        """점수 계산"""
        # 1. 밸류에이션 점수 (25점)
        self.valuation_score = self._calc_valuation_score()
        
        # 2. 수익성 점수 (25점)
        self.profitability_score = self._calc_profitability_score()
        
        # 3. 성장성 점수 (25점)
        self.growth_score = self._calc_growth_score()
        
        # 4. 안정성 점수 (25점)
        self.stability_score = self._calc_stability_score()
        
        # 총점
        self.total_score = (
            self.valuation_score + 
            self.profitability_score + 
            self.growth_score + 
            self.stability_score
        )
    
    def _calc_valuation_score(self) -> int:
        """밸류에이션 점수 계산"""
        score = 0
        
        # PER 평가 (0~15점)
        if self.per is not None:
            if self.per < 0:
                score += 0  # 적자
            elif self.per < 8:
                score += 15  # 저평가
            elif self.per < 12:
                score += 12  # 적정
            elif self.per < 20:
                score += 8   # 약간 고평가
            elif self.per < 30:
                score += 4   # 고평가
            else:
                score += 0   # 매우 고평가
        
        # PBR 평가 (0~10점)
        if self.pbr is not None:
            if self.pbr < 0:
                score += 0
            elif self.pbr < 0.7:
                score += 10  # 저평가
            elif self.pbr < 1.0:
                score += 8   # 적정
            elif self.pbr < 1.5:
                score += 6   # 약간 고평가
            elif self.pbr < 3.0:
                score += 3   # 고평가
            else:
                score += 0
        
        return min(score, 25)
    
    def _calc_profitability_score(self) -> int:
        """수익성 점수 계산"""
        score = 0
        
        # ROE 평가 (0~12점)
        if self.roe is not None:
            if self.roe >= 20:
                score += 12  # 우수
            elif self.roe >= 15:
                score += 10  # 양호
            elif self.roe >= 10:
                score += 7   # 보통
            elif self.roe >= 5:
                score += 4   # 미흡
            elif self.roe > 0:
                score += 2   # 저조
            else:
                score += 0   # 적자
        
        # 영업이익률 평가 (0~8점)
        if self.operating_margin is not None:
            if self.operating_margin >= 20:
                score += 8
            elif self.operating_margin >= 15:
                score += 6
            elif self.operating_margin >= 10:
                score += 4
            elif self.operating_margin >= 5:
                score += 2
            else:
                score += 0
        
        # 순이익률 평가 (0~5점)
        if self.net_margin is not None:
            if self.net_margin >= 15:
                score += 5
            elif self.net_margin >= 10:
                score += 4
            elif self.net_margin >= 5:
                score += 2
            else:
                score += 0
        
        return min(score, 25)
    
    def _calc_growth_score(self) -> int:
        """성장성 점수 계산."""
        score = 0

        has_growth_data = (
            self.revenue_growth_3y is not None
            or self.operating_profit_growth_3y is not None
            or self.revenue_yoy_change is not None
            or self.operating_profit_yoy_change is not None
        )

        if has_growth_data:
            if self.revenue_growth_3y is not None:
                if self.revenue_growth_3y >= 20:
                    score += 8
                elif self.revenue_growth_3y >= 10:
                    score += 6
                elif self.revenue_growth_3y >= 3:
                    score += 4
                elif self.revenue_growth_3y > 0:
                    score += 2

            if self.operating_profit_growth_3y is not None:
                if self.operating_profit_growth_3y >= 25:
                    score += 8
                elif self.operating_profit_growth_3y >= 12:
                    score += 6
                elif self.operating_profit_growth_3y >= 3:
                    score += 4
                elif self.operating_profit_growth_3y > 0:
                    score += 2

            if self.revenue_yoy_change is not None:
                if self.revenue_yoy_change >= 15:
                    score += 4
                elif self.revenue_yoy_change >= 5:
                    score += 3
                elif self.revenue_yoy_change > 0:
                    score += 1

            if self.operating_margin_trend is not None:
                if self.operating_margin_trend >= 3:
                    score += 3
                elif self.operating_margin_trend > 0:
                    score += 2

            if self.dividend_yield is not None and self.dividend_yield < 2:
                score += 2

            return min(score, 25)
        
        # ROE가 높으면 재투자 수익률이 높아 성장 가능성 높음
        if self.roe is not None:
            if self.roe >= 25:
                score += 20
            elif self.roe >= 20:
                score += 16
            elif self.roe >= 15:
                score += 12
            elif self.roe >= 10:
                score += 8
            elif self.roe >= 5:
                score += 4
            else:
                score += 0
        
        # 배당을 적게 주면 재투자 여력 (0~5점)
        if self.dividend_yield is not None:
            if self.dividend_yield < 1:
                score += 5  # 재투자 중심
            elif self.dividend_yield < 2:
                score += 4
            elif self.dividend_yield < 3:
                score += 3
            else:
                score += 2  # 배당 중심 (성숙기업)
        
        return min(score, 25)
    
    def _calc_stability_score(self) -> int:
        """재무 안정성 점수 계산"""
        score = 0
        
        # 부채비율 평가 (0~15점)
        if self.debt_ratio is not None:
            if self.debt_ratio < 30:
                score += 15  # 매우 안정
            elif self.debt_ratio < 50:
                score += 12  # 안정
            elif self.debt_ratio < 100:
                score += 8   # 보통
            elif self.debt_ratio < 150:
                score += 4   # 주의
            else:
                score += 0   # 위험
        
        # 유동비율 평가 (0~5점)
        if self.current_ratio is not None:
            if self.current_ratio >= 200:
                score += 5
            elif self.current_ratio >= 150:
                score += 4
            elif self.current_ratio >= 100:
                score += 2

        # 배당 지급 여부 (안정적 기업 지표) (0~3점)
        if self.dividend_yield is not None and self.dividend_yield > 0:
            score += 3
        
        # PBR > 0 (자본잠식 아님) (0~2점)
        if self.pbr is not None and self.pbr > 0:
            score += 2
        
        return min(score, 25)
    
    def get_opinion(self) -> str:
        """투자 의견 반환"""
        if self.total_score >= 80:
            return "적극 매수"
        elif self.total_score >= 65:
            return "매수"
        elif self.total_score >= 50:
            return "관망"
        elif self.total_score >= 35:
            return "매도"
        else:
            return "적극 매도"
    
    def summary(self) -> str:
        """분석 결과 요약 텍스트"""
        lines = [
            f"═══════════════════════════════════════════════════",
            f"📊 {self.stock_name}({self.stock_code}) 정량적 분석",
            f"═══════════════════════════════════════════════════",
            f"💰 현재가: {self.current_price:,.0f}원",
            f"📈 시가총액: {self.market_cap}",
            f"",
            f"【 밸류에이션 】 {self.valuation_score}/25점",
            f"  • PER: {self._fmt(self.per)}배 {self._per_comment()}",
            f"  • PBR: {self._fmt(self.pbr)}배 {self._pbr_comment()}",
            f"  • EPS: {self._fmt(self.eps)}원",
            f"  • BPS: {self._fmt(self.bps)}원",
            f"",
            f"【 수익성 】 {self.profitability_score}/25점",
            f"  • ROE: {self._fmt(self.roe)}% {self._roe_comment()}",
            f"  • ROA: {self._fmt(self.roa)}%",
            f"  • 영업이익률: {self._fmt(self.operating_margin)}%",
            f"  • 순이익률: {self._fmt(self.net_margin)}%",
            f"",
            f"【 성장성 】 {self.growth_score}/25점",
            f"  • ROE 기반 재투자 수익률 추정",
            f"",
            f"【 재무 안정성 】 {self.stability_score}/25점",
            f"  • 부채비율: {self._fmt(self.debt_ratio)}% {self._debt_comment()}",
            f"  • 유동비율: {self._fmt(self.current_ratio)}%",
            f"  • 배당수익률: {self._fmt(self.dividend_yield)}%",
            f"",
            f"═══════════════════════════════════════════════════",
            f"📊 종합 점수: {self.total_score}/100점",
            f"💡 투자 의견: {self.get_opinion()}",
            f"═══════════════════════════════════════════════════",
        ]
        return "\n".join(lines)
    
    def _fmt(self, value: Optional[float]) -> str:
        """숫자 포맷팅"""
        if value is None:
            return "N/A"
        if abs(value) >= 1000:
            return f"{value:,.0f}"
        return f"{value:.2f}"
    
    def _per_comment(self) -> str:
        if self.per is None:
            return ""
        if self.per < 0:
            return "(적자)"
        if self.per < 10:
            return "(저평가)"
        if self.per < 20:
            return "(적정)"
        return "(고평가)"
    
    def _pbr_comment(self) -> str:
        if self.pbr is None:
            return ""
        if self.pbr < 0:
            return "(자본잠식)"
        if self.pbr < 1:
            return "(저평가)"
        if self.pbr < 2:
            return "(적정)"
        return "(고평가)"
    
    def _roe_comment(self) -> str:
        if self.roe is None:
            return ""
        if self.roe >= 15:
            return "(우수)"
        if self.roe >= 10:
            return "(양호)"
        if self.roe >= 5:
            return "(보통)"
        return "(저조)"
    
    def _debt_comment(self) -> str:
        if self.debt_ratio is None:
            return ""
        if self.debt_ratio < 50:
            return "(안정)"
        if self.debt_ratio < 100:
            return "(보통)"
        return "(주의)"


# ============================================================
# 정량적 분석기
# ============================================================

class QuantitativeAnalyzer:
    """정량적 분석기"""
    
    def __init__(self, data_dir: Optional[str] = None):
        self.naver_crawler = NaverFinanceCrawler()
        self.data_dir = Path(data_dir) if data_dir else get_data_dir()
    
    def analyze(self, stock_code: str) -> QuantitativeAnalysis:
        """
        종목의 정량적 분석 수행
        
        Args:
            stock_code: 종목 코드
            
        Returns:
            QuantitativeAnalysis 객체
        """
        # 네이버는 현재가/종목명 보조 소스로만 사용하고,
        # PER/PBR/EPS/BPS는 로컬 KRX fundamentals를 우선한다.
        stock_info = self.naver_crawler.get_stock_info(stock_code)
        krx_fundamental = self._load_krx_fundamental(stock_code)
        financial_data = self._load_financial_snapshot(stock_code)
        if not financial_data:
            financial_data = self.naver_crawler.get_financial_summary(stock_code)

        financial_source = financial_data.get("source", "naver_finance")
        if krx_fundamental:
            financial_source = f"{financial_source}+krx_fundamental"
        
        # 분석 결과 생성
        analysis = QuantitativeAnalysis(
            stock_code=stock_code,
            stock_name=stock_info.get("stock_name") or krx_fundamental.get("stock_name") or "Unknown",
            current_price=stock_info.get("current_price", 0),
            market_cap=stock_info.get("market_cap", "N/A"),
            per=self._first_number(krx_fundamental.get("per"), stock_info.get("per")),
            pbr=self._first_number(krx_fundamental.get("pbr"), stock_info.get("pbr")),
            eps=self._first_number(krx_fundamental.get("eps"), stock_info.get("eps")),
            bps=self._first_number(krx_fundamental.get("bps"), stock_info.get("bps")),
            roe=financial_data.get("roe"),
            roa=financial_data.get("roa"),
            operating_margin=financial_data.get("operating_margin"),
            net_margin=financial_data.get("net_margin"),
            debt_ratio=financial_data.get("debt_ratio"),
            current_ratio=financial_data.get("current_ratio"),
            current_assets=financial_data.get("current_assets"),
            current_liabilities=financial_data.get("current_liabilities"),
            revenue=financial_data.get("revenue"),
            operating_profit=financial_data.get("operating_profit"),
            net_income=financial_data.get("net_income"),
            revenue_yoy_change=financial_data.get("revenue_yoy_change"),
            operating_profit_yoy_change=financial_data.get("operating_profit_yoy_change"),
            revenue_growth_3y=financial_data.get("revenue_growth_3y"),
            operating_profit_growth_3y=financial_data.get("operating_profit_growth_3y"),
            operating_margin_trend=financial_data.get("operating_margin_trend"),
            net_margin_trend=financial_data.get("net_margin_trend"),
            financial_history_years=financial_data.get("financial_history_years") or [],
            dividend_yield=self._first_number(
                krx_fundamental.get("dividend_yield"),
                stock_info.get("dividend_yield"),
            ),
            financial_source=financial_source,
        )
        
        # 점수 계산
        analysis.calculate_scores()
        
        return analysis

    def _load_financial_snapshot(self, stock_code: str) -> Dict:
        candidates = list(self._financial_snapshot_paths())
        snapshots = []
        for path in candidates:
            for row in self._iter_jsonl(path):
                if str(row.get("stock_code", "")).strip() == stock_code:
                    snapshots.append(row)
        if not snapshots:
            return {}

        snapshots.sort(
            key=lambda row: (
                str(row.get("fiscal_year", "")),
                str(row.get("as_of", "")),
            ),
            reverse=True,
        )
        latest = snapshots[0]
        annual_history = snapshots[:3]
        trend_metrics = self._financial_trend_metrics(annual_history)
        return {
            "revenue": latest.get("revenue"),
            "operating_profit": latest.get("operating_profit"),
            "net_income": latest.get("net_income"),
            "assets": latest.get("assets"),
            "liabilities": latest.get("liabilities"),
            "equity": latest.get("equity"),
            "current_assets": latest.get("current_assets"),
            "current_liabilities": latest.get("current_liabilities"),
            "roe": latest.get("roe"),
            "roa": latest.get("roa"),
            "debt_ratio": latest.get("debt_ratio"),
            "current_ratio": latest.get("current_ratio"),
            "operating_margin": latest.get("operating_margin"),
            "net_margin": latest.get("net_margin"),
            "source": "dart_financial_snapshot",
            **trend_metrics,
        }

    def _financial_trend_metrics(self, rows: list[Dict[str, Any]]) -> Dict[str, Any]:
        if not rows:
            return {}
        latest_first = sorted(
            rows,
            key=lambda row: (str(row.get("fiscal_year", "")), str(row.get("as_of", ""))),
            reverse=True,
        )
        chronological = list(reversed(latest_first))
        latest = latest_first[0]
        previous = latest_first[1] if len(latest_first) >= 2 else {}
        oldest = chronological[0]

        return {
            "revenue_yoy_change": self._pct_change(latest.get("revenue"), previous.get("revenue")),
            "operating_profit_yoy_change": self._pct_change(
                latest.get("operating_profit"),
                previous.get("operating_profit"),
            ),
            "revenue_growth_3y": self._cagr(
                oldest.get("revenue"),
                latest.get("revenue"),
                len(chronological) - 1,
            ),
            "operating_profit_growth_3y": self._cagr(
                oldest.get("operating_profit"),
                latest.get("operating_profit"),
                len(chronological) - 1,
            ),
            "operating_margin_trend": self._point_change(
                oldest.get("operating_margin"),
                latest.get("operating_margin"),
            ),
            "net_margin_trend": self._point_change(oldest.get("net_margin"), latest.get("net_margin")),
            "financial_history_years": [str(row.get("fiscal_year", "")) for row in latest_first if row.get("fiscal_year")],
        }

    @staticmethod
    def _pct_change(current: Any, previous: Any) -> Optional[float]:
        current_number = QuantitativeAnalyzer._first_number(current)
        previous_number = QuantitativeAnalyzer._first_number(previous)
        if current_number is None or previous_number in (None, 0):
            return None
        return round(((current_number - previous_number) / previous_number) * 100, 2)

    @staticmethod
    def _cagr(start: Any, end: Any, periods: int) -> Optional[float]:
        start_number = QuantitativeAnalyzer._first_number(start)
        end_number = QuantitativeAnalyzer._first_number(end)
        if periods <= 0 or start_number is None or end_number is None or start_number <= 0 or end_number <= 0:
            return None
        return round(((end_number / start_number) ** (1 / periods) - 1) * 100, 2)

    @staticmethod
    def _point_change(start: Any, end: Any) -> Optional[float]:
        start_number = QuantitativeAnalyzer._first_number(start)
        end_number = QuantitativeAnalyzer._first_number(end)
        if start_number is None or end_number is None:
            return None
        return round(end_number - start_number, 2)

    def _load_krx_fundamental(self, stock_code: str) -> Dict[str, Any]:
        for path in self._krx_fundamental_paths():
            rows = [
                row for row in self._iter_jsonl(path)
                if str(row.get("stock_code") or row.get("code") or row.get("ticker") or "").strip() == stock_code
            ]
            if not rows:
                continue
            rows.sort(key=self._fundamental_sort_key, reverse=True)
            return self._normalize_krx_fundamental(rows[0])

        return self._load_krx_fundamental_from_pykrx(stock_code)

    def _krx_fundamental_paths(self) -> Iterable[Path]:
        market_root = self.data_dir / "market_data"
        if market_root.exists():
            yield from sorted(market_root.glob("*/fundamentals.jsonl"))

        raw_root = self.data_dir / "raw"
        for relative in ("krx_fundamentals", "fundamentals"):
            root = raw_root / relative
            if root.exists():
                yield from sorted(root.glob("*.jsonl"))

    @staticmethod
    def _fundamental_sort_key(row: Dict[str, Any]) -> Tuple[str, str, str]:
        return (
            str(row.get("date") or row.get("bas_dd") or row.get("as_of") or ""),
            str(row.get("collected_at") or row.get("timestamp") or ""),
            str(row.get("fiscal_year") or ""),
        )

    def _normalize_krx_fundamental(self, row: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "stock_code": row.get("stock_code") or row.get("code") or row.get("ticker"),
            "stock_name": row.get("stock_name") or row.get("name") or row.get("isu_abbrv"),
            "per": self._first_number(row.get("per"), row.get("PER")),
            "pbr": self._first_number(row.get("pbr"), row.get("PBR")),
            "eps": self._first_number(row.get("eps"), row.get("EPS")),
            "bps": self._first_number(row.get("bps"), row.get("BPS")),
            "dividend_yield": self._first_number(
                row.get("dividend_yield"),
                row.get("div"),
                row.get("DIV"),
                row.get("dvd_yld"),
            ),
            "source": "krx_fundamental",
        }

    def _load_krx_fundamental_from_pykrx(self, stock_code: str) -> Dict[str, Any]:
        try:
            from datetime import datetime
            from pykrx import stock
        except Exception:
            return {}

        try:
            today = datetime.now().strftime("%Y%m%d")
            frame = stock.get_market_fundamental(today, market="ALL")
            if frame is None or stock_code not in frame.index:
                return {}
            row = frame.loc[stock_code].to_dict()
        except Exception:
            return {}

        return self._normalize_krx_fundamental(
            {
                "stock_code": stock_code,
                "per": row.get("PER"),
                "pbr": row.get("PBR"),
                "eps": row.get("EPS"),
                "bps": row.get("BPS"),
                "dividend_yield": row.get("DIV"),
            }
        )

    def _financial_snapshot_paths(self) -> Iterable[Path]:
        raw_root = self.data_dir / "raw" / "financials"
        if raw_root.exists():
            yield from sorted(raw_root.glob("*.jsonl"))

        market_root = self.data_dir / "market_data"
        if market_root.exists():
            for path in sorted(market_root.glob("*/financials.jsonl")):
                yield path

    @staticmethod
    def _iter_jsonl(path: Path) -> Iterable[Dict]:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict):
                    yield row

    @staticmethod
    def _first_number(*values: Any) -> Optional[float]:
        for value in values:
            if value in (None, "", "-", "N/A"):
                continue
            if isinstance(value, (int, float)):
                return float(value)
            try:
                cleaned = str(value).replace(",", "").replace("%", "").strip()
                return float(cleaned)
            except ValueError:
                continue
        return None


# ============================================================
# CrewAI Tool 구현
# ============================================================

if HAS_CREWAI:
    
    class FinancialAnalysisTool(BaseTool):
        """종합 재무 분석 도구 (CrewAI)"""
        name: str = "Financial Analysis"
        description: str = (
            "Performs comprehensive quantitative financial analysis on a Korean stock. "
            "Analyzes valuation (PER, PBR), profitability (ROE, ROA), and financial stability (debt ratio). "
            "Input should be the stock code (e.g., '005930' for Samsung Electronics). "
            "Returns detailed financial metrics and investment score."
        )
        
        _analyzer: QuantitativeAnalyzer = None
        
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self._analyzer = QuantitativeAnalyzer()
        
        def _run(self, stock_code: str) -> str:
            """재무 분석 실행"""
            try:
                stock_code = stock_code.strip().replace(" ", "")
                result = self._analyzer.analyze(stock_code)
                return result.summary()
            except Exception as e:
                return f"재무 분석 오류: {str(e)}"
    
    
    class ValuationTool(BaseTool):
        """밸류에이션 분석 도구 (CrewAI)"""
        name: str = "Valuation Analysis"
        description: str = (
            "Analyzes the valuation of a Korean stock using PER and PBR. "
            "Determines if the stock is overvalued or undervalued. "
            "Input should be the stock code (e.g., '005930')."
        )
        
        _analyzer: QuantitativeAnalyzer = None
        
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self._analyzer = QuantitativeAnalyzer()
        
        def _run(self, stock_code: str) -> str:
            """밸류에이션 분석"""
            try:
                stock_code = stock_code.strip()
                result = self._analyzer.analyze(stock_code)
                
                per_status = "저평가" if result.per and result.per < 15 else "고평가" if result.per and result.per > 25 else "적정"
                pbr_status = "저평가" if result.pbr and result.pbr < 1 else "고평가" if result.pbr and result.pbr > 2 else "적정"
                
                return f"""
📊 밸류에이션 분석 ({result.stock_name})
━━━━━━━━━━━━━━━━━━━━━━━━
• 현재가: {result.current_price:,.0f}원
• 시가총액: {result.market_cap}

• PER: {result._fmt(result.per)}배 → {per_status}
  (업종 평균 대비 평가)
  
• PBR: {result._fmt(result.pbr)}배 → {pbr_status}
  (순자산 대비 평가)
  
• EPS: {result._fmt(result.eps)}원 (주당순이익)
• BPS: {result._fmt(result.bps)}원 (주당순자산)

💡 밸류에이션 점수: {result.valuation_score}/25점
"""
            except Exception as e:
                return f"밸류에이션 분석 오류: {str(e)}"
    
    
    class ProfitabilityTool(BaseTool):
        """수익성 분석 도구 (CrewAI)"""
        name: str = "Profitability Analysis"
        description: str = (
            "Analyzes the profitability of a Korean stock using ROE, ROA, and profit margins. "
            "Evaluates how efficiently the company generates profits. "
            "Input should be the stock code (e.g., '005930')."
        )
        
        _analyzer: QuantitativeAnalyzer = None
        
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self._analyzer = QuantitativeAnalyzer()
        
        def _run(self, stock_code: str) -> str:
            """수익성 분석"""
            try:
                stock_code = stock_code.strip()
                result = self._analyzer.analyze(stock_code)
                
                roe_status = "우수" if result.roe and result.roe >= 15 else "양호" if result.roe and result.roe >= 10 else "보통"
                
                return f"""
📊 수익성 분석 ({result.stock_name})
━━━━━━━━━━━━━━━━━━━━━━━━
• ROE: {result._fmt(result.roe)}% → {roe_status}
  (자기자본 대비 순이익률, 15% 이상 우수)
  
• ROA: {result._fmt(result.roa)}%
  (총자산 대비 순이익률)
  
• 영업이익률: {result._fmt(result.operating_margin)}%
  (매출 대비 영업이익)
  
• 순이익률: {result._fmt(result.net_margin)}%
  (매출 대비 순이익)

💡 수익성 점수: {result.profitability_score}/25점
"""
            except Exception as e:
                return f"수익성 분석 오류: {str(e)}"
    
    
    class FinancialHealthTool(BaseTool):
        """재무 건전성 분석 도구 (CrewAI)"""
        name: str = "Financial Health Analysis"
        description: str = (
            "Analyzes the financial health and stability of a Korean stock. "
            "Evaluates debt ratio and dividend sustainability. "
            "Input should be the stock code (e.g., '005930')."
        )
        
        _analyzer: QuantitativeAnalyzer = None
        
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self._analyzer = QuantitativeAnalyzer()
        
        def _run(self, stock_code: str) -> str:
            """재무 건전성 분석"""
            try:
                stock_code = stock_code.strip()
                result = self._analyzer.analyze(stock_code)
                
                debt_status = "안정" if result.debt_ratio and result.debt_ratio < 50 else "보통" if result.debt_ratio and result.debt_ratio < 100 else "주의"
                
                return f"""
📊 재무 건전성 분석 ({result.stock_name})
━━━━━━━━━━━━━━━━━━━━━━━━
• 부채비율: {result._fmt(result.debt_ratio)}% → {debt_status}
  (100% 이하 권장, 50% 이하 우량)
  
• 배당수익률: {result._fmt(result.dividend_yield)}%
  (안정적 현금흐름 지표)
  
• PBR: {result._fmt(result.pbr)}배
  (1 이상이면 자본잠식 아님)

💡 안정성 점수: {result.stability_score}/25점
"""
            except Exception as e:
                return f"재무 건전성 분석 오류: {str(e)}"


# ============================================================
# 직접 사용 가능한 함수
# ============================================================

def analyze_financials(stock_code: str) -> QuantitativeAnalysis:
    """종목 재무 분석"""
    analyzer = QuantitativeAnalyzer()
    return analyzer.analyze(stock_code)


def get_valuation(stock_code: str) -> Dict:
    """밸류에이션 지표만 조회"""
    result = analyze_financials(stock_code)
    return {
        "per": result.per,
        "pbr": result.pbr,
        "eps": result.eps,
        "bps": result.bps,
        "score": result.valuation_score
    }


def get_profitability(stock_code: str) -> Dict:
    """수익성 지표만 조회"""
    result = analyze_financials(stock_code)
    return {
        "roe": result.roe,
        "roa": result.roa,
        "operating_margin": result.operating_margin,
        "net_margin": result.net_margin,
        "score": result.profitability_score
    }


# ============================================================
# 테스트
# ============================================================

if __name__ == "__main__":
    print("=" * 60)
    print("📊 정량적 분석 도구 테스트")
    print("=" * 60)
    
    analyzer = QuantitativeAnalyzer()
    
    # SK하이닉스 테스트
    result = analyzer.analyze("000660")
    print(result.summary())
