from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

import pytest

from src.utils.llm_budget import LLMBudgetAccountingError, LLMBudgetLedger

NOW = datetime(2026, 9, 28, 1, tzinfo=timezone.utc)


def ledger(path):
    return LLMBudgetLedger(path, now=lambda: NOW)


def overrun(book):
    request = book.reserve("risk_manager", 10, 10)
    book.mark_sent(request)
    with pytest.raises(LLMBudgetAccountingError):
        book.settle(request, input_tokens=10_000, output_tokens=10_000)
    return request


def test_overrun_blocks_until_an_operator_records_the_review(tmp_path):
    book = ledger(tmp_path / "budget.sqlite3")
    request = overrun(book)
    assert book.snapshot()["accounting_blocked"] is True
    with pytest.raises(LLMBudgetAccountingError):
        book.reserve("analyst", 100, 100, critical=True)
    assert [row["request_id"] for row in book.overruns()] == [request]
    with pytest.raises(ValueError):
        book.acknowledge_overrun(request, "  ")
    book.acknowledge_overrun(request, "price table corrected for 2026-09 rates")
    assert book.snapshot()["accounting_blocked"] is False and book.overruns() == []
    book.reserve("analyst", 100, 100)  # calls resume
    with pytest.raises(LLMBudgetAccountingError):
        book.acknowledge_overrun(book.reserve("quant", 1, 1), "not an overrun")


def test_unknown_requests_stay_reserved_until_settled_from_provider_usage(tmp_path):
    book = ledger(tmp_path / "budget.sqlite3")
    request = book.reserve("analyst", 1_000, 1_000)
    book.mark_sent(request)
    book.mark_unknown(request)
    assert [row["request_id"] for row in book.unresolved()] == [request]
    reserved = book.snapshot()["reserved_usd"]
    assert reserved > 0
    book.settle(request, input_tokens=0, output_tokens=0)  # provider shows no charge
    assert book.unresolved() == [] and book.snapshot()["reserved_usd"] == 0


def test_existing_ledgers_are_migrated_in_place(tmp_path):
    path = tmp_path / "old.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("""CREATE TABLE llm_spend (request_id TEXT PRIMARY KEY, month TEXT NOT NULL, role TEXT NOT NULL,
            model TEXT NOT NULL, price_version TEXT NOT NULL, state TEXT NOT NULL, reserved_nano INTEGER NOT NULL,
            actual_nano INTEGER, input_tokens INTEGER, output_tokens INTEGER, cached_tokens INTEGER,
            cache_write_tokens INTEGER, reasoning_tokens INTEGER, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
        db.execute("INSERT INTO llm_spend VALUES ('old','2026-09','analyst','gpt-5.6-luna','v','settled',5,9,1,1,0,0,0,'t','t')")
    book = ledger(path)
    assert book.snapshot()["accounting_blocked"] is True  # the historic overrun is still visible
    book.acknowledge_overrun("old", "reviewed legacy row")
    assert book.snapshot()["accounting_blocked"] is False


def test_operator_cli_reports_and_reconciles(tmp_path, capsys, monkeypatch):
    from scripts import llm_budget as cli

    monkeypatch.setattr(cli, "load_project_env", lambda: None)
    path = tmp_path / "budget.sqlite3"
    book = ledger(path)
    request = book.reserve("analyst", 100, 100)
    book.mark_sent(request)
    assert cli.main(["--path", str(path), "status"]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["unresolved"][0]["request_id"] == request
    assert cli.main(["--path", str(path), "settle", request, "--input-tokens", "90", "--output-tokens", "40"]) == 0
    settled = json.loads(capsys.readouterr().out)
    assert settled["unresolved"] == [] and settled["snapshot"]["spent_usd"] > 0
    with pytest.raises(SystemExit):
        cli.main(["--path", str(tmp_path / "missing.sqlite3"), "status"])
