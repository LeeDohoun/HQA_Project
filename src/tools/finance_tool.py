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
from typing import Dict, Optional, Tuple
from dataclasses import dataclass

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
            
            for table in tables:
                caption = table.select_one("caption")
                if not caption:
                    continue
                
                # 수익성 지표
                if "수익성" in caption.text or "기업개요" in caption.text:
                    rows = table.select("tr")
                    for row in rows:
                        th = row.select_one("th")
                        tds = row.select("td")
                        
                        if not th or not tds:
                            continue
                        
                        label = th.text.strip()
                        # 가장 최근 연도 값 사용
                        value = self._parse_number(tds[-1].text) if tds else None
                        
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
    
    # 배당
    dividend_yield: Optional[float]
    
    # 점수
    valuation_score: int = 0      # 밸류에이션 점수 (25점)
    profitability_score: int = 0  # 수익성 점수 (25점)
    growth_score: int = 0         # 성장성 점수 (25점)
    stability_score: int = 0      # 안정성 점수 (25점)
    total_score: int = 0          # 총점 (100점)
    
    def calculate_scores(self):
        """점수 계산"""
        # 1. 밸류에이션 점수 (25점)
        self.valuation_score = self._calc_valuation_score()
        
        # 2. 수익성 점수 (25점)
        self.profitability_score = self._calc_profitability_score()
        
        # 3. 성장성 점수 (25점) - 현재는 ROE 기반 추정
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
        """성장성 점수 계산 (ROE 기반 추정)"""
        score = 0
        
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
        
        # 배당 지급 여부 (안정적 기업 지표) (0~5점)
        if self.dividend_yield is not None and self.dividend_yield > 0:
            score += 5
        
        # PBR > 0 (자본잠식 아님) (0~5점)
        if self.pbr is not None and self.pbr > 0:
            score += 5
        
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
    
    def __init__(self):
        self.naver_crawler = NaverFinanceCrawler()
    
    def analyze(self, stock_code: str) -> QuantitativeAnalysis:
        """
        종목의 정량적 분석 수행
        
        Args:
            stock_code: 종목 코드
            
        Returns:
            QuantitativeAnalysis 객체
        """
        # 네이버 금융에서 데이터 수집
        stock_info = self.naver_crawler.get_stock_info(stock_code)
        financial_data = self.naver_crawler.get_financial_summary(stock_code)
        
        # 분석 결과 생성
        analysis = QuantitativeAnalysis(
            stock_code=stock_code,
            stock_name=stock_info.get("stock_name", "Unknown"),
            current_price=stock_info.get("current_price", 0),
            market_cap=stock_info.get("market_cap", "N/A"),
            per=stock_info.get("per"),
            pbr=stock_info.get("pbr"),
            eps=stock_info.get("eps"),
            bps=stock_info.get("bps"),
            roe=financial_data.get("roe"),
            roa=financial_data.get("roa"),
            operating_margin=financial_data.get("operating_margin"),
            net_margin=financial_data.get("net_margin"),
            debt_ratio=financial_data.get("debt_ratio"),
            dividend_yield=stock_info.get("dividend_yield"),
        )
        
        # 점수 계산
        analysis.calculate_scores()
        
        return analysis


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