"""Produce the collector/FW001 deployment file inventory locally, without SSH."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DESTINATION = Path("research/experiments/FW001_forward_replication/deployment_manifest.json")
AUDIT_DIRS = (DESTINATION.parent / "manifest_history", DESTINATION.parent / "manifest_changes")


def files(root=ROOT):
    result = []
    for folder in ("src", "scripts/data", "scripts/ops", "scripts/forward", "deploy/collector"):
        result += [p for p in (root / folder).rglob("*") if p.is_file()
                   and "__pycache__" not in p.parts and not p.name.startswith(".env")]
    result += [root / "scripts/__init__.py"]
    result += [root / "backtesting" / (name + ".py") for name in
               ("__init__", "append_only", "capacity", "cost_model", "experiment_registry", "holdout", "signal_eval")]
    result += [root / "backtesting/experiments" / (name + ".py") for name in
               ("__init__", "common", "d001", "hc001", "hc002", "hc003", "hf001", "av001", "ordering")]
    result += list(root.glob("requirements*.txt"))
    result += [root / "research/experiments/FW001_forward_replication" / name for name in
               ("preregistration.md", "comparison_values.json")]
    for folder in AUDIT_DIRS:
        result += list((root / folder).glob("*.json"))
    return sorted(set(result))


def checksum(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def changes(before, after):
    return {name: {"before": before.get(name), "after": after.get(name)}
            for name in sorted(before.keys() | after.keys())
            if not any(Path(name).is_relative_to(folder) for folder in AUDIT_DIRS)
            and before.get(name) != after.get(name)}


def verify(root=ROOT, *, implementation=False):
    """Every run checks its seal; amendments must explain the exact hash delta."""
    root = Path(root)
    saved = json.loads((root / DESTINATION).read_text(encoding="utf-8"))
    inventory = {p.relative_to(root).as_posix() for p in files(root)}
    if inventory != set(saved["files"]):
        raise ValueError("collector manifest inventory differs: " + ", ".join(sorted(inventory ^ set(saved["files"]))))
    expected = dict(saved["files"])
    if implementation:
        expected.update(saved["implementation_files"])
    changed = [name for name, value in expected.items()
               if not (root / name).is_file() or checksum(root / name) != value]
    if changed:
        raise ValueError("collector manifest differs: " + ", ".join(changed))
    if "change_manifest" in saved:
        link = saved["change_manifest"]
        if checksum(root / link["path"]) != link["sha256"]:
            raise ValueError("collector change manifest hash differs")
        amendment = json.loads((root / link["path"]).read_text(encoding="utf-8"))
        previous = saved["previous_manifest"]
        if (checksum(root / previous["path"]) != previous["sha256"]
                or amendment["previous_manifest_sha256"] != previous["sha256"]
                or not amendment["reason"] or not amendment["changed_at"]):
            raise ValueError("collector change manifest is not linked to its previous seal")
        old = json.loads((root / previous["path"]).read_text(encoding="utf-8"))
        if amendment["changes"] != changes(old["files"], saved["files"]):
            raise ValueError("collector change manifest does not describe the deployed changes")
        if amendment["implementation_changes"] != changes(old["implementation_files"], saved["implementation_files"]):
            raise ValueError("collector change manifest does not describe the PC implementation changes")
    return saved


def reseal(root=ROOT, *, reason, data_dir=None):
    root = Path(root)
    previous_path = root / DESTINATION
    previous = json.loads(previous_path.read_text(encoding="utf-8"))
    old_hash = checksum(previous_path)
    history = AUDIT_DIRS[0] / (old_hash + ".json")
    (root / history).parent.mkdir(parents=True, exist_ok=True)
    if not (root / history).exists():
        (root / history).write_bytes(previous_path.read_bytes())
    updated = manifest(root, data_dir=data_dir)
    amendment = {"previous_manifest_sha256": old_hash, "reason": reason,
                 "changed_at": updated["generated_at"],
                 "changes": changes(previous["files"], updated["files"]),
                 "implementation_changes": changes(previous["implementation_files"], updated["implementation_files"])}
    content = json.dumps(amendment, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    amendment_hash = hashlib.sha256(content.encode()).hexdigest()
    relative = AUDIT_DIRS[1] / (amendment_hash + ".json")
    (root / relative).parent.mkdir(parents=True, exist_ok=True)
    with (root / relative).open("x", encoding="utf-8") as handle:
        handle.write(content)
    updated["files"][relative.as_posix()] = amendment_hash
    updated.update(status="sealed", previous_manifest={"path": history.as_posix(), "sha256": old_hash},
                   change_manifest={"path": relative.as_posix(), "sha256": amendment_hash})
    previous_path.write_text(json.dumps(updated, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                             encoding="utf-8")
    return updated


def manifest(root=ROOT, *, data_dir=None):
    result = {"experiment_id": "FW001_forward_replication", "preregistration_commit": "23e2051",
        "generated_at": datetime.now(timezone.utc).isoformat(), "status": "prepared_for_sealing",
        "files": {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in files(root)},
        "self_excluded": DESTINATION.as_posix(),
        "implementation_files": {p: hashlib.sha256((root / p).read_bytes()).hexdigest() for p in
            ("backtesting/experiments/fw001.py", "tests/test_forward_fw001.py", "tests/test_experiments_fw001.py",
             "tests/test_ops_fw001.py", "tests/test_shadow_ops.py", "tests/test_trading_calendar_overrides.py",
             "research/experiments/FW001_forward_replication/resource_replay_guard.py")},
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
    parser.add_argument("--reseal", action="store_true", help="Preserve the old seal and link an explicit amendment")
    parser.add_argument("--reason", help="Required explanation for --reseal")
    parser.add_argument("--data-dir", type=Path, help="Read-only collection metadata hashes to attach before sealing")
    args = parser.parse_args(argv)
    if args.verify:
        saved = verify()
        print(json.dumps({"status": "verified", "files_checked": len(saved["files"])}))
        return
    if args.reseal:
        if not args.reason:
            parser.error("--reseal requires --reason")
        reseal(reason=args.reason, data_dir=args.data_dir)
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
