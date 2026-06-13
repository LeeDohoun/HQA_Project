import json

from bs4 import BeautifulSoup

import src.agents.quant as quant_module
from src.agents.quant import QuantAgent
from src.tools.finance_tool import NaverFinanceCrawler, QuantitativeAnalysis, QuantitativeAnalyzer


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


def test_quant_agent_does_not_emit_f_when_naver_core_financials_are_missing(monkeypatch):
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

    monkeypatch.setattr(quant_module, "_WEB_SEARCH_AVAILABLE", False)
    agent = QuantAgent()
    agent.analyzer = FakeAnalyzer()

    score = agent.full_analysis("삼성전자", "005930")

    assert score.grade == "C"
    assert score.total_score == 48
    assert score.quality_flags["data_quality"] == "insufficient"
    assert score.quality_flags["missing_core_metrics"] == [
        "roe",
        "operating_margin",
        "debt_ratio",
    ]


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
