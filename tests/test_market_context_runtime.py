import hashlib
import json
from collections import Counter
from datetime import datetime, timedelta

import pytest

from src.runner.corporate_actions import build_corporate_action_context
from test_shared_analysis import Accounts, EventData, Model, NOW, buy_plan, holding, service, snapshot


class BenchmarkData(EventData):
    def __init__(self, *, corporate_action=False, malformed=False):
        super().__init__()
        self.bars = [self.bar(datetime.fromisoformat(row["available_at"]).date(), 100.0)
                     for row in self.bars]
        for offset, close in enumerate((101.0, 102.0, 103.0), start=1):
            index = len(self.bars) - 4 + offset
            self.bars[index] = self.bar(datetime.fromisoformat(self.bars[index]["available_at"]).date(), close)
        self.bar_version = "v1"
        self.mapping_version = "v1"
        self.corporate_action = corporate_action
        self.malformed = malformed

    def load_market_context(self, candidate, as_of):
        scopes = {}
        for kind, index_name in (("market", "KOSPI"), ("sector", "Verified sector")):
            bars = []
            for index, stock in enumerate(self.bars):
                day = datetime.fromisoformat(stock["available_at"]).date().isoformat()
                close = 1000.0
                if index >= len(self.bars) - 3:
                    close += (index - len(self.bars) + 4) * (10.0 if kind == "sector" else 40.0 / 3)
                version = self.bar_version if index == len(self.bars) - 1 else "v1"
                bars.append({"series": "KOSPI", "index_name": index_name, "trade_date": day,
                    "bar_at": stock["available_at"], "available_at": stock["available_at"], "close": close,
                    "source_id": f"benchmark:{kind}:{day}:{version}", "version": version,
                    "price_basis": "price_index"})
            scopes[kind] = {"status": "ready", "series": "KOSPI", "index_name": index_name,
                "mapping_source_id": f"mapping:{kind}", "mapping_version": self.mapping_version,
                "mapping_available_at": self.bars[0]["available_at"], "effective_from": "2026-01-01",
                "effective_to": None, "bars": bars}
        if self.malformed:
            scopes["market"]["bars"][-1]["close"] = "unverified"
        return scopes

    def load_evidence(self, candidate, as_of):
        evidence = super().load_evidence(candidate, as_of)
        documents = list(evidence["documents"])
        if self.corporate_action:
            receipt = "20260901000002"
            # The calendar guard consumes full evidence, not only selected event packets.
            documents.append({**documents[0], "source_id": "corporate:outside-event-packet",
                "title": "\ubb34\uc0c1\uc99d\uc790\uacb0\uc815",
                "url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={receipt}",
                "metadata": {"rcept_no": receipt, "structured_rcept_no": receipt,
                    "structured_endpoint": "fricDecsn", "structured_body_error_type": "structured_too_short",
                    "structured_row": {"rcept_no": receipt, "nstk_asstd": "2026-09-15",
                                       "nstk_lstprd": "2026-09-30"}}})
        evidence["corporate_actions"] = build_corporate_action_context(documents, as_of)
        return evidence


class ScopedAccounts(Accounts):
    def __init__(self, *, held=True):
        super().__init__()
        self.held = held

    def fetch_accounts(self, ids):
        return {user: snapshot(user, [holding()] if self.held else []).model_copy(update={
            "equity": 20000.0 if user == "account-a" else 50000.0}) for user in ids}


class BenchmarkCitingModel(Model):
    def invoke(self, messages):
        result = super().invoke(messages).model_dump()
        payload = json.loads(messages[-1][1])
        rows = payload["candidates"] if self.role == "risk_manager" else [payload]
        outputs = result["plans"] if self.role == "risk_manager" else [result]
        for row, output in zip(rows, outputs):
            source = row["event_reactions"][0]["benchmark_comparison"]["source_id"]
            output["citations"].append({"source_id": source, "claim": "Same-date index comparison"})
        return self.schema.model_validate(result)


class ActionModel(Model):
    def __init__(self, calls, action):
        super().__init__("risk_manager", calls)
        self.action = action

    def invoke(self, messages):
        result = super().invoke(messages).model_dump()
        if self.action == "BUY":
            plan = buy_plan()
            plan["citations"] = result["plans"][0]["citations"]
            result["plans"] = [plan]
        else:
            for plan in result["plans"]:
                plan["action"] = self.action
        return self.schema.model_validate(result)


def test_benchmark_citations_reach_chartist_and_isolated_account_reviews(tmp_path):
    from src.tracing.paper_audit import PaperAudit

    engine, calls = service(BenchmarkData(), ScopedAccounts())
    engine.audit = PaperAudit(tmp_path / "market-audit.sqlite3")
    for role in ("chartist", "risk_manager"):
        engine.models[role] = BenchmarkCitingModel(role, calls)
    cycle = engine.run_cycle([{"userId": "account-a", "investorProfile": {"private_marker": "profile-a"}},
                              {"userId": "account-b", "investorProfile": {"private_marker": "profile-b"}}])
    assert cycle["errors"] == []
    assert all(row["status"] == "completed" for row in cycle["accounts"].values())
    assert Counter(role for role, _ in calls) == {"analyst": 1, "quant": 1, "chartist": 1, "risk_manager": 2}
    chart = next(payload for role, payload in calls if role == "chartist")
    comparison = chart["event_reactions"][0]["benchmark_comparison"]
    market = comparison["market"]["horizons"]["3"]
    assert market["status"] == "observed"
    assert market["index_return_pct"] == pytest.approx(4.0)
    assert market["excess_return_pp"] == pytest.approx(-1.0)
    expected_sources = {"mapping:market", "benchmark:market:2026-08-31:v1", "benchmark:market:2026-09-03:v1"}
    derived_source = comparison["source_id"]
    assert derived_source.startswith("benchmark-comparison:") and derived_source in chart["source_ids"]
    assert not expected_sources & set(chart["source_ids"])
    assert "source_ids" not in market and "benchmark_observations" not in market
    assert chart["reaction_contract"]["corporate_action_adjustment"] == "unverified"
    audited = engine.audit.read("benchmark_context")
    assert len(audited) == 1
    provenance = audited[0]["payload"]
    assert provenance["source_id"] == derived_source
    assert expected_sources <= set(provenance["comparison"]["source_ids"])
    observations = provenance["comparison"]["market"]["horizons"]["3"]["benchmark_observations"]
    assert observations["baseline"]["source_id"] == "benchmark:market:2026-08-31:v1"
    assert observations["endpoint"]["source_id"] == "benchmark:market:2026-09-03:v1"
    for role, payload in calls:
        if role != "risk_manager":
            assert "account" not in payload and "investor_profile" not in payload
            assert all(marker not in json.dumps(payload) for marker in ("account-a", "account-b", "profile-a", "profile-b"))
            continue
        user = payload["account"]["userId"]
        assert payload["account"]["equity"] == (20000.0 if user == "account-a" else 50000.0)
        assert payload["investor_profile"] == {"private_marker": "profile-" + user[-1]}
        candidate = payload["candidates"][0]
        compact_comparison = candidate["event_reactions"][0]["benchmark_comparison"]
        compact = compact_comparison["market"]
        assert compact_comparison["projection"] == "latest_observation_only"
        assert compact_comparison["omitted_horizons"] == ["1", "3", "5"]
        assert compact["excess_return_pp"] == market["excess_return_pp"]
        assert "benchmark_observations" not in compact
        assert derived_source in candidate["source_ids"]
        assert derived_source in {c["source_id"] for c in cycle["accounts"][user]["plans"][0]["citations"]}


@pytest.mark.parametrize("changed", ["bar_version", "mapping_version"])
def test_benchmark_revision_only_invalidates_chartist_and_clock_ticks_do_not(changed):
    data = BenchmarkData()
    engine, calls = service(data)
    first = engine.run_cycle([], as_of=NOW)
    warm = engine.run_cycle([], as_of=NOW + timedelta(minutes=15))
    assert first["errors"] == warm["errors"] == []
    assert first["manifest"]["role_input_hashes"] == warm["manifest"]["role_input_hashes"]
    assert len(calls) == 3
    setattr(data, changed, "v2")
    updated = engine.run_cycle([], as_of=NOW + timedelta(minutes=30))
    assert updated["errors"] == []
    old_hashes, new_hashes = first["manifest"]["role_input_hashes"], updated["manifest"]["role_input_hashes"]
    assert old_hashes["000001:analyst"] == new_hashes["000001:analyst"]
    assert old_hashes["000001:quant"] == new_hashes["000001:quant"]
    assert old_hashes["000001:chartist"] != new_hashes["000001:chartist"]
    assert Counter(role for role, _ in calls) == {"analyst": 1, "quant": 1, "chartist": 2}


def test_unverified_mechanical_action_blocks_model_buy_even_outside_event_packet():
    engine, calls = service(BenchmarkData(corporate_action=True), ScopedAccounts(held=False))
    engine.models["risk_manager"] = ActionModel(calls, "BUY")
    cycle = engine.run_cycle([{"userId": "account-a"}])
    assert cycle["errors"] == []
    assert cycle["completed_stock_count"] == 1
    account = cycle["accounts"]["account-a"]
    assert account["status"] == "failed" and account["plans"] == []
    assert "unverified_corporate_action_price_basis" in account["error"]
    analyst = next(payload for role, payload in calls if role == "analyst")
    risk = next(payload for role, payload in calls if role == "risk_manager")["candidates"][0]
    assert "corporate:outside-event-packet" not in analyst["source_ids"]
    assert risk["price_safety"]["entry_block_reasons"] == ["unverified_corporate_action_price_basis"]
    assert "corporate:outside-event-packet" in risk["source_ids"]
    assert {event["date_kind"] for event in risk["corporate_actions"]["upcoming_events"]} == {
        "record_date", "expected_listing_date"}


@pytest.mark.parametrize("action", ["HOLD", "SELL"])
def test_corporate_entry_guard_preserves_existing_position_protection(action):
    engine, calls = service(BenchmarkData(corporate_action=True), ScopedAccounts())
    engine.models["risk_manager"] = ActionModel(calls, action)
    cycle = engine.run_cycle([{"userId": "account-a"}])
    assert cycle["errors"] == []
    account = cycle["accounts"]["account-a"]
    assert account["status"] == "completed"
    plan = account["plans"][0]
    assert plan["action"] == action and plan["holding_quantity"] == 4
    assert plan["condition_payload"]["exit_conditions"][0]["all"] == [
        {"field": "current_price", "operator": "<=", "value": 95.0}]


@pytest.mark.parametrize("held", [False, True])
def test_malformed_benchmark_blocks_new_analysis_without_dropping_holdings(held):
    engine, calls = service(BenchmarkData(malformed=True), ScopedAccounts(held=held))
    cycle = engine.run_cycle([{"userId": "account-a"}])
    assert cycle["completed_stock_count"] == 0
    assert any(error["stage"] == "specialist_input" and "benchmark.close" in error["error"]
               for error in cycle["errors"])
    assert not any(role == "chartist" for role, _ in calls)
    account = cycle["accounts"]["account-a"]
    assert account["status"] == "completed"
    if held:
        assert account["plans"][0]["holding_quantity"] == 4
        assert account["plans"][0]["condition_payload"]["exit_conditions"]
        risk = next(payload for role, payload in calls if role == "risk_manager")
        assert risk["candidates"][0]["leader_score"] is None
        assert risk["candidates"][0]["specialist_errors"]
    else:
        assert account["plans"] == []
        assert not any(role == "risk_manager" for role, _ in calls)


@pytest.mark.parametrize("flag,expected", [
    ("is_correction", {"correction", "unlinked_correction"}),
    ("is_withdrawal", {"withdrawal"}),
    ("has_correction", {"subsequent_correction_reported"}),
])
def test_risk_event_projection_preserves_disclosure_correction_and_withdrawal_flags(flag, expected):
    from src.runner.event_evidence import build_event_evidence

    class ChangedDisclosureData(BenchmarkData):
        def load_evidence(self, candidate, as_of):
            evidence = super().load_evidence(candidate, as_of)
            evidence["documents"][0]["metadata"][flag] = True
            evidence["events"] = build_event_evidence(evidence["documents"], candidate["stock_code"])
            return evidence

    engine, calls = service(ChangedDisclosureData(), ScopedAccounts())
    cycle = engine.run_cycle([{"userId": "account-a"}])
    assert cycle["errors"] == []
    assert cycle["accounts"]["account-a"]["status"] == "completed"
    candidate = next(payload for role, payload in calls if role == "risk_manager")["candidates"][0]
    assert "events" not in candidate
    assert expected <= set(candidate["event_reactions"][0]["risk_flags"])


class FullBenchmarkData(BenchmarkData):
    def __init__(self):
        super().__init__()
        self.count = 6

    @staticmethod
    def digest(value):
        return hashlib.sha256(value.encode()).hexdigest()

    def load_evidence(self, candidate, as_of):
        from src.runner.event_evidence import build_event_evidence

        evidence = super().load_evidence(candidate, as_of)
        base = evidence["documents"][0]
        documents = []
        for index in range(8):
            receipt = f"202608{20 + index:02}000001"
            text = ("\uacf5\uc2dc\ub41c \uacf5\uae09\uacc4\uc57d\uc758 \uaddc\ubaa8\uc640 \ub300\uae08 \uc9c0\uae09 \uc870\uac74 \ubc0f \uacc4\uc57d \ubcc0\uacbd \uc704\ud5d8\uc744 \ud655\uc778\ud569\ub2c8\ub2e4. " * 80)[:2400]
            documents.append({**base,
                "source_id": self.digest(candidate["stock_code"] + receipt) + ":" + self.digest(text)[:16],
                "title": f"\ub2e8\uc77c\ud310\ub9e4 \uacf5\uae09\uacc4\uc57d \uccb4\uacb0 {index}",
                "text": text, "truncated": True, "original_characters": 4000,
                "source_text_hash": self.digest(text),
                "published_at": f"2026-08-{20 + index:02}T00:00:00+09:00",
                "available_at": f"2026-08-{20 + index:02}T10:00:00+09:00",
                "url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={receipt}",
                "metadata": {"published_at_precision": "date", "rcept_no": receipt}})
        evidence["documents"] = documents
        evidence["events"] = [build_event_evidence([document], candidate["stock_code"])[0] for document in documents]
        evidence["corporate_actions"] = build_corporate_action_context(documents, as_of)
        return evidence

    def load_market_context(self, candidate, as_of):
        result = super().load_market_context(candidate, as_of)
        for kind, scope in result.items():
            scope.update(mapping_source_id="mapping:" + self.digest(kind), mapping_version=self.digest(kind))
            for row in scope["bars"]:
                row["source_id"] = "krx-benchmark:" + self.digest(row["source_id"])
                row["version"] = self.digest(row["version"])
        return result


def test_risk_prefers_flagged_events_and_discloses_risks_from_omitted_events():
    from src.runner.event_evidence import build_event_evidence

    class FlaggedData(FullBenchmarkData):
        def __init__(self):
            super().__init__()
            self.count = 1

        def load_evidence(self, candidate, as_of):
            evidence = super().load_evidence(candidate, as_of)
            documents = evidence["documents"]
            documents[3]["metadata"]["is_withdrawal"] = True
            documents[4]["metadata"]["has_correction"] = True
            documents[5]["title"] = "\uc804\ud658\uc0ac\ucc44\uad8c \ubc1c\ud589\uacb0\uc815"
            documents[7]["metadata"]["is_correction"] = True
            evidence["events"] = [build_event_evidence([document], candidate["stock_code"])[0]
                                  for document in documents]
            return evidence

    engine, calls = service(FlaggedData(), ScopedAccounts())
    cycle = engine.run_cycle([{"userId": "account-a"}])
    assert cycle["errors"] == []
    assert cycle["accounts"]["account-a"]["status"] == "completed"
    chart = next(payload for role, payload in calls if role == "chartist")
    risk = next(payload for role, payload in calls if role == "risk_manager")["candidates"][0]
    assert len(chart["event_reactions"]) == 8
    assert [event["event_id"] for event in risk["event_reactions"]] == [
        event["event_id"] for event in chart["event_reactions"][3:6]]
    assert risk["omitted_event_count"] == 5
    assert {"withdrawal", "subsequent_correction_reported", "dilution_review",
            "correction", "unlinked_correction"} <= set(risk["event_risk_flags_all"])
    assert not any("unlinked_correction" in event["risk_flags"] for event in risk["event_reactions"])
    selected = {event["event_id"] for event in risk["event_reactions"]}
    for event in chart["event_reactions"]:
        benchmark_source = event["benchmark_comparison"]["source_id"]
        assert (benchmark_source in risk["source_ids"]) == (event["event_id"] in selected)


@pytest.fixture
def full_benchmark_prompts():
    messages_by_role = {}

    class CapturingModel(Model):
        def invoke(self, messages):
            messages_by_role[self.role] = messages
            output = super().invoke(messages)
            if self.role == "risk_manager":
                return output
            values = output.model_dump()
            values["risks"] = [("Contract execution delays may affect realized revenue and price response. " * 14).strip()
                               for _ in range(3)]
            return self.schema.model_validate(values)

    engine, calls = service(FullBenchmarkData(), ScopedAccounts())
    engine.models = {role: CapturingModel(role, calls) for role in ("analyst", "quant", "chartist", "risk_manager")}
    cycle = engine.run_cycle([{"userId": "account-a"}])
    assert cycle["errors"] == []
    assert cycle["accounts"]["account-a"]["status"] == "completed"
    return messages_by_role


@pytest.mark.parametrize("role", ["chartist", "risk_manager"])
def test_max_event_benchmark_prompts_fit_role_input_budget_offline(role, full_benchmark_prompts, monkeypatch):
    from src.agents.llm_config import get_role_limits
    from src.runner.analysis_contracts import AccountDecision, SpecialistResult

    tiktoken = pytest.importorskip("tiktoken")
    import tiktoken.load

    def unavailable_cache(_):
        pytest.skip("Local tokenizer cache unavailable; this regression test never downloads tokenizers")

    with monkeypatch.context() as local:
        local.setattr(tiktoken.load, "read_file", unavailable_cache)
        encoding = tiktoken.get_encoding("o200k_base")
    messages = full_benchmark_prompts[role]
    payload = json.loads(messages[-1][1])
    rows = payload["candidates"] if role == "risk_manager" else [payload]
    assert len(rows) == (6 if role == "risk_manager" else 1)
    for row in rows:
        assert len(row["event_reactions"]) == (3 if role == "risk_manager" else 8)
        if role == "risk_manager":
            assert row["omitted_event_count"] == 5
            assert row["event_risk_flags_all"] == []
            for specialist in row["specialists"].values():
                specialist_tokens = len(encoding.encode(json.dumps(specialist, ensure_ascii=False)))
                assert 500 <= specialist_tokens < 1200
        for reaction in row["event_reactions"]:
            for kind in ("market", "sector"):
                comparison = reaction["benchmark_comparison"][kind]
                if role == "chartist":
                    assert all(window["status"] == "observed" for window in comparison["horizons"].values())
                    assert comparison["latest"]["status"] == "observed"
                else:
                    assert comparison["status"] == "observed"
                    assert reaction["benchmark_comparison"]["projection"] == "latest_observation_only"
                    assert reaction["benchmark_comparison"]["omitted_horizons"] == ["1", "3", "5"]
    if role == "chartist":
        assert len(payload["recent_ohlcv"]) == 20
    schema = SpecialistResult if role == "chartist" else AccountDecision
    # This is an offline regression estimate, not a substitute for provider admission counting.
    wire = json.dumps({"messages": messages, "schema": schema.model_json_schema()}, ensure_ascii=False)
    estimated_tokens = len(encoding.encode(wire))
    limit = get_role_limits(role).input_tokens
    assert estimated_tokens < limit, f"{role} offline estimate {estimated_tokens} exceeds {limit}"
    if role == "risk_manager":
        assert limit - estimated_tokens >= 5000
