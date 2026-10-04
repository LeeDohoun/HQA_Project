from __future__ import annotations

import json

import pytest

from backtesting import agent_replay as replay


def _cache_row(horizon: str, as_of: str, code: str, *, catalyst: int, risk: int, llm_score: int) -> dict:
    key = "|".join(["temporal_theme_leader_multi_agent_v3", horizon, "ollama", "qwen3:14b", "gpt-oss:20b",
                    "ai", as_of, code, "73.0", "0.159", "0.3596"])
    return {"cache_key": key, "result": {
        "llm_score": llm_score, "llm_horizon": horizon,
        "llm_agent_scores": {"analyst": {"catalyst_score": catalyst, "risk_score": risk},
                             "chartist": {"total_score": 90}},
    }}


@pytest.fixture
def index(tmp_path):
    path = tmp_path / "cache.jsonl"
    rows = [_cache_row("short", "20240105", "200710", catalyst=80, risk=30, llm_score=61),
            _cache_row("short", "20240105", "000660", catalyst=40, risk=70, llm_score=44),
            {"cache_key": "malformed", "result": {}}]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    return replay.load_cache_index(path)


ROW = {"stock_code": "200710", "leader_score": 73.0, "return_20d": 0.159, "return_60d": 0.3596}


def _scorer(index, profile, **kwargs):
    return replay.CachedReplayScorer(index=index, theme_key="ai", horizon="short", profile=profile, **kwargs)


def test_index_ignores_model_names_and_malformed_rows(index):
    assert set(index) == {("short", "ai", "20240105", "200710", "73.0", "0.159", "0.3596"),
                          ("short", "ai", "20240105", "000660", "73.0", "0.159", "0.3596")}


def test_current_profile_returns_cached_result_unchanged(index):
    result = _scorer(index, "current_4agent").score(as_of_ymd="20240105", row=ROW)
    assert result["llm_score"] == 61
    assert "llm_agent_scores" in result


def test_analyst_text_uses_catalyst_and_inverted_risk_without_agent_scores(index):
    result = _scorer(index, "analyst_text").score(as_of_ymd="20240105", row=ROW)
    assert result["llm_score"] == pytest.approx(0.5 * 80 + 0.5 * (100 - 30))
    assert "llm_agent_scores" not in result  # keeps the short-horizon Chartist floor out


def test_random_control_is_seeded_and_drawn_from_analyst_distribution(index):
    first = _scorer(index, "random_control", seed=3).score(as_of_ymd="20240105", row=ROW)["llm_score"]
    again = _scorer(index, "random_control", seed=3).score(as_of_ymd="20240105", row=ROW)["llm_score"]
    assert first == again
    assert first in {75.0, 35.0}


def test_cache_miss_raises_and_is_counted(index):
    scorer = _scorer(index, "analyst_text")
    with pytest.raises(KeyError):
        scorer.score(as_of_ymd="20240105", row={**ROW, "leader_score": 72.0})
    assert (scorer.hits, scorer.misses) == (0, 1)


def test_unknown_profile_is_rejected(index):
    with pytest.raises(ValueError):
        _scorer(index, "risk_manager")


def test_summary_compares_against_rule_baseline_and_controls():
    def row(variant, profile, excess, period="p1"):
        return {"suite": "s", "theme_key": "ai", "period": period, "horizon": "short", "variant": variant,
                "profile": profile, "excess_return_pct": excess}

    rows = [row("deterministic", "deterministic", 10.0), row("deterministic", "deterministic", 0.0, "p2"),
            row("analyst_text_w20", "analyst_text", 14.0), row("analyst_text_w20", "analyst_text", 2.0, "p2"),
            row("random_control_w20_s0", "random_control", 9.0), row("random_control_w20_s0", "random_control", 1.0, "p2"),
            row("random_control_w20_s1", "random_control", 20.0), row("random_control_w20_s1", "random_control", 0.0, "p2")]
    [summary] = replay.summarize(rows)
    assert summary["variant"] == "analyst_text_w20"
    assert summary["mean_delta_vs_rule_pp"] == 3.0
    assert summary["cells_better_than_rule"] == 2
    assert summary["control_runs"] == 2
    assert summary["share_of_controls_at_or_above"] == 0.5
