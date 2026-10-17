"""Only fixtures: FW001 post-hoc gates, sealed-fill evaluation and controls."""
from datetime import datetime, timedelta
import json
import csv
import gzip
import os
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from backtesting.experiments import fw001 as evaluation
from src.forward import fw001 as fw
from src.ingestion.dart_listing import file_hash
from src.ingestion.storage import write_rows
from test_forward_fw001 import data, entry_data, DAY, synthetic_future_calendar, publication_clock


def monthly(values, name="f_both", cost15=None, cost2=-100):
    key = "difference" if name == "f_both" else "excess"
    return [{"month": str(month), "costs": {"1.0": {key: value},
             "1.5": {key: value if cost15 is None else cost15}, "2.0": {key: cost2}}}
            for month, value in zip(fw.MONTHS, values)]


@pytest.mark.parametrize("baseline,value,cost15,valid,expected", [
    (0.004, 0.002, 0.001, 10, "pass"), (0.004, 0.001999, 0.001, 12, "fail"),
    (0.004, 0.002, 0, 12, "fail"), (0.004, 0.002, 0.001, 9, "insufficient"),
    (0, 0.002, 0.001, 12, "not_applicable"), (-1, 0.002, 0.001, 12, "not_applicable"),
    (None, 0.002, 0.001, 12, "not_applicable")])
def test_exact_half_effect_gate_without_t_control_or_cost2_gate(baseline, value, cost15, valid, expected):
    result = evaluation.judge_rule("f_both", monthly([value] * 12, cost15=cost15), baseline, valid_decisions=valid)
    assert result["verdict"] == expected
    assert result["newey_west"]["t_stat"] is None  # Constant observations still pass the economic gate.
    assert not result["cost_2_used_for_judgement"] and not result["controls_used_for_judgement"]


def test_av_mean_includes_no_trade_months_and_requires_all_twelve():
    rows = monthly([0.004] * 11 + [-0.004], "av_all")
    result = evaluation.judge_rule("av_all", rows, 0.004, valid_decisions=11)
    assert result["mean_by_cost_multiplier"]["1.0"] == pytest.approx(0.003333333333333333)
    assert result["verdict"] == "pass"
    assert evaluation.judge_rule("av_all", rows[:-1], 0.004, valid_decisions=11)["verdict"] == "insufficient"
    assert evaluation.judge_rule("av_all", rows, 0.004, valid_decisions=11, continuous=False)["verdict"] == "insufficient"
    assert result["newey_west"]["lag_pair_counts"] == {"1": 11, "2": 10, "3": 9}


def test_f_denominator_is_paired_bundles_with_separate_valid_decision_gate():
    result = evaluation.judge_rule("f_both", monthly([0.003] * 8), 0.004, valid_decisions=10)
    assert result["monthly_observations"] == 8 and result["verdict"] == "pass"


def test_delisting_sensitive_cannot_pass():
    result = evaluation.apply_sensitivity({"verdict": "pass"}, {"verdict": "fail"})
    assert result["verdict"] == "delisting_sensitive" and result["delisting_sensitive"]


def test_sealed_values_and_pre_freeze_final_guard(data, monkeypatch):
    values, checksum = evaluation.comparison_values()
    assert values["f_both"]["value"] == 0.0025855947612501116
    assert values["av_all"]["value"] == 0.0031823698965281247 and len(checksum) == 64
    monkeypatch.setattr(fw, "now", lambda: datetime(2027, 11, 14, 23, 59, tzinfo=fw.KST))
    with pytest.raises(ValueError, match="data freeze"):
        evaluation.read_records(data, final=True)
    with pytest.raises(ValueError, match="2027-11-15"):
        fw.seal_evaluation_inputs(data, execute=True)


def test_final_publication_is_immutable_and_registry_is_once_per_rule(tmp_path, monkeypatch):
    monkeypatch.setattr(fw, "now", lambda: fw.FREEZE_AT)
    rows = monthly([0.003] * 12)
    rules = {name: evaluation.apply_sensitivity(
        evaluation.judge_rule(name, rows if name == "f_both" else monthly([0.003] * 12, name),
                              0.004, valid_decisions=12), {"verdict": "pass"})
        for name in ("f_both", "av_all")}
    result = {"stage": "final", "rules": rules, "missing_months": []}
    registry = tmp_path / evaluation.experiment_registry.REGISTRY_PATH
    monkeypatch.setattr(evaluation.experiment_registry, "verify_preregistration",
                        lambda *a: SimpleNamespace(commit_hash="a" * 40))
    calls = []
    registry.parent.mkdir(parents=True)
    with registry.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=evaluation.experiment_registry.REGISTRY_COLUMNS)
        writer.writeheader()
        writer.writerow({"experiment_id": "LH002_legacy", "variant": "legacy",
                         "metrics_json": json.dumps({"legacy_large_field": "x" * 200_000})})
    csv.field_size_limit(131072)  # Reproduce the historical LH002 default-limit failure.
    def append(experiment_id, variant, metrics, verdict, **kwargs):
        calls.append(variant)
        first = not registry.exists()
        with registry.open("a", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=evaluation.experiment_registry.REGISTRY_COLUMNS)
            if first:
                writer.writeheader()
            writer.writerow({"experiment_id": experiment_id, "prereg_commit": "a" * 40,
                             "variant": variant, "metrics_json": json.dumps(metrics)})
    monkeypatch.setattr(evaluation.experiment_registry, "record_trial", append)
    output = tmp_path / "published.json"
    evaluation.publish(result, output, repo_root=tmp_path)
    original = output.read_bytes()
    evaluation.publish(result, output, repo_root=tmp_path)
    assert calls == ["f_both", "av_all"] and output.read_bytes() == original
    with pytest.raises(ValueError, match="immutable"):
        evaluation.publish({**result, "missing_months": ["2026-10"]}, output, repo_root=tmp_path)


@pytest.mark.parametrize("rejected_publication", [None, "2026-11-02T08:59:00+09:00",
                                               "2026-11-02T08:58:59.999999999+09:00"])
def test_full_fixture_freezes_prices_carries_sealed_fills_and_runs_reporting_controls(
        data, monkeypatch, synthetic_future_calendar, rejected_publication):
    # Fees here are injected fixture inputs, not a proposed statutory tax rate.
    monkeypatch.setattr(fw.cost_model, "one_way_cost", lambda *a, **k: 0.001 * k.get("multiplier", 1.0))
    monkeypatch.setattr(evaluation, "original_hf_comparison", lambda *a, **k: {
        "status": "fixture_not_supplied", "used_for_judgement": False})
    monkeypatch.setattr("src.config.settings.load_project_env", lambda *a, **k: pytest.fail("env read"))
    entry_data(data, DAY, end="2027-11-12")
    sessions = fw.trading_calendar.session_calendar(DAY, "2027-11-12")
    for day in pd.date_range("2026-10-30", "2027-09-30"):
        write_rows(data / f"disclosures/dart_full/list/{day:%Y}/{day:%Y%m%d}.jsonl", [])
    listing_state = data / "disclosures/dart_full/_state.json"
    listing_state.write_text(json.dumps({"completed_listing_days": sorted(
        p.stem for p in (data / "disclosures/dart_full/list").glob("*/*.jsonl"))}))
    for month in fw.MONTHS:
        day = sessions[sessions.to_period("M") == month][-1]
        stamp = datetime.combine(day.date(), datetime.min.time(), fw.KST).replace(hour=17)
        monkeypatch.setattr(fw, "now", lambda stamp=stamp: stamp)
        if month == fw.MONTHS[0] and rejected_publication:
            link = os.link
            def crash(source, target):
                link(source, target)
                target = Path(target)
                if target.name == "complete.json":
                    published = pd.Timestamp(rejected_publication).value
                    os.utime(target.parent, ns=(published, published))
                    raise KeyboardInterrupt("crash after rejected publication")
            with monkeypatch.context() as patch:
                patch.setattr(os, "link", crash)
                with pytest.raises(KeyboardInterrupt):
                    fw.decide(day, data, execute=True)
            decision_path = fw.path_for(data, "decisions", str(month))
            marker = fw.marker_path(data, "decisions", str(month))
            evidence = (decision_path.read_bytes(), marker.read_bytes(), marker.parent.stat().st_mtime_ns)
            decision = fw.load_json(decision_path)
            # No restart path may try to re-complete the rejected candidate.
            complete = fw.complete
            def refuse_recompletion(root, kind, record, **kwargs):
                assert kind != "decisions" or record["month"] != "2026-10"
                return complete(root, kind, record, **kwargs)
            monkeypatch.setattr(fw, "complete", refuse_recompletion)
            # Retry while the precision interval still straddles the cutoff.
            monkeypatch.setattr(fw, "now", lambda: pd.Timestamp(rejected_publication).to_pydatetime(warn=False))
            fw.daily(data, execute=True)
            assert fw.decide(day, data, execute=True)["label"] == "reconstructed"
        else:
            # This annual fixture already contains future entry prices. Resume
            # on entry morning, while the current decision is still eligible.
            entry_morning = datetime.fromisoformat(fw.plan(day)["deadline"]) - timedelta(minutes=19)
            monkeypatch.setattr(fw, "now", lambda stamp=entry_morning: stamp)
            report = fw.daily(data, execute=True)
            assert any(row["month"] == str(month) and row.get("label") == "forward"
                       for row in report["decisions"])
            decision = fw.load_json(fw.path_for(data, "decisions", str(month)))
        entry_stamp = datetime.combine(pd.Timestamp(decision["trade_date"]).date(), datetime.min.time(), fw.KST)
        monkeypatch.setattr(fw, "now", lambda stamp=entry_stamp + timedelta(days=1, hours=8): stamp)
        report = fw.daily(data, execute=True)
        assert report["missing"] == (["2026-10"] if rejected_publication else [])
        if rejected_publication:
            assert (decision_path.read_bytes(), marker.read_bytes(), marker.parent.stat().st_mtime_ns) == evidence
            missing = fw.load_json(fw.path_for(data, "executions", "2026-10", 1.0))
            assert missing["label"] == "missing" and missing["av_all"]["no_trade"]
            assert missing["av_all"]["state_after"] == missing["av_all"]["state_before"]
            assert missing["f_both"]["slots"] == {}
    monkeypatch.setattr(fw, "now", lambda: datetime(2027, 11, 13, 8, tzinfo=fw.KST))
    fw.daily(data, execute=True)
    monkeypatch.setattr(fw, "now", lambda: datetime(2027, 11, 15, tzinfo=fw.KST))
    freeze = fw.seal_evaluation_inputs(data, execute=True)
    assert freeze["freeze_at"] == "2027-11-15T00:00:00+09:00"
    result = evaluation.evaluate(data, final=True)
    assert result["stage"] == "final"
    assert result["missing_months"] == (["2026-10"] if rejected_publication else [])
    assert len(result["rules"]["f_both"]["per_month"]) == (11 if rejected_publication else 12)
    assert result["rules"]["f_both"]["valid_decisions"] == (11 if rejected_publication else 12)
    assert len(result["rules"]["av_all"]["per_month"]) == 12
    assert result["rules"]["av_all"]["continuous_accounting"] == (not rejected_publication)
    if rejected_publication:
        assert result["rules"]["av_all"]["verdict"] == "insufficient"
    if not rejected_publication:
        assert len(result["rules"]["av_all"]["random_control"]["path_means"]) == 200
        assert result["rules"]["f_both"]["random_control"]["used_for_judgement"] is False
    # A correction already present on the server at the freeze is a valuation
    # input, while quantities/cash/charges remain the original sealed fills.
    records, _ = evaluation.read_records(data, final=True)
    if rejected_publication:
        assert "202610" not in records["decisions"] and "202611" in records["decisions"]
        assert marker.relative_to(data).as_posix() in freeze["input_hashes"]
        assert decision_path.relative_to(data).as_posix() in freeze["input_hashes"]
    corrected = fw.load_json(data / fw.RECORD_DIR / "evaluation_freeze.json")
    assert corrected["selected_prices"]
    # A later PC entry correction must not change primary fills, quantities or returns.
    source = data / "market/krx_daily/2026/20261102.jsonl"
    original = source.read_text()
    source.write_text(original.replace('"open": 10000', '"open": 50000'))
    again = evaluation.evaluate(data, pc_dir=data, final=True, controls=False)
    assert again["rules"]["av_all"]["per_month"] == result["rules"]["av_all"]["per_month"]
    assert again["rules"]["f_both"]["per_month"] == result["rules"]["f_both"]["per_month"]
    exe_path = fw.path_for(data, "executions", "2026-11", 1.0)
    exe_path.write_text(exe_path.read_text().replace('"cash": 0.0', '"cash": 0.2'))
    assert file_hash(exe_path) != freeze["input_hashes"][exe_path.relative_to(data).as_posix()]
    with pytest.raises(ValueError, match="frozen input changed"):
        evaluation.read_records(data, final=True)


def test_final_freeze_uses_latest_correction_and_keeps_original_execution_input(data, monkeypatch):
    monkeypatch.setattr(fw, "now", lambda: datetime(2026, 10, 31, 8, tzinfo=fw.KST))
    decision = fw.decide(DAY, data, execute=True)
    entry_data(data, DAY)
    monkeypatch.setattr(fw, "now", lambda: datetime(2026, 11, 3, 8, tzinfo=fw.KST))
    original = fw.execution(decision, data, multiplier=1.0, execute=True)
    source = data / "market/krx_daily/2026/20261102.jsonl"
    text = source.read_text()
    source.write_text(text.replace('"open": 10000', '"open": 12000'))
    monkeypatch.setattr(fw, "now", lambda: fw.FREEZE_AT + timedelta(seconds=2))
    freeze = fw.seal_evaluation_inputs(data, execute=True)
    chosen = freeze["selected_prices"]["20261102"]
    assert chosen["sha256"] == file_hash(source)
    original_input = fw.load_json(fw.path_for(data, "execution_prices", "20261102"))
    assert original_input["sha256"] != chosen["sha256"]
    assert fw.load_json(fw.path_for(data, "executions", "2026-10", 1.0)) == original
    records, _ = evaluation.read_records(data, final=True)
    prices, days = evaluation.price_frame(data, records)
    assert prices.loc[(pd.Timestamp("2026-11-02"), "000010"), "open"] == 12000


def test_freeze_refuses_a_server_price_published_after_the_registered_cutoff(data, monkeypatch):
    fw.freeze_price(data, DAY, execute=True)
    source = data / "market/krx_daily/2026/20261030.jsonl"
    real_stat = Path.stat
    stat = real_stat(source)
    late = int(fw.FREEZE_AT.timestamp()) * 1_000_000_000 + 1
    fake = SimpleNamespace(st_mtime_ns=late, st_ctime_ns=late, st_ino=stat.st_ino,
                           st_size=stat.st_size, st_mode=stat.st_mode)
    monkeypatch.setattr(Path, "stat", lambda path, *a, **k: fake if path == source else real_stat(path, *a, **k))
    monkeypatch.setattr(fw, "now", lambda: fw.FREEZE_AT + timedelta(seconds=1))
    with pytest.raises(ValueError, match="published after cutoff"):
        fw.seal_evaluation_inputs(data, execute=True)
    assert not (data / fw.RECORD_DIR / "evaluation_freeze.json").exists()


def test_original_HF_auxiliary_value_is_reporting_only(tmp_path):
    path = tmp_path / "research/experiments/HF001_hegemony_filters/results/original.json.gz"
    path.parent.mkdir(parents=True)
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        json.dump({"variants": {"f_both": {"design": {"primary": {"mean": -0.2}}}}}, handle)
    result = evaluation.original_hf_comparison({"f_both": {"secondary_original_hf001": "results/original.json.gz"}},
                                               repo_root=tmp_path)
    assert result["value"] == -0.2 and result["sha256"] == file_hash(path)
    assert result["used_for_judgement"] is False


def test_invalid_registry_metrics_are_rejected_before_any_result_publication(tmp_path, monkeypatch):
    monkeypatch.setattr(fw, "now", lambda: fw.FREEZE_AT)
    monkeypatch.setattr(evaluation.experiment_registry, "verify_preregistration",
                        lambda *a: SimpleNamespace(commit_hash="a" * 40))
    rule = evaluation.apply_sensitivity(
        evaluation.judge_rule("f_both", monthly([0.003] * 12), 0.004, valid_decisions=12), {"verdict": "pass"})
    bad = {**rule, "mean_by_cost_multiplier": {"1.0": float("nan")}}
    result = {"stage": "final", "rules": {"f_both": rule, "av_all": bad}, "missing_months": []}
    with pytest.raises(ValueError, match="finite JSON"):
        evaluation.publish(result, tmp_path / "output.json", repo_root=tmp_path)
    assert not (tmp_path / fw.RESEARCH_DIR / "results/final.json").exists()
    assert not (tmp_path / "output.json").exists()
    assert not (tmp_path / evaluation.experiment_registry.REGISTRY_PATH).exists()


def test_reader_requires_completion_proof_and_checks_actual_record_bytes(data, monkeypatch):
    monkeypatch.setattr(fw, "now", lambda: datetime(2026, 10, 31, 8, tzinfo=fw.KST))
    fw.decide(DAY, data, execute=True)
    path = fw.path_for(data, "decisions", "2026-10")
    marker = fw.marker_path(data, "decisions", "2026-10")
    saved = marker.read_bytes()
    marker.unlink()
    records, _ = evaluation.read_records(data)
    assert records["decisions"] == {}
    marker.write_bytes(saved)
    path.write_bytes(path.read_bytes() + b" ")  # Same JSON value, different actual bytes.
    with pytest.raises(ValueError, match="invalid completion proof"):
        evaluation.read_records(data)


@pytest.mark.parametrize("field,value", [("record_digest", "0" * 64),
                                         ("deadline", "2026-11-02T09:00:00+09:00"),
                                         ("publication_precision_ns", 0)])
def test_corrupt_late_marker_fails_recording_and_evaluation(data, monkeypatch, field, value):
    monkeypatch.setattr(fw, "now", lambda: datetime(2026, 10, 31, 8, tzinfo=fw.KST))
    fw.decide(DAY, data, execute=True)
    marker = fw.marker_path(data, "decisions", "2026-10")
    proof = fw.load_json(marker)
    proof[field] = value
    marker.write_text(fw.encoded(proof))
    published = pd.Timestamp("2026-11-02T08:59:00+09:00").value
    os.utime(marker.parent, ns=(published, published))
    monkeypatch.setattr(fw, "now", lambda: datetime(2026, 11, 3, 8, tzinfo=fw.KST))
    with pytest.raises(ValueError, match="completion"):
        fw.daily(data, execute=True)
    with pytest.raises(ValueError, match="completion"):
        evaluation.read_records(data)
    assert not fw.path_for(data, "missing", "2026-10").exists()
    monkeypatch.setattr(fw, "now", lambda: fw.FREEZE_AT)
    if field == "publication_precision_ns":
        with pytest.raises(ValueError, match="completion"):
            fw.seal_evaluation_inputs(data, execute=True)
    else:
        fw.seal_evaluation_inputs(data, execute=True)
        with pytest.raises(ValueError, match="completion"):
            evaluation.evaluate(data, final=True)


def test_november_retrading_changes_loss_detection_without_extending_october_valuation(synthetic_future_calendar):
    days = fw.trading_calendar.session_calendar("2027-09-01", "2027-11-12")
    prices = []
    for day in days:
        for code in ("000010", "000020"):
            resumed = code == "000010" and day >= pd.Timestamp("2027-11-01")
            prices.append({"trade_date": day, "stock_code": code, "open": 200 if resumed else 100,
                "close": 200 if resumed else 100, "base_price": 100,
                "volume": 10 if code == "000020" or resumed else 0, "market": "KOSPI",
                "trading_value": 2e9, "calendar_status": "verified",
                "ret_1d": 1.0 if resumed and day == pd.Timestamp("2027-11-01") else 0.0})
    prices = pd.DataFrame(prices).set_index(["trade_date", "stock_code"]).sort_index()
    chain = {code: {"adjusted_close": 1.0, "raw_close": 100.0, "adv_history": [2e9] * 20,
                    "last_trade": "2027-08-31"} for code in ("000010", "000020")}
    records = {"decisions": {"seed": {"chain": chain, "chain_date": "2027-08-31"}}, "missing": {},
               "executions": {"s": {"trade_date": "2027-09-01"}, "o": {"trade_date": "2027-10-01"}}}
    truncated_days = days[days <= pd.Timestamp("2027-10-29")]
    truncated = evaluation.valuation_book(records, prices.loc[prices.index.get_level_values(0).isin(truncated_days)], truncated_days)
    extended = evaluation.valuation_book(records, prices, days)
    assert truncated.zero_from["000010"] == pd.Timestamp("2027-09-01")
    assert "000010" not in extended.zero_from
    assert truncated.marks("2027-10-29", "close") == extended.marks("2027-10-29", "close")
    assert extended.marks("2027-10-29", "close", loss=True)["000010"] == 1.0
    assert extended.marks("2027-11-12", "close")["000010"] == 2.0
