from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

RUNS = "experiment_results/backtesting/ai_strategy_comparison/source_multi_agent_runs_2023_2024/runs"
CACHE = "experiment_results/backtesting/ai_strategy_comparison/llm_cache/ai.multi_agent.jsonl"
IDENTITY = {"prompt_version": "temporal_theme_leader_multi_agent_v3", "provider": "ollama",
            "model_name": "qwen3:14b", "thinking_model_name": "gpt-oss:20b"}


def candidate(code, score, realized):
    return {"stock_code": code, "stock_name": code, "deterministic_leader_score": score, "leader_score": score,
            "return_20d_pct": 10.0 + score / 10, "return_60d_pct": 20.0 + score / 10, "realized_return_pct": realized,
            "llm_score": 50, "volume_ratio_20d": 1.0, "volatility_20d": 0.5}


def cache_row(version, as_of, row, analyst, quant, chartist, risk):
    key = "|".join([version, "short", "ollama", "qwen3:14b", "gpt-oss:20b", "ai", as_of, row["stock_code"],
                    str(float(row["deterministic_leader_score"])), str(round(row["return_20d_pct"] / 100, 4)),
                    str(round(row["return_60d_pct"] / 100, 4))])
    scores = {"analyst": {"total_score": analyst}, "quant": {"total_score": quant},
              "chartist": {"total_score": chartist}, "risk_manager": {"raw_final_score": risk, "risk_score": 40}}
    return {"cache_key": key, "result": {"llm_score": 60, "llm_agent_scores": scores}}


def build_inputs(root: Path, *, cached_share="most"):
    periods = []
    rows = []
    for index, as_of in enumerate(("20240105", "20240112", "20240119")):
        candidates = [candidate(f"00000{i}", 60 + i * 5, realized=float(i - 2) * (index + 1)) for i in range(1, 6)]
        periods.append({"as_of_date": f"{as_of[:4]}-{as_of[4:6]}-{as_of[6:]}", "benchmark_return_pct": 0.5,
                        "selected_count": 2, "candidate_rankings": candidates})
        scored = candidates if index < 2 else candidates[:1]  # third period: too few joined rows
        if cached_share == "few":
            scored = candidates[:1]
        for i, row in enumerate(scored):
            rows.append(cache_row(IDENTITY["prompt_version"], as_of, row, 50 + i, 60 - i, 40 + 3 * i, 30 + 10 * i))
        # Same stock/date under another prompt version must never be joined.
        rows.append(cache_row("other_prompt_version", as_of, candidates[-1], 99, 99, 99, 99))
    run = {"task_id": "proof-ai-validation_2024-short_hybrid_05", "theme": "AI", "theme_key": "ai",
           "strategy": {"strategy_id": "short_hybrid_05", "horizon": "short"},
           "metadata": {"llm": {**IDENTITY, "cache_path": CACHE}},
           "execution": {"costs": {"round_trip_cost_bps": 0.0}}, "periods": periods}
    (root / RUNS).mkdir(parents=True)
    (root / RUNS / "proof-ai-validation_2024-short_hybrid_05.json").write_text(json.dumps(run), encoding="utf-8")
    (root / CACHE).parent.mkdir(parents=True, exist_ok=True)
    (root / CACHE).write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


@pytest.fixture()
def generator():
    import scripts.research.build_agent_architecture_validation as module
    return importlib.reload(module)  # module globals are reconfigured by main()


def test_generator_joins_exact_keys_skips_unscored_periods_and_derives_findings(tmp_path, generator):
    build_inputs(tmp_path)
    out = tmp_path / "out"
    assert generator.main(["--source-root", str(tmp_path), "--output-dir", str(out), "--min-agent-coverage", "0.5"]) == 0
    coverage = list(__import__("csv").DictReader((out / "agent-architecture-coverage.csv").open(encoding="utf-8")))[0]
    assert coverage["candidate_rows"] == "15" and coverage["candidate_rows_with_agent_scores"] == "11"
    assert coverage["periods_compared"] == "2" and coverage["periods_skipped_insufficient_agent_rows"] == "1"
    loo = list(__import__("csv").DictReader((out / "leave-one-out-agent-summary.csv").open(encoding="utf-8")))
    risk = next(row for row in loo if row["removed_agent"] == "RiskManager")
    assert (risk["current_variant"], risk["reduced_variant"]) == ("four_agent_raw_blend", "three_agent_no_risk_manager")
    assert all(row["current_variant"] == "three_agent_no_risk_manager" for row in loo if row["removed_agent"] != "RiskManager")
    report = (out / "AGENT_ABLATION_EVIDENCE_KO.md").read_text(encoding="utf-8")
    assert "## 계산된 결과" in report and "독립 효과가 아직 증명되지" not in report


def test_generator_fails_without_inputs_or_coverage(tmp_path, generator):
    assert generator.main(["--source-root", str(tmp_path), "--output-dir", str(tmp_path / "out")]) == 2
    build_inputs(tmp_path, cached_share="few")
    reloaded = importlib.reload(generator)
    assert reloaded.main(["--source-root", str(tmp_path), "--output-dir", str(tmp_path / "out2")]) == 3
