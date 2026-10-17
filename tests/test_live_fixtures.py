"""Offline fixtures shared by tests/test_live_*.py (no tests here)."""
from __future__ import annotations

import csv
import json
import os
import shutil
from datetime import datetime, timedelta
from pathlib import Path

from src.live import shadow

REPO = Path(__file__).resolve().parents[1]
DECISION_DATE = "2026-10-30"
TRADE_DATE = "2026-11-02"
EXIT_DATE = "2026-11-27"
REGISTRY_HEADER = ["recorded_at", "experiment_id", "prereg_commit", "variant", "verdict", "metrics_json", "note"]


def make_repo(tmp_path, rows=None, approvals=None) -> Path:
    """Copy the real strategy book into a temporary repo with a chosen registry."""
    root = tmp_path / "repo"
    (root / "config").mkdir(parents=True)
    shutil.copy(REPO / "config/strategy_book.yaml", root / "config/strategy_book.yaml")
    (root / "config/strategy_approvals.yaml").write_text(
        "approvals: []\n" if approvals is None else approvals, encoding="utf-8")
    write_registry(root, rows if rows is not None else [
        ("2026-10-17T12:11:15+00:00", "HF001_hegemony_filters", "f_both", "pass"),
        ("2026-10-17T11:54:26+00:00", "AV001_avoidance_portfolio", "av_all", "pass"),
    ])
    return root


def write_registry(root, rows):
    path = Path(root) / "research/experiments/registry.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(REGISTRY_HEADER)
        for recorded_at, experiment, variant, verdict in rows:
            writer.writerow([recorded_at, experiment, "abc123", variant, verdict, "{}", ""])
    return path


def krx_row(code, *, open_=10000, close=10000, base=10000, volume=100000, value=5e9, market="KOSPI",
            status="verified", day="2026-11-02", version="v1"):
    return {"stock_code": code, "stock_name": f"S{code}", "market": market, "trade_date": day,
            "open": None if open_ is None else str(open_), "high": str(max(open_ or 0, close)),
            "low": str(min(open_ or close, close)), "close": str(close), "volume": str(volume),
            "trading_value": str(value), "base_price": None if base is None else str(base),
            "calendar_status": status, "version": version, "collected_at": f"{day}T16:00:00+09:00"}


def write_krx(data_dir, day, rows):
    day = day if isinstance(day, str) else day.isoformat()
    path = Path(data_dir) / "market/krx_daily" / day[:4] / (day.replace("-", "") + ".jsonl")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps({**row, "trade_date": day}, ensure_ascii=False) + "\n")
    return path


def write_flat_days(data_dir, start, end, codes, price=10000, overrides=None):
    """A verified row per code for every session in [start, end]; overrides[(day, code)] = kwargs."""
    overrides = overrides or {}
    for day in shadow.sessions_between(start, end):
        rows = []
        for code in codes:
            kwargs = {"open_": price, "close": price, "base": price, **overrides.get((day, code), {})}
            if kwargs.pop("absent", False):
                continue
            rows.append(krx_row(code, day=day, **kwargs))
        write_krx(data_dir, day, rows)


def decision_record(*, f_both=(), av_all=(), label="forward", month="2026-10", decision_date=DECISION_DATE,
                    trade_date=TRADE_DATE, exit_date=EXIT_DATE, adv=5e9):
    def rule(codes):
        codes = sorted(codes)
        return {"targets": codes, "weights": {c: 1 / len(codes) for c in codes} if codes else {},
                "universe": [{"stock_code": c, "avg_trading_value_20d": adv, "phase": 2} for c in codes],
                "F_vol": [], "F_raise": []}
    return {"experiment_id": "FW001_forward_replication", "record_type": "decision", "label": label,
            "month": month, "decision_date": decision_date, "trade_date": trade_date,
            "deadline": f"{trade_date}T08:59:00+09:00", "f_both_exit": exit_date,
            "finalised_at": f"{decision_date}T17:00:00+09:00",
            "rules": {"f_both": rule(f_both), "av_all": rule(av_all)}, "states": None}


def encoded(record) -> str:
    """FW001 recorder encoding (fw001.encoded)."""
    return json.dumps(record, sort_keys=True, ensure_ascii=False, allow_nan=False, indent=2) + "\n"


def _ns(stamp: str) -> int:
    return int(datetime.fromisoformat(stamp).timestamp() * 1_000_000_000)


def write_seal(data_dir, kind, month, record_path, *, deadline, sealed_at, multiplier=None, **overrides):
    """A completion proof exactly as fw001.complete() writes it, published with fw001's own
    publish_completion; the private directory's mtime is then set to sealed_at."""
    from src.forward import fw001
    from src.ingestion import dart_listing

    prepared = (datetime.fromisoformat(sealed_at) - timedelta(minutes=1)).isoformat()
    proof = {"record_path": Path(record_path).relative_to(data_dir).as_posix(),
             "record_digest": dart_listing.file_hash(record_path), "prepared_at": prepared,
             "publication_clock": "private_directory_mtime_ns",
             "publication_precision_ns": fw001.PUBLICATION_PRECISION.value,
             "deadline": deadline, "reconstructed": False, **overrides}
    marker = fw001.marker_path(data_dir, kind, month, multiplier)
    if marker.parent.exists():  # fixtures may rewrite a month; the recorder never does
        shutil.rmtree(marker.parent)
    assert fw001.publish_completion(marker, proof)
    os.utime(marker, ns=(_ns(prepared), _ns(prepared)))
    set_sealed_at(marker, sealed_at)
    return marker


def set_sealed_at(marker, sealed_at):
    os.utime(Path(marker).parent, ns=(_ns(sealed_at), _ns(sealed_at)))


def write_decision(data_dir, record, *, seal=None, sealed_at=None, **marker):
    """Write a decision as the recorder does; forward records are sealed (17:05 KST on the
    decision date) unless seal=False."""
    month = record["month"].replace("-", "")
    path = Path(data_dir) / "forward/fw001/decisions" / (month + ".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(encoded(record), encoding="utf-8")
    if seal if seal is not None else record.get("label") == "forward":
        write_seal(data_dir, "decisions", record["month"], path, deadline=record["deadline"],
                   sealed_at=sealed_at or f"{record['decision_date']}T17:05:00+09:00", **marker)
    return path


def write_execution(data_dir, record, *, decision, seal=True, sealed_at=None, **marker):
    """A FW001 1.0x execution record (recorded_at 09:10 KST on the trade date) and its proof."""
    month = record["month"]
    record = {"recorded_at": f"{record['trade_date']}T09:10:00+09:00", **record}
    path = Path(data_dir) / "forward/fw001/executions" / f"{month.replace('-', '')}_1.0.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(encoded(record), encoding="utf-8")
    if seal:
        write_seal(data_dir, "executions", month, path, deadline=decision["deadline"], multiplier=1.0,
                   sealed_at=sealed_at or f"{record['trade_date']}T09:20:00+09:00", **marker)
    return path


def write_ledger_entry(data_dir, strategy_id, day, *, mode="shadow", incidents=(), match=True, nav=1e8):
    path = shadow.ledger_path(data_dir, strategy_id, day)
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {"strategy_id": strategy_id, "date": day, "mode": mode, "incidents": list(incidents),
             "match": {"targets_match": match, "fill_prices_match": True}, "nav_close_krw": nav,
             "holdings_after": {}, "cash_after": nav, "marks_close": {}, "state_after": {"baskets": []},
             "orders": [], "warnings": []}
    path.write_text(json.dumps(entry), encoding="utf-8")
    return entry
