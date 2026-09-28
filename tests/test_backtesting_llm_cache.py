from __future__ import annotations

import json
from pathlib import Path

import pytest

pd = pytest.importorskip("pandas")

from backtesting.llm_signal import LLMCacheMissError, TemporalMultiAgentStockScorer  # noqa: E402


ROW = {"stock_name": "Alpha", "stock_code": "000001", "leader_score": 70.0, "return_5d": 0.01, "return_20d": 0.05,
       "return_60d": 0.10, "trend_150d": 0.08, "volatility_20d": 0.4, "volume_ratio_20d": 1.3,
       "avg_trading_value_20d": 5_000_000_000.0, "doc_counts": {"news": 2}}


def scorer(tmp_path, monkeypatch, *, cache_only=False):
    instance = TemporalMultiAgentStockScorer.__new__(TemporalMultiAgentStockScorer)
    instance.data_dir, instance.theme, instance.theme_key = tmp_path, "AI", "ai"
    instance.context_docs, instance.horizon = 5, "short"
    instance.provider, instance.model_name, instance.thinking_model_name = "ollama", "m", "t"
    instance.agent_score_profile, instance.cache_only = "current_hybrid_4agent", cache_only
    instance.cache_path, instance.cache = tmp_path / "cache.jsonl", {}
    monkeypatch.setattr(instance, "_context_for_row", lambda **_: "")
    return instance


def use_agents(monkeypatch, instance, *, analyst_fails):
    analyst = (lambda *a: instance._fallback_analyst(ROW)) if analyst_fails else (lambda *a: {
        "theme_fit_score": 80, "moat_score": 70, "growth_score": 75, "catalyst_score": 70, "confidence": 70,
        "summary": "AI 매출 증가", "catalysts": ["수주"], "risks": ["밸류에이션"]})
    monkeypatch.setattr(instance, "_evaluate_analyst", analyst)
    monkeypatch.setattr(instance, "_evaluate_quant", lambda *a: {
        "valuation_score": 60, "profitability_score": 60, "growth_score": 60, "stability_score": 60,
        "confidence": 60, "summary": "재무 양호", "risks": []})
    monkeypatch.setattr(instance, "_evaluate_risk_manager", lambda *a: {
        "final_score": 65, "confidence": 60, "risk_score": 40, "action": "BUY", "summary": "진입 가능",
        "catalysts": [], "risks": []})


def test_multi_agent_fallback_score_is_not_cached(tmp_path, monkeypatch):
    instance = scorer(tmp_path, monkeypatch)
    use_agents(monkeypatch, instance, analyst_fails=True)
    result = instance.score(as_of_ymd="20250303", row=ROW)
    assert result["llm_fallback_used"] is True
    assert instance.cache == {} and not instance.cache_path.exists()

    use_agents(monkeypatch, instance, analyst_fails=False)
    result = instance.score(as_of_ymd="20250303", row=ROW)
    assert result["llm_fallback_used"] is False
    assert len(instance.cache) == 1 and instance.cache_path.exists()
    assert instance.score(as_of_ymd="20250303", row=ROW)["cache_hit"] is True


def test_prompt_changing_settings_get_their_own_cache_keys(tmp_path, monkeypatch):
    instance = scorer(tmp_path, monkeypatch)
    monkeypatch.delenv("AGENT_PURE_FEATURES", raising=False)
    monkeypatch.delenv("AGENT_FREE_RISK_MANAGER", raising=False)
    default_key = instance._cache_key(as_of_ymd="20250303", row=ROW)
    assert default_key.endswith(str(round(ROW["return_60d"], 4)))  # original layout preserved
    monkeypatch.setenv("AGENT_PURE_FEATURES", "1")
    pure_key = instance._cache_key(as_of_ymd="20250303", row=ROW)
    monkeypatch.setenv("AGENT_FREE_RISK_MANAGER", "1")
    both_key = instance._cache_key(as_of_ymd="20250303", row=ROW)
    instance.context_docs = 8
    assert len({default_key, pure_key, both_key, instance._cache_key(as_of_ymd="20250303", row=ROW)}) == 4


def test_legacy_cache_keys_are_used_only_when_explicitly_accepted(tmp_path, monkeypatch):
    instance = scorer(tmp_path, monkeypatch, cache_only=True)
    legacy_key = instance._cache_key(as_of_ymd="20250303", row=ROW, legacy=True)
    assert legacy_key.startswith("temporal_theme_leader_multi_agent_v3|")
    instance.cache[legacy_key] = {"llm_score": 77, "llm_agent_scores": {}, "llm_horizon": "short"}
    monkeypatch.setenv("AGENT_PURE_FEATURES", "1")
    monkeypatch.delenv("AGENT_CACHE_LEGACY_KEYS", raising=False)
    with pytest.raises(LLMCacheMissError):
        instance.score(as_of_ymd="20250303", row=ROW)
    monkeypatch.setenv("AGENT_CACHE_LEGACY_KEYS", "1")
    assert instance.score(as_of_ymd="20250303", row=ROW)["cache_hit"] is True


def _write_jsonl(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def test_cache_only_miss_fails_the_backtest_instead_of_using_the_rule_score(tmp_path, monkeypatch):
    from backtesting.leader_backtest import run_leader_backtest

    monkeypatch.delenv("AGENT_FAIL_ON_LLM_ERROR", raising=False)
    _write_jsonl(tmp_path / "raw/theme_targets/ai.jsonl",
                 [{"stock_name": name, "stock_code": code} for name, code in (("Alpha", "000001"), ("Beta", "000002"))])
    dates = pd.bdate_range("2025-01-01", periods=80)
    _write_jsonl(tmp_path / "market_data/ai/chart.jsonl", [
        {"source_type": "chart", "stock_name": name, "stock_code": code, "timestamp": day.strftime("%Y-%m-%dT00:00:00"),
         "open": 100 + i * slope - 1, "high": 100 + i * slope + 2, "low": 100 + i * slope - 2,
         "close": 100 + i * slope, "volume": 1000 + i}
        for i, day in enumerate(dates) for name, code, slope in (("Alpha", "000001", 2.0), ("Beta", "000002", 0.4))])
    _write_jsonl(tmp_path / "canonical_index/ai/corpus.jsonl", [])

    class CacheOnlyScorer:
        cache_only = True

        def metadata(self):
            return {"provider": "ollama", "model_name": "unit-test", "cache_only": True}

        def score(self, *, as_of_ymd, row):
            raise LLMCacheMissError(f"miss {as_of_ymd} {row['stock_code']}")

    with pytest.raises(LLMCacheMissError):
        run_leader_backtest(data_dir=tmp_path, theme="AI", theme_key="ai", from_date="20250228", to_date="20250331",
                            top_n=1, hold_days=5, min_history_days=20, llm_rerank_top_k=2, llm_weight=1.0,
                            llm_scorer=CacheOnlyScorer(), output_dir=tmp_path / "results", task_id="bt-cache-only")
