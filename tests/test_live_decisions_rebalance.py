from __future__ import annotations

import dataclasses
import hashlib
import json
import math
import os

import pytest

from backtesting import cost_model
from src.live import decisions, rebalance
from src.forward import fw001
from test_live_fixtures import decision_record, set_sealed_at, write_decision, write_seal


def _decision(targets, *, trade_date="2026-11-02", weights=None):
    targets = tuple(sorted(targets))
    return decisions.Decision(rule="av_all", month="2026-10", decision_date="2026-10-30", trade_date=trade_date,
                              exit_date=None, targets=targets,
                              weights=weights or {c: 1 / len(targets) for c in targets},
                              record_hash="h" * 64, label="forward")


# ---------------------------------------------------------------- decisions


def test_forward_and_reconstructed_records_and_hash(tmp_path):
    record = decision_record(f_both=["000020", "000010"], av_all=["000030"])
    write_decision(tmp_path, record)
    november = decision_record(f_both=["000040"], label="reconstructed", month="2026-11",
                               decision_date="2026-11-30", trade_date="2026-12-01", exit_date="2026-12-28")
    write_decision(tmp_path, november)
    directory = tmp_path / decisions.DECISION_DIR
    (directory / ".fw001-tmp.json").write_text("{", encoding="utf-8")
    (directory / "reruns").mkdir()
    (directory / "reruns" / "abc.json").write_text("{", encoding="utf-8")

    listed = decisions.list_decisions(tmp_path, "f_both")
    assert [(d.month, d.label) for d in listed] == [("2026-10", "forward"), ("2026-11", "reconstructed")]
    first = listed[0]
    assert first.targets == ("000010", "000020") and first.weights == {"000010": 0.5, "000020": 0.5}
    assert (first.trade_date, first.exit_date) == ("2026-11-02", "2026-11-27")
    canonical = json.dumps(record, sort_keys=True, ensure_ascii=False, allow_nan=False, indent=2) + "\n"  # FW001 digest()
    assert first.record_hash == hashlib.sha256(canonical.encode()).hexdigest()
    assert first.adv20 == {"000010": 5e9, "000020": 5e9}
    assert decisions.latest_decision(tmp_path, "f_both", "2026-12-15") == first  # reconstructed never trades
    assert decisions.latest_decision(tmp_path, "f_both", "2026-12-15", forward_only=False).month == "2026-11"
    assert decisions.latest_decision(tmp_path, "f_both", "2026-10-29") is None
    continuous = decisions.list_decisions(tmp_path, "av_all", execution="continuous")[0]
    assert continuous.exit_date is None and continuous.targets == ("000030",)


@pytest.mark.parametrize("mutate, message", [
    (lambda r: r["rules"]["f_both"]["weights"].update({"000010": 0.9}), "sum to 1"),
    (lambda r: r["rules"]["f_both"]["targets"].append("000099"), "differ"),
    (lambda r: r.update(label="weird"), "unknown label"),
    (lambda r: r.update(record_type="rerun"), "not an FW001 decision"),
    (lambda r: r["rules"].pop("f_both"), "rules.f_both"),
    (lambda r: r.update(trade_date="2026-10-29"), "dates"),
])
def test_invalid_decisions_raise(tmp_path, mutate, message):
    record = decision_record(f_both=["000010", "000020"])
    mutate(record)
    write_decision(tmp_path, record)
    with pytest.raises(decisions.DecisionError, match=message):
        decisions.list_decisions(tmp_path, "f_both")


def test_file_name_must_match_month(tmp_path):
    path = write_decision(tmp_path, decision_record(f_both=["000010"]))
    path.rename(path.with_name("202611.json"))
    with pytest.raises(decisions.DecisionError, match="file name"):
        decisions.list_decisions(tmp_path, "f_both")


# ---------------------------------------------------------------- basket_20


def _buy_rate(price=10000, adv=5e9, version="v1", market="KOSPI", day="20261102"):
    return cost_model.one_way_cost(price, market, day, adv, "buy", model_version=version)


def test_basket_entry_sizes_floor_and_skips():
    d = _decision(["000010", "000020", "000030", "000040", "000050"])
    ref = {"000010": 10000.0, "000020": 10000.0, "000030": 30_000_000.0, "000050": 10000.0}
    markets = {c: "KOSPI" for c in d.targets}
    adv = {"000010": 5e9, "000020": 1e8, "000030": 5e9, "000040": 5e9}
    plan = rebalance.plan_basket_entry("hf001_f_both", d, cash=1e8, ref_prices=ref, markets=markets, adv20=adv,
                                       model_version="v1")
    rate = _buy_rate()
    expected = math.floor(1e8 * 0.2 * (1 - rate) / 10000)
    assert [(o.seq, o.stock_code, o.side, o.quantity) for o in plan.orders] == [
        (1, "000010", "BUY", expected), (2, "000020", "BUY", math.floor(1e8 * 0.2 * (1 - _buy_rate(adv=1e8)) / 1e4))]
    assert {s["stock_code"]: s["reason"] for s in plan.skipped} == {
        "000030": "zero_quantity", "000040": "missing_ref_price", "000050": "missing_adv20"}
    first, second = plan.orders
    assert not first.adv_warning and second.adv_warning  # 19.9M notional > 1% of 100M ADV, never shrunk
    assert first.notional == expected * 10000
    assert plan.cash_after_estimate >= 1e8 * 0.6 - 1  # unfilled weight stays cash
    assert plan.phase == "OPEN" and plan.session_date == "2026-11-02"


def test_basket_entry_never_exceeds_cash_with_costs():
    d = _decision([f"{i:06d}" for i in range(1, 8)])
    ref = {c: 1234.0 + 17 * i for i, c in enumerate(d.targets)}
    plan = rebalance.plan_basket_entry("x", d, cash=12_345_678, ref_prices=ref,
                                       markets={c: "KOSDAQ" for c in d.targets}, adv20={c: 5e7 for c in d.targets},
                                       model_version="v1", safety=0.001)
    spent = sum(o.notional * (1 + o.cost_rate) for o in plan.orders)
    assert spent <= 12_345_678 and plan.cash_after_estimate == pytest.approx(12_345_678 - spent)
    assert all(isinstance(o.quantity, int) for o in plan.orders)


def test_basket_exit_sells_everything_at_close():
    plan = rebalance.plan_basket_exit("hf001_f_both", decision_hash="d" * 64, decision_date="2026-10-30",
                                      session_date="2026-11-27", holdings={"000020": 7, "000010": 3}, cash=100.0,
                                      ref_prices={"000010": 1000.0, "000020": 2000.0},
                                      markets={"000010": "KOSPI", "000020": "KOSDAQ"},
                                      adv20={"000010": 5e9, "000020": 5e9}, model_version="v1")
    assert [(o.seq, o.stock_code, o.side, o.quantity) for o in plan.orders] == [
        (1, "000010", "SELL", 3), (2, "000020", "SELL", 7)]
    assert plan.phase == "CLOSE" and plan.value_estimate == 100 + 3000 + 14000
    with pytest.raises(rebalance.PlanError, match="unvalued_holding"):
        rebalance.plan_basket_exit("x", decision_hash="d", decision_date="2026-10-30", session_date="2026-11-27",
                                   holdings={"000010": 1}, cash=0, ref_prices={}, markets={}, adv20={},
                                   model_version="v1")


def test_unknown_2027_tax_is_a_plan_error_not_a_guess():
    # The cost model prices 2027 buys (no tax) but refuses 2027 sells until a sourced rate exists.
    with pytest.raises(rebalance.PlanError, match="cost_model_error"):
        rebalance.plan_basket_exit("x", decision_hash="d" * 64, decision_date="2026-12-30", session_date="2027-01-28",
                                   holdings={"000010": 10}, cash=0.0, ref_prices={"000010": 1000.0},
                                   markets={"000010": "KOSPI"}, adv20={"000010": 5e9}, model_version="v1")


# ---------------------------------------------------------------- continuous (HC003 conventions)


def test_continuous_sells_first_band_full_exit_and_integer_shares():
    # value = 0 cash + 4 holdings; target = 3 codes (A kept, B kept, N new); X exits fully.
    d = _decision(["00000A", "00000B", "00000N"])
    ref = {"00000A": 1000.0, "00000B": 1000.0, "00000N": 1000.0, "00000X": 1000.0}
    holdings = {"00000A": 2600, "00000B": 5000, "00000X": 1}
    markets = {c: "KOSPI" for c in ref}
    adv = {c: 5e9 for c in ref}
    plan = rebalance.plan_continuous("av001_av_all", d, holdings=holdings, cash=200_000.0, ref_prices=ref,
                                     markets=markets, adv20=adv, model_version="v2", band=0.001)
    value = 200_000 + 2600 * 1000 + 5000 * 1000 + 1000
    target = value / 3
    sides = [(o.seq, o.stock_code, o.side, o.reason) for o in plan.orders]
    assert sides == [(1, "00000B", "SELL", "rebalance_decrease"), (2, "00000X", "SELL", "full_exit"),
                     (3, "00000N", "BUY", "new_position")]
    assert plan.orders[0].quantity == math.floor((5000 * 1000 - target) / 1000)
    assert plan.orders[1].quantity == 1  # full exit ignores the band (1000 < 0.1% of value)
    assert {s["stock_code"]: s["reason"] for s in plan.skipped} == {"00000A": "within_band"}
    assert all(isinstance(o.quantity, int) and o.quantity > 0 for o in plan.orders)
    assert plan.cash_after_estimate >= 0


def test_continuous_scales_buys_proportionally_to_cash_after_sells():
    d = _decision(["00000A", "00000B"])
    ref = {"00000A": 100.0, "00000B": 100.0, "00000X": 100.0}
    holdings = {"00000X": 1000}
    plan = rebalance.plan_continuous("x", d, holdings=holdings, cash=0.0, ref_prices=ref,
                                     markets={c: "KOSPI" for c in ref}, adv20={c: 5e9 for c in ref},
                                     model_version="v2", band=0.001)
    sell, *buys = plan.orders
    assert sell.side == "SELL" and sell.quantity == 1000
    proceeds = 100_000 * (1 - sell.cost_rate)
    assert plan.meta["buy_scale"] < 1
    assert plan.meta["buy_scale"] == pytest.approx(proceeds / sum(50_000 * (1 + o.cost_rate) for o in buys))
    assert [o.quantity for o in buys] == [math.floor(50_000 * plan.meta["buy_scale"] / 100)] * 2
    assert plan.cash_after_estimate >= 0


def test_continuous_freezes_holdings_without_cost_inputs():
    d = _decision(["00000A"])
    ref = {"00000A": 100.0, "00000Z": 100.0}
    plan = rebalance.plan_continuous("x", d, holdings={"00000Z": 10}, cash=10_000.0, ref_prices=ref,
                                     markets={"00000A": "KOSPI"}, adv20={"00000A": 5e9}, model_version="v2",
                                     band=0.001)
    assert {s["stock_code"]: s["reason"] for s in plan.skipped} == {"00000Z": "frozen_holding"}
    assert [(o.stock_code, o.side) for o in plan.orders] == [("00000A", "BUY")]
    assert plan.meta["allocatable"] == 10_000.0


# ---------------------------------------------------------------- batch payload


def test_batch_payload_shape_and_idempotency():
    d = _decision(["00000A", "00000B"])
    ref = {"00000A": 70000.0, "00000B": 51000.0, "00000X": 1000.0}
    plan = rebalance.plan_continuous("hf001_f_both", d, holdings={"00000X": 10}, cash=1e6, ref_prices=ref,
                                     markets={c: "KOSPI" for c in ref}, adv20={c: 5e9 for c in ref},
                                     model_version="v2", band=0.001)
    payload = rebalance.batch_payload(plan, user_id="user-1", expected_nav_krw=1_010_000.4, dry_run=True)
    assert list(payload) == ["strategyId", "userId", "decisionHash", "decisionDate", "sessionDate", "phase",
                             "idempotencyKey", "dryRun", "expectedNavKrw", "orders"]
    assert payload["expectedNavKrw"] == 1_010_000 and payload["dryRun"] is True and payload["phase"] == "OPEN"
    key = hashlib.sha256(f"hf001_f_both|{'h' * 64}|2026-11-02|OPEN|user-1".encode()).hexdigest()
    assert payload["idempotencyKey"] == key == rebalance.idempotency_key("hf001_f_both", "h" * 64, "2026-11-02",
                                                                         "OPEN", "user-1")
    assert payload["orders"][0] == {"seq": 1, "stockCode": "00000X", "side": "SELL", "quantity": 10,
                                    "refPrice": 1000, "adv20Krw": 5e9}
    assert [o["side"] for o in payload["orders"]] == ["SELL", "BUY", "BUY"]
    assert isinstance(payload["orders"][1]["refPrice"], int)
    with pytest.raises(rebalance.PlanError, match="user id"):
        rebalance.batch_payload(plan, user_id="", expected_nav_krw=1, dry_run=True)
    swapped = tuple(dataclasses.replace(o, seq=len(plan.orders) + 1 - o.seq) for o in plan.orders)
    bad = rebalance.RebalancePlan(**{**plan.__dict__, "orders": swapped})
    with pytest.raises(rebalance.PlanError, match="precede"):
        rebalance.batch_payload(bad, user_id="u", expected_nav_krw=1, dry_run=False)
    empty = rebalance.RebalancePlan(**{**plan.__dict__, "orders": ()})
    with pytest.raises(rebalance.PlanError, match="at least one order"):
        rebalance.batch_payload(empty, user_id="u", expected_nav_krw=1, dry_run=False)


# ---------------------------------------------------------------- FW001 completion proofs


def _sealed(tmp_path, **marker):
    record = decision_record(f_both=["000010"])
    path = write_decision(tmp_path, record, **marker)
    return record, path, fw001.marker_path(tmp_path, "decisions", "2026-10")


def _only(tmp_path):
    (decision,) = decisions.list_decisions(tmp_path, "f_both")
    return decision


def test_valid_proof_makes_the_decision_final(tmp_path):
    record, path, marker = _sealed(tmp_path)
    decision = _only(tmp_path)
    assert decision.sealed and decision.forward and decision.seal_reason is None
    assert decision.sealed_at == "2026-10-30T17:05:00+09:00"
    assert decision.record_hash == fw001.digest(record) == hashlib.sha256(path.read_bytes()).hexdigest()
    assert decisions.latest_decision(tmp_path, "f_both", "2026-12-01") == decision


def test_absent_marker_is_not_sealed(tmp_path):
    _sealed(tmp_path, seal=False)
    decision = _only(tmp_path)
    assert (decision.forward, decision.seal_reason, decision.seal_error) == (False, "decision_not_sealed", None)
    assert decisions.latest_decision(tmp_path, "f_both", "2026-12-01") is None
    assert decisions.completion(tmp_path, "decisions", "2026-09") == (None, "decision_missing", None)


@pytest.mark.parametrize("sealed_at", ["2026-11-02T08:58:59+09:00", "2026-11-02T08:59:00+09:00",
                                       "2026-11-02T09:30:00+09:00"])
def test_publication_within_a_second_of_the_deadline_is_late_no_trade(tmp_path, sealed_at):
    _sealed(tmp_path, sealed_at=sealed_at)
    decision = _only(tmp_path)
    assert (decision.forward, decision.seal_reason, decision.seal_error) == (False, "decision_seal_late", None)


def test_just_before_the_precision_bound_is_final(tmp_path):
    _sealed(tmp_path, sealed_at="2026-11-02T08:58:58.900000+09:00")
    assert _only(tmp_path).forward


@pytest.mark.parametrize("change", ["digest", "extra_file", "reconstructed", "path", "clock", "garbled"])
def test_corrupt_evidence_raises_a_visible_error_not_missing(tmp_path, change):
    record, path, marker = _sealed(tmp_path)
    stamp = marker.parent.stat().st_mtime_ns
    proof = json.loads(marker.read_text(encoding="utf-8"))
    if change == "digest":
        path.write_text(path.read_text(encoding="utf-8").replace('"forward"', '"forward" '), encoding="utf-8")
    elif change == "extra_file":
        (marker.parent / "note.txt").write_text("x", encoding="utf-8")
    elif change == "garbled":
        marker.write_text("{", encoding="utf-8")
    else:
        key, value = {"reconstructed": ("reconstructed", True), "path": ("record_path", "elsewhere.json"),
                      "clock": ("publication_clock", "wall")}[change]
        marker.write_text(json.dumps({**proof, key: value}), encoding="utf-8")
    os.utime(marker, ns=(stamp - 10**9, stamp - 10**9))
    set_sealed_at(marker, "2026-10-30T17:05:00+09:00")
    decision = _only(tmp_path)
    assert (decision.forward, decision.seal_reason) == (False, "decision_seal_invalid")
    assert decision.seal_error


def test_reconstructed_records_need_no_marker_but_a_marker_on_one_is_invalid(tmp_path):
    path = write_decision(tmp_path, decision_record(f_both=["000010"], label="reconstructed"))
    decision = _only(tmp_path)
    assert (decision.label, decision.sealed, decision.seal_reason, decision.forward) == (
        "reconstructed", False, None, False)
    write_seal(tmp_path, "decisions", "2026-10", path, deadline="2026-11-02T08:59:00+09:00",
               sealed_at="2026-10-30T17:05:00+09:00")
    assert _only(tmp_path).seal_reason == "decision_seal_invalid"


# ---------------------------------------------------------------- funding


def test_funding_flags_buys_that_need_same_auction_sell_proceeds():
    d = _decision(["00000A"])
    ref = {"00000A": 100.0, "00000X": 100.0}
    common = dict(ref_prices=ref, markets={c: "KOSPI" for c in ref}, adv20={c: 5e9 for c in ref},
                  model_version="v2", band=0.001)
    plan = rebalance.plan_continuous("x", d, holdings={"00000X": 1000}, cash=0.0, **common)
    funding = rebalance.funding(plan)
    assert funding["deposit_cash_krw"] == 0 and funding["requires_same_auction_sell_proceeds"] is True
    assert funding["sell_proceeds_assumed_krw"] == round(100_000 * (1 - plan.orders[0].cost_rate))
    assert plan.to_dict()["funding"] == funding
    flat = rebalance.plan_continuous("x", d, holdings={}, cash=100_000.0, **common)
    assert rebalance.funding(flat)["requires_same_auction_sell_proceeds"] is False
