from __future__ import annotations

import configparser
import dataclasses
import json
import math
import os
import subprocess
from datetime import UTC, datetime, timedelta

import pytest

from backtesting import cost_model
from src.live import backend_client, book, decisions, pf001, rebalance, runtime, shadow
from test_live_fixtures import REPO, decision_record, make_repo, write_decision, write_flat_days

KST = shadow.KST
A, B, C, X = "000010", "000020", "000030", "000099"
BOOK_TEXT = (REPO / "config/strategy_book.yaml").read_text(encoding="utf-8")
PF_START = BOOK_TEXT.index("  pf001:")
DEPLOY = REPO / "deploy/strategy-runtime"


def _decision(targets):
    targets = tuple(sorted(targets))
    return decisions.Decision(rule="av_all", month="2026-10", decision_date="2026-10-30", trade_date="2026-11-02",
                              exit_date=None, targets=targets, weights={c: 1 / len(targets) for c in targets},
                              record_hash="h" * 64, label="forward", sealed=True)


def _rate(price, side, adv=5e9):
    return cost_model.one_way_cost(price, "KOSPI", "20261102", adv, side, model_version="v2")


# ---------------------------------------------------------------- book


def _book(tmp_path, text):
    path = tmp_path / "book.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def _pf_text(old, new):
    head, tail = BOOK_TEXT[:PF_START], BOOK_TEXT[PF_START:]
    assert old in tail, old
    return head + tail.replace(old, new, 1)


@pytest.mark.parametrize("old, new, message", [
    ("        weight: 0.2\n", "        weight: 0.3\n", "sum above 1"),
    ("        weight: 0.2\n        source: null\n", "        weight: 0.2\n        source:\n          kind: fw001\n"
     "          rule: av_all\n", "exactly one sleeve must trade"),
    ("          rule: av_all\n      bb:", "          rule: f_both\n      bb:", "exactly one sleeve must trade"),
    ("    limits:\n      max_name_weight: 0.02\n      max_adv_share: 0.01\n", "", "limits must have"),
    ("      max_adv_share: 0.01\n", "      max_adv_share: 0\n", "max_adv_share"),
    ('    t0: "2026-11-02"\n', "", "t0"),
    ("    band: 0.001\n", "", "requires band"),
    ("    early_paper:\n      decided_by: \"user\"\n      decided_at: \"2026-10-21T19:00:00+09:00\"\n"
     "      reason: \"결합 포트폴리오 AV80+BB20, 섀도 없이 PAPER (사용자 결정)\"\n", "", "nonempty list"),
    ("      variants: []\n", "      variants: [holdout_pf001]\n", "both be empty"),
    ("      bb:\n        weight: 0.2\n        source: null\n", "      bb:\n        weight: 0.2\n        source: null\n"
     "        extra: 1\n", "must have exactly"),
])
def test_pf001_book_validation(tmp_path, old, new, message):
    with pytest.raises(book.BookError, match=message):
        book.load_book(_book(tmp_path, _pf_text(old, new)))


def test_sleeves_only_for_continuous_intraday_and_empty_holdout_only_with_early_paper(tmp_path):
    text = BOOK_TEXT.replace("    execution: continuous\n", "    execution: continuous\n    t0: \"2026-11-02\"\n", 1)
    with pytest.raises(book.BookError, match="apply only to continuous_intraday"):
        book.load_book(_book(tmp_path, text))
    text = BOOK_TEXT.replace("      experiment_ids: [HF001_hegemony_filters]\n      variants: [holdout_f_both]",
                             "      experiment_ids: []\n      variants: []", 1)
    assert book.load_book(_book(tmp_path, text))[0].holdout_experiment_ids == ()  # hf001 has early_paper


def test_gate_reason_for_undefined_holdout(tmp_path):
    root = make_repo(tmp_path, early_paper=True)
    ctx = runtime.load_context(root, tmp_path / "data")
    stage = runtime.stage_for(ctx, runtime.find_entry(ctx, "pf001"))
    assert stage.effective == "paper" and "gate_bypassed:holdout_not_defined" in stage.reasons
    assert "gate_bypassed:design_not_passed:OP001_combined_paper_portfolio/pf001:none" in stage.reasons


# ---------------------------------------------------------------- planner


COMMON = dict(session_date="2026-11-02", model_version="v2", band=0.001, max_name_weight=0.02, max_adv_share=0.01)


def _plan(decision, holdings, cash, *, refs=None, adv=None, markets=None, reserved=0.0, **more):
    codes = set(decision.targets if decision else ()) | set(holdings)
    refs = refs or {c: 1000.0 for c in codes}
    adv = adv or {c: 5e9 for c in codes}
    markets = markets or {c: "KOSPI" for c in codes}
    return rebalance.plan_intraday("pf001", decision, reserved_krw=reserved, holdings=holdings, cash=cash,
                                   ref_prices=refs, markets=markets, adv20=adv, **COMMON, **more)


def test_two_plans_weight_cap_adv_cap_full_exit_and_no_reallocation():
    sell, buy = _plan(_decision([A, B]), {X: 100}, 1_000_000.0,
                      adv={A: 5e9, B: 1_000_000.0, X: 5e9})
    assert (sell.phase, buy.phase) == ("CONT_SELL", "CONT_BUY")
    assert [(o.stock_code, o.side, o.quantity, o.binding) for o in sell.orders] == [(X, "SELL", 100, "full_exit")]
    value = 1_100_000.0
    cap_qty = math.floor(0.02 * value / (1000 * (1 + _rate(1000, "buy"))))
    assert [(o.stock_code, o.quantity, o.binding) for o in buy.orders] == [
        (A, cap_qty, "weight_cap"), (B, 10, "adv_cap")]  # B: floor(1% x 1,000,000 / 1000)
    spent = sum(o.notional * (1 + o.cost_rate) for o in buy.orders)
    assert spent < 0.05 * value  # capped amounts stay cash: no reallocation
    assert buy.meta["cash_factor"] == "backend_at_cont_buy_window"
    assert all(isinstance(o.quantity, int) for o in sell.orders + buy.orders)


def test_integer_floor_after_cost_reserve_and_reserved_sleeve_cash():
    decision = _decision([A] + [f"{i:06d}" for i in range(100, 199)])  # 100 names: 1% each, under the cap
    sell, buy = _plan(decision, {}, 10_000_000.0, reserved=2_000_000.0,
                      refs={c: 3333.0 for c in decision.targets})
    target = 8_000_000.0 / 100  # AV sleeve NAV = account value - reserved BB cash
    first = buy.orders[0]
    assert first.quantity == math.floor(target / (3333.0 * (1 + _rate(3333.0, "buy")))) and first.binding == "target"
    assert buy.meta["sleeve_nav_krw"] == 8_000_000.0 and buy.meta["reserved_krw"] == 2_000_000.0
    assert sell.orders == ()


def test_frozen_reserved_cap_trim_and_outside_names_sold_without_band():
    decision = _decision([A, B])
    z = "000077"  # no market: frozen, valued and reserved first
    refs = {A: 100.0, B: 100.0, z: 100.0, X: 100.0}
    sell, buy = _plan(decision, {A: 4_000, z: 2_000, X: 1}, 200_100.0, refs=refs,
                      markets={A: "KOSPI", B: "KOSPI", X: "KOSPI"}, adv={A: 5e9, B: 5e9, X: 5e9})
    value = 200_100.0 + 400_000 + 200_000 + 100
    assert buy.meta["frozen_value_krw"] == 200_000.0 and buy.meta["allocatable_krw"] == value - 200_000.0
    assert {s["stock_code"]: s["reason"] for s in sell.skipped}[z] == "frozen_holding"
    cap_qty = 4_000 - math.floor((400_000 - 0.02 * value) / 100)
    assert [(o.stock_code, o.binding) for o in sell.orders] == [(A, "weight_cap"), (X, "full_exit")]
    assert sell.orders[0].quantity == 4_000 - cap_qty and sell.orders[1].quantity == 1  # X: 100 KRW < band


def test_band_is_measured_on_av_sleeve_nav():
    names = [A] + [f"{i:06d}" for i in range(200, 259)]  # 60 names: 1/60 each, under the 2% cap
    decision = _decision(names)
    sell, buy = _plan(decision, {A: 100}, 590_000.0, refs={c: 100.0 for c in names})
    assert {s["stock_code"]: s["reason"] for s in sell.skipped + buy.skipped}[A] == "within_band"
    assert A not in {o.stock_code for o in sell.orders + buy.orders}
    # Reserving BB cash shrinks the AV sleeve NAV, so A is now above its target by more than the band.
    sell, _ = _plan(decision, {A: 100}, 590_000.0, refs={c: 100.0 for c in names}, reserved=120_000.0)
    assert [(o.stock_code, o.reason) for o in sell.orders] == [(A, "rebalance_decrease")]


def test_terminated_liquidates_everything_and_never_buys():
    sell, buy = _plan(None, {A: 50, X: 10}, 1_000.0, terminated=True, adv={A: 1_000_000.0, X: 5e9})
    assert [(o.stock_code, o.quantity, o.binding) for o in sell.orders] == [(A, 10, "adv_cap"), (X, 10, "liquidation")]
    assert buy.orders == () and sell.decision_hash == rebalance.liquidation_hash("pf001", "2026-11-02")


def test_block_buys_keeps_sells():
    sell, buy = _plan(_decision([A]), {X: 3}, 1_000_000.0, block_buys=True)
    assert len(sell.orders) == 1 and buy.orders == ()
    assert {s["reason"] for s in buy.skipped} == {"buys_blocked"}


def test_payload_phases_sides_and_cost_reserve():
    sell, buy = _plan(_decision([A]), {X: 3}, 1_000_000.0)
    sell_payload = rebalance.batch_payload(sell, user_id="u1", expected_nav_krw=1, dry_run=True)
    buy_payload = rebalance.batch_payload(buy, user_id="u1", expected_nav_krw=1, dry_run=True)
    assert sell_payload["phase"] == "CONT_SELL" and "costReserveRate" not in sell_payload
    assert {o["side"] for o in sell_payload["orders"]} == {"SELL"}
    assert buy_payload["phase"] == "CONT_BUY" and buy_payload["costReserveRate"] == pytest.approx(_rate(1000, "buy"))
    assert sell_payload["idempotencyKey"] != buy_payload["idempotencyKey"]
    wrong = dataclasses.replace(buy, phase="CONT_SELL")
    with pytest.raises(rebalance.PlanError, match="only SELL"):
        rebalance.batch_payload(wrong, user_id="u1", expected_nav_krw=1, dry_run=True)


def test_allocate_fills_pro_rata_floor_leftover_av_then_bb_slots():
    assert rebalance.allocate_fills(10, [("bb", 2, 3), ("av", None, 7), ("bb", 1, 3)]) == {
        ("bb", 2): 2, ("av", None): 6, ("bb", 1): 2}  # 7.69->7? floor 5.38/2.3/2.3 then leftovers
    result = rebalance.allocate_fills(5, [("av", None, 1), ("bb", 3, 2), ("bb", 1, 2)])
    assert result == {("av", None): 1, ("bb", 3): 2, ("bb", 1): 2}
    assert sum(rebalance.allocate_fills(7, [("av", None, 4), ("bb", 1, 4), ("bb", 2, 4)]).values()) == 7
    with pytest.raises(rebalance.PlanError):
        rebalance.allocate_fills(9, [("av", None, 4), ("bb", 1, 4)])


def test_december_extension_points_are_guarded():
    for fn in (pf001.load_op001_targets, pf001.plan_bb_slots, pf001.aggregate_sleeve_orders):
        with pytest.raises(NotImplementedError):
            fn("data", "2026-12-01")


# ---------------------------------------------------------------- submit-due, activation, NAV, termination


class FakeBackend:
    def __init__(self, *, holdings=None, cash=100_000_000.0, risk=None, responses=None, nav_conflict=False,
                 unknown=(), bound=True, settled="same", orderable=None):
        self.holdings = dict(holdings or {})
        self.cash = cash
        self.settled = cash if settled == "same" else settled
        self.orderable = cash if orderable is None else orderable
        self.risk_body = {"activationNavKrw": 100_000_000, "terminated": False, "buyAllowed": True,
                          **(risk or {})}
        self.responses = list(responses or [])
        self.nav_conflict, self.unknown, self.bound = nav_conflict, list(unknown), bound
        self.batches, self.navs, self.calls = [], [], []

    def accounts(self):
        return [{"userId": "u1", "strategyId": "pf001" if self.bound else "other", "accountBinding": "acct-1"}]

    def fetch_account(self, user_id):
        self.calls.append("account")
        return backend_client.AccountState(user_id, self.cash + 1000.0 * sum(self.holdings.values()), self.orderable,
                                           dict(self.holdings), dict(self.holdings), datetime.now(UTC),
                                           settled_cash_d2=self.settled)

    def risk(self, user_id):
        return dict(self.risk_body)

    def unknown_orders(self):
        return self.unknown

    def post_nav_close(self, user_id, body):
        self.navs.append(body)
        return {"stored": False, "conflict": True, "reason": "NAV_CLOSE_CONFLICT"} if self.nav_conflict else \
            {"stored": True}

    def submit_batch(self, payload):
        self.batches.append(payload)
        if self.responses:
            return self.responses.pop(0)
        return {"batchId": f"b{len(self.batches)}", "status": "QUEUED", "deduplicated": False, "reasons": []}

    def get_batch(self, batch_id):
        return {"batchId": batch_id, "orders": [{"stockCode": "005930", "side": "BUY", "filledQuantity": 1}]}


NOW = datetime(2026, 11, 2, 9, 1, tzinfo=KST)


def _world(tmp_path, *, seal=True, bound=True):
    root = make_repo(tmp_path, early_paper=True)
    data = tmp_path / "data"
    write_decision(data, decision_record(f_both=[A], av_all=[A, B, C]), **({} if seal else {"seal": False}))
    write_flat_days(data, "2026-10-01", "2026-11-30", [A, B, C, X, "005930"])
    ctx = runtime.load_context(root, data)
    # The real book binds pf001 to "are01"; tests use "u1" (bound) or None (virtual).
    ctx = dataclasses.replace(ctx, entries=tuple(
        dataclasses.replace(e, account_user_id="u1" if bound else None) if e.strategy_id == "pf001" else e
        for e in ctx.entries))
    return ctx


def _activate(ctx, fake):
    result = runtime.activate_command(ctx, "pf001", execute=True, client_factory=lambda: fake,
                                      now=datetime(2026, 11, 1, 20, 0, tzinfo=KST))
    assert result["status"] == "activated", result
    return result


def _pf(result):
    return [s for s in result["submissions"] if s["strategyId"] == "pf001"]


def test_activation_dry_run_refusals_and_immutability(tmp_path):
    ctx = _world(tmp_path)
    dry = runtime.activate_command(ctx, "pf001")
    assert dry["status"] == "dry_run" and dry["record"]["t0"] == "2026-11-02" and "config_hash" in dry["record"]
    held = runtime.activate_command(ctx, "pf001", execute=True, client_factory=lambda: FakeBackend(
        holdings={A: 1}, unknown=[{"userId": "u1"}], bound=False))
    assert held["status"] == "refused" and set(held["reasons"]) == {
        "account_not_bound_to_strategy", "account_not_cash_only", "account_has_unknown_orders"}
    record = _activate(ctx, FakeBackend())["record"]
    assert record["t0_nav_krw"] == 100_000_000 and len(record["account_id_hash"]) == 64
    assert "acct-1" not in json.dumps(record) and "u1|" not in json.dumps(record)
    again = runtime.activate_command(ctx, "pf001", execute=True, client_factory=lambda: FakeBackend(),
                                     now=datetime(2026, 11, 1, 21, 0, tzinfo=KST))
    assert again["status"] == "already_activated"
    changed = FakeBackend(risk={"activationNavKrw": 90_000_000})
    refused = runtime.activate_command(ctx, "pf001", execute=True, client_factory=lambda: changed)
    assert refused["status"] == "refused" and refused["reasons"][0].startswith("activation_exists_with_different")


def test_submit_due_dry_run_previews_two_batches_without_network(tmp_path):
    ctx = _world(tmp_path)
    result = runtime.submit_due_command(ctx, now=NOW, env={})
    assert result["status"] == "ok" and result["dryRun"]
    assert [(s["phase"], s["status"]) for s in _pf(result)] == [("CONT_SELL", "nothing_to_submit"),
                                                                 ("CONT_BUY", "preview")]
    assert {s["strategyId"]: s["reason"] for s in result["skipped"]} == {
        "hf001_f_both": "account_user_id_missing", "av001_av_all": "account_user_id_missing"}
    assert not (ctx.data_dir / "live").exists()


def test_submit_due_refuses_unfinal_decision_and_alerts(tmp_path):
    ctx = _world(tmp_path, seal=False)
    fake = FakeBackend()
    _activate(ctx, fake)
    result = runtime.submit_due_command(ctx, now=NOW, execute=True, client_factory=lambda: fake, env={})
    assert result["status"] == "failed" and fake.batches == []
    assert result["seals"][0]["state"] == "decision_not_sealed"
    codes = [a["code"] for a in result["alerts"]["alerts"]]
    assert codes == ["decision_not_final"]
    assert not pf001.inputs_path(ctx.data_dir, "pf001", "2026-11-02").exists()


def test_submit_due_execute_seals_inputs_posts_nav_and_submits_two_batches(tmp_path):
    ctx = _world(tmp_path)
    fake = FakeBackend(holdings={X: 100}, cash=99_000_000.0)  # X closes at 10,000
    _activate(ctx, FakeBackend())
    inputs = runtime.submit_due_command(ctx, now=datetime(2026, 11, 2, 8, 57, tzinfo=KST), execute=True,
                                        inputs_only=True, client_factory=lambda: fake, env={})
    assert inputs["status"] == "ok" and fake.batches == []
    path = pf001.inputs_path(ctx.data_dir, "pf001", "2026-11-02")
    sealed = json.loads(path.read_text(encoding="utf-8"))
    assert sealed["sealedBeforeDeadline"] is True and set(sealed["plans"]) == {"CONT_SELL", "CONT_BUY"}
    assert sealed["decision"]["recordHash"] == decisions.list_decisions(ctx.data_dir, "av_all")[0].record_hash
    assert sealed["sleeves"]["bb"]["source"] is None and sealed["nav"]["navKrw"] == 100_000_000.0
    assert fake.navs[0] == {"sessionDate": "2026-10-30", "navKrw": 100_000_000, "cashKrw": 99_000_000,
                            "holdingsValueKrw": 1_000_000, "source": "krx_official",
                            "inputHash": sealed["nav"]["inputHash"]}
    result = runtime.submit_due_command(ctx, now=NOW, execute=True, client_factory=lambda: fake, env={})
    assert result["status"] == "ok"
    assert [(b["phase"], {o["side"] for o in b["orders"]}) for b in fake.batches] == [
        ("CONT_SELL", {"SELL"}), ("CONT_BUY", {"BUY"})]
    assert [o["stockCode"] for o in fake.batches[0]["orders"]] == [X]
    planned = [o["quantity"] for o in sealed["plans"]["CONT_BUY"]["orders"]]
    assert [o["quantity"] for o in fake.batches[1]["orders"]] == planned  # the sealed quantities, not a rebuild
    assert "costReserveRate" in fake.batches[1]
    assert all("exemption" not in b for b in fake.batches)  # no exemption outside liquidation
    assert len(list(runtime.batches_dir(ctx.data_dir, "pf001").iterdir())) == 2


def test_drawdown_breach_records_termination_and_liquidates(tmp_path):
    ctx = _world(tmp_path)
    _activate(ctx, FakeBackend())
    # Binding close NAV 70M vs T0 100M: breach (<= 75M).
    fake = FakeBackend(holdings={A: 100}, cash=69_000_000.0)
    result = runtime.submit_due_command(ctx, now=NOW, execute=True, client_factory=lambda: fake, env={})
    termination = pf001.load_termination(ctx.data_dir, "pf001")
    assert termination["state"] == "terminated_drawdown" and termination["navKrw"] == 70_000_000.0
    assert [b["phase"] for b in fake.batches] == ["CONT_SELL"]
    assert fake.batches[0]["orders"] == [{"seq": 1, "stockCode": A, "side": "SELL", "quantity": 100,
                                          "refPrice": 10000, "adv20Krw": 5e9}]
    assert fake.batches[0]["exemption"] == "TERMINATION_LIQUIDATION"
    statuses = {s["phase"]: s["status"] for s in _pf(result)}
    assert statuses == {"CONT_SELL": "submitted", "CONT_BUY": "skipped"}
    assert "drawdown_breach" in [a["code"] for a in result["alerts"]["alerts"]] and result["status"] == "failed"
    status = runtime.status_command(ctx, generated_at="t")
    pf = next(p for p in status["strategies"] if p["strategyId"] == "pf001")
    assert pf["termination"]["terminated"] and pf["termination"]["state"] == "liquidating"
    # Next session (not a trade date): liquidation continues while holdings remain.
    later = runtime.submit_due_command(ctx, now=datetime(2026, 11, 3, 9, 1, tzinfo=KST), execute=True,
                                       client_factory=lambda: fake, env={})
    assert [s["phase"] for s in _pf(later)] == ["CONT_SELL", "CONT_BUY"] and fake.batches[-1]["phase"] == "CONT_SELL"


def test_backend_termination_and_nav_refusals(tmp_path):
    ctx = _world(tmp_path)
    _activate(ctx, FakeBackend())
    terminated = FakeBackend(holdings={A: 5}, risk={"terminated": True})
    result = runtime.submit_due_command(ctx, now=datetime(2026, 11, 3, 9, 1, tzinfo=KST), execute=True,
                                        client_factory=lambda: terminated, env={})
    assert [b["phase"] for b in terminated.batches] == ["CONT_SELL"]
    assert pf001.load_termination(ctx.data_dir, "pf001") is None  # the backend's own flag
    blocked = FakeBackend(responses=[{"batchId": None, "status": "REJECTED", "deduplicated": False,
                                      "reasons": ["NAV_CLOSE_MISSING"]}])
    ctx2 = _world(tmp_path / "b")
    _activate(ctx2, FakeBackend())
    runtime.submit_due_command(ctx2, now=datetime(2026, 11, 2, 8, 57, tzinfo=KST), execute=True, inputs_only=True,
                               client_factory=lambda: blocked, env={})
    out = runtime.submit_due_command(ctx2, now=NOW, execute=True, client_factory=lambda: blocked, env={})
    assert {s["phase"]: s["status"] for s in _pf(out)} == {"CONT_SELL": "nothing_to_submit", "CONT_BUY": "refused"}
    assert "nav_check_blocked" in [a["code"] for a in out["alerts"]["alerts"]] and out["status"] == "failed"
    assert result["status"] == "ok"


def test_nav_conflict_and_missing_valuation_block_buys(tmp_path):
    ctx = _world(tmp_path)
    _activate(ctx, FakeBackend())
    conflict = FakeBackend(nav_conflict=True)
    out = runtime.submit_due_command(ctx, now=NOW, execute=True, client_factory=lambda: conflict, env={})
    assert [b["phase"] for b in conflict.batches] == [] and "nav_close_conflict" in [
        a["code"] for a in out["alerts"]["alerts"]]
    ctx2 = _world(tmp_path / "m")
    _activate(ctx2, FakeBackend())
    missing = FakeBackend(holdings={"000555": 3})  # no KRX row and no confirmed price
    out = runtime.submit_due_command(ctx2, now=NOW, execute=True, client_factory=lambda: missing, env={})
    assert "valuation_missing" in [a["code"] for a in out["alerts"]["alerts"]]
    assert all(b["phase"] != "CONT_BUY" for b in missing.batches)
    assert pf001.nav_records(ctx2.data_dir, "pf001") == []


def test_not_activated_refuses(tmp_path):
    ctx = _world(tmp_path)
    fake = FakeBackend()
    out = runtime.submit_due_command(ctx, now=NOW, execute=True, client_factory=lambda: fake, env={})
    assert _pf(out)[0]["reasons"] == ["not_activated"] and fake.batches == [] and fake.calls == []


def test_status_payload_hides_sleeve_detail(tmp_path):
    ctx = _world(tmp_path)
    status = runtime.status_command(ctx, generated_at="t")
    pf = next(p for p in status["strategies"] if p["strategyId"] == "pf001")
    assert pf["sleeveDetailSealed"] is True
    assert set(pf["portfolio"]) == {"navKrw", "runningMaxNavKrw", "drawdownPct", "source", "indices"}
    assert pf["termination"] == {"terminated": False, "state": None, "terminatedAt": None, "reason": None,
                                 "source": None}
    text = json.dumps(pf, ensure_ascii=False)
    assert "sleeve_nav" not in text and '"sleeves"' not in text and "bb" not in json.dumps(pf["portfolio"])
    hf = next(p for p in status["strategies"] if p["strategyId"] == "hf001_f_both")
    assert "sleeveDetailSealed" not in hf


def test_index_levels_skip_holdout_rows(tmp_path):
    path = tmp_path / "market_context/benchmarks.jsonl"
    path.parent.mkdir(parents=True)
    rows = [{"series": "KOSPI", "index_name": "코스피", "trade_date": "2026-03-02", "close": 1.0},
            {"series": "KOSPI", "index_name": "코스피", "trade_date": "2026-10-30", "close": 2.0},
            {"series": "KOSPI", "index_name": "코스피 200", "trade_date": "2026-10-31", "close": 3.0}]
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    assert runtime.index_levels(tmp_path) == {"KOSPI": {"date": "2026-10-30", "close": 2.0}}
    assert runtime.index_levels(tmp_path / "none") is None


# ---------------------------------------------------------------- deploy


def _unit(name):
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str
    parser.read(DEPLOY / "systemd" / name, encoding="utf-8")
    return parser


@pytest.mark.parametrize("kind, calendar", [("submit", "Mon..Fri *-*-* 09:01 Asia/Seoul"),
                                            ("inputs", "Mon..Fri *-*-* 08:57 Asia/Seoul")])
def test_submit_and_inputs_units(kind, calendar):
    timer = _unit(f"hqa-strategy-runtime-{kind}.timer")
    assert timer["Timer"]["OnCalendar"] == calendar and timer["Timer"]["Persistent"] == "true"
    assert timer["Timer"]["Unit"] == f"hqa-strategy-runtime-{kind}.service"
    service = _unit(f"hqa-strategy-runtime-{kind}.service")
    morning = _unit("hqa-strategy-runtime-morning.service")
    assert service["Service"]["ExecStart"].endswith(f"deploy/strategy-runtime/{kind}.sh")
    for key in ("User", "Group", "WorkingDirectory", "EnvironmentFile", "NoNewPrivileges", "ProtectSystem",
                "ReadWritePaths", "ReadOnlyPaths", "PrivateTmp", "MemoryMax"):
        assert service["Service"][key] == morning["Service"][key], key
    assert service["Unit"]["OnFailure"] == "hqa-strategy-runtime-failure@%n.service"
    assert "readiness" not in service["Service"]["ExecStart"]


def test_run_sh_submit_and_inputs_modes(tmp_path):
    fake = tmp_path / "fake-python"
    fake.write_text("#!/usr/bin/env bash\nprintf 'RAN %s\\n' \"$*\"\n", encoding="utf-8")
    fake.chmod(0o755)
    env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path), "HQA_PYTHON": str(fake)}
    for script, expected in (("submit.sh", "submit-due --execute"), ("inputs.sh", "submit-due --inputs-only --execute")):
        done = subprocess.run(["bash", str(DEPLOY / script)], env=env, capture_output=True, text=True, timeout=60)
        assert done.returncode == 0 and f"RAN -m scripts.ops.strategy_runtime {expected}" in done.stdout


def test_virtual_shadow_fill_and_daily_plans_for_pf001(tmp_path):
    ctx = _world(tmp_path, bound=False)
    entry = runtime.find_entry(ctx, "pf001")
    record = shadow.shadow_fill(entry, "2026-11-02", ctx.data_dir, mode="shadow")
    phases = {o["phase"] for o in record["orders"] if not o.get("skipped")}
    assert phases == {"CONT_BUY"} and all(o.get("fill_basis") == "open_proxy" for o in record["orders"]
                                         if not o.get("skipped"))
    assert set(record["sleeves"]) == {"av", "bb"} and record["sleeves"]["bb"]["holdings_value_krw"] == 0.0
    invested = sum(o["notional"] for o in record["orders"] if o.get("filled"))
    assert invested <= 0.8 * entry.shadow_nav_krw  # BB cash (20%) stays reserved
    summary, code = runtime.daily_command(ctx, session="2026-11-02", execute=True,
                                          now=datetime(2026, 11, 2, 9, 10, tzinfo=KST), env={})
    plans = next(s for s in summary["steps"] if s["step"] == "plans")["result"]["plans"]
    assert {(p["strategyId"], p["phase"]) for p in plans if p["strategyId"] == "pf001"} == {
        ("pf001", "CONT_SELL"), ("pf001", "CONT_BUY")}
    assert runtime.plan_path(ctx.data_dir, "pf001", "2026-11-02", "CONT_BUY").is_file()
    status = next(s for s in summary["steps"] if s["step"] == "status")["result"]
    pf = next(p for p in status["strategies"] if p["strategyId"] == "pf001")
    assert pf["plan"]["phase"] == "CONT_SELL+CONT_BUY" and code == 0


class Clock:
    def __init__(self, start):
        self.now = start
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now = self.now + timedelta(seconds=seconds)


def test_inputs_only_retries_every_20s_until_085859_without_alerting(tmp_path):
    ctx = _world(tmp_path, seal=False)
    fake = FakeBackend()
    _activate(ctx, fake)
    clock = Clock(datetime(2026, 11, 2, 8, 57, tzinfo=KST))
    out = runtime.submit_due_command(ctx, execute=True, inputs_only=True, client_factory=lambda: fake, env={},
                                     clock=clock, sleep=clock.sleep)
    assert out["status"] == "ok" and out["pendingSeals"][0]["state"] == "decision_not_sealed"
    assert "alerts" not in out and fake.batches == []
    assert clock.sleeps == [20] * 5 and out["intraday"]["pf001"]["attempts"] == 6  # 08:57:00 .. 08:58:40
    assert not pf001.inputs_path(ctx.data_dir, "pf001", "2026-11-02").exists()


def test_inputs_only_stops_once_the_decision_is_final(tmp_path):
    ctx = _world(tmp_path, seal=False)
    fake = FakeBackend()
    _activate(ctx, fake)
    clock = Clock(datetime(2026, 11, 2, 8, 57, tzinfo=KST))

    def sleep(seconds):
        clock.sleep(seconds)
        if len(clock.sleeps) == 2:  # FW001 completes at 08:57:40
            write_decision(ctx.data_dir, decision_record(f_both=[A], av_all=[A, B, C]),
                           sealed_at="2026-11-02T08:57:30+09:00")

    out = runtime.submit_due_command(ctx, execute=True, inputs_only=True, client_factory=lambda: fake, env={},
                                     clock=clock, sleep=sleep)
    sealed = json.loads(pf001.inputs_path(ctx.data_dir, "pf001", "2026-11-02").read_text(encoding="utf-8"))
    assert out["intraday"]["pf001"]["attempts"] == 3 and sealed["sealedBeforeDeadline"] is True
    assert sealed["sealedAt"] == "2026-11-02T08:57:40+09:00"


def test_no_rebalance_without_an_input_seal_before_0859(tmp_path):
    ctx = _world(tmp_path)
    fake = FakeBackend()
    _activate(ctx, fake)
    out = runtime.submit_due_command(ctx, now=NOW, execute=True, client_factory=lambda: fake, env={})
    assert fake.batches == [] and out["status"] == "failed"
    assert _pf(out) == [{"strategyId": "pf001", "phase": None, "status": "skipped",
                         "reasons": ["input_not_sealed_before_deadline"]}]
    assert "input_not_sealed_before_deadline" in [a["code"] for a in out["alerts"]["alerts"]]
    assert not pf001.inputs_path(ctx.data_dir, "pf001", "2026-11-02").exists()
    # An inputs-only run after the deadline does not seal either.
    late = runtime.submit_due_command(ctx, now=datetime(2026, 11, 2, 8, 59, tzinfo=KST), execute=True,
                                      inputs_only=True, client_factory=lambda: fake, env={})
    assert late["intraday"]["pf001"]["note"] == "input_not_sealed_before_deadline"
    assert not pf001.inputs_path(ctx.data_dir, "pf001", "2026-11-02").exists()


def test_liquidation_is_not_gated_by_the_input_deadline(tmp_path):
    ctx = _world(tmp_path)
    _activate(ctx, FakeBackend())
    fake = FakeBackend(holdings={A: 100}, cash=69_000_000.0)  # breach at 09:01, no input seal that morning
    out = runtime.submit_due_command(ctx, now=NOW, execute=True, client_factory=lambda: fake, env={})
    assert [b["phase"] for b in fake.batches] == ["CONT_SELL"]
    assert pf001.liquidation_inputs_path(ctx.data_dir, "pf001", "2026-11-02").is_file()
    assert "input_not_sealed_before_deadline" not in [a["code"] for a in out["alerts"]["alerts"]]



def test_binding_nav_uses_settled_d2_cash_never_orderable_cash(tmp_path):
    ctx = _world(tmp_path)
    _activate(ctx, FakeBackend())
    # Orderable (D+0) cash overstates after buys; the binding NAV uses D+2 settled cash.
    fake = FakeBackend(holdings={X: 100}, cash=95_000_000.0, orderable=99_000_000.0)
    runtime.submit_due_command(ctx, now=NOW, execute=True, client_factory=lambda: fake, env={})
    (record,) = pf001.nav_records(ctx.data_dir, "pf001")
    assert (record["cashField"], record["cashKrw"], record["orderableCashKrw"]) == (
        "settledCashD2Krw", 95_000_000.0, 99_000_000.0)
    assert record["navKrw"] == 96_000_000.0 and fake.navs[0]["cashKrw"] == 95_000_000
    assert record["fields"]["settledCashD2Krw"] == 95_000_000.0 and record["fields"]["settledCashSource"] == "snapshot"
    assert record["fields"]["orderableCash"] == 99_000_000.0 and "snapshotCapturedAt" in record["fields"]
    assert "not subtracted" in record["definition"]
    assert record["holdings"] == {X: 100} and record["prices"][X] == {"price": 10000.0, "source": "krx_close"}


def test_missing_settled_cash_is_valuation_missing(tmp_path):
    ctx = _world(tmp_path)
    _activate(ctx, FakeBackend())
    fake = FakeBackend(holdings={X: 100}, settled=None)
    out = runtime.submit_due_command(ctx, now=NOW, execute=True, client_factory=lambda: fake, env={})
    alert = next(a for a in out["alerts"]["alerts"] if a["code"] == "valuation_missing")
    assert "settled_cash_d2_missing" in alert["details"]["missing"]
    assert fake.navs == [] and pf001.nav_records(ctx.data_dir, "pf001") == []
    assert all(b["phase"] != "CONT_BUY" for b in fake.batches)
    # The backend's GET risk may carry it instead.
    ctx2 = _world(tmp_path / "r")
    _activate(ctx2, FakeBackend())
    via_risk = FakeBackend(holdings={X: 100}, settled=None, risk={"settledCashD2Krw": 90_000_000})
    runtime.submit_due_command(ctx2, now=NOW, execute=True, client_factory=lambda: via_risk, env={})
    assert pf001.nav_records(ctx2.data_dir, "pf001")[0]["cashKrw"] == 90_000_000.0


def test_snapshot_parses_settled_cash_fields():
    now = datetime.now(UTC)
    row = {"userId": "u", "success": True, "accountMode": "PAPER", "capturedAt": now.isoformat(), "equity": 1e8,
           "orderableCash": 5e7, "settledCashD2Krw": 4e7, "cashD1Krw": 4.5e7, "netAssetKrw": 9.9e7,
           "totalEvaluationKrw": 1e8, "holdings": []}
    state = backend_client.parse_account_snapshot({"snapshots": [row]}, "u", now=now)
    assert (state.settled_cash_d2, state.cash_d1, state.net_asset, state.total_evaluation) == (4e7, 4.5e7, 9.9e7, 1e8)
    del row["settledCashD2Krw"]
    assert backend_client.parse_account_snapshot({"snapshots": [row]}, "u", now=now).settled_cash_d2 is None


def test_activation_requires_the_account_binding_fingerprint(tmp_path):
    ctx = _world(tmp_path)

    class NoBinding(FakeBackend):
        def accounts(self):
            return [{"userId": "u1", "strategyId": "pf001"}]

    out = runtime.activate_command(ctx, "pf001", execute=True, client_factory=lambda: NoBinding())
    assert out["status"] == "refused" and out["reasons"] == ["account_binding_missing"]
    assert pf001.load_activation(ctx.data_dir, "pf001") is None


def test_exemption_rules_in_batch_payload():
    sell, buy = _plan(_decision([A]), {X: 3}, 1_000_000.0)
    payload = rebalance.batch_payload(sell, user_id="u1", expected_nav_krw=1, dry_run=True,
                                      exemption="TERMINATION_LIQUIDATION")
    assert payload["exemption"] == "TERMINATION_LIQUIDATION"
    with pytest.raises(rebalance.PlanError, match="not allowed"):
        rebalance.batch_payload(buy, user_id="u1", expected_nav_krw=1, dry_run=True,
                                exemption="TERMINATION_LIQUIDATION")
    with pytest.raises(rebalance.PlanError, match="not allowed"):
        rebalance.batch_payload(sell, user_id="u1", expected_nav_krw=1, dry_run=True, exemption="BB_SCHEDULED_EXIT")
    with pytest.raises(rebalance.PlanError, match="not allowed"):
        rebalance.batch_payload(sell, user_id="u1", expected_nav_krw=1, dry_run=True, exemption="CANCEL_ANYTHING")
    close = dataclasses.replace(sell, phase="CLOSE")
    assert rebalance.batch_payload(close, user_id="u1", expected_nav_krw=1, dry_run=True,
                                   exemption="BB_SCHEDULED_EXIT")["exemption"] == "BB_SCHEDULED_EXIT"
