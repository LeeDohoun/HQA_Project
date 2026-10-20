"""Small durable recovery receipts; never expand the complete monthly result."""
import json
from pathlib import Path

from backtesting.experiments.qp001 import artifacts
from backtesting.experiments.qp001.runner import content_hash
from . import RULES


def receipt_path(output):
    return Path(output).with_suffix(".recovery.json")


def metadata(payload):
    keys = ("experiment_id", "variant", "stage", "run_id", "start_record", "prereg_commit", "code_commit",
            "registry_seal", "M", "judgement", "qp001_precheck", "precheck", "input_spec_sha256", "monthly_summary")
    result = {key: payload[key] for key in keys}
    result["input_spec"] = {"code": payload["input_spec"]["code"]}
    for section in ("rules", "vanished"):
        result[section] = {rule: {name: {"details": payload[section][rule][name]["details"]}
            for name in ("random_control", "independent_redraw")} for rule in RULES}
    for rule in RULES:
        result["rules"][rule]["metrics"] = payload["rules"][rule]["metrics"]
    return result


def write(output, payload):
    if content_hash(payload["input_spec"]) != payload["input_spec_sha256"]:
        raise ValueError("result input-spec digest differs before durable save")
    artifacts.write_json(output, payload)
    artifacts.write_json(receipt_path(output), {"result_sha256": artifacts.digest(output), "payload": metadata(payload)})


def read(output):
    receipt = receipt_path(output)
    if not receipt.is_file():
        raise ValueError("durable recovery receipt missing; use --abort-attempt with the original run_id")
    with receipt.open(encoding="utf-8") as handle:
        saved = json.load(handle)
    if artifacts.digest(output) != saved["result_sha256"]:
        raise ValueError("saved result hash differs from its durable recovery receipt")
    return saved["payload"]
