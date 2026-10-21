from datetime import timedelta
import json

import pytest

from backtesting.experiments.xo001 import Refused, aware
from backtesting.experiments.xo001 import activation
from backtesting.experiments.xo001.calendar import Calendar
from backtesting.experiments.xo001.diagnostic import Diagnostic, OrderPermit
from backtesting.experiments.xo001.kis import BASE, CANCEL, INQUIRY, ORDER, NotTransmitted, Redactor, validate_request
from backtesting.experiments.xo001.storage import Store, encoded, load, put, sha
from test_xo001_support import Clock, SPEC, credentials, fake_kernel, plan, profile_data


class Orders:
    def __init__(self, clock, replies=()):
        self.clock, self.credentials = clock, credentials()
        self.redactor = Redactor(self.credentials)
        self.replies, self.calls = list(replies), []

    def request(self, method, path, tr_id, *, body=None, permit=None, deadline, **kwargs):
        validate_request(method, BASE + path, tr_id)
        if path != INQUIRY:
            assert permit.consume(tr_id, body, self.clock())
        self.calls.append((method, path, tr_id, body, kwargs))
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        if "data" not in reply:
            return {"data": reply, "error": None, "response_headers": {"tr_cont": "D"}, "raw": encoded(reply).decode()}
        return reply


def ack(number="1"):
    return {"rt_cd": "0", "output": {"ODNO": number, "KRX_FWDG_ORD_ORGNO": "org"}}


def inquiry(*rows):
    return {"rt_cd": "0", "output1": list(rows)}


def order_row(number="1", code="005930", *, filled=0, remaining=1, cancelled=False, sell=False):
    return {"odno": number, "pdno": code, "ord_qty": "1", "tot_ccld_qty": str(filled), "rmn_qty": str(remaining),
            "avg_prvs": "10000", "sll_buy_dvsn_cd": "01" if sell else "02", "cncl_yn": "Y" if cancelled else "N"}


def ready(tmp_path, replies, codes=("005930",), account_changes=None):
    clock = Clock("2026-11-03T08:58:00+09:00")
    store = Store(tmp_path / "own", clock=clock)
    client = Orders(clock, replies)
    binding = credentials().identifiers()
    diagnostic = Diagnostic(store, client, binding, gate=lambda *_: True)
    decisions = [{"event": {"rcept_no": f"2026110200000{i}", "stock_code": c},
                  "xo001_input_sha256": sha(str(i).encode())} for i, c in enumerate(codes, 1)]
    diagnostic.seal_intents(plan(), decisions)
    source = store.blob(b"sanitized coordinator account evidence")
    evidence = {"date": "2026-11-03", "as_of": clock().isoformat(), "account_sha256": binding["account_sha256"],
                "positions": {}, "open_orders": [], "cash": 100000, "buying_power": 100000,
                "margin_per_share": {c: 10000 for c in codes}, "settlement_basis": "coordinator verified settlement/margin",
                "source_sha256": source, "coordinator_verified": True}
    evidence.update(account_changes or {})
    diagnostic.account(plan(), evidence)
    clock.set("2026-11-03T08:59:05+09:00")
    return diagnostic, client, clock, evidence


def test_diagnostic_default_off_performs_no_order_or_inquiry(tmp_path):
    clock, client = Clock(), Orders(Clock())
    diagnostic = Diagnostic(Store(tmp_path / "own", clock=clock), client, {})
    assert diagnostic.submit(plan())["status"] == "OFF"
    assert diagnostic.inquire("2026-11-03", deadline=clock() + timedelta(seconds=10))["status"] == "OFF"
    assert diagnostic.cancel(plan())["status"] == "OFF"
    assert diagnostic.flatten(plan())["status"] == "OFF"
    assert client.calls == []


def test_only_one_share_sealed_event_order_and_acceptance_not_fill(tmp_path, fake_kernel):
    diagnostic, client, clock, _ = ready(tmp_path, [ack()])
    result = diagnostic.submit(plan())
    assert result["results"][0]["state"] == "accepted"
    assert client.calls[0][2] == "VTTC0012U"
    assert client.calls[0][3]["ORD_QTY"] == "1"
    assert client.calls[0][3]["PDNO"] == "005930"
    assert diagnostic.positions() == {}
    assert result["results"][0]["acceptance_is_fill"] is False
    assert diagnostic.submit(plan())["results"] == []
    assert len(client.calls) == 1


@pytest.mark.parametrize("at,expected", [("08:59:04", "waiting"), ("08:59:30", "late"), ("09:00:00", "late")])
def test_fixed_submission_window_no_late_calls(tmp_path, fake_kernel, at, expected):
    diagnostic, client, clock, _ = ready(tmp_path, [])
    clock.set("2026-11-03T" + at + "+09:00")
    result = diagnostic.submit(plan())
    assert (result["status"] if expected == "waiting" else result["results"][0]["state"]) == expected
    assert client.calls == []


def test_timeout_unknown_stops_remaining_buys_and_restart_never_resends(tmp_path, fake_kernel):
    diagnostic, client, clock, _ = ready(tmp_path, [Refused("transport failure")], codes=("005930", "000660"))
    result = diagnostic.submit(plan())
    assert result["results"][0]["state"] == "UNKNOWN"
    assert result["results"][1]["reason"] == "UNKNOWN_order_remains"
    restarted = Diagnostic(diagnostic.store, client, diagnostic.binding, gate=lambda *_: True)
    assert restarted.submit(plan())["results"] == []
    assert len(client.calls) == 1


def test_crash_between_durable_attempt_and_response_remains_unknown(tmp_path, fake_kernel):
    diagnostic, client, clock, _ = ready(tmp_path, [], codes=("005930", "000660"))
    intent = diagnostic.store.get("order_intents", "2026-11-03")["targets"][0]
    diagnostic.store.seal("order_attempt", "2026-11-03.000000.buy", {**intent, "date": "2026-11-03",
                         "account_sha256": diagnostic.binding["account_sha256"]})
    result = diagnostic.submit(plan())
    assert result["results"][0]["reason"] == "UNKNOWN_order_remains"
    assert client.calls == []


def test_rejection_is_separate_from_unknown_and_not_retried(tmp_path, fake_kernel):
    diagnostic, client, _, _ = ready(tmp_path, [{"rt_cd": "1", "msg1": "rejected"}])
    result = diagnostic.submit(plan())
    assert result["results"][0]["state"] == "rejected"
    assert diagnostic.submit(plan())["results"] == []


def test_cancel_requires_0901_and_final_inquiry_zero_remaining(tmp_path, fake_kernel):
    diagnostic, client, clock, _ = ready(tmp_path, [ack(), inquiry(order_row()), ack("cancel"),
        inquiry(order_row(remaining=0, cancelled=True))])
    diagnostic.submit(plan())
    clock.set("2026-11-03T09:00:50+09:00")
    assert diagnostic.cancel(plan())["status"] == "waiting"
    clock.set("2026-11-03T09:01:00+09:00")
    diagnostic.inquire("2026-11-03", deadline=clock() + timedelta(seconds=30))
    diagnostic.cancel(plan())
    assert diagnostic._orders()[0]["state"] == "open"
    diagnostic.inquire("2026-11-03", deadline=clock() + timedelta(seconds=30))
    assert diagnostic._orders()[0]["state"] == "cancelled"
    assert diagnostic._orders()[0]["remaining"] == 0
    assert any(call[2] == "VTTC0013U" for call in client.calls)


def test_flatten_fixed_time_only_confirmed_quantity_and_no_new_sell_while_unknown(tmp_path, fake_kernel):
    diagnostic, client, clock, _ = ready(tmp_path, [ack(), inquiry(order_row(filled=1, remaining=0)), ack("sell")])
    diagnostic.submit(plan())
    clock.set("2026-11-03T09:01:00+09:00")
    diagnostic.inquire("2026-11-03", deadline=clock() + timedelta(seconds=30))
    assert diagnostic.positions() == {"005930": 1}
    assert diagnostic.flatten(plan())["status"] == "waiting"
    clock.set("2026-11-03T15:20:00+09:00")
    result = diagnostic.flatten(plan())
    assert result["flattened"] is False
    assert client.calls[-1][2] == "VTTC0011U"
    assert client.calls[-1][3]["ORD_QTY"] == "1"
    # Accepted sell is unresolved until fills are confirmed; never duplicate next day.
    clock.set("2026-11-04T15:20:00+09:00")
    assert diagnostic.flatten(plan("2026-11-04"))["results"] == []
    assert len([c for c in client.calls if c[2] == "VTTC0011U"]) == 1


def test_sale_fill_query_is_required_before_flattened(tmp_path, fake_kernel):
    diagnostic, client, clock, _ = ready(tmp_path, [ack(), inquiry(order_row(filled=1, remaining=0)), ack("2"),
        inquiry(order_row(filled=1, remaining=0), order_row("2", filled=1, remaining=0, sell=True))])
    diagnostic.submit(plan())
    clock.set("2026-11-03T15:20:00+09:00")
    diagnostic.inquire("2026-11-03", deadline=clock() + timedelta(seconds=30))
    assert diagnostic.flatten(plan())["flattened"] is False
    diagnostic.inquire("2026-11-03", deadline=clock() + timedelta(seconds=30))
    assert diagnostic.positions() == {}


def test_paginated_inquiry_missing_continuation_is_unknown(tmp_path, fake_kernel):
    first_page = {"data": {**inquiry(order_row()), "ctx_area_fk100": "fk", "ctx_area_nk100": "nk"},
                  "error": None, "response_headers": {"tr_cont": "F"}}
    diagnostic, client, clock, _ = ready(tmp_path, [ack(), first_page, inquiry()])
    diagnostic.submit(plan())
    clock.set("2026-11-03T09:01:00+09:00")
    result = diagnostic.inquire("2026-11-03", deadline=clock() + timedelta(seconds=30))
    assert result["complete"] and result["page_sizes"] == [1, 0]
    assert client.calls[-1][-1]["continuation"] == "N"
    broken = {"data": inquiry(order_row()), "error": None, "response_headers": {"tr_cont": "F"}}
    client.replies.append(broken)
    assert diagnostic.inquire("2026-11-03", deadline=clock() + timedelta(seconds=30))["complete"] is False
    assert diagnostic._orders()[0]["state"] == "UNKNOWN"


def test_missing_account_margin_cash_or_positions_are_explicit_no_order(tmp_path, fake_kernel):
    diagnostic, client, clock, evidence = ready(tmp_path, [])
    # Existing confirmed account proof cannot be replaced by better cash after seeing results.
    source = diagnostic.store.path(diagnostic.store.proof("account", "2026-11-03")["record_path"])
    before = source.read_bytes()
    diagnostic.account(plan(), {**evidence, "cash": 0})
    assert source.read_bytes() == before
    new = Store(tmp_path / "other", clock=clock)
    new.seal("order_intents", "2026-11-03", diagnostic.store.get("order_intents", "2026-11-03"))
    other = Diagnostic(new, client, diagnostic.binding, gate=lambda *_: True)
    assert other.submit(plan())["results"][0]["reason"] == "main_account_evidence_missing"
    assert client.calls == []


def test_intents_after_0859_refused_and_permit_consumes_exact_body_once(tmp_path, fake_kernel):
    clock = Clock("2026-11-03T08:59:01+09:00")
    diagnostic = Diagnostic(Store(tmp_path / "own", clock=clock), Orders(clock), {}, gate=lambda *_: True)
    with pytest.raises(Refused, match="08:59"):
        diagnostic.seal_intents(plan(), [])
    body = {"ORD_QTY": "1"}
    permit = OrderPermit("VTTC0012U", sha(encoded(body)), aware("2026-11-03T08:59:05+09:00"), aware("2026-11-03T08:59:30+09:00"))
    assert permit.consume("VTTC0012U", body, aware("2026-11-03T08:59:05+09:00"))
    assert not permit.consume("VTTC0012U", body, aware("2026-11-03T08:59:06+09:00"))


def activation_plan():
    test = lambda day: {"date": day, "stock_code": "005930", "market_order_type": "01", "limit_order_type": "00", "limit_price": 7000}
    return {"tests": [test(d) for d in ("2026-11-02", "2026-11-03", "2026-11-04")], "main_account": test("2026-11-05"),
            "account_policy": {"main_valid_through": "2028-10-20", "competition_valid_through": "2026-11-20",
                               "replacement": "forbidden", "renewal": "forbidden", "on_expiry": "disable_buys_preserve_state_cleanup_same_account"}}


def activation_ready(tmp_path):
    clock = Clock("2026-10-31T10:00:00+09:00")
    store = Store(tmp_path / "own", clock=clock)
    activation.seal_plan(store, Calendar(store), activation_plan())
    activation.seal_profile(store, profile_data(), SPEC)
    binding = {**credentials().identifiers(), "code_sha256": "a" * 64, "environment_sha256": "b" * 64,
               "profile_sha256": sha(encoded(profile_data())), "config_sha256": sha(encoded(activation.CONFIG)), "files": {}}
    evidence = store.blob(b"sanitized reviewed shakedown evidence")
    return store, clock, binding, evidence


def test_plan_is_fixed_before_any_results_and_failed_tests_cannot_be_replaced(tmp_path, fake_kernel):
    store, clock, binding, digest = activation_ready(tmp_path)
    with pytest.raises(Refused, match="before the first"):
        activation.seal_plan(store, Calendar(store), activation_plan())
    clock.set("2026-11-06T10:00:00+09:00")
    evidence = {"date": "2026-11-02", "checks": {k: False for k in activation.ORDER_CHECKS}, "evidence_hashes": [digest]}
    proof = activation.attest(store, "order", evidence, binding)
    changed = {**evidence, "checks": {k: True for k in activation.ORDER_CHECKS}}
    assert activation.attest(store, "order", changed, binding)["record_sha256"] == proof["record_sha256"]
    with pytest.raises(Refused, match="substitute"):
        activation.attest(store, "order", {**changed, "date": "2026-11-06"}, binding)


def test_activation_main_observation_required_and_order_failure_does_not_block_it(tmp_path, fake_kernel):
    store, clock, binding, digest = activation_ready(tmp_path)
    clock.set("2026-11-30T10:00:00+09:00")
    with pytest.raises(Refused, match="observation validation incomplete"):
        activation.activate(store, binding)
    obs = {"date": "2026-11-05", "checks": {k: True for k in activation.OBS_CHECKS}, "evidence_hashes": [digest]}
    activation.attest(store, "observation", obs, binding)
    proof = activation.activate(store, binding)
    assert proof["valid"]
    assert proof["record"]["diagnostic_approved"] is False
    assert activation.status(store) == "active"
    assert activation.diagnostic_enabled(store, binding) is False
    with pytest.raises(Refused, match="cannot be replaced"):
        activation.activate(store, binding)


def test_competition_success_alone_never_activates_main_account_diagnostic(tmp_path, fake_kernel):
    store, clock, binding, digest = activation_ready(tmp_path)
    clock.set("2026-11-30T10:00:00+09:00")
    activation.attest(store, "observation", {"date": "2026-11-05", "checks": {k: True for k in activation.OBS_CHECKS},
                        "evidence_hashes": [digest]}, binding)
    competition = {**binding, "key_sha256": "c" * 64, "account_sha256": "d" * 64}
    for day in ("2026-11-02", "2026-11-03", "2026-11-04"):
        activation.attest(store, "order", {"date": day, "checks": {k: True for k in activation.ORDER_CHECKS},
                            "evidence_hashes": [digest]}, competition)
    assert activation.activate(store, binding)["record"]["diagnostic_approved"] is False


def test_account_verification_and_three_successes_only_approve_still_no_flag(tmp_path, fake_kernel):
    store, clock, binding, digest = activation_ready(tmp_path)
    clock.set("2026-11-30T10:00:00+09:00")
    for kind, days, checks in (("observation", ["2026-11-05"], activation.OBS_CHECKS),
                              ("order", ["2026-11-02", "2026-11-03", "2026-11-04"], activation.ORDER_CHECKS),
                              ("main_account", ["2026-11-05"], activation.MAIN_CHECKS)):
        for day in days:
            activation.attest(store, kind, {"date": day, "checks": {k: True for k in checks}, "evidence_hashes": [digest]}, binding)
    assert activation.activate(store, binding)["record"]["diagnostic_approved"] is True
    assert activation.diagnostic_enabled(store, binding) is False
    with pytest.raises(Refused, match="binding differs"):
        activation.verify(store, {**binding, "key_sha256": "e" * 64})


def test_activation_after_november_deadline_is_not_started(tmp_path, fake_kernel):
    store, clock, binding, digest = activation_ready(tmp_path)
    clock.set("2026-12-01T00:00:00+09:00")
    assert activation.status(store) == "not_started"
    with pytest.raises(Refused, match="not_started"):
        activation.activate(store, binding)


@pytest.mark.parametrize("changes,reason", [({"cash": 0}, "insufficient_cash_or_buying_power"),
    ({"buying_power": 0}, "insufficient_cash_or_buying_power"), ({"margin_per_share": {}}, "unverified_margin_requirement"),
    ({"positions": {"005930": 1}}, "unflattened_position"), ({"open_orders": ["external"]}, "prior_open_order_remains")])
def test_actual_cash_margin_positions_and_open_orders_guards(tmp_path, fake_kernel, changes, reason):
    diagnostic, client, _, _ = ready(tmp_path, [], account_changes=changes)
    assert diagnostic.submit(plan())["results"][0]["reason"] == reason
    assert client.calls == []


def test_expired_main_account_blocks_buys_without_replacing_identity(tmp_path, fake_kernel):
    diagnostic, client, _, _ = ready(tmp_path, [])
    diagnostic.store.seal("activation", "activation", {"account_policy": {"main_valid_through": "2026-11-02"}})
    assert diagnostic.submit(plan())["results"][0]["reason"] == "main_account_expired_no_undefined_replacement"
    assert client.calls == []


def test_provably_untransmitted_is_not_unknown_and_does_not_block_next_intent(tmp_path, fake_kernel):
    diagnostic, client, clock, _ = ready(tmp_path, [NotTransmitted("no HTTP call"), ack("2"),
        inquiry(order_row("2", "000660", filled=1, remaining=0))], codes=("005930", "000660"))
    result = diagnostic.submit(plan())
    assert [r["state"] for r in result["results"]] == ["not_submitted", "accepted"]
    clock.set("2026-11-03T09:01:00+09:00")
    diagnostic.inquire("2026-11-03", deadline=clock() + timedelta(seconds=30))
    assert diagnostic._orders()[0]["state"] == "not_submitted"


def test_intents_skipped_by_clock_or_late_decision_have_explicit_records(tmp_path, fake_kernel):
    diagnostic, client, clock, _ = ready(tmp_path, [])
    diagnostic.record_omitted_targets(plan(), [{"event": {"rcept_no": "20261102009999", "stock_code": "000660"}}])
    clock.set("2026-11-03T09:05:00+09:00")
    diagnostic.finish_submissions(plan(), clock_verified=False)
    rows = [diagnostic.store.get("order_skip", k) for k in diagnostic.store.keys("order_skip")]
    assert {r["reason"] for r in rows} == {"not_in_timely_sealed_intent_set", "clock_unverified"}
    assert client.calls == []
