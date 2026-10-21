from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path

import numpy as np
import pytest

from backtesting.experiments.ld002 import Refused
from backtesting.experiments.ld002.controls import initial_controls, replace
from backtesting.experiments.ld002.ledger import initial_state
from backtesting.experiments.ld002.runner import LlmRunner
from backtesting.experiments.ld002.storage import aware, cutoff, digest, lock, publish, seal, timely, verified
from backtesting.experiments.ld002.workflow import ForwardRunner, book_name
from ld002_fixtures import CODES, Clock, FakeCalendar, FakeCodex, free_cost, identity, output, publication_clock, quote, snapshot


@pytest.fixture
def setup(tmp_path):
    clock = Clock()
    seal(tmp_path / "start.json", {"synthetic": True})
    heavy = tmp_path / "heavy.lock"
    heavy.touch()
    workflow = ForwardRunner(tmp_path, calendar=FakeCalendar(), clock=clock, publication_clock=publication_clock,
                             synthetic=True, code_verifier=lambda: {"implementation_hashes": {"fixture": "hash"}})
    return workflow, clock, heavy


def cycle(setup, *, answer=None, day="2026-11-16"):
    workflow, clock, heavy = setup
    clock.set(day + "T09:00:00+09:00")
    workflow.input_seal(day, lambda state: snapshot(day), heavy_path=heavy)
    process = FakeCodex([answer or output()])
    llm = LlmRunner(workflow.root, identity(), process=process, identity_probe=identity, clock=clock, synthetic=True)
    decision = workflow.decide(day, llm)
    clock.set(day + "T16:00:00+09:00")
    record = workflow.finalize(day, {c: quote() for c in CODES}, required_files={"KOSPI": True, "KOSDAQ": True}, cost_fn=free_cost)
    return decision, record, process


def test_initial_day_return_includes_execution_and_all_612_independent_books(setup):
    decision, record, process = cycle(setup)
    assert decision["valid"] and len(record["books"]) == 612
    assert len(process.calls) == 1 and record["books"][book_name("ld", 1.0)]["turnover"] == .15
    assert record["books"][book_name("ew", 1.0)]["turnover"] == 1


def test_duplicate_finalize_is_idempotent_and_price_corrections_are_audit_only(setup):
    workflow, clock, _ = setup
    _, record, _ = cycle(setup)
    original = (workflow.root / "ledgers/2026-11-16.json").read_bytes()
    quotes = {c: quote(20000) for c in CODES}
    assert workflow.finalize("2026-11-16", quotes, required_files={"KOSPI": True, "KOSDAQ": True}, cost_fn=free_cost) == record
    audit = workflow.finalize("2026-11-16", quotes, required_files={"KOSPI": True, "KOSDAQ": True}, cost_fn=free_cost, audit=True)
    assert audit["audit_only"] and audit["original_sha256"] == digest(record)
    assert (workflow.root / "ledgers/2026-11-16.json").read_bytes() == original
    assert len(list((workflow.root / "audits/2026-11-16").glob("*.json"))) == 1


def test_input_at_ten_and_decision_at_noon_are_excluded(setup):
    workflow, clock, heavy = setup
    clock.set("2026-11-16T10:00:00+09:00")
    assert workflow.input_seal("2026-11-16", lambda s: pytest.fail("must not build"), heavy_path=heavy)["reason"] == "input_not_sealed"
    clock.set("2026-11-16T12:00:00+09:00")
    process = FakeCodex()
    result = workflow.decide("2026-11-16", LlmRunner(workflow.root, identity(), process=process, identity_probe=identity, clock=clock, synthetic=True))
    assert not result["valid"] and result["label"] == "reconstructed" and not process.calls


def test_timing_uses_atomic_publication_time_including_one_second_bound(tmp_path):
    clock = Clock("2026-11-16T09:59:58+09:00")
    path = tmp_path / "record.json"
    assert publish(path, {"x": 1}, deadline=cutoff("2026-11-16", 10), clock=clock, publication_clock=publication_clock)
    assert not timely(path, publication_clock=lambda p: clock() + timedelta(seconds=1))
    assert not timely(tmp_path / "absent.json")


def test_ledger_not_final_gates_build_and_call_and_never_reconstructs_later(setup):
    workflow, clock, heavy = setup
    clock.set("2026-11-17T10:00:00+09:00")
    assert workflow.input_seal("2026-11-17", lambda s: pytest.fail("no build"), heavy_path=heavy)["reason"] == "ledger_not_final"
    process = FakeCodex()
    result = workflow.decide("2026-11-17", LlmRunner(workflow.root, identity(), process=process, identity_probe=identity, clock=clock, synthetic=True))
    assert result["reason"] == "ledger_not_final" and not process.calls
    cycle(setup)
    clock.set("2026-11-17T10:30:00+09:00")
    assert workflow.input_seal("2026-11-17", lambda s: pytest.fail("do not reconstruct"), heavy_path=heavy)["reason"] == "ledger_not_final"


def test_llm_call_happens_outside_heavy_lock(setup):
    workflow, clock, heavy = setup
    workflow.input_seal("2026-11-16", lambda state: snapshot(), heavy_path=heavy)
    def check_lock():
        with lock(heavy, existing=True):
            pass
    process = FakeCodex(after=check_lock)
    result = workflow.decide("2026-11-16", LlmRunner(workflow.root, identity(), process=process, identity_probe=identity, clock=clock, synthetic=True))
    assert result["valid"]


def test_input_build_crossing_deadline_never_publishes_an_input(setup):
    workflow, clock, heavy = setup
    def slow(state):
        clock.set("2026-11-16T10:00:01+09:00")
        return snapshot()
    assert workflow.input_seal("2026-11-16", slow, heavy_path=heavy)["reason"] == "input_not_sealed"
    assert not (workflow.root / "inputs/2026-11-16.json").exists()


def test_valid_output_finishing_late_is_reconstructed_and_holds(setup):
    workflow, clock, heavy = setup
    workflow.input_seal("2026-11-16", lambda s: snapshot(), heavy_path=heavy)
    process = FakeCodex(after=lambda: clock.set("2026-11-16T12:00:01+09:00"))
    decision = workflow.decide("2026-11-16", LlmRunner(workflow.root, identity(), process=process, identity_probe=identity, clock=clock, synthetic=True))
    assert decision["reason"] == "decision_late" and not decision["valid"]
    clock.set("2026-11-16T16:00:00+09:00")
    ledger = workflow.finalize("2026-11-16", {c: quote() for c in CODES}, required_files={"KOSPI": True, "KOSDAQ": True}, cost_fn=free_cost)
    assert ledger["books"][book_name("ld", 1.0)]["state"]["cash"] == 100e6
    assert ledger["books"][book_name("ew", 1.0)]["state"]["cash"] == 0


@pytest.mark.parametrize("recognized,first", [("2026-11-17T11:59:59+09:00", "2026-11-17"),
    ("2026-11-17T12:00:00+09:00", "2026-11-18"), ("2026-11-17T12:00:01+09:00", "2026-11-18"),
    ("2026-11-21T09:00:00+09:00", "2026-11-23")])
def test_termination_recognition_time_exact_noon_boundary(setup, recognized, first):
    workflow, clock, _ = setup
    clock.set(recognized)
    record = workflow.terminate("terminated_drawdown", reason="fixture")
    assert record["liquidation_start"] == first
    assert workflow.terminate("model_changed", reason="later reason") == record


def funded_worker(setup):
    workflow, clock, _ = setup
    _, first, _ = cycle(setup)
    # Provide a previously sealed, entirely invested fixture state for all LD books.
    for m in (1.0, 1.5, 2.0):
        book = first["books"][book_name("ld", m)]
        state = book["state"]
        state["cash"] = 0
        for p in state["positions"].values():
            p["quantity"] = 1000
        state["nav"] = state["peak_nav"] = 100e6
    # This is a new fixture root, never a rewrite of an official seal.
    other = workflow.root / "drawdown-fixture"
    seal(other / "start.json", {"synthetic": True})
    seal(other / "ledgers/2026-11-16.json", first)
    worker = ForwardRunner(other, calendar=FakeCalendar(), clock=clock, publication_clock=publication_clock, synthetic=True,
                           code_verifier=lambda: {})
    return worker, clock


@pytest.mark.parametrize("price,stops", [(7501, False), (7500, True), (7499, True)])
def test_drawdown_exact_25_percent_from_initial_high_is_sealed_and_not_retroactive(setup, price, stops):
    worker, clock = funded_worker(setup)
    clock.set("2026-11-18T09:00:00+09:00")
    result = worker.finalize("2026-11-17", {c: quote(price) for c in CODES}, required_files={"KOSPI": True, "KOSDAQ": True}, cost_fn=free_cost)
    term = worker.termination()
    if stops:
        assert term["status"] == "terminated_drawdown" and term["recognized_at"] == clock().isoformat()
        assert term["liquidation_start"] == "2026-11-18"
    else:
        assert term is None
    assert not result["books"][book_name("ld", 1.0)]["trades"]
    assert result["books"][book_name("ew", 1.0)]["state"]["nav"] < 100e6


@pytest.mark.parametrize("delay_phase", ["record", "marker"])
def test_drawdown_recognition_waits_for_durable_ledger_publication_across_noon(setup, monkeypatch, delay_phase):
    from backtesting.experiments.ld002 import storage
    worker, clock = funded_worker(setup)
    clock.set("2026-11-18T11:59:59+09:00")
    original = storage.seal
    def delayed(path, value):
        result = original(path, value)
        if (delay_phase == "record" and Path(path) == worker.path("ledgers", "2026-11-17")
                or delay_phase == "marker" and Path(path).name == "receipt.json"):
            clock.set("2026-11-18T12:00:01+09:00")
        return result
    monkeypatch.setattr(storage, "seal", delayed)
    result = worker.finalize("2026-11-17", {c: quote(7500) for c in CODES},
        required_files={"KOSPI": True, "KOSDAQ": True}, cost_fn=free_cost)
    assert result["termination"]["recognized_at"] == "2026-11-18T12:00:01+09:00"
    assert result["termination"]["liquidation_start"] == "2026-11-19"
    assert result["finalized_at"] == result["termination"]["recognized_at"]


def test_crash_after_finalization_ack_recovers_original_drawdown_time(setup, monkeypatch):
    worker, clock = funded_worker(setup)
    clock.set("2026-11-18T11:30:00+09:00")
    result = worker.finalize("2026-11-17", {c: quote(7500) for c in CODES},
        required_files={"KOSPI": True, "KOSDAQ": True}, cost_fn=free_cost)
    (worker.root / "termination.json").unlink()  # Fixture crash before separate stop publication.
    clock.set("2026-11-18T13:00:00+09:00")
    monkeypatch.setattr(worker.calendar, "next", lambda day: pytest.fail("never recompute a sealed liquidation start"))
    worker.sync_model_stop()
    assert worker.termination() == result["termination"]
    assert worker.termination()["liquidation_start"] == "2026-11-18"


def test_unacknowledged_ledger_gates_production_reader_and_recovers_at_current_time(setup):
    from scripts.ld002.__main__ import next_finalization_day
    workflow, clock, _ = setup
    _, first, process = cycle(setup)
    workflow.path("ledger_finalizations", "2026-11-16").unlink()
    reader = ForwardRunner(workflow.root, calendar=FakeCalendar(), clock=clock, publication_clock=publication_clock)
    assert reader.ledger("2026-11-16") is None
    clock.set("2026-11-17T12:01:00+09:00")
    assert next_finalization_day(workflow, clock()) == "2026-11-16"
    restored = workflow.finalize("2026-11-16", {}, required_files={})
    assert restored["finalized_at"] == clock().isoformat() and restored["books"] == first["books"]
    assert len(process.calls) == 1
    assert next_finalization_day(workflow, clock()) is None


def test_monthly_ew_entry_and_next_first_session_even_if_llm_invalid(setup):
    _, first, _ = cycle(setup, answer={**output(), "memo": "a" * 801})
    assert not first["valid_decision"] and first["books"][book_name("ew", 1.0)]["state"]["cash"] == 0
    workflow, clock, heavy = setup
    for day in workflow.calendar.sessions("2026-11-17", "2026-11-30"):
        clock.set(day + "T16:00:00+09:00")
        value = workflow.finalize(day, {c: quote() for c in CODES}, required_files={"KOSPI": True, "KOSDAQ": True}, cost_fn=free_cost)
        assert not value["books"][book_name("ew", 1.0)]["trades"]
    clock.set("2026-12-01T09:00:00+09:00")
    workflow.input_seal("2026-12-01", lambda state: snapshot("2026-12-01", CODES[10:20]), heavy_path=heavy)
    clock.set("2026-12-01T16:00:00+09:00")
    value = workflow.finalize("2026-12-01", {c: quote() for c in CODES}, required_files={"KOSPI": True, "KOSDAQ": True}, cost_fn=free_cost)
    assert set(value["books"][book_name("ew", 1.0)]["state"]["positions"]) == set(CODES[10:20])


@pytest.mark.parametrize("required", [{"KOSPI": True, "KOSDAQ": False}, {"KOSPI": False, "KOSDAQ": True}])
def test_incomplete_market_day_stays_unfinalized_and_blocks_next_llm(setup, required):
    workflow, clock, heavy = setup
    _, first, _ = cycle(setup)
    clock.set("2026-11-18T08:45:00+09:00")
    with pytest.raises(Refused, match="ledger_not_final"):
        workflow.finalize("2026-11-17", {c: quote() for c in CODES}, required_files=required, cost_fn=free_cost)
    assert workflow.ledger("2026-11-17") is None
    assert not workflow.path("ledgers", "2026-11-17").exists()
    assert workflow.ledger("2026-11-16")["books"] == first["books"]
    assert workflow.input_seal("2026-11-18", lambda s: pytest.fail("no build"), heavy_path=heavy)["reason"] == "ledger_not_final"
    clock.set("2026-11-18T10:05:00+09:00")
    process = FakeCodex()
    result = workflow.decide("2026-11-18", LlmRunner(workflow.root, identity(), process=process, identity_probe=identity, clock=clock, synthetic=True))
    assert result["reason"] == "ledger_not_final" and not process.calls


def test_prior_ledger_ready_at_nine_allows_retry_after_0845_deferral(setup):
    workflow, clock, heavy = setup
    clock.set("2026-11-17T08:45:00+09:00")
    assert workflow.input_seal("2026-11-17", lambda s: pytest.fail("not ready"), heavy_path=heavy)["reason"] == "ledger_not_final"
    assert not workflow.path("omissions", "2026-11-17").exists()
    # The prior decision is already missed; finalize its hold at the real recovery time.
    clock.set("2026-11-17T09:00:00+09:00")
    workflow.finalize("2026-11-16", {c: quote() for c in CODES}, required_files={"KOSPI": True, "KOSDAQ": True}, cost_fn=free_cost)
    built = workflow.input_seal("2026-11-17", lambda s: snapshot("2026-11-17"), heavy_path=heavy)
    assert built["snapshot"]["decision_day"] == "2026-11-17"
    process = FakeCodex()
    result = workflow.decide("2026-11-17", LlmRunner(workflow.root, identity(), process=process, identity_probe=identity, clock=clock, synthetic=True))
    assert result["valid"] and len(process.calls) == 1


def test_control_seeds_draw_order_persistence_and_invalid_day_no_draw():
    initial = initial_controls()
    expected = np.random.Generator(np.random.PCG64(np.random.SeedSequence(0).spawn(200)[0]))
    first, plans = replace(initial, output(CODES[:2], 25), CODES[5:10][::-1], valid=True)
    picked1 = str(expected.choice(sorted(CODES[5:10])))
    picked2 = str(expected.choice(sorted(set(CODES[5:10]) - {picked1})))
    assert first[0]["mapping"] == {CODES[0]: picked1, CODES[1]: picked2}
    assert replace(initial, output(CODES[:2], 25), CODES[5:10], valid=True) == (first, plans)
    unchanged, no_plans = replace(first, None, [], valid=False)
    assert unchanged == first and no_plans == [None] * 200
    kept, _ = replace(unchanged, output(CODES[:2], 25), [], valid=True)
    assert kept == first


def test_new_ids_sorted_dropped_ids_release_mapping_and_shortage_is_cash():
    answer = output(CODES[:3], 25)
    states, plans = replace(initial_controls(), answer, [CODES[10]], valid=True)
    assert set(states[0]["mapping"]) == {CODES[0]}
    assert plans[0]["weights"] == {CODES[10]: 1 / 12}
    assert plans[0]["shortage_ids"] == CODES[1:3]
    kept, _ = replace(states, answer, CODES[10:15], valid=True)
    assert kept[0]["mapping"] == states[0]["mapping"]  # no refill on a retained short ID
    reset, _ = replace(kept, output([], 0), CODES, valid=True)
    assert not reset[0]["mapping"] and not reset[0]["ids"]
    reopened, _ = replace(reset, output(CODES[:1], 25), CODES[10:15], valid=True)
    assert len(reopened[0]["mapping"]) == 1


def test_unsealed_input_and_missing_decision_do_not_trigger_llm_or_delete_day(setup):
    workflow, clock, _ = setup
    process = FakeCodex()
    value = workflow.decide("2026-11-16", LlmRunner(workflow.root, identity(), process=process, identity_probe=identity, clock=clock, synthetic=True))
    assert value["reason"] == "input_not_sealed" and not process.calls
    clock.set("2026-11-16T16:00:00+09:00")
    final = workflow.finalize("2026-11-16", {c: quote() for c in CODES}, required_files={"KOSPI": True, "KOSDAQ": True}, cost_fn=free_cost)
    assert final["day"] == "2026-11-16" and final["books"][book_name("ld", 1.0)]["return"] == 0


def test_finalize_before_close_and_synthetic_observation_misuse_refuse(setup):
    workflow, _, _ = setup
    with pytest.raises(Refused, match="before"):
        workflow.finalize("2026-11-16", {}, required_files={})
    with pytest.raises(Refused, match="synthetic"):
        ForwardRunner(workflow.root).start()


def test_busy_heavy_lock_defers_input_without_consuming_the_day(setup):
    workflow, clock, heavy = setup
    with lock(heavy, existing=True):
        value = workflow.input_seal("2026-11-16", lambda s: pytest.fail("locked builder"), heavy_path=heavy)
    assert value["reason"] == "heavy_lock_busy" and not list((workflow.root / "omissions").glob("*.json"))
    assert workflow.input_seal("2026-11-16", lambda s: snapshot(), heavy_path=heavy)["day"] == "2026-11-16"


def test_model_changed_signal_liquidates_all_ld_scenarios_but_not_controls(setup):
    workflow, clock, _ = setup
    _, first, _ = cycle(setup)
    clock.set("2026-11-17T09:00:00+09:00")
    seal(workflow.root / "model_changed.json", {"status": "model_changed", "recognized_at": clock().isoformat(), "reason": "fixture drift"})
    workflow.sync_model_stop()
    clock.set("2026-11-17T16:00:00+09:00")
    value = workflow.finalize("2026-11-17", {c: quote() for c in CODES}, required_files={"KOSPI": True, "KOSDAQ": True},
        cost_fn=free_cost, liquidation_adv20=dict.fromkeys(CODES, 1e11))
    assert value["termination"]["status"] == "model_changed"
    for m in (1.0, 1.5, 2.0):
        assert not value["books"][book_name("ld", m)]["state"]["positions"]
        assert value["books"][book_name("random", m, 0)]["state"]["positions"]
        assert value["books"][book_name("decomposition", m)]["state"]["positions"]
    assert value["control_state"] == first["control_state"]


def test_after_noon_stop_holds_today_and_liquidates_next_day_with_independent_adv(setup):
    workflow, clock, heavy = setup
    _, first, _ = cycle(setup)
    clock.set("2026-11-17T09:00:00+09:00")
    workflow.input_seal("2026-11-17", lambda s: snapshot("2026-11-17"), heavy_path=heavy)
    process = FakeCodex([output(CODES[10:20])])
    workflow.decide("2026-11-17", LlmRunner(workflow.root, identity(), process=process, identity_probe=identity, clock=clock, synthetic=True))
    clock.set("2026-11-17T13:00:00+09:00")
    workflow.terminate("forward_aborted", reason="fixture stop")
    clock.set("2026-11-17T16:00:00+09:00")
    held = workflow.finalize("2026-11-17", {c: quote() for c in CODES}, required_files={"KOSPI": True, "KOSDAQ": True}, cost_fn=free_cost)
    assert not held["books"][book_name("ld", 1.0)]["trades"]
    assert held["control_state"] == first["control_state"]
    clock.set("2026-11-18T16:00:00+09:00")
    liquidated = workflow.finalize("2026-11-18", {c: quote() for c in CODES}, required_files={"KOSPI": True, "KOSDAQ": True},
        cost_fn=free_cost, liquidation_adv20=dict.fromkeys(CODES, 1e11))
    assert not liquidated["books"][book_name("ld", 1.0)]["state"]["positions"]


def test_stop_recognized_after_a_close_does_not_cancel_that_days_sealed_decision(setup):
    workflow, clock, heavy = setup
    cycle(setup)
    clock.set("2026-11-17T09:00:00+09:00")
    workflow.input_seal("2026-11-17", lambda s: snapshot("2026-11-17"), heavy_path=heavy)
    workflow.decide("2026-11-17", LlmRunner(workflow.root, identity(), process=FakeCodex([output(CODES[10:20])]),
        identity_probe=identity, clock=clock, synthetic=True))
    clock.set("2026-11-17T16:00:00+09:00")
    workflow.terminate("forward_aborted", reason="recognized after close")
    value = workflow.finalize("2026-11-17", {c: quote() for c in CODES}, required_files={"KOSPI": True, "KOSDAQ": True}, cost_fn=free_cost)
    assert value["books"][book_name("ld", 1.0)]["trades"] and value["valid_decision"]


def test_dividend_entitlement_uses_each_cost_books_actual_earlier_shares(setup):
    workflow, clock, _ = setup
    _, first, _ = cycle(setup)
    clock.set("2026-11-17T16:00:00+09:00")
    event = {"id": "cash-dividend", "type": "dividend", "code": CODES[0], "cash_per_share": 100,
        "entitlement_day": "2026-11-16", "effective_day": "2026-11-17", "official_source": "fixture"}
    value = workflow.finalize("2026-11-17", {c: quote() for c in CODES}, required_files={"KOSPI": True, "KOSDAQ": True}, actions=[event], cost_fn=free_cost)
    for name in value["books"]:
        before = first["books"][name]["state"]
        entitled = before["positions"].get(CODES[0], {}).get("quantity", 0)
        assert value["books"][name]["state"]["cash"] - before["cash"] == entitled * 100


def test_pre_t0_dividend_entitlement_is_zero_without_an_invented_prior_ledger(setup, monkeypatch):
    workflow, clock, _ = setup
    clock.set("2026-11-16T16:00:00+09:00")
    event = {"id": "pre-t0-dividend", "type": "dividend", "code": CODES[0], "cash_per_share": 100,
        "entitlement_day": "2026-11-13", "effective_day": "2026-11-16", "official_source": "fixture"}
    original = workflow.ledger
    def guarded(day):
        assert day >= "2026-11-16", "no experimental shares or ledgers before T0"
        return original(day)
    monkeypatch.setattr(workflow, "ledger", guarded)
    result = workflow.finalize("2026-11-16", {c: quote() for c in CODES}, required_files={"KOSPI": True, "KOSDAQ": True},
        actions=[event], cost_fn=free_cost)
    assert all(book["state"]["nav"] == 100e6 for book in result["books"].values())
    assert result["entitlement_ledger_hashes"] == {"2026-11-13": None}


def test_pending_drawdown_seal_recovery_preserves_earlier_reason_before_model_stop(setup):
    workflow, clock, _ = setup
    _, first, _ = cycle(setup)
    term = {"status": "terminated_drawdown", "recognized_at": "2026-11-16T16:00:00+09:00",
            "liquidation_start": "2026-11-17", "reason": "fixture"}
    other = workflow.root / "pending-fixture"
    first["termination"] = term
    seal(other / "ledgers/2026-11-16.json", first)
    seal(other / "model_changed.json", {"status": "model_changed", "recognized_at": "2026-11-17T09:00:00+09:00", "reason": "later"})
    worker = ForwardRunner(other, calendar=FakeCalendar(), clock=clock, synthetic=True, code_verifier=lambda: {})
    worker.sync_model_stop()
    assert worker.termination() == term


def test_fractional_rights_require_official_fraction_price_and_use_per_book_floor(setup):
    workflow, clock, _ = setup
    _, first, _ = cycle(setup)
    clock.set("2026-11-17T16:00:00+09:00")
    event = {"id": "rights", "type": "rights", "code": CODES[0], "cash_per_share": 0, "shares_per_share": .03,
        "entitlement_day": "2026-11-16", "effective_day": "2026-11-17", "official_source": "fixture"}
    with pytest.raises(Refused, match="cash-in-lieu"):
        workflow.finalize("2026-11-17", {c: quote() for c in CODES}, required_files={"KOSPI": True, "KOSDAQ": True},
            actions=[event], cost_fn=free_cost)
    assert not workflow.path("ledgers", "2026-11-17").exists()
    result = workflow.finalize("2026-11-17", {c: quote() for c in CODES}, required_files={"KOSPI": True, "KOSDAQ": True},
        actions=[{**event, "fraction_cash_price": 100}], cost_fn=free_cost)
    before, after = first["books"][book_name("ld", 1.0)]["state"], result["books"][book_name("ld", 1.0)]["state"]
    assert after["positions"][CODES[0]]["quantity"] - before["positions"][CODES[0]]["quantity"] == 4
    assert after["cash"] - before["cash"] == 50


@pytest.mark.parametrize("kind,hour", [("inputs", 10), ("decisions", 12)])
@pytest.mark.parametrize("late", [False, True])
def test_crash_between_candidate_and_receipt_recovers_exact_payload_only_before_cutoff(setup, kind, hour, late):
    workflow, clock, heavy = setup
    original = workflow.input_seal("2026-11-16", lambda state: snapshot(), heavy_path=heavy)
    process = FakeCodex()
    llm = LlmRunner(workflow.root, identity(), process=process, identity_probe=identity, clock=clock, synthetic=True)
    if kind == "decisions":
        original = workflow.decide("2026-11-16", llm)
    path = workflow.path(kind, "2026-11-16")
    receipt = path.with_suffix(".publication") / "complete.json"
    receipt.unlink()  # Explicitly simulate an incomplete fixture publication.
    receipt.parent.rmdir()
    before = path.read_bytes()
    clock.set(f"2026-11-16T{hour if late else hour - 1:02d}:00:00+09:00")
    if kind == "inputs":
        result = workflow.input_seal("2026-11-16", lambda s: pytest.fail("must reuse snapshot"), heavy_path=heavy)
        assert (result.get("reason") == "input_not_sealed") == late
    else:
        result = workflow.decide("2026-11-16", llm)
        assert result["valid"] != late and len(process.calls) == 1
    assert path.read_bytes() == before and receipt.exists() != late


@pytest.mark.parametrize("resume_first", [False, True])
def test_last_day_crash_after_ack_recovers_stop_before_evaluation(setup, monkeypatch, resume_first):
    from backtesting.experiments.ld002 import workflow as module, evaluate as evaluation
    from scripts.ld002.__main__ import next_finalization_day
    workflow, clock, _ = setup
    for day in workflow.calendar.sessions("2026-11-16", "2026-11-24"):
        cycle(setup, day=day)
    previous = workflow.calendar.previous
    monkeypatch.setattr(workflow.calendar, "previous", lambda d: "2026-11-24" if d == "2027-11-15" else previous(d))
    clock.set("2027-11-16T11:30:00+09:00")
    original_seal = module.seal
    def crash(path, value):
        if Path(path) == workflow.root / "termination.json":
            raise RuntimeError("crash after durable approval")
        return original_seal(path, value)
    monkeypatch.setattr(module, "seal", crash)
    with pytest.raises(RuntimeError, match="durable approval"):
        workflow.finalize("2027-11-15", {c: quote(1) for c in CODES}, required_files={"KOSPI": True, "KOSDAQ": True}, cost_fn=free_cost)
    approval = verified(workflow.path("ledger_finalizations", "2027-11-15"))
    assert approval["termination"]["status"] == "terminated_drawdown"
    assert not workflow.termination()
    monkeypatch.setattr(module, "seal", original_seal)
    # A later model signal must not supersede the drawdown approval.
    seal(workflow.root / "model_changed.json", {"status": "model_changed", "recognized_at": "2027-11-17T09:00:00+09:00", "reason": "later"})
    clock.set("2027-11-23T00:05:00+09:00")
    monkeypatch.setattr(workflow.calendar, "next", lambda d: pytest.fail("sealed liquidation date must survive recovery"))
    if resume_first:
        assert next_finalization_day(workflow, clock()) is None
        assert workflow.termination() == approval["termination"]
    monkeypatch.setattr(evaluation, "canonical_repo", lambda r: workflow.root)
    monkeypatch.setattr(evaluation.hc003, "registry_rows", lambda r: [])
    monkeypatch.setattr(evaluation.experiment_registry, "record_trials", lambda *a, **k: None)
    result = evaluation.publish_evaluation(workflow, clock=clock)
    assert result["status"] == "terminated_drawdown" and result["verdict"] is None
    assert result["termination"] == approval["termination"]
    assert result["termination"]["recognized_at"] == "2027-11-16T11:30:00+09:00"
    assert result["termination"]["liquidation_start"] == "2027-11-16"


def test_recovery_selects_model_change_earlier_than_drawdown_approval(setup):
    workflow, clock = funded_worker(setup)
    clock.set("2026-11-18T11:30:00+09:00")
    workflow.finalize("2026-11-17", {c: quote(7500) for c in CODES}, required_files={"KOSPI": True, "KOSDAQ": True}, cost_fn=free_cost)
    (workflow.root / "termination.json").unlink()
    seal(workflow.root / "model_changed.json", {"status": "model_changed", "recognized_at": "2026-11-18T10:00:00+09:00", "reason": "earlier"})
    with lock(workflow.root / ".state.lock"):
        workflow.sync_model_stop()
    assert workflow.termination()["status"] == "model_changed"
    assert workflow.termination()["recognized_at"] == "2026-11-18T10:00:00+09:00"


def test_input_build_failure_can_retry_before_ten(setup):
    workflow, clock, heavy = setup
    clock.set("2026-11-16T08:45:00+09:00")
    def unavailable(state):
        raise Refused("input source not ready")
    with pytest.raises(Refused, match="not ready"):
        workflow.input_seal("2026-11-16", unavailable, heavy_path=heavy)
    assert not workflow.path("omissions", "2026-11-16").exists()
    clock.set("2026-11-16T09:00:00+09:00")
    assert workflow.input_seal("2026-11-16", lambda s: snapshot(), heavy_path=heavy)["snapshot"]["decision_day"] == "2026-11-16"


def test_input_deployment_orders_finalizer_before_each_retry():
    root = Path(__file__).resolve().parents[1] / "deploy/ld002"
    unit = (root / "hqa-ld002-input.service").read_text()
    assert "After=local-fs.target hqa-ld002-finalize.service" in unit
    assert "Wants=hqa-ld002-finalize.service" in unit
    assert "09:00/5:00" in (root / "hqa-ld002-input.timer").read_text()
