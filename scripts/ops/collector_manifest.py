"""Produce the collector/FW001 deployment file inventory locally, without SSH."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DESTINATION = Path("research/experiments/FW001_forward_replication/deployment_manifest.json")


def files(root=ROOT):
    result = []
    for folder in ("src", "scripts/data", "scripts/ops", "scripts/forward", "deploy/collector"):
        result += [p for p in (root / folder).rglob("*") if p.is_file()
                   and "__pycache__" not in p.parts and not p.name.startswith(".env")]
    result += [root / "scripts/__init__.py"]
    result += [root / "backtesting" / (name + ".py") for name in
               ("__init__", "capacity", "cost_model", "experiment_registry", "holdout", "signal_eval")]
    result += [root / "backtesting/experiments" / (name + ".py") for name in
               ("__init__", "common", "d001", "hc001", "hc002", "hc003", "hf001", "av001", "ordering")]
    result += list(root.glob("requirements*.txt"))
    result += [root / "research/experiments/FW001_forward_replication" / name for name in
               ("preregistration.md", "comparison_values.json")]
    return sorted(set(result))


def manifest(root=ROOT, *, data_dir=None):
    result = {"experiment_id": "FW001_forward_replication", "preregistration_commit": "23e2051",
        "generated_at": datetime.now(timezone.utc).isoformat(), "status": "prepared_for_sealing",
        "files": {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in files(root)},
        "self_excluded": DESTINATION.as_posix(),
        "implementation_files": {p: hashlib.sha256((root / p).read_bytes()).hexdigest() for p in
            ("backtesting/experiments/fw001.py", "tests/test_forward_fw001.py", "tests/test_experiments_fw001.py",
             "tests/test_ops_fw001.py")},
        "required_data": ["market/krx_daily: recent windows PLUS every path in supplement target_scan.price_files; reader verifies the historical inventory and non-protected hashes without historical return evaluation",
            "reference/corp_codes.csv", "reference/dart_company/companies.jsonl",
            "disclosures/dart_full/list/**", "disclosures/dart_full/_state.json",
            "disclosures/dart_full/list_supplement/** (including _state.json with target_scan and file_manifest)",
            "fundamentals/dart_quarterly/*_110*.jsonl", "fundamentals/dart_quarterly/_state.json",
            "fundamentals/dart_quarterly/_batch_status.json (created by future successful/attempted batches)",
            "fundamentals/_refresh_last_run.json (when the weekly refresh has run)"]}
    if data_dir is not None:
        names = ("disclosures/dart_full/list_supplement/_state.json",
                 "disclosures/dart_full/_state.json", "reference/corp_codes.csv",
                 "reference/dart_company/companies.jsonl")
        result["data_artifacts_at_preparation"] = {name: hashlib.sha256(
            (Path(data_dir) / name).read_bytes()).hexdigest() for name in names}
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="Create the manifest once; existing files are never replaced")
    parser.add_argument("--verify", action="store_true", help="Check the deployed file hashes; no network or writes")
    parser.add_argument("--data-dir", type=Path, help="Read-only collection metadata hashes to attach before sealing")
    args = parser.parse_args(argv)
    if args.verify:
        saved = json.loads((ROOT / DESTINATION).read_text(encoding="utf-8"))
        changed = [name for name, expected in saved["files"].items() if not (ROOT / name).is_file()
                   or hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != expected]
        if changed:
            raise ValueError("collector manifest differs: " + ", ".join(changed))
        print(json.dumps({"status": "verified", "files_checked": len(saved["files"])}))
        return
    result = manifest(data_dir=args.data_dir)
    if args.write:
        path = ROOT / DESTINATION
        with path.open("x", encoding="utf-8") as handle:
            json.dump(result, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
    else:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
