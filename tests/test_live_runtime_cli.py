from __future__ import annotations

import dataclasses
import json
import os
import subprocess
import sys
from datetime import UTC, datetime

import pytest

from scripts.ops import strategy_runtime as cli
from src.live import backend_client, gates, runtime, shadow
from test_live_fixtures import REPO, decision_record, make_repo, write_decision, write_flat_days

A, B, C = "000010", "000020", "000030"
HF_PASS = [("2026-10-17T12:11:15+00:00", "HF001_hegemony_filters", "f_both", "pass"),
           ("2026-10-18T00:00:00+00:00", "HF001_hegemony_filters", "holdout_f_both", "pass"),
           ("2026-10-17T11:54:26+00:00", "AV001_avoidance_portfolio", "av_all", "pass"),
           ("2026-10-17T12:52:56+00:00", "AV001_avoidance_portfolio", "holdout_av_all", "holdout_aborted")]
LATE = datetime(2026, 12, 31, 18, 0, tzinfo=shadow.KST)
PAYLOAD_KEYS = {"strategyId", "title", "description", "generatedAt", "stage", "research", "decision", "shadow",
                "plan", "account"}


def _world(tmp_path):
    root = make_repo(tmp_path, rows=HF_PASS)
    data = tmp_path / "data"
    write_decision(data, decision_record(f_both=[A, B, C], av_all=[A, B]))
    write_flat_days(data, "2026-10-30", "2026-11-30", [A, B, C],
                    overrides={("2026-11-02", B): {"open_": 13000, "close": 13000}})
    return root, data


def _run(capsys, *argv):
    code = cli.main(list(argv))
    return code, json.loads(capsys.readouterr().out)


def test_offline_end_to_end_plan_fill_status(tmp_path, capsys, monkeypatch):
    root, data = _world(tmp_path)
    monkeypatch.setattr(shadow, "now_kst", lambda: LATE)
    base = ["--repo-root", str(root), "--data-dir", str(data)]
    code, dry = _run(capsys, *base, "plan", "--strategy", "hf001_f_both", "--session", "2026-11-02")
    assert code == 0 and dry["dryRun"] and dry["plans"][0]["phase"] == "OPEN"
    assert not (data / "live").exists()
    code, planned = _run(capsys, *base, "plan", "--strategy", "hf001_f_both", "--session", "2026-11-02", "--execute")
    plan = planned["plans"][0]
    assert code == 0 and plan["_written"] and len(plan["orders"]) == 3
    assert runtime.plan_path(data, "hf001_f_both", "2026-11-02", "OPEN").is_file()

    code, filled = _run(capsys, *base, "shadow-fill", "--session", "2026-11-02", "--execute")
    assert code == 0
    results = {r["strategyId"]: r for r in filled["results"]}
    assert results["hf001_f_both"]["mode"] == "shadow" and results["hf001_f_both"]["written"]
    assert results["hf001_f_both"]["filledCount"] == 2  # B opened limit-up
    assert results["av001_av_all"]["mode"] == "observe"  # AV001 holdout aborted
    ledger = shadow.read_ledger(data, "hf001_f_both")
    planned_qty = {o["stock_code"]: o["quantity"] for o in plan["orders"]}
    assert {o["stock_code"]: o["quantity_planned"] for o in ledger[0]["orders"]} == planned_qty

    code, status = _run(capsys, *base, "status")
    assert code == 0 and status["errors"] == []
    payloads = {p["strategyId"]: p for p in status["strategies"]}
    hf = payloads["hf001_f_both"]
    assert set(hf) == PAYLOAD_KEYS
    assert hf["stage"] == {"requested": "shadow", "effective": "shadow", "reasons": []}
    assert set(hf["research"]) == {"experimentId", "variant", "design", "holdout"}
    assert hf["research"]["holdout"] == {"experimentId": "HF001_hegemony_filters", "variant": "holdout_f_both",
                                         "verdict": "pass", "recordedAt": "2026-10-18T00:00:00+00:00"}
    assert hf["decision"] == {"month": "2026-10", "decisionDate": "2026-10-30", "tradeDate": "2026-11-02",
                              "exitDate": "2026-11-27", "targetCount": 3,
                              "recordHash": ledger[0]["decision_hash"], "label": "forward"}
    assert set(hf["shadow"]) == {"mode", "sessions", "firstDate", "lastDate", "initialNavKrw", "navKrw",
                                 "returnPct", "incidents", "history"}
    assert (hf["shadow"]["mode"], hf["shadow"]["sessions"], hf["shadow"]["firstDate"]) == ("shadow", 1, "2026-11-02")
    assert hf["shadow"]["navKrw"] == round(ledger[0]["nav_close_krw"]) and hf["shadow"]["incidents"] == 0
    assert hf["shadow"]["history"] == [{"date": "2026-11-02", "navKrw": hf["shadow"]["navKrw"]}]
    assert hf["plan"] == {"sessionDate": "2026-11-02", "phase": "OPEN", "buyCount": 3, "sellCount": 0,
                          "buyNotionalKrw": plan["summary"]["buyNotionalKrw"], "sellNotionalKrw": 0,
                          "advWarnings": 0}
    assert hf["account"] == {"userId": None, "bound": False}
    av = payloads["av001_av_all"]
    assert av["stage"]["effective"] == "observe" and av["shadow"]["sessions"] == 0
    assert av["shadow"]["history"][0]["date"] == "2026-11-02" and av["plan"] is None

    code, publish = _run(capsys, *base, "publish-status")
    assert code == 0 and publish["dryRun"] and len(publish["requests"]) == 2


def test_plan_without_due_action_and_refused_submit(tmp_path, capsys):
    root, data = _world(tmp_path)
    base = ["--repo-root", str(root), "--data-dir", str(data)]
    code, result = _run(capsys, *base, "plan", "--strategy", "av001_av_all", "--session", "2026-11-03")
    assert code == 0 and result["status"] == "no_action_due"
    code, result = _run(capsys, *base, "submit", "--strategy", "hf001_f_both", "--session", "2026-11-02",
                        "--phase", "OPEN", "--execute")
    assert code == 1 and result["status"] == "refused"
    assert result["reasons"][:1] == ["stage_not_paper:shadow"] and "account_user_id_missing" in result["reasons"]


class FakeClient:
    def __init__(self, account):
        self.account, self.batches, self.statuses = account, [], []

    def fetch_account(self, user_id):
        assert user_id == "user-1"
        return self.account

    def submit_batch(self, payload):
        self.batches.append(payload)
        return {"batchId": None, "status": "VALIDATED", "deduplicated": False, "reasons": [], "orderCount": 0,
                "rejectedOrders": []}

    def publish_status(self, strategy_id, generated_at, payload):
        self.statuses.append((strategy_id, generated_at, payload))
        return {"stored": True}


def _paper_ctx(tmp_path, monkeypatch):
    root, data = _world(tmp_path)
    ctx = runtime.load_context(root, data)
    entries = tuple(dataclasses.replace(e, account_user_id="user-1") if e.strategy_id == "hf001_f_both" else e
                    for e in ctx.entries)
    ctx = dataclasses.replace(ctx, entries=entries)
    monkeypatch.setattr(runtime, "stage_for", lambda ctx, entry: gates.StageDecision(
        entry.strategy_id, "paper", "paper", "paper", (), {}))
    return ctx


def test_submit_preview_is_offline_and_execute_uses_account(tmp_path, monkeypatch):
    ctx = _paper_ctx(tmp_path, monkeypatch)
    monkeypatch.setattr(backend_client, "StrategyBackendClient", lambda: pytest.fail("preview must not connect"))
    preview = runtime.submit_command(ctx, "hf001_f_both", "2026-11-02", "OPEN")
    assert preview["status"] == "preview" and preview["payload"]["dryRun"] is True
    assert preview["payload"]["userId"] == "user-1" and len(preview["payload"]["orders"]) == 3

    account = backend_client.AccountState("user-1", 50_000_000.0, 50_000_000.0, {}, {}, datetime.now(UTC))
    fake = FakeClient(account)
    validated = runtime.submit_command(ctx, "hf001_f_both", "2026-11-02", "OPEN", execute=True, validate=True,
                                       client_factory=lambda: fake)
    assert validated["status"] == "submitted" and fake.batches[-1]["dryRun"] is True
    assert fake.batches[-1]["expectedNavKrw"] == 50_000_000
    first_qty = fake.batches[-1]["orders"][0]["quantity"]
    assert first_qty < preview["payload"]["orders"][0]["quantity"]  # sized from the 50M account cash
    runtime.submit_command(ctx, "hf001_f_both", "2026-11-02", "OPEN", execute=True, client_factory=lambda: fake)
    assert fake.batches[-1]["dryRun"] is False
    assert fake.batches[-1]["idempotencyKey"] == fake.batches[-2]["idempotencyKey"]

    locked = backend_client.AccountState("user-1", 1e8, 1e6, {A: 10}, {A: 5}, datetime.now(UTC))
    refused = runtime.submit_command(ctx, "hf001_f_both", "2026-11-02", "OPEN", execute=True,
                                     client_factory=lambda: FakeClient(locked))
    assert refused == {"status": "refused", "strategyId": "hf001_f_both", "reasons": ["unsellable_holdings:000010"]}


def test_submit_basket_exit_from_account_holdings(tmp_path, monkeypatch):
    ctx = _paper_ctx(tmp_path, monkeypatch)
    account = backend_client.AccountState("user-1", 1e8, 1e6, {A: 10, "999999": 4}, {A: 10, "999999": 4},
                                          datetime.now(UTC))
    fake = FakeClient(account)
    result = runtime.submit_command(ctx, "hf001_f_both", "2026-11-27", "CLOSE", execute=True, validate=True,
                                    client_factory=lambda: fake)
    assert result["status"] == "submitted"
    assert [(o["stockCode"], o["side"], o["quantity"]) for o in fake.batches[-1]["orders"]] == [(A, "SELL", 10)]
    assert fake.batches[-1]["phase"] == "CLOSE"


def test_publish_status_execute_with_fake_client(tmp_path):
    root, data = _world(tmp_path)
    ctx = runtime.load_context(root, data)
    fake = FakeClient(None)
    result = runtime.publish_status_command(ctx, execute=True, client_factory=lambda: fake)
    assert result["status"] == "ok" and [s[0] for s in fake.statuses] == ["hf001_f_both", "av001_av_all"]
    assert set(fake.statuses[0][2]) == PAYLOAD_KEYS


class FakeResponse:
    def __init__(self, body, status=200):
        self.body, self.status_code = body, status

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(f"{self.status_code}")

    def json(self):
        return self.body


class FakeSession:
    def __init__(self, body):
        self.body, self.calls = body, []

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append((url, json, headers))
        return FakeResponse(self.body)

    def get(self, url, headers=None, timeout=None):
        self.calls.append((url, None, headers))
        return FakeResponse(self.body)


def test_backend_client_configuration_and_parsing(monkeypatch):
    for name in ("BACKEND_INTERNAL_BASE_URL", "BACKEND_BASE_URL", "HQA_INTERNAL_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(backend_client.BackendError, match="BACKEND_INTERNAL_BASE_URL"):
        backend_client.StrategyBackendClient()
    monkeypatch.setenv("BACKEND_BASE_URL", "http://backend.invalid/")
    with pytest.raises(backend_client.BackendError, match="HQA_INTERNAL_TOKEN"):
        backend_client.StrategyBackendClient()
    monkeypatch.setenv("HQA_INTERNAL_TOKEN", "t0ken")
    session = FakeSession({"stored": True})
    client = backend_client.StrategyBackendClient(session=session)
    client.publish_status("hf001_f_both", "2026-10-30T17:10:00+09:00", {"x": 1})
    url, body, headers = session.calls[0]
    assert url == "http://backend.invalid/api/v1/internal/strategies/status"
    assert body == {"strategyId": "hf001_f_both", "generatedAt": "2026-10-30T17:10:00+09:00", "payload": {"x": 1}}
    assert headers == {"X-HQA-Internal-Token": "t0ken"}

    now = datetime.now(UTC)
    snapshot = {"userId": "u", "success": True, "accountMode": "PAPER", "capturedAt": now.isoformat(),
                "equity": 1e8, "orderableCash": 5e7,
                "holdings": [{"stockCode": A, "quantity": 3, "sellableQuantity": 3}]}
    session = FakeSession({"snapshots": [snapshot]})
    state = backend_client.StrategyBackendClient(session=session).fetch_account("u")
    assert (state.equity, state.cash, state.holdings) == (1e8, 5e7, {A: 3})
    assert session.calls[0][1] == {"userIds": ["u"]}
    assert session.calls[0][0].endswith("/api/v1/internal/trading/account-snapshots")
    for bad, message in [({"accountMode": "REAL"}, "PAPER"), ({"capturedAt": "2020-01-01T00:00:00+00:00"}, "stale"),
                         ({"userId": "other"}, "match"), ({"success": False}, "failed"),
                         ({"holdings": [{"stockCode": A, "quantity": 1, "sellableQuantity": 2}]}, "invalid holding")]:
        with pytest.raises(backend_client.BackendError, match=message):
            backend_client.parse_account_snapshot({"snapshots": [{**snapshot, **bad}]}, "u", now=now)
    with pytest.raises(backend_client.BackendError, match="unexpected batch"):
        backend_client.StrategyBackendClient(session=FakeSession({"status": "?"})).submit_batch({})


def test_cli_subprocess_status_is_read_only(tmp_path):
    root, data = _world(tmp_path)
    before = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*"))
    env = {k: v for k, v in os.environ.items() if k != "HQA_DATA_DIR"}
    completed = subprocess.run([sys.executable, "-m", "scripts.ops.strategy_runtime", "status", "--repo-root",
                                str(root), "--data-dir", str(data)], cwd=REPO, env=env, capture_output=True,
                               text=True, timeout=120)
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert [p["strategyId"] for p in result["strategies"]] == ["hf001_f_both", "av001_av_all"]
    assert sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*")) == before


# ---------------------------------------------------------------- review round 2


def test_shadow_fill_command_refusals_and_missing_file_incident(tmp_path, monkeypatch):
    root, data = _world(tmp_path)
    ctx = runtime.load_context(root, data)
    early = runtime.shadow_fill_command(ctx, "2026-11-02", "hf001_f_both", execute=True)
    assert early["status"] == "error" and early["results"][0]["reason"] == "session_not_closed"
    assert not (data / "live").exists()
    monkeypatch.setattr(shadow, "now_kst", lambda: LATE)
    (data / "market/krx_daily/2026/20261102.jsonl").unlink()
    dry = runtime.shadow_fill_command(ctx, "2026-11-02", "hf001_f_both")
    assert dry["results"][0]["reason"] == "price_file_missing" and not (data / "live").exists()
    result = runtime.shadow_fill_command(ctx, "2026-11-02", "hf001_f_both", execute=True)
    assert result["results"][0]["status"] == "refused"
    assert not shadow.ledger_path(data, "hf001_f_both", "2026-11-02").exists()
    (incident,) = shadow.read_incidents(data, "hf001_f_both")
    assert (incident["kind"], incident["date"]) == ("price_file_missing", "2026-11-02")


def test_status_hides_unsealed_decision_and_reports_reason(tmp_path):
    root, data = _world(tmp_path)
    (data / "forward/fw001/sealed/202610.decision.json").unlink()
    status = runtime.status_command(runtime.load_context(root, data))
    hf = status["strategies"][0]
    assert set(hf) == PAYLOAD_KEYS and hf["decision"] is None
    assert hf["stage"]["reasons"] == ["decision_not_sealed:2026-10"]


class AnyUserClient(FakeClient):
    def fetch_account(self, user_id):
        return self.account


def test_account_exit_retries_after_exit_date(tmp_path, monkeypatch):
    ctx = _paper_ctx(tmp_path, monkeypatch)
    account = backend_client.AccountState("user-1", 1e8, 1e6, {A: 10}, {A: 10}, datetime.now(UTC))
    fake = FakeClient(account)
    result = runtime.submit_command(ctx, "hf001_f_both", "2026-11-30", "CLOSE", execute=True, validate=True,
                                    client_factory=lambda: fake)
    assert result["status"] == "submitted"
    assert [(o["stockCode"], o["side"]) for o in fake.batches[-1]["orders"]] == [(A, "SELL")]
    early = runtime.submit_command(ctx, "hf001_f_both", "2026-11-26", "CLOSE", execute=True, validate=True,
                                   client_factory=lambda: fake)
    assert early["status"] == "error" and early["error"].startswith("no_forward_basket_exit_due")


def test_account_exit_refuses_ambiguous_overlap_and_multiple_due(tmp_path, monkeypatch):
    ctx = _paper_ctx(tmp_path, monkeypatch)
    data = ctx.data_dir
    # October basket exits 2026-12-02 (artificially late); November basket entered 2026-12-01 shares A.
    write_decision(data, decision_record(f_both=[A, B, C], exit_date="2026-12-02"))
    write_decision(data, decision_record(f_both=[A], month="2026-11", decision_date="2026-11-30",
                                         trade_date="2026-12-01", exit_date="2026-12-29"))
    write_flat_days(data, "2026-12-01", "2026-12-29", [A, B, C])
    account = backend_client.AccountState("user-1", 1e8, 1e6, {A: 10, B: 5}, {A: 10, B: 5}, datetime.now(UTC))
    result = runtime.submit_command(ctx, "hf001_f_both", "2026-12-02", "CLOSE", execute=True, validate=True,
                                    client_factory=lambda: FakeClient(account))
    assert result == {"status": "error", "strategyId": "hf001_f_both",
                      "error": "basket_overlap_requires_manual_review:000010"}
    held = backend_client.AccountState("user-1", 1e8, 1e6, {A: 10, B: 5}, {A: 10, B: 5}, datetime.now(UTC))
    write_decision(data, decision_record(f_both=[B, C], exit_date="2026-11-27"))
    result = runtime.submit_command(ctx, "hf001_f_both", "2026-12-29", "CLOSE", execute=True, validate=True,
                                    client_factory=lambda: FakeClient(held))
    assert result["error"] == "multiple_baskets_due_requires_manual_review"


def test_submit_fails_closed_on_live_calendar(tmp_path, monkeypatch):
    ctx = _paper_ctx(tmp_path, monkeypatch)
    result = runtime.submit_command(ctx, "hf001_f_both", "2026-11-19", "OPEN")
    assert result["status"] == "refused"
    assert result["reasons"][0].startswith("live_calendar_refused:calendar_special_session_coverage_unverified")
    result = runtime.submit_command(ctx, "hf001_f_both", "2027-10-01", "OPEN")
    assert result["reasons"][0].startswith("live_calendar_refused:calendar_review_expired")


def _continuous_paper(tmp_path, monkeypatch):
    ctx = _paper_ctx(tmp_path, monkeypatch)
    entries = tuple(dataclasses.replace(e, account_user_id="user-2") if e.strategy_id == "av001_av_all" else e
                    for e in ctx.entries)
    ctx = dataclasses.replace(ctx, entries=entries)
    write_flat_days(ctx.data_dir, "2026-10-01", "2026-10-29", [C])  # ADV20 for the held, non-target C
    account = backend_client.AccountState("user-2", 1e8, 0.0, {C: 10_000}, {C: 10_000}, datetime.now(UTC))
    return ctx, AnyUserClient(account)


def test_submit_reports_funding_and_max_buy_to_deposit_refuses(tmp_path, monkeypatch):
    ctx, fake = _continuous_paper(tmp_path, monkeypatch)
    result = runtime.submit_command(ctx, "av001_av_all", "2026-11-02", "OPEN", execute=True, validate=True,
                                    client_factory=lambda: fake)
    assert result["status"] == "submitted"
    assert result["funding"]["deposit_cash_krw"] == 0
    assert result["funding"]["requires_same_auction_sell_proceeds"] is True
    assert result["funding"]["sell_proceeds_assumed_krw"] > 0
    sent = len(fake.batches)
    refused = runtime.submit_command(ctx, "av001_av_all", "2026-11-02", "OPEN", execute=True, validate=True,
                                     max_buy_to_deposit=True, client_factory=lambda: fake)
    assert refused["status"] == "refused" and refused["reasons"] == ["buys_exceed_deposit_cash"]
    assert len(fake.batches) == sent  # refused before sending, never resized
    preview = runtime.submit_command(ctx, "av001_av_all", "2026-11-02", "OPEN")
    assert preview["funding"]["requires_same_auction_sell_proceeds"] is False  # shadow state is all cash
    code = cli.main(["--repo-root", str(ctx.repo_root), "--data-dir", str(ctx.data_dir), "submit", "--strategy",
                     "av001_av_all", "--session", "2026-11-02", "--phase", "OPEN", "--max-buy-to-deposit"])
    assert code == 1  # the CLI loads the real book: no account user is bound, so submit refuses


@pytest.mark.parametrize("response, status", [
    ({"batchId": "b", "status": "SUBMITTED", "deduplicated": True, "reasons": []}, "deduplicated"),
    ({"batchId": "b", "status": "PARTIAL", "deduplicated": True, "reasons": []}, "deduplicated"),
    ({"batchId": None, "status": "REJECTED", "deduplicated": False,
      "reasons": ["BATCH_ALREADY_EXISTS_FOR_PHASE"]}, "refused"),
    ({"batchId": None, "status": "REJECTED", "deduplicated": False, "reasons": ["CALENDAR_UNVERIFIED"]}, "refused"),
    ({"batchId": None, "status": "REJECTED", "deduplicated": False, "reasons": ["DRAWDOWN_STOP"]}, "rejected"),
    ({"batchId": "b", "status": "QUEUED", "deduplicated": False, "reasons": []}, "submitted"),
])
def test_submit_reports_backend_outcomes(tmp_path, monkeypatch, response, status):
    ctx = _paper_ctx(tmp_path, monkeypatch)
    account = backend_client.AccountState("user-1", 1e8, 1e8, {}, {}, datetime.now(UTC))
    fake = FakeClient(account)
    fake.submit_batch = lambda payload: response
    result = runtime.submit_command(ctx, "hf001_f_both", "2026-11-02", "OPEN", execute=True,
                                    client_factory=lambda: fake)
    assert result["status"] == status and result["batchStatus"] == response["status"]
    assert result["reasons"] == response["reasons"]


def test_backend_client_accepts_stored_batch_statuses(monkeypatch):
    monkeypatch.setenv("BACKEND_INTERNAL_BASE_URL", "http://backend.invalid")
    monkeypatch.setenv("HQA_INTERNAL_TOKEN", "t")
    for status in ("SUBMITTING", "SUBMITTED", "PARTIAL", "COMPLETED", "HALTED", "REJECTED", "VALIDATED", "QUEUED"):
        body = {"status": status, "deduplicated": True}
        assert backend_client.StrategyBackendClient(session=FakeSession(body)).submit_batch({}) == body

    class Conflict(FakeSession):
        def post(self, url, json=None, headers=None, timeout=None):
            return FakeResponse(self.body, status=409)

    body = {"status": "REJECTED", "reasons": ["BATCH_ALREADY_EXISTS_FOR_PHASE"]}
    assert backend_client.StrategyBackendClient(session=Conflict(body)).submit_batch({}) == body
    with pytest.raises(backend_client.BackendError, match="HTTP"):
        backend_client.StrategyBackendClient(session=Conflict({"error": "x"})).submit_batch({})
