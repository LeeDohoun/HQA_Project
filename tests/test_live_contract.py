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
