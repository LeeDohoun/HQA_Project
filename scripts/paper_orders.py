"""Operator tool for PAPER orders whose broker outcome is unknown.

An order whose KIS call timed out or failed has no broker order ID (status
UNKNOWN). Until an operator records what the broker did, every trigger of its
plan, including protective sells, waits for it. Look the order up in the KIS
paper order history (app or HTS) for the listed day, stock, side and quantity:

  unknown                      list the orders waiting for an operator
  adopt EXECUTION ODNO         the broker has the order: link its order number
  not-submitted EXECUTION      the broker has no such order

The backend checks both against the KIS order history before changing anything,
and refuses not-submitted while KIS lists an order that could be this one.
"""
from __future__ import annotations

import argparse
import json
import os

import requests

from src.config.settings import load_project_env


def _backend() -> tuple[str, dict]:
    url = (os.getenv("BACKEND_INTERNAL_BASE_URL") or os.getenv("BACKEND_BASE_URL") or "").strip().rstrip("/")
    token = os.getenv("HQA_INTERNAL_TOKEN", "").strip()
    if not url or not token:
        raise SystemExit("BACKEND_INTERNAL_BASE_URL and HQA_INTERNAL_TOKEN are required")
    return f"{url}/api/v1/internal/trading/executions", {"X-HQA-Internal-Token": token}


def _answer(response: requests.Response) -> dict:
    if response.status_code >= 400:
        raise SystemExit(f"backend refused (HTTP {response.status_code}): {response.text[:500]}")
    return response.json()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("unknown", help="List UNKNOWN orders waiting for an operator")
    adopt = commands.add_parser("adopt", help="Link the broker order found in the KIS order history")
    adopt.add_argument("execution_id")
    adopt.add_argument("broker_order_id", help="KIS order number (ODNO); leading zeros may be omitted")
    adopt.add_argument("--note", required=True, help="Where and how the order was found")
    unsent = commands.add_parser("not-submitted", help="Record that the KIS order history has no such order")
    unsent.add_argument("execution_id")
    unsent.add_argument("--note", required=True, help="Where and how the absence was confirmed")
    args = parser.parse_args(argv)
    load_project_env()
    base, headers = _backend()
    if args.command == "unknown":
        result = _answer(requests.get(f"{base}/unknown", headers=headers, timeout=30))
    else:
        body = ({"brokerOrderId": args.broker_order_id} if args.command == "adopt" else {"notSubmitted": True})
        # The backend queries the KIS order history (one paced call) before it decides.
        result = _answer(requests.post(f"{base}/{args.execution_id}/resolution", json={**body, "note": args.note},
                                       headers=headers, timeout=60))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
