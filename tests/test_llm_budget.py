from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest

from src.utils.llm_budget import (
    LLMBudgetAccountingError,
    LLMBudgetExceeded,
    LLMBudgetLedger,
)


def test_parallel_reservations_cannot_overspend(tmp_path):
    path = tmp_path / "spend.sqlite3"
    ledger = LLMBudgetLedger(path, monthly_limit_usd="0.001", operating_target_usd="0.001")

    def reserve(_):
        try:
            return ledger.reserve("quant", 1000, 0)
        except LLMBudgetExceeded:
            return None

    with ThreadPoolExecutor(max_workers=12) as pool:
        ids = list(pool.map(reserve, range(30)))
    assert sum(value is not None for value in ids) == 4
    assert ledger.snapshot()["reserved_usd"] == 0.001


def test_settlement_accounts_for_cache_writes_reads_and_reasoning_once(tmp_path):
    ledger = LLMBudgetLedger(tmp_path / "spend.sqlite3")
    request_id = ledger.reserve("risk_manager", 1000, 400)
    ledger.mark_sent(request_id)
    usage = dict(input_tokens=1000, output_tokens=400, cached_tokens=200, cache_write_tokens=300, reasoning_tokens=250)
    ledger.settle(request_id, **usage)
    ledger.settle(request_id, **usage)
    assert ledger.snapshot()["spent_usd"] == 0.000659
    assert ledger.snapshot()["reserved_usd"] == 0


def test_sent_timeout_reservation_survives_restart_and_month_change(tmp_path):
    path = tmp_path / "spend.sqlite3"
    old = LLMBudgetLedger(path, now=lambda: datetime(2026, 9, 30, tzinfo=timezone.utc))
    request_id = old.reserve("analyst", 1000, 400)
    old.mark_sent(request_id)
    old.mark_unknown(request_id)
    with pytest.raises(LLMBudgetAccountingError):
        old.release_unsent(request_id)
    restarted = LLMBudgetLedger(path, now=lambda: datetime(2026, 10, 1, tzinfo=timezone.utc))
    assert restarted.snapshot()["month"] == "2026-10"
    assert restarted.snapshot()["reserved_usd"] == 0.00073
    assert restarted.snapshot()["unresolved_requests"] == 1
    restarted.settle(request_id, input_tokens=1000, output_tokens=100)
    assert restarted.snapshot()["spent_usd"] == 0.00032
    assert restarted.snapshot()["reserved_usd"] == 0


def test_only_unsent_reservation_can_be_released(tmp_path):
    ledger = LLMBudgetLedger(tmp_path / "spend.sqlite3")
    request_id = ledger.reserve("quant", 1000, 400)
    ledger.release_unsent(request_id)
    assert ledger.snapshot()["reserved_usd"] == 0
    with pytest.raises(LLMBudgetAccountingError):
        ledger.mark_sent(request_id)


def test_operating_target_preserves_headroom_for_critical_calls(tmp_path):
    ledger = LLMBudgetLedger(tmp_path / "spend.sqlite3", monthly_limit_usd="0.002", operating_target_usd="0.001")
    ledger.reserve("analyst", 4000, 0)
    with pytest.raises(LLMBudgetExceeded):
        ledger.reserve("quant", 1, 0)
    ledger.reserve("risk_manager", 4000, 0, critical=True)
    with pytest.raises(LLMBudgetExceeded):
        ledger.reserve("risk_manager", 1, 0, critical=True)


def test_unexpected_usage_overrun_is_recorded_and_blocks_more_calls(tmp_path):
    ledger = LLMBudgetLedger(tmp_path / "spend.sqlite3")
    request_id = ledger.reserve("quant", 100, 100)
    ledger.mark_sent(request_id)
    with pytest.raises(LLMBudgetAccountingError, match="exceeded"):
        ledger.settle(request_id, input_tokens=1000, output_tokens=1000)
    assert ledger.snapshot()["spent_usd"] == 0.0014
    assert ledger.snapshot()["accounting_blocked"]
    with pytest.raises(LLMBudgetAccountingError):
        ledger.reserve("analyst", 100, 100)


def test_budget_cannot_exceed_authorized_monthly_cap(tmp_path):
    with pytest.raises(ValueError, match="USD 100"):
        LLMBudgetLedger(tmp_path / "spend.sqlite3", monthly_limit_usd="101")


def test_claude_reservations_cover_the_fallback_attempt_and_settle_at_claude_rates(tmp_path):
    ledger = LLMBudgetLedger(tmp_path / "spend.sqlite3")
    request_id = ledger.reserve("risk_manager", 10_000, 16_000, model="claude-opus-5-5",
                                fallback_models=("claude-opus-5",))
    # Opus 5.5 (10k x $5/M cache-write bound + 16k x $20/M) plus Opus 5 (10k x $6.25/M + 16k x $25/M).
    assert ledger.snapshot()["reserved_usd"] == pytest.approx(0.05 + 0.32 + 0.0625 + 0.4)
    ledger.mark_sent(request_id)
    ledger.settle(request_id, input_tokens=10_000, output_tokens=3_000, reasoning_tokens=1_000)
    assert ledger.snapshot()["spent_usd"] == pytest.approx(0.04 + 0.06)
    assert ledger.snapshot()["models"] == ["claude-opus-5-5"] and ledger.snapshot()["reserved_usd"] == 0


def test_attempts_on_several_models_are_priced_separately(tmp_path):
    from src.utils.llm_budget import Attempt

    ledger = LLMBudgetLedger(tmp_path / "spend.sqlite3")
    request_id = ledger.reserve("quant", 1_000, 4_000, model="claude-opus-5-5", fallback_models=("claude-opus-5",))
    ledger.mark_sent(request_id)
    ledger.settle_attempts(request_id, [Attempt("claude-opus-5-5", 1_000, 50), Attempt("claude-opus-5", 1_000, 400)],
                           reasoning_tokens=100)
    assert ledger.snapshot()["spent_usd"] == pytest.approx(0.004 + 0.001 + 0.005 + 0.01)
    with pytest.raises(LLMBudgetAccountingError):
        ledger.settle_attempts(ledger.reserve("quant", 1, 1), [])


def test_unpriced_models_are_refused_before_any_spend(tmp_path):
    ledger = LLMBudgetLedger(tmp_path / "spend.sqlite3")
    with pytest.raises(LLMBudgetAccountingError, match="price"):
        ledger.reserve("quant", 1_000, 1_000, model="claude-unlisted")
    assert ledger.snapshot()["reserved_usd"] == 0 and ledger.unresolved() == []


def test_haiku_bills_the_long_context_rate_above_100k_prompt_tokens(tmp_path):
    ledger = LLMBudgetLedger(tmp_path / "spend.sqlite3")
    short, long = (ledger.reserve("risk_manager", tokens, 1_000, model="claude-haiku-5-5") for tokens in (100_000, 100_001))
    for request_id, tokens in ((short, 100_000), (long, 100_001)):
        ledger.mark_sent(request_id)
        ledger.settle(request_id, input_tokens=tokens, output_tokens=1_000)
    # $0.10/$0.50 per MTok up to 100k prompt tokens; $0.50/$2.50 above.
    assert ledger.snapshot()["spent_usd"] == pytest.approx((0.01 + 0.0005) + (0.0500005 + 0.0025))
