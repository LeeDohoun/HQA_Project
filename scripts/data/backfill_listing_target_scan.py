"""Offline operator migration; default dry-run never opens protected prices.

--write scans local KRX identities and adds only target_scan to the completed
supplement state. It does not run an experiment, call APIs or load environment.
"""
from __future__ import annotations

import argparse
import json
from contextlib import nullcontext
from pathlib import Path

from src.ingestion import dart_listing as listing
from src.ingestion.dart_backfill import _write_json
from src.ingestion.storage import file_lock


def backfill(data_dir, *, write=False):
    root = listing.directory(data_dir)
    with file_lock(root / ".backfill.lock") if write else nullcontext():
        path = root / "list_supplement/_state.json"
        state = listing.validate_supplement_state(listing._state(path) or {})
        manifest = listing.supplement_manifest(data_dir)
        if (state["completed_corps"] != state["target_corps"] or state.get("file_manifest") != manifest
                or sum(v["rows"] for v in manifest.values()) != sum(state["rows_per_corp"].values())):
            raise ValueError("backfill requires a complete, unchanged supplement manifest")
        if "target_scan" in state:
            listing.verified_target_scan(Path(data_dir), state)
            return {"status": "already_frozen", "state_changed": False}
        paths = listing.target_scan_paths(data_dir, state["end_de"])
        summary = {"status": "dry_run", "state_changed": False, "target_corps": len(state["target_corps"]),
                   "start_de": state["start_de"], "end_de": state["end_de"], "krx_files": len(paths),
                   "protected_files": sum("20260101" <= p.stem <= "20260630" for p in paths),
                   "supplement_files": len(manifest), "supplement_rows": sum(v["rows"] for v in manifest.values()),
                   "adds_field": "target_scan", "supplement_rows_and_manifest_unchanged": True,
                   "protected_price_contents_opened": False}
        if write:
            before = path.read_bytes()
            scan = listing.freeze_target_scan(data_dir, state["end_de"])
            if path.read_bytes() != before or listing.supplement_manifest(data_dir) != manifest:
                raise ValueError("supplement changed during backfill")
            state["target_scan"] = scan
            _write_json(path, state)
            summary.update(status="frozen", state_changed=True,
                           protected_price_contents_opened=bool(summary["protected_files"]))
        return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--write", action="store_true", help="operator-only local metadata scan and state migration")
    args = parser.parse_args(argv)
    print(json.dumps(backfill(args.data_dir, write=args.write), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
