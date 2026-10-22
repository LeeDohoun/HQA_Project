from __future__ import annotations

import configparser
import json
from datetime import UTC, datetime

import pytest

from src.live import backend_client, readiness, runtime, shadow
from test_live_fixtures import REPO, make_repo, write_flat_days

KST = shadow.KST
DAY = "2026-10-27"
DEPLOY = REPO / "deploy/strategy-runtime"
ENV = {"HQA_READINESS_USER_ID": "comp"}


def at(hhmm, day=DAY):
    hour, minute = map(int, hhmm.split(":"))
    return datetime.fromisoformat(day).replace(hour=hour, minute=minute, tzinfo=KST)


class Backend:
    def __init__(self, *, holdings=None, orders=None, accounts=None, filled=1, open_orders=None):
        self.holdings = dict(holdings or {})
        self.orders = list(orders or [])
        self.accounts_list = accounts if accounts is not None else [
            {"userId": "are01", "strategyId": "pf001", "enabled": False},
            {"userId": "comp", "strategyId": "op001_readiness", "enabled": True}]
        self.filled = filled
        self.open_orders = open_orders
        self.batches, self.snapshots = [], 0

    def accounts(self):
        return self.accounts_list

    def fetch_account(self, user_id):
        assert user_id == "comp"
        self.snapshots += 1
        return backend_client.AccountState(user_id, 1e7, 1e7, dict(self.holdings), dict(self.holdings),
                                           datetime.now(UTC))

    def broker_orders(self, user_id, day):
        open_count = sum(1 for o in self.orders if not o.get("cancelled") and o.get("remainingQty", 0) > 0)
        return {"date": day, "orders": self.orders,
                "openOrders": open_count if self.open_orders is None else self.open_orders}

    def submit_batch(self, payload):
        self.batches.append(payload)
        return {"batchId": f"b{len(self.batches)}", "status": "QUEUED", "deduplicated": False, "reasons": []}

    def get_batch(self, batch_id):
        side = "BUY" if batch_id == "b1" else "SELL"
        return {"batchId": batch_id, "status": "COMPLETED", "sizing": {"orderableCashBefore": 1e7,
                                                                       "orderableCashAfter": 9.8e6},
                "orders": [{"seq": 1, "stockCode": "005380", "side": side, "quantity": self.filled,
                            "status": "FILLED", "brokerOrderId": f"odno-{batch_id}", "filledQuantity": self.filled,
                            "averageFillPrice": 200000.0}]}


@pytest.fixture
def ctx(tmp_path):
    root = make_repo(tmp_path, early_paper=True)
    data = tmp_path / "data"
    write_flat_days(data, "2026-10-12", "2026-10-26", ["005380", "005930"], price=200000)
    return runtime.load_context(root, data)


def run(ctx, step, fake, hhmm, day=DAY, env=ENV):
    return runtime.readiness_command(ctx, "op001_readiness", step, execute=True, client_factory=lambda: fake,
                                     now=at(hhmm, day), env=env)


def test_dry_run_user_and_strategy_rules(ctx):
    assert runtime.readiness_command(ctx, "op001_readiness", "buy", env={})["reasons"] == ["readiness_user_missing"]
    dry = runtime.readiness_command(ctx, "op001_readiness", "buy", user="comp", env={})
    assert dry["status"] == "dry_run" and dry["stock"] == "005380"
    with pytest.raises(ValueError):
        runtime.readiness_command(ctx, "pf001", "buy", env=ENV)


def test_prewarm_is_read_only(ctx):
    fake = Backend(holdings={"005930": 2})
    result = run(ctx, "prewarm", fake, "08:45")
    assert result["status"] == "ok" and fake.batches == [] and fake.snapshots == 1
    assert json.loads(open(result["record"], encoding="utf-8").read())["holdings"] == ["005930"]


def test_buy_one_share_005380_and_ignore_foreign_005930(ctx):
    fake = Backend(holdings={"005930": 3}, orders=[{"odno": "x1", "stockCode": "005930", "side": "BUY",
                                                     "orderQty": 1, "filledQty": 0, "remainingQty": 1,
                                                     "cancelled": False}])
    result = run(ctx, "buy", fake, "09:19")
    assert result["status"] == "submitted"
    (batch,) = fake.batches
    assert batch["phase"] == "CONT_BUY" and batch["userId"] == "comp" and "exemption" not in batch
    assert batch["orders"] == [{"seq": 1, "stockCode": "005380", "side": "BUY", "quantity": 1, "refPrice": 200000,
                                "adv20Krw": None}]


@pytest.mark.parametrize("setup, reason", [
    ("marker", "block_marker:blocked_20261026.json"),
    ("pf001_enabled", "pf001_account_enabled"),
    ("extra_account", "unexpected_strategy_account:hf001_f_both"),
    ("own_holding", "own_stock_already_held:005380:1"),
    ("own_open_order", "own_stock_open_orders:005380"),
    ("not_bound", "readiness_account_not_bound"),
])
def test_buy_refusals_alert_readiness_blocked(ctx, setup, reason):
    fake = Backend(holdings={"005930": 3})
    if setup == "marker":
        path = readiness.marker_path(ctx.data_dir, "2026-10-26")
        path.parent.mkdir(parents=True)
        path.write_text("{}", encoding="utf-8")
    elif setup == "pf001_enabled":
        fake.accounts_list[0]["enabled"] = True
    elif setup == "extra_account":
        fake.accounts_list.append({"userId": "x", "strategyId": "hf001_f_both", "enabled": False})
    elif setup == "own_holding":
        fake.holdings["005380"] = 1
    elif setup == "own_open_order":
        fake.orders = [{"odno": "y", "stockCode": "005380", "remainingQty": 1, "cancelled": False}]
    else:
        fake.accounts_list = fake.accounts_list[:1]
    result = run(ctx, "buy", fake, "09:19")
    assert result["status"] == "refused" and reason in result["reasons"] and fake.batches == []
    assert [a["code"] for a in result["alerts"]["alerts"]] == ["readiness_blocked"]
    lines = (ctx.data_dir / "live/alerts/20261027.jsonl").read_text(encoding="utf-8")
    assert "readiness_blocked" in lines


def test_same_day_marker_does_not_block_and_flatten_sells_only_own_fill(ctx):
    fake = Backend(holdings={"005930": 7}, filled=1)
    assert run(ctx, "buy", fake, "09:19")["status"] == "submitted"
    flat = run(ctx, "flatten", fake, "15:22")
    assert flat["status"] == "submitted" and flat["quantity"] == 1
    sell = fake.batches[-1]
    assert sell["phase"] == "CLOSE" and sell["orders"][0]["stockCode"] == "005380"
    assert sell["orders"][0]["quantity"] == 1 and sell["orders"][0]["side"] == "SELL" and "exemption" not in sell
    record = json.loads(open(flat["record"], encoding="utf-8").read())
    assert record["buy"]["orders"][0]["brokerOrderId"] == "odno-b1"
    assert record["buy"]["sizing"] == {"orderableCashBefore": 1e7, "orderableCashAfter": 9.8e6}


def test_flatten_without_fill_submits_nothing(ctx):
    fake = Backend(filled=0)
    run(ctx, "buy", fake, "09:19")
    assert run(ctx, "flatten", fake, "15:22")["reason"] == "buy_batch_filled_nothing" and len(fake.batches) == 1


def _verify(ctx, fake, day=DAY):
    run(ctx, "buy", fake, "09:19", day=day)
    run(ctx, "flatten", fake, "15:22", day=day)
    return run(ctx, "verify", fake, "15:35", day=day)


def test_verify_clean_account_passes(ctx):
    result = _verify(ctx, Backend())
    assert result["status"] == "ok" and "marker" not in result and "alerts" not in result
    record = json.loads(open(result["record"], encoding="utf-8").read())
    assert record["ownOdnos"] == ["odno-b1", "odno-b2"] and record["own"]["buy"]["sizing"]["orderableCashAfter"]
    assert not readiness.marker_path(ctx.data_dir, DAY).exists()


def test_verify_m8_leftover_blocks_next_buy(ctx):
    fake = Backend()
    run(ctx, "buy", fake, "09:19")
    run(ctx, "flatten", fake, "15:22")
    fake.holdings = {"005380": 1}
    result = run(ctx, "verify", fake, "15:35")
    assert result["status"] == "leftover" and result["leftovers"][0]["owner"] == "M8"
    assert readiness.marker_path(ctx.data_dir, DAY).is_file()
    alert = result["alerts"]["alerts"][0]
    assert alert["code"] == "readiness_leftover" and alert["details"]["owners"] == ["M8"]
    assert alert["details"]["blocksNextBuy"] is True
    nxt = Backend()
    refused = run(ctx, "buy", nxt, "09:19", day="2026-10-28")
    assert refused["reasons"] == ["block_marker:blocked_20261027.json"] and nxt.batches == []


def test_verify_xo001_only_leftover_alerts_without_blocking(ctx):
    fake = Backend()
    run(ctx, "buy", fake, "09:19")
    run(ctx, "flatten", fake, "15:22")
    fake.holdings = {"005930": 2}
    fake.orders = [{"odno": "x9", "stockCode": "005930", "remainingQty": 1, "cancelled": False}]
    result = run(ctx, "verify", fake, "15:35")
    assert {i["owner"] for i in result["leftovers"]} == {"XO001"}
    assert result["alerts"]["alerts"][0]["details"]["owners"] == ["XO001"]
    assert result["alerts"]["alerts"][0]["details"]["blocksNextBuy"] is False
    assert not readiness.marker_path(ctx.data_dir, DAY).exists()
    assert run(ctx, "buy", Backend(), "09:19", day="2026-10-28")["status"] == "submitted"


def test_verify_unknown_leftover_and_own_odno_labels(ctx):
    fake = Backend()
    run(ctx, "buy", fake, "09:19")
    run(ctx, "flatten", fake, "15:22")
    fake.holdings = {"035720": 4}
    fake.orders = [{"odno": "odno-b2", "stockCode": "123456", "remainingQty": 1, "cancelled": False}]
    result = run(ctx, "verify", fake, "15:35")
    owners = {(i["kind"], i["owner"]) for i in result["leftovers"]}
    assert owners == {("holding", "unknown"), ("open_order", "M8")}
    assert readiness.marker_path(ctx.data_dir, DAY).is_file()


def _unit(name):
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str
    parser.read(DEPLOY / "systemd" / name, encoding="utf-8")
    return parser


@pytest.mark.parametrize("step, at_time", [("prewarm", "08:45:00"), ("buy", "09:19:30"), ("flatten", "15:22:00"),
                                           ("verify", "15:35:00")])
def test_readiness_timers_fire_only_on_the_four_dates(step, at_time):
    timer = _unit(f"hqa-op001-readiness-{step}.timer")["Timer"]
    assert timer["OnCalendar"] == f"2026-10-27..30 {at_time} Asia/Seoul"
    assert timer["Persistent"] == "false" and timer["Unit"] == f"hqa-op001-readiness-{step}.service"
    service = _unit(f"hqa-op001-readiness-{step}.service")
    submit = _unit("hqa-strategy-runtime-submit.service")
    assert service["Service"]["ExecStart"].endswith(f"deploy/strategy-runtime/readiness.sh {step}")
    for key in ("User", "Group", "WorkingDirectory", "EnvironmentFile", "NoNewPrivileges", "ProtectSystem",
                "ReadWritePaths", "PrivateTmp", "MemoryMax"):
        assert service["Service"][key] == submit["Service"][key], key
    script = (DEPLOY / "readiness.sh").read_text(encoding="utf-8")
    assert "run.sh" not in script and "pull_collector_data" not in script and "--execute" in script
