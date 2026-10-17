from __future__ import annotations

import dataclasses
import json
import math
from datetime import datetime

import pytest

from backtesting import cost_model
from src.live import book, decisions, shadow
from test_live_fixtures import (REPO, decision_record, krx_row, write_decision, write_execution, write_flat_days,
                               write_krx)

A, B, C, D = "000010", "000020", "000030", "000040"
LATE = datetime(2026, 12, 31, 18, 0, tzinfo=shadow.KST)


def _entries():
    return {e.strategy_id: e for e in book.load_book(REPO / book.BOOK_PATH)}


def _rate(price, side, day, version="v1", adv=5e9, market="KOSPI"):
    return cost_model.one_way_cost(price, market, day.replace("-", ""), adv, side, model_version=version)


# ---------------------------------------------------------------- prices


def test_holdout_dates_are_never_opened(tmp_path):
    path = tmp_path / "market/krx_daily/2026/20260303.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text("not json\n", encoding="utf-8")
    with pytest.raises(PermissionError, match="protected_holdout_date:2026-03-03"):
        shadow.load_quotes(tmp_path, "2026-03-03")
    for day in ("2026-01-01", "2026-06-30"):
        with pytest.raises(PermissionError):
            shadow.krx_path(tmp_path, day)
    assert shadow.krx_path(tmp_path, "2026-07-01").name == "20260701.jsonl"
    with pytest.raises(PermissionError):  # the ADV window would reach into June
        shadow.adv20_from_files(tmp_path, "2026-07-15", [A])


def test_latest_version_per_stock_and_verified_flag(tmp_path):
    write_krx(tmp_path, "2026-11-02", [krx_row(A, close=1, version="old"), krx_row(B, status="unverified_special_session"),
                                       krx_row(A, close=2, version="new")])
    quotes = shadow.load_quotes(tmp_path, "2026-11-02")
    assert quotes[A].close == 2 and quotes[A].verified and not quotes[B].verified
    assert shadow.load_quotes(tmp_path, "2026-11-03") is None


def test_adv20_from_files_needs_all_twenty_sessions(tmp_path):
    sessions = shadow.last_sessions("2026-10-30", 20)
    assert sessions[-1] == "2026-10-30" and len(sessions) == 20
    write_flat_days(tmp_path, sessions[0], sessions[-1], [A, B], overrides={(sessions[3], B): {"absent": True}})
    assert shadow.adv20_from_files(tmp_path, "2026-10-30", [A, B]) == {A: 5e9}


def test_executable_price_rules():
    day = "2026-11-02"
    q = shadow.Quote(A, "KOSPI", 10000.0, 10100.0, 1000.0, 10000.0, 1e9, True)
    assert shadow.executable_price(q, "BUY", "OPEN", day) == (10000.0, None)
    up = dataclasses.replace(q, open=12950.0)
    assert shadow.executable_price(up, "BUY", "OPEN", day) == (None, "limit_up_open")
    assert shadow.executable_price(up, "SELL", "OPEN", day) == (12950.0, None)
    down = dataclasses.replace(q, open=7050.0, close=7050.0)
    assert shadow.executable_price(down, "SELL", "OPEN", day) == (None, "limit_down_open")
    assert shadow.executable_price(down, "SELL", "CLOSE", day) == (None, "limit_down_close")
    assert shadow.executable_price(dataclasses.replace(q, volume=0.0), "BUY", "OPEN", day) == (None, "halted")
    assert shadow.executable_price(None, "BUY", "OPEN", day) == (None, "missing_price_row")
    assert shadow.executable_price(dataclasses.replace(q, verified=False), "BUY", "OPEN", day)[1] == \
        "unverified_price_row"
    assert shadow.executable_price(dataclasses.replace(q, base_price=None), "BUY", "OPEN", day)[1] == \
        "missing_base_price"


# ---------------------------------------------------------------- basket_20


def _basket_fixture(tmp_path, overrides=None):
    data = tmp_path / "data"
    write_decision(data, decision_record(f_both=[A, B, C, D], av_all=[A, B]))
    base = {("2026-11-02", A): {"open_": 10000, "close": 10500},
            ("2026-11-02", B): {"open_": 13000, "close": 13000},  # limit-up open: buy unfillable
            ("2026-11-02", C): {"volume": 0},  # halted
            ("2026-11-02", D): {"absent": True}}
    write_flat_days(data, "2026-10-30", "2026-12-01", [A, B, C, D], overrides={**base, **(overrides or {})})
    return data


def test_basket_entry_fills_at_open_and_marks_nav_at_close(tmp_path):
    data = _basket_fixture(tmp_path)
    entry = _entries()["hf001_f_both"]
    record = shadow.shadow_fill(entry, "2026-11-02", data, mode="observe")
    rate = _rate(10000, "buy", "2026-11-02")
    qty = math.floor(1e8 * 0.25 * (1 - rate) / 10000)
    filled = {o["stock_code"]: o for o in record["orders"]}
    assert filled[A]["filled"] and filled[A]["quantity"] == qty and filled[A]["fill_price"] == 10000
    assert (filled[B]["reason"], filled[C]["reason"], filled[D]["reason"]) == (
        "limit_up_open", "halted", "missing_price_row")
    assert record["holdings_after"] == {A: qty}
    cash = 1e8 - qty * 10000 * (1 + rate)
    assert record["cash_after"] == pytest.approx(cash)
    assert record["nav_close_krw"] == pytest.approx(cash + qty * 10500)
    assert record["match"] == {"targets_match": True, "fill_prices_match": True,
                               "independent_check": "unavailable", "independent_check_reason": "execution_missing"}
    assert record["incidents"] == ["price_row_missing:" + D] and record["mode"] == "observe"
    assert record["match"]["independent_check"] == "unavailable"  # no FW001 execution record
    assert record["state_after"]["baskets"][0]["exit_date"] == "2026-11-27"
    assert not shadow.ledger_path(data, entry.strategy_id, "2026-11-02").exists()  # dry run writes nothing


def test_basket_exit_at_close_with_limit_down_retry(tmp_path):
    data = _basket_fixture(tmp_path, {("2026-11-27", A): {"close": 7000}})  # limit-down close on exit day
    entry = _entries()["hf001_f_both"]
    for day in shadow.sessions_between("2026-11-02", "2026-11-26"):
        record = shadow.shadow_fill(entry, day, data, mode="shadow", execute=True, now=LATE)
        assert record["_written"]
        assert record["incidents"] == (["price_row_missing:" + D] if day == "2026-11-02" else [])
    qty = record["holdings_after"][A]
    exit_day = shadow.shadow_fill(entry, "2026-11-27", data, mode="shadow", execute=True, now=LATE)
    (order,) = exit_day["orders"]
    assert (order["side"], order["phase"], order["filled"], order["reason"]) == ("SELL", "CLOSE", False,
                                                                                  "limit_down_close")
    assert exit_day["holdings_after"] == {A: qty} and exit_day["nav_close_krw"] == pytest.approx(
        exit_day["cash_after"] + qty * 7000)
    retry = shadow.shadow_fill(entry, "2026-11-30", data, mode="shadow", execute=True, now=LATE)
    (order,) = retry["orders"]
    assert order["filled"] and order["fill_price"] == 10000 and order["quantity"] == qty
    rate = _rate(10000, "sell", "2026-11-30")
    assert order["cost_krw"] == pytest.approx(qty * 10000 * rate)
    assert retry["holdings_after"] == {} and retry["state_after"] == {"baskets": []}
    assert retry["cash_after"] == pytest.approx(exit_day["cash_after"] + qty * 10000 * (1 - rate))
    assert any(w.startswith("exit_retry:") for w in retry["warnings"])
    december = shadow.shadow_fill(entry, "2026-12-01", data, mode="shadow")
    assert december["incidents"] == ["decision_missing:2026-11"]


def test_missing_price_file_gap_conflict_and_order(tmp_path):
    data = _basket_fixture(tmp_path)
    entry = _entries()["hf001_f_both"]
    shadow.shadow_fill(entry, "2026-11-02", data, mode="shadow", execute=True, now=LATE)
    (data / "market/krx_daily/2026/20261104.jsonl").unlink()
    with pytest.raises(shadow.ShadowRefused, match="price_file_missing:2026-11-04") as refused:
        shadow.shadow_fill(entry, "2026-11-04", data, mode="shadow")
    assert refused.value.kind == "price_file_missing"
    assert not shadow.ledger_path(data, entry.strategy_id, "2026-11-04").exists()
    # The 2026-11-03 file exists but has no row for the held stock A.
    (data / "market/krx_daily/2026/20261103.jsonl").write_text(
        json.dumps(krx_row(B, day="2026-11-03")) + "\n", encoding="utf-8")
    missing = shadow.shadow_fill(entry, "2026-11-03", data, mode="shadow", execute=True, now=LATE)
    assert missing["incidents"] == ["price_row_missing:" + A]
    assert missing["marks_close"] == {A: 10500} and "stale_mark:" + A in missing["warnings"]
    gap = shadow.shadow_fill(entry, "2026-11-05", data, mode="shadow")
    assert gap["incidents"] == ["ledger_gap:1:2026-11-04..2026-11-04"]
    with pytest.raises(shadow.ShadowRefused, match="session_not_closed"):
        shadow.shadow_fill(entry, "2026-11-05", data, mode="shadow", execute=True,
                           now=datetime(2026, 11, 5, 16, 29, tzinfo=shadow.KST))
    assert not shadow.ledger_path(data, entry.strategy_id, "2026-11-05").exists()
    again = shadow.shadow_fill(entry, "2026-11-03", data, mode="shadow", execute=True, now=LATE)
    assert again["_written"] is False
    path = shadow.ledger_path(data, entry.strategy_id, "2026-11-03")
    tampered = json.loads(path.read_text(encoding="utf-8"))
    tampered["nav_close_krw"] = 1.0
    path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(shadow.LedgerConflict):
        shadow.shadow_fill(entry, "2026-11-03", data, mode="shadow", execute=True, now=LATE)
    with pytest.raises(shadow.LedgerConflict):
        shadow.shadow_fill(entry, "2026-11-03", data, mode="shadow")
    with pytest.raises(shadow.LedgerError, match="after"):
        shadow.shadow_fill(entry, "2026-11-02", data, mode="shadow")
    with pytest.raises(ValueError, match="not_a_krx_session"):
        shadow.shadow_fill(entry, "2026-11-07", data, mode="shadow")


def test_reconstructed_decision_never_trades(tmp_path):
    data = tmp_path / "data"
    write_decision(data, decision_record(f_both=[A], label="reconstructed"))
    write_flat_days(data, "2026-10-30", "2026-11-02", [A])
    record = shadow.shadow_fill(_entries()["hf001_f_both"], "2026-11-02", data, mode="shadow")
    assert record["incidents"] == ["decision_not_forward:2026-10:reconstructed"]
    assert record["orders"] == [] and record["nav_close_krw"] == 1e8


def test_unverified_2027_calendar_fails_closed(tmp_path):
    data = tmp_path / "data"
    entry = dataclasses.replace(_entries()["hf001_f_both"])
    write_flat_days(data, "2026-12-01", "2026-12-30", [A])
    with pytest.raises(ValueError, match="calendar_unverified"):
        shadow.shadow_fill(entry, "2027-01-04", data, mode="shadow")


# ---------------------------------------------------------------- continuous


def test_continuous_rebalance_sells_first_and_skips_limit_up(tmp_path):
    data = _basket_fixture(tmp_path)
    entry = _entries()["av001_av_all"]
    first = shadow.shadow_fill(entry, "2026-11-02", data, mode="observe", execute=True, now=LATE)
    assert set(first["holdings_after"]) == {A}  # B limit-up: unfilled buy stays cash
    assert {o["stock_code"]: o["reason"] for o in first["orders"] if not o["filled"]} == {B: "limit_up_open"}
    for day in shadow.sessions_between("2026-11-03", "2026-11-30"):
        shadow.shadow_fill(entry, day, data, mode="observe", execute=True, now=LATE)
    write_decision(data, decision_record(av_all=[B, C], month="2026-11", decision_date="2026-11-30",
                                         trade_date="2026-12-01", exit_date="2026-12-28"))
    december = shadow.shadow_fill(entry, "2026-12-01", data, mode="observe")
    orders = [o for o in december["orders"] if not o.get("skipped")]
    assert [(o["stock_code"], o["side"], o["plan_reason"]) for o in orders] == [
        (A, "SELL", "full_exit"), (B, "BUY", "new_position"), (C, "BUY", "new_position")]
    assert [o["seq"] for o in orders] == [1, 2, 3]
    assert all(o["filled"] for o in orders) and set(december["holdings_after"]) == {B, C}
    assert december["cash_after"] >= 0 and december["incidents"] == []
    assert december["match"] == {"targets_match": True, "fill_prices_match": True,
                                 "independent_check": "unavailable", "independent_check_reason": "execution_missing"}
    assert december["state_after"]["last_decision_hash"] == december["decision_hash"]
    v2 = _rate(10000, "sell", "2026-12-01", version="v2")
    assert orders[0]["cost_rate"] == pytest.approx(v2)


def test_cost_model_error_on_fill_is_an_incident_and_keeps_position(tmp_path, monkeypatch):
    data = _basket_fixture(tmp_path)
    entry = _entries()["hf001_f_both"]
    record = shadow.shadow_fill(entry, "2026-11-02", data, mode="shadow")
    original = cost_model.one_way_cost

    def failing(price, market, trade_date, adv, side, **kwargs):
        if side == "buy":
            raise ValueError("unknown tax year: 2027")
        return original(price, market, trade_date, adv, side, **kwargs)

    monkeypatch.setattr(shadow.cost_model, "one_way_cost", failing)
    failed = shadow.shadow_fill(entry, "2026-11-02", data, mode="shadow")
    assert failed["holdings_after"] == {} and failed["nav_close_krw"] == 1e8
    assert any(i.startswith("plan_failed:OPEN:cost_model_error") for i in failed["incidents"])
    assert record["holdings_after"]


# ---------------------------------------------------------------- sealed decisions and FW001 executions


def test_unsealed_decision_is_an_incident_on_its_trade_date(tmp_path):
    data = tmp_path / "data"
    write_decision(data, decision_record(f_both=[A]), seal=False)
    write_flat_days(data, "2026-10-30", "2026-11-02", [A])
    record = shadow.shadow_fill(_entries()["hf001_f_both"], "2026-11-02", data, mode="shadow")
    assert record["incidents"] == ["decision_not_sealed:2026-10"] and record["orders"] == []


def _execution(data, *, slots=None, quotes=None, av_status="executed", **marker):
    (decision,) = decisions.list_decisions(data, "f_both")
    record = {"experiment_id": "FW001_forward_replication", "record_type": "execution", "month": "2026-10",
              "trade_date": "2026-11-02", "decision_hash": decision.record_hash, "label": "forward",
              "cost_multiplier": 1.0, "f_both": {"slots": slots or {}, "baskets": {}},
              "av_all": {"status": av_status}, "quotes": quotes or {}}
    return write_execution(data, record, **marker)


SLOTS = {A: {"raw_open": 10000.0, "filled": True, "reason": None},
         B: {"raw_open": 13000.0, "filled": False, "reason": "limit_up_entry"},
         C: {"raw_open": 10000.0, "filled": False, "reason": "untradable_entry"},
         D: {"raw_open": None, "filled": False, "reason": "missing_entry_price"}}


def test_independent_check_against_sealed_fw001_execution(tmp_path):
    data = _basket_fixture(tmp_path)
    entry = _entries()["hf001_f_both"]
    _execution(data, slots=SLOTS)
    record = shadow.shadow_fill(entry, "2026-11-02", data, mode="shadow")
    assert (record["match"]["independent_check"], record["match"]["independent_check_reason"]) == (True, None)
    _execution(data, slots={**SLOTS, A: {**SLOTS[A], "raw_open": 9990.0}})
    record = shadow.shadow_fill(entry, "2026-11-02", data, mode="shadow")
    assert (record["match"]["independent_check"], record["match"]["independent_check_reason"]) == (
        False, "execution_fill_mismatch")
    _execution(data, slots={**SLOTS, B: {**SLOTS[B], "filled": True}})
    assert shadow.shadow_fill(entry, "2026-11-02", data, mode="shadow")["match"]["independent_check"] is False
    _execution(data, slots=SLOTS, seal=False)
    (data / "forward/fw001/sealed/202610.execution.1.0.json").unlink()
    record = shadow.shadow_fill(entry, "2026-11-02", data, mode="shadow")
    assert (record["match"]["independent_check"], record["match"]["independent_check_reason"]) == (
        "unavailable", "execution_not_sealed")
    _execution(data, slots={A: SLOTS[A]})
    assert shadow.shadow_fill(entry, "2026-11-02", data, mode="shadow")["match"]["independent_check_reason"] == \
        "execution_field_missing"
    nothing = shadow.shadow_fill(entry, "2026-11-03", data, mode="shadow")
    assert nothing["match"]["independent_check"] == "not_applicable"


def test_independent_check_continuous_uses_fw001_quotes(tmp_path):
    data = _basket_fixture(tmp_path)
    entry = _entries()["av001_av_all"]
    quotes = {A: {"raw_open": 10000.0, "tradable": True, "limit_up": False, "limit_down": False},
              B: {"raw_open": 13000.0, "tradable": True, "limit_up": True, "limit_down": False}}
    _execution(data, quotes=quotes)
    record = shadow.shadow_fill(entry, "2026-11-02", data, mode="shadow")
    assert record["match"]["independent_check"] is True
    _execution(data, quotes={**quotes, B: {**quotes[B], "limit_up": False}})
    assert shadow.shadow_fill(entry, "2026-11-02", data, mode="shadow")["match"]["independent_check"] is False
    _execution(data, quotes=quotes, av_status="cost_evaluation_blocked")
    assert shadow.shadow_fill(entry, "2026-11-02", data, mode="shadow")["match"]["independent_check"] == \
        "unavailable"
