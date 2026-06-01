from __future__ import annotations

import json
from datetime import date, timedelta
from types import SimpleNamespace

from src.agents.theme_orchestrator import (
    ThemeAnalystEvaluation,
    ThemeQuantEvaluation,
    ThemeLeaderOrchestrator,
    ThemeCandidate,
)
from src.agents.chartist import ChartistScore
from src.agents.risk_manager import FinalDecision, InvestmentAction, RiskLevel


def _orchestrator() -> ThemeLeaderOrchestrator:
    return ThemeLeaderOrchestrator.__new__(ThemeLeaderOrchestrator)


def test_extract_first_json_object_ignores_trailing_extra_data():
    orchestrator = _orchestrator()

    payload = orchestrator._extract_first_json_object(
        '{"moat_score": 31, "summary": "ok"}\n{"extra": true}'
    )

    assert payload == {"moat_score": 31, "summary": "ok"}


def test_invoke_json_prefers_structured_output_when_available():
    orchestrator = _orchestrator()

    class StructuredRunner:
        def invoke(self, _prompt):
            return ThemeAnalystEvaluation(
                moat_score=34,
                growth_score=20,
                grade="A",
                summary="structured",
                key_points=["point"],
            )

    class StructuredLLM:
        def __init__(self):
            self.method = None
            self.raw_called = False

        def with_structured_output(self, _schema, method="json_schema"):
            self.method = method
            return StructuredRunner()

        def invoke(self, _prompt):
            self.raw_called = True
            return SimpleNamespace(content='{"moat_score": 1}')

    llm = StructuredLLM()
    payload = orchestrator._invoke_json(
        llm,
        "prompt",
        ThemeAnalystEvaluation,
        label="test-structured",
    )

    assert llm.method == "json_schema"
    assert llm.raw_called is False
    assert payload["moat_score"] == 34
    assert payload["summary"] == "structured"


def test_invoke_json_recovers_first_json_object_from_raw_response():
    orchestrator = _orchestrator()

    class RawOnlyLLM:
        def with_structured_output(self, _schema, method="json_schema"):
            raise RuntimeError("structured output unavailable")

        def invoke(self, _prompt):
            return SimpleNamespace(
                content=(
                    "설명 문장\n"
                    "```json\n"
                    '{"moat_score": 28, "growth_score": 19, "grade": "B", "summary": "raw"}'
                    "\n```\n"
                    '{"ignored": true}'
                )
            )

    payload = orchestrator._invoke_json(
        RawOnlyLLM(),
        "prompt",
        ThemeAnalystEvaluation,
        label="test-raw-recovery",
    )

    assert payload["moat_score"] == 28
    assert payload["growth_score"] == 19
    assert payload["summary"] == "raw"
    assert payload["structured_parse_failed"] is True
    assert payload["parse_fallback_used"] is True


def test_invoke_json_coerces_numeric_strings_and_sets_flags():
    orchestrator = _orchestrator()

    class RawOnlyLLM:
        def with_structured_output(self, _schema, method="json_schema"):
            raise RuntimeError("structured output unavailable")

        def invoke(self, _prompt):
            return SimpleNamespace(
                content=(
                    '{"valuation_score": "21", "profitability_score": "19", '
                    '"growth_score": "17", "stability_score": "15", '
                    '"per": "10.4", "pbr": "1.2", "roe": "9.8", "debt_ratio": "48.5"}'
                )
            )

    payload = orchestrator._invoke_json(
        RawOnlyLLM(),
        "prompt",
        ThemeQuantEvaluation,
        label="test-coerce",
    )

    assert payload["valuation_score"] == 21
    assert payload["roe"] == 9.8
    assert payload["structured_parse_failed"] is True
    assert payload["parse_fallback_used"] is True
    assert "valuation_score" in payload["coerced_fields"]
    assert "roe" in payload["coerced_fields"]


def test_fallback_outputs_set_quality_flags():
    orchestrator = _orchestrator()
    candidate = type(
        "Candidate",
        (),
        {
            "stock_name": "테스트",
            "stock_code": "000000",
            "news_docs": 0,
            "forum_docs": 0,
            "dart_docs": 0,
            "market_rows": 0,
            "source_coverage": 0,
            "corpus_docs": 0,
            "data_coverage": "missing",
        },
    )()

    analyst = orchestrator._fallback_analyst("테마", candidate, "", {})
    quant = orchestrator._fallback_quant("테마", candidate, "", {})

    assert analyst["quality_flags"]["fallback_used"] is True
    assert analyst["quality_flags"]["parse_fallback_used"] is True
    assert quant.quality_flags["fallback_used"] is True
    assert quant.quality_flags["parse_fallback_used"] is True


def test_strategy_profile_leader_score_weights():
    orchestrator = _orchestrator()

    default_score = orchestrator._compute_leader_score(
        analyst_total=60,
        quant_total=85,
        chartist_total=95,
        final_total=72,
        strategy_profile="default",
    )
    short_score = orchestrator._compute_leader_score(
        analyst_total=60,
        quant_total=85,
        chartist_total=95,
        final_total=72,
        strategy_profile="short",
    )
    long_score = orchestrator._compute_leader_score(
        analyst_total=60,
        quant_total=85,
        chartist_total=95,
        final_total=72,
        strategy_profile="long",
    )

    assert default_score == 72
    assert short_score > long_score


def test_leader_score_does_not_change_with_data_coverage():
    orchestrator = _orchestrator()

    score = orchestrator._compute_leader_score(
        analyst_total=50,
        quant_total=50,
        chartist_total=50,
        final_total=64,
        strategy_profile="default",
    )

    assert score == 64


def test_unknown_strategy_profile_falls_back_to_default():
    orchestrator = _orchestrator()
    assert orchestrator._normalize_strategy_profile("unknown") == "default"


def test_evaluate_candidate_passes_investor_profile_to_risk_manager(monkeypatch):
    orchestrator = _orchestrator()
    orchestrator._load_stock_records = lambda _theme_key, _stock_code: []
    orchestrator._compose_context = lambda _records, _sources, max_docs: ""
    orchestrator._evaluate_analyst_candidate = lambda *args, **kwargs: {
        "moat_score": 20,
        "growth_score": 20,
        "total_score": 40,
        "grade": "B",
        "summary": "분석",
        "packet": SimpleNamespace(to_dict=lambda: {}, catalysts=[], risks=[]),
        "quality_flags": {},
    }
    orchestrator._evaluate_quant_candidate = lambda *args, **kwargs: SimpleNamespace(
        valuation_score=20,
        profitability_score=20,
        growth_score=20,
        stability_score=20,
        total_score=80,
        grade="A",
        opinion="양호",
        analysis_packet={},
        quality_flags={},
    )
    orchestrator._evaluate_chartist_candidate = lambda *args, **kwargs: ChartistScore(
        trend_score=20,
        momentum_score=20,
        volatility_score=15,
        volume_score=15,
        total_score=70,
        signal="매수",
        trend_analysis="",
        momentum_analysis="",
        volatility_analysis="",
        volume_analysis="",
        short_term_opinion="매수",
        mid_term_opinion="중립",
        analysis_packet={},
    )
    monkeypatch.setattr(
        "src.agents.theme_orchestrator.run_agents_parallel",
        lambda tasks, max_workers=3: {name: fn(*args) for name, (fn, args) in tasks.items()},
    )

    received = {}

    class FakeRiskManager:
        def make_decision(self, stock_name, stock_code, scores, portfolio_context=None, investor_profile=None):
            received["investor_profile"] = investor_profile
            return FinalDecision(
                stock_name=stock_name,
                stock_code=stock_code,
                total_score=70,
                action=InvestmentAction.BUY,
                confidence=75,
                risk_level=RiskLevel.LOW,
                risk_factors=[],
                position_size="10%",
                entry_strategy="분할",
                exit_strategy="분할",
                stop_loss="-5%",
                signal_alignment="일치",
                key_catalysts=[],
                contrarian_view="",
                summary="매수",
                detailed_reasoning="",
            )

    orchestrator.risk_manager = FakeRiskManager()
    profile = {"investment_type": "STABLE", "loss_tolerance": "LEVEL_1"}

    orchestrator.evaluate_candidate(
        "AI",
        "ai",
        ThemeCandidate(stock_name="테스트", stock_code="000001", data_coverage="enough"),
        investor_profile=profile,
    )

    assert received["investor_profile"] == profile


def test_evaluate_candidate_passes_price_snapshot_only_to_chartist(monkeypatch):
    orchestrator = _orchestrator()
    orchestrator._load_stock_records = lambda _theme_key, _stock_code: []
    orchestrator._compose_context = lambda _records, _sources, max_docs: ""
    orchestrator._evaluate_analyst_candidate = lambda *args, **kwargs: {
        "moat_score": 20,
        "growth_score": 20,
        "total_score": 40,
        "grade": "B",
        "summary": "분석",
        "packet": SimpleNamespace(to_dict=lambda: {}, catalysts=[], risks=[]),
        "quality_flags": {},
    }
    orchestrator._evaluate_quant_candidate = lambda *args, **kwargs: SimpleNamespace(
        valuation_score=20,
        profitability_score=20,
        growth_score=20,
        stability_score=20,
        total_score=80,
        grade="A",
        opinion="양호",
        analysis_packet={},
        quality_flags={},
    )

    received = {}

    def fake_chartist(theme_key, candidate, price_snapshot=None):
        received["chartist_price_snapshot"] = price_snapshot
        return ChartistScore(
            trend_score=20,
            momentum_score=20,
            volatility_score=15,
            volume_score=15,
            total_score=70,
            signal="매수",
            trend_analysis="",
            momentum_analysis="",
            volatility_analysis="",
            volume_analysis="",
            short_term_opinion="매수",
            mid_term_opinion="중립",
            analysis_packet={"price_snapshot": price_snapshot},
        )

    orchestrator._evaluate_chartist_candidate = fake_chartist
    orchestrator._fetch_price_snapshot = lambda user_id, stock_code: {
        "stock_code": stock_code,
        "current_price": 11200,
        "snapshot_at": "2026-06-02T10:15:00+09:00",
        "source": "kis",
        "success": True,
    }
    monkeypatch.setattr(
        "src.agents.theme_orchestrator.run_agents_parallel",
        lambda tasks, max_workers=3: {name: fn(*args) for name, (fn, args) in tasks.items()},
    )

    class FakeRiskManager:
        def make_decision(self, stock_name, stock_code, scores, portfolio_context=None, investor_profile=None):
            received["risk_manager_scores"] = scores
            received["risk_manager_investor_profile"] = investor_profile
            return FinalDecision(
                stock_name=stock_name,
                stock_code=stock_code,
                total_score=70,
                action=InvestmentAction.BUY,
                confidence=75,
                risk_level=RiskLevel.LOW,
                risk_factors=[],
                position_size="10%",
                entry_strategy="분할",
                exit_strategy="분할",
                stop_loss="-5%",
                signal_alignment="일치",
                key_catalysts=[],
                contrarian_view="",
                summary="매수",
                detailed_reasoning="",
            )

    orchestrator.risk_manager = FakeRiskManager()

    result = orchestrator.evaluate_candidate(
        "AI",
        "ai",
        ThemeCandidate(stock_name="테스트", stock_code="000001", data_coverage="enough"),
        user_id="user-1",
    )

    assert received["chartist_price_snapshot"]["current_price"] == 11200
    assert received["risk_manager_investor_profile"] is None
    assert received["risk_manager_scores"].chartist_context["price_snapshot"]["current_price"] == 11200
    assert result["chartist"]["price_snapshot"]["current_price"] == 11200


def _write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )


def _price_rows(stock_code: str, close: int = 10000, days: int = 65):
    start = date(2026, 1, 1)
    return [
        {
            "stock_code": stock_code,
            "date": (start + timedelta(days=idx)).isoformat(),
            "close": close + idx,
            "volume": 200000,
            "trading_value": 2_000_000_000,
        }
        for idx in range(days)
    ]


def test_extract_candidates_applies_universe_filter_before_limit(tmp_path):
    theme_key = "ai"
    _write_jsonl(
        tmp_path / "raw" / "theme_targets" / f"{theme_key}.jsonl",
        [
            {"stock_code": "111111", "stock_name": "통과"},
            {"stock_code": "222222", "stock_name": "제외"},
        ],
    )
    _write_jsonl(
        tmp_path / "canonical_index" / theme_key / "corpus.jsonl",
        [
            {"text": "문서", "metadata": {"stock_code": "111111", "stock_name": "통과", "source_type": "news"}},
            {"text": "문서", "metadata": {"stock_code": "222222", "stock_name": "제외", "source_type": "news"}},
        ],
    )
    _write_jsonl(tmp_path / "market_data" / theme_key / "chart.jsonl", _price_rows("111111"))

    orchestrator = ThemeLeaderOrchestrator(
        data_dir=str(tmp_path),
        universe_filters={
            "enabled": True,
            "require_price_history": True,
            "require_recent_documents": True,
            "min_history_days": 60,
            "min_avg_trading_value_20d": 1_000_000_000,
        },
    )

    candidates = orchestrator.extract_candidates("AI", theme_key, candidate_limit=10)

    assert [candidate.stock_code for candidate in candidates] == ["111111"]
    report = orchestrator._last_candidate_filter_report
    assert report["enabled"] is True
    assert report["raw_candidate_count"] == 2
    assert report["passed_count"] == 1
    assert report["rejected_count"] == 1
    assert report["rejection_reasons"]["missing_price_history"] == 1


def test_extract_candidates_keeps_legacy_behavior_when_filter_disabled(tmp_path):
    theme_key = "ai"
    _write_jsonl(
        tmp_path / "raw" / "theme_targets" / f"{theme_key}.jsonl",
        [{"stock_code": "222222", "stock_name": "제외아님"}],
    )
    _write_jsonl(
        tmp_path / "canonical_index" / theme_key / "corpus.jsonl",
        [{"text": "문서", "metadata": {"stock_code": "222222", "stock_name": "제외아님", "source_type": "news"}}],
    )

    orchestrator = ThemeLeaderOrchestrator(
        data_dir=str(tmp_path),
        universe_filters={"enabled": False, "require_price_history": True},
    )

    candidates = orchestrator.extract_candidates("AI", theme_key, candidate_limit=10)

    assert [candidate.stock_code for candidate in candidates] == ["222222"]
    assert candidates[0].data_coverage == "weak"
    assert orchestrator._last_candidate_filter_report["status"] == "disabled"


def test_extract_candidates_excludes_candidates_without_documents_or_market_data(tmp_path):
    theme_key = "ai"
    _write_jsonl(
        tmp_path / "raw" / "theme_targets" / f"{theme_key}.jsonl",
        [{"stock_code": "333333", "stock_name": "데이터없음"}],
    )

    orchestrator = ThemeLeaderOrchestrator(
        data_dir=str(tmp_path),
        universe_filters={"enabled": False},
    )

    candidates = orchestrator.extract_candidates("AI", theme_key, candidate_limit=10)

    assert candidates == []


def test_extract_candidates_reports_data_coverage(tmp_path):
    theme_key = "ai"
    _write_jsonl(
        tmp_path / "raw" / "theme_targets" / f"{theme_key}.jsonl",
        [
            {"stock_code": "111111", "stock_name": "충분"},
            {"stock_code": "222222", "stock_name": "약함"},
        ],
    )
    _write_jsonl(
        tmp_path / "canonical_index" / theme_key / "corpus.jsonl",
        [
            {"text": "뉴스", "metadata": {"stock_code": "111111", "stock_name": "충분", "source_type": "news"}},
            {"text": "공시", "metadata": {"stock_code": "111111", "stock_name": "충분", "source_type": "dart"}},
            {"text": "뉴스", "metadata": {"stock_code": "222222", "stock_name": "약함", "source_type": "news"}},
        ],
    )
    _write_jsonl(tmp_path / "market_data" / theme_key / "chart.jsonl", _price_rows("111111"))

    orchestrator = ThemeLeaderOrchestrator(
        data_dir=str(tmp_path),
        universe_filters={"enabled": False},
    )

    candidates = orchestrator.extract_candidates("AI", theme_key, candidate_limit=10)
    coverage_by_code = {candidate.stock_code: candidate.data_coverage for candidate in candidates}

    assert coverage_by_code == {"111111": "enough", "222222": "weak"}
