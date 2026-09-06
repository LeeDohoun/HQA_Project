import json
from types import SimpleNamespace

from bs4 import BeautifulSoup

from src.agents.quant import QuantAgent
from src.tools.finance_tool import NaverFinanceCrawler, QuantitativeAnalysis, QuantitativeAnalyzer


def test_quant_prompt_and_agent_do_not_reference_web_search():
    prompt = open("prompts/quant/quant.md", encoding="utf-8").read()
    agent_source = open("src/agents/quant.py", encoding="utf-8").read()

    forbidden_terms = [
        "웹 검색",
        "웹검색",
        "web_search",
        "search_web",
        "duckduckgo",
        "web_fallback",
    ]

    combined = prompt + "\n" + agent_source
    for term in forbidden_terms:
        assert term not in combined


def test_quant_agent_exposes_only_full_analysis_entrypoint():
    assert not hasattr(QuantAgent, "quick_check")


def test_naver_financial_summary_parses_company_performance_table():
    html = """
    <html><body>
      <table class="tb_type1 tb_num tb_type1_ifrs">
        <caption>기업실적분석 테이블</caption>
        <tbody>
          <tr><th>매출액</th><td>2,589,355</td><td>3,008,709</td><td>3,336,059</td><td>6,932,502</td></tr>
          <tr><th>영업이익</th><td>65,670</td><td>327,260</td><td>436,010</td><td>3,586,290</td></tr>
          <tr><th>당기순이익</th><td>154,871</td><td>344,514</td><td>452,068</td><td>2,962,779</td></tr>
          <tr><th>영업이익률</th><td>2.54</td><td>10.88</td><td>13.07</td><td>51.73</td></tr>
          <tr><th>순이익률</th><td>5.98</td><td>11.45</td><td>13.55</td><td>42.74</td></tr>
          <tr><th>ROE(지배주주)</th><td>4.15</td><td>9.03</td><td>10.85</td><td>52.18</td></tr>
          <tr><th>ROA</th><td>3.20</td><td>7.10</td><td>8.40</td><td>30.00</td></tr>
          <tr><th>부채비율</th><td>25.36</td><td>27.93</td><td>29.94</td><td></td></tr>
        </tbody>
      </table>
    </body></html>
    """

    soup = BeautifulSoup(html, "html.parser")
    result = NaverFinanceCrawler()._get_financial_data(soup, "005930")

    assert result["revenue"] == 6932502.0
    assert result["operating_profit"] == 3586290.0
    assert result["net_income"] == 2962779.0
    assert result["operating_margin"] == 51.73
    assert result["net_margin"] == 42.74
    assert result["roe"] == 52.18
    assert result["roa"] == 30.0
    assert result["debt_ratio"] == 29.94


def test_naver_financial_summary_does_not_overwrite_with_peer_tables():
    html = """
    <html><body>
      <table class="tb_type1 tb_num tb_type1_ifrs">
        <caption>기업실적분석 테이블</caption>
        <tbody>
          <tr><th>영업이익률</th><td>10.88</td><td>13.07</td></tr>
          <tr><th>ROE(지배주주)</th><td>9.03</td><td>10.85</td></tr>
          <tr><th>부채비율</th><td>27.93</td><td>29.94</td></tr>
        </tbody>
      </table>
      <table class="tb_type1 tb_num">
        <tbody>
          <tr><th>영업이익률(%)</th><td>99.99</td></tr>
          <tr><th>ROE(%)</th><td>88.88</td></tr>
          <tr><th>부채비율(%)</th><td>77.77</td></tr>
        </tbody>
      </table>
    </body></html>
    """

    soup = BeautifulSoup(html, "html.parser")
    result = NaverFinanceCrawler()._get_financial_data(soup, "005930")

    assert result["operating_margin"] == 13.07
    assert result["roe"] == 10.85
    assert result["debt_ratio"] == 29.94


def test_naver_financial_summary_prefers_latest_actual_annual_column():
    html = """
    <html><body>
      <table class="tb_type1 tb_num tb_type1_ifrs">
        <caption>기업실적분석 테이블</caption>
        <thead>
          <tr><th>주요재무정보</th><th colspan="4">최근 연간 실적</th><th colspan="3">최근 분기 실적</th></tr>
          <tr><th>2023.12</th><th>2024.12</th><th>2025.12</th><th>2026.12 (E)</th><th>2025.03</th><th>2025.06</th><th>2025.09</th></tr>
        </thead>
        <tbody>
          <tr><th>영업이익률</th><td>2.54</td><td>10.88</td><td>13.07</td><td>51.73</td><td>8.45</td><td>6.27</td><td>14.14</td></tr>
          <tr><th>ROE(지배주주)</th><td>4.15</td><td>9.03</td><td>10.85</td><td>52.18</td><td>9.24</td><td>7.95</td><td>8.37</td></tr>
          <tr><th>부채비율</th><td>25.36</td><td>27.93</td><td>29.94</td><td></td><td>26.99</td><td>26.36</td><td>26.64</td></tr>
        </tbody>
      </table>
    </body></html>
    """

    soup = BeautifulSoup(html, "html.parser")
    result = NaverFinanceCrawler()._get_financial_data(soup, "005930")

    assert result["operating_margin"] == 13.07
    assert result["roe"] == 10.85
    assert result["debt_ratio"] == 29.94


def test_quantitative_analysis_marks_missing_core_financials_as_insufficient():
    analysis = QuantitativeAnalysis(
        stock_code="005930",
        stock_name="삼성전자",
        current_price=0,
        market_cap="N/A",
        per=7.0,
        pbr=4.48,
        eps=None,
        bps=None,
        roe=None,
        roa=None,
        operating_margin=None,
        net_margin=None,
        debt_ratio=None,
        dividend_yield=0.5,
    )

    assert analysis.missing_core_metrics() == [
        "roe",
        "operating_margin",
        "debt_ratio",
    ]
    assert analysis.has_sufficient_financial_data() is False


def test_quant_agent_passes_missing_metric_warning_to_llm(monkeypatch):
    class FakeAnalyzer:
        def analyze(self, stock_code):
            analysis = QuantitativeAnalysis(
                stock_code=stock_code,
                stock_name="삼성전자",
                current_price=0,
                market_cap="N/A",
                per=7.0,
                pbr=4.48,
                eps=None,
                bps=None,
                roe=None,
                roa=None,
                operating_margin=None,
                net_margin=None,
                debt_ratio=None,
                dividend_yield=0.5,
            )
            analysis.calculate_scores()
            return analysis

    class FakeLLM:
        def __init__(self):
            self.last_prompt = ""

        def invoke(self, prompt):
            self.last_prompt = prompt
            return SimpleNamespace(
                content=json.dumps(
                    {
                        "valuation_score": 12,
                        "valuation_analysis": "PER/PBR 외 핵심 재무자료가 부족하다.",
                        "profitability_score": 12,
                        "profitability_analysis": "수익성 자료 부족.",
                        "growth_score": 12,
                        "growth_analysis": "성장성 자료 부족.",
                        "stability_score": 12,
                        "stability_analysis": "안정성 자료 부족.",
                        "opinion": "자료 부족으로 중립 판단.",
                    },
                    ensure_ascii=False,
                )
            )

    monkeypatch.setattr("src.agents.quant.get_quant_llm", FakeLLM)
    agent = QuantAgent()
    agent.analyzer = FakeAnalyzer()
    agent.llm = FakeLLM()

    score = agent.full_analysis("삼성전자", "005930")

    assert score.grade == "D"
    assert score.total_score == 48
    assert score.quality_flags["data_quality"] == "limited"
    assert score.quality_flags["missing_core_metrics"] == [
        "roe",
        "operating_margin",
        "debt_ratio",
    ]
    assert "핵심 재무 지표 누락" in agent.llm.last_prompt


def test_quantitative_analyzer_prefers_dart_financial_snapshot(tmp_path):
    financials = tmp_path / "raw" / "financials" / "semiconductor.jsonl"
    financials.parent.mkdir(parents=True)
    financials.write_text(
        json.dumps(
            {
                "source_type": "financials",
                "stock_name": "삼성전자",
                "stock_code": "005930",
                "corp_code": "00126380",
                "fiscal_year": "2025",
                "report_code": "11011",
                "report_name": "사업보고서",
                "revenue": 333605900000000.0,
                "operating_profit": 43601000000000.0,
                "net_income": 45206800000000.0,
                "assets": 550000000000000.0,
                "liabilities": 124000000000000.0,
                "equity": 416000000000000.0,
                "roe": 10.87,
                "operating_margin": 13.07,
                "net_margin": 13.55,
                "debt_ratio": 29.81,
                "currency": "KRW",
                "as_of": "2025-12-31",
                "metadata": {"source": "dart"},
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    class FakeCrawler:
        def get_stock_info(self, stock_code):
            return {
                "stock_code": stock_code,
                "stock_name": "삼성전자",
                "current_price": 70000,
                "market_cap": "N/A",
                "per": 12.0,
                "pbr": 1.2,
                "eps": None,
                "bps": None,
                "dividend_yield": 1.0,
            }

        def get_financial_summary(self, stock_code):
            raise AssertionError("Naver financial summary should not be used when DART snapshot exists")

    analyzer = QuantitativeAnalyzer(data_dir=str(tmp_path))
    analyzer.naver_crawler = FakeCrawler()

    analysis = analyzer.analyze("005930")

    assert analysis.roe == 10.87
    assert analysis.operating_margin == 13.07
    assert analysis.net_margin == 13.55
    assert analysis.debt_ratio == 29.81
    assert analysis.financial_source == "dart_financial_snapshot"
    assert analysis.has_sufficient_financial_data() is True


def test_quantitative_analyzer_uses_recent_three_year_financial_trends(tmp_path):
    financials = tmp_path / "raw" / "financials" / "semiconductor.jsonl"
    financials.parent.mkdir(parents=True)
    rows = [
        {"stock_code": "005930", "fiscal_year": "2023", "revenue": 100.0, "operating_profit": 10.0, "operating_margin": 10.0, "net_margin": 7.0, "roe": 8.0, "debt_ratio": 80.0},
        {"stock_code": "005930", "fiscal_year": "2024", "revenue": 120.0, "operating_profit": 18.0, "operating_margin": 15.0, "net_margin": 9.0, "roe": 10.0, "debt_ratio": 70.0},
        {"stock_code": "005930", "fiscal_year": "2025", "revenue": 150.0, "operating_profit": 30.0, "operating_margin": 20.0, "net_margin": 12.0, "roe": 12.0, "debt_ratio": 60.0},
    ]
    financials.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )

    class FakeCrawler:
        def get_stock_info(self, stock_code):
            return {
                "stock_code": stock_code,
                "stock_name": "삼성전자",
                "current_price": 70000,
                "market_cap": "N/A",
                "per": 12.0,
                "pbr": 1.2,
                "eps": None,
                "bps": None,
                "dividend_yield": 1.0,
            }

        def get_financial_summary(self, stock_code):
            raise AssertionError("DART snapshot should be used")

    analyzer = QuantitativeAnalyzer(data_dir=str(tmp_path))
    analyzer.naver_crawler = FakeCrawler()

    analysis = analyzer.analyze("005930")

    assert analysis.revenue == 150.0
    assert analysis.operating_profit == 30.0
    assert analysis.revenue_yoy_change == 25.0
    assert analysis.operating_profit_yoy_change == 66.67
    assert analysis.revenue_growth_3y == 22.47
    assert analysis.operating_profit_growth_3y == 73.21
    assert analysis.operating_margin_trend == 10.0
    assert analysis.net_margin_trend == 5.0
    assert analysis.financial_history_years == ["2025", "2024", "2023"]
    assert analysis.growth_score >= 20


def test_quantitative_analyzer_prefers_local_krx_fundamentals(tmp_path):
    fundamentals = tmp_path / "market_data" / "semiconductor" / "fundamentals.jsonl"
    fundamentals.parent.mkdir(parents=True)
    fundamentals.write_text(
        json.dumps(
            {
                "date": "2026-06-15",
                "stock_code": "005930",
                "stock_name": "삼성전자",
                "PER": 12.0,
                "PBR": 1.2,
                "EPS": 5800,
                "BPS": 58000,
                "DIV": 1.3,
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    financials = tmp_path / "raw" / "financials" / "semiconductor.jsonl"
    financials.parent.mkdir(parents=True)
    financials.write_text(
        json.dumps(
            {
                "stock_code": "005930",
                "fiscal_year": "2025",
                "revenue": 333605900000000.0,
                "operating_profit": 43601000000000.0,
                "net_income": 45206800000000.0,
                "roe": 10.87,
                "roa": 8.22,
                "operating_margin": 13.07,
                "net_margin": 13.55,
                "debt_ratio": 29.81,
                "current_ratio": 200.0,
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    class FakeCrawler:
        def get_stock_info(self, stock_code):
            return {
                "stock_code": stock_code,
                "stock_name": "삼성전자",
                "current_price": 70000,
                "market_cap": "N/A",
                "per": 99.0,
                "pbr": 9.9,
                "eps": 1,
                "bps": 1,
                "dividend_yield": 0.1,
            }

        def get_financial_summary(self, stock_code):
            raise AssertionError("DART snapshot should be used")

    analyzer = QuantitativeAnalyzer(data_dir=str(tmp_path))
    analyzer.naver_crawler = FakeCrawler()

    analysis = analyzer.analyze("005930")

    assert analysis.per == 12.0
    assert analysis.pbr == 1.2
    assert analysis.eps == 5800.0
    assert analysis.bps == 58000.0
    assert analysis.dividend_yield == 1.3
    assert analysis.current_ratio == 200.0
    assert analysis.financial_source == "dart_financial_snapshot+krx_fundamental"


def test_quant_agent_passes_dart_and_krx_metrics_to_llm(monkeypatch):
    class FakeAnalyzer:
        def analyze(self, stock_code):
            analysis = QuantitativeAnalysis(
                stock_code=stock_code,
                stock_name="삼성전자",
                current_price=70000,
                market_cap="N/A",
                per=12.0,
                pbr=1.2,
                eps=5800,
                bps=58000,
                roe=10.87,
                roa=8.22,
                operating_margin=13.07,
                net_margin=13.55,
                debt_ratio=29.81,
                current_ratio=200.0,
                revenue=333605900000000.0,
                operating_profit=43601000000000.0,
                net_income=45206800000000.0,
                revenue_yoy_change=25.0,
                operating_profit_yoy_change=66.67,
                revenue_growth_3y=22.47,
                operating_profit_growth_3y=73.21,
                operating_margin_trend=10.0,
                net_margin_trend=5.0,
                financial_history_years=["2025", "2024", "2023"],
                dividend_yield=1.0,
                financial_source="dart_financial_snapshot+krx_fundamental",
            )
            analysis.calculate_scores()
            return analysis

    class FakeLLM:
        def __init__(self):
            self.last_prompt = ""

        def invoke(self, prompt):
            self.last_prompt = prompt
            return SimpleNamespace(
                content=json.dumps(
                    {
                        "valuation_score": 18,
                        "valuation_analysis": "PER/PBR은 과도하지 않다.",
                        "profitability_score": 19,
                        "profitability_analysis": "ROE와 영업이익률이 양호하다.",
                        "growth_score": 16,
                        "growth_analysis": "성장성은 중립 이상이다.",
                        "stability_score": 22,
                        "stability_analysis": "부채비율과 유동비율이 안정적이다.",
                        "opinion": "재무적으로 양호하나 고성장 프리미엄은 제한적이다.",
                    },
                    ensure_ascii=False,
                )
            )

    monkeypatch.setattr("src.agents.quant.get_quant_llm", FakeLLM)
    agent = QuantAgent()
    agent.analyzer = FakeAnalyzer()
    agent.llm = FakeLLM()

    score = agent.full_analysis("삼성전자", "005930")

    assert score.total_score == 75
    assert score.grade == "B"
    assert score.per == 12.0
    assert score.pbr == 1.2
    assert score.roe == 10.87
    assert score.debt_ratio == 29.81
    assert score.quality_flags["source"] == "dart_financial_snapshot+krx_fundamental"
    assert '"per": 12.0' in agent.llm.last_prompt
    assert '"eps": 5800' in agent.llm.last_prompt
    assert '"current_ratio": 200.0' in agent.llm.last_prompt
    assert '"revenue": 333605900000000.0' in agent.llm.last_prompt
    assert '"revenue_growth_3y": 22.47' in agent.llm.last_prompt
    assert '"operating_profit_yoy_change": 66.67' in agent.llm.last_prompt
    assert score.revenue_growth_3y == 22.47
    assert score.financial_history_years == ["2025", "2024", "2023"]
