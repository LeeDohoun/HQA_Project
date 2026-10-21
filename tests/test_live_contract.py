"""Cross-language batch contract: the Python payload is the backend test's input.

backend/.../StrategyPythonContractTest posts this exact JSON through the backend's
batch validation. Regenerate after an intended payload change with
HQA_REGENERATE_STRATEGY_CONTRACT=1 and rerun both test suites.
"""
import json
import os

from src.live import decisions, rebalance
from tests.test_live_fixtures import REPO, decision_record

CONTRACT = REPO / "backend/src/test/resources/strategy/python_batch_payload.json"
CODES = ["000020", "000040", "000050", "000070", "000080", "000100",
         "000120", "000140", "000150", "000180", "000210", "0126Z0"]


def contract_payload():
    record = decision_record(f_both=CODES)
    decision = decisions.parse_decision(record, "f_both")
    prices = {code: 10_000 + 1_250 * i for i, code in enumerate(CODES)}
    plan = rebalance.plan_basket_entry(
        "hf001_f_both", decision, cash=100_000_000, ref_prices=prices,
        markets={code: "KOSDAQ" if code == "0126Z0" else "KOSPI" for code in CODES},
        adv20={code: 5e9 for code in CODES}, model_version="v1")
    return rebalance.batch_payload(plan, user_id="u1", expected_nav_krw=100_000_000, dry_run=True)


def test_payload_matches_the_backend_contract_fixture():
    payload = contract_payload()
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if os.environ.get("HQA_REGENERATE_STRATEGY_CONTRACT") == "1":
        CONTRACT.parent.mkdir(parents=True, exist_ok=True)
        CONTRACT.write_text(text, encoding="utf-8")
    assert CONTRACT.read_text(encoding="utf-8") == text
    assert len(payload["orders"]) == len(CODES)
    assert all(order["side"] == "BUY" for order in payload["orders"])
    assert payload["idempotencyKey"] == rebalance.idempotency_key(
        "hf001_f_both", payload["decisionHash"], "2026-11-02", "OPEN", "u1")


CONT_CONTRACT = REPO / "backend/src/test/resources/strategy/python_cont_buy_payload.json"


def cont_buy_payload():
    """pf001-style CONT_BUY batch (continuous_intraday plan) for the backend contract test."""
    record = decision_record(av_all=CODES)
    decision = decisions.parse_decision(record, "av_all", execution="continuous")
    prices = {code: 10_000 + 1_250 * i for i, code in enumerate(CODES)}
    _, buy = rebalance.plan_intraday(
        "hf001_f_both", decision, session_date="2026-11-02", reserved_krw=20_000_000, holdings={},
        cash=100_000_000, ref_prices=prices,
        markets={code: "KOSDAQ" if code == "0126Z0" else "KOSPI" for code in CODES},
        adv20={code: 5e10 for code in CODES}, model_version="v2", band=0.001,
        max_name_weight=0.02, max_adv_share=0.01)
    return rebalance.batch_payload(buy, user_id="u1", expected_nav_krw=100_000_000, dry_run=True)


def test_cont_buy_payload_matches_the_backend_contract_fixture():
    payload = cont_buy_payload()
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if os.environ.get("HQA_REGENERATE_STRATEGY_CONTRACT") == "1":
        CONT_CONTRACT.write_text(text, encoding="utf-8")
    assert CONT_CONTRACT.read_text(encoding="utf-8") == text
    assert payload["phase"] == "CONT_BUY" and 0 <= payload["costReserveRate"] < 0.05
    assert payload["orders"] and all(order["side"] == "BUY" for order in payload["orders"])
    # 2% of NAV per name: no order above 2,000,000 KRW at its reference price.
    assert all(order["quantity"] * order["refPrice"] <= 2_000_000 for order in payload["orders"])
