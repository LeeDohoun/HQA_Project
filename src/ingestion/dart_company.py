"""Resumable OpenDART company snapshots; execution is opt-in.

induty_code is the current classification, not a point-in-time history. Industry
changes over time are therefore not captured, a known limitation for backtests.
The CSV reference stock code is retained separately when a delisted company's
current OpenDART stock_code is empty. Successful JSONL records are the progress
checkpoint; the daily quota counts attempts before transport, including failures.
The quota is local to this collector, not a shared budget for other DART clients.
"""
from __future__ import annotations

import csv
import json
import math
import re
import time
from datetime import datetime
from pathlib import Path

import requests

from src.utils.stock_codes import is_stock_code

from .dart_api import DartAPIError, read_dart_payload
from .dart_poller import KST, _kst
from .storage import atomic_write, file_lock, read_rows, write_rows


DEFAULT_DATA_DIR = Path(__file__).resolve().parents[2] / "data"
COMPANY_URL = "https://opendart.fss.or.kr/api/company.json"
FIELDS = ("corp_name", "stock_code", "corp_cls", "induty_code", "est_dt", "acc_mt")


def load_corp_codes(path: str | Path, *, stock_codes=None) -> list[dict]:
    """Include formerly listed companies by default, or scope to explicit stocks."""
    corps = {}
    stocks = {}
    selected = None if stock_codes is None else set(stock_codes)
    if selected is not None and any(not is_stock_code(code) for code in selected):
        raise ValueError("DART company stock subset contains invalid codes")
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not {"corp_code", "stock_code"}.issubset(reader.fieldnames or []):
            raise ValueError("DART company CSV requires corp_code and stock_code")
        for row in reader:
            corp = (row.get("corp_code") or "").strip()
            stock = (row.get("stock_code") or "").strip()
            if selected is not None and stock not in selected:
                continue
            if not re.fullmatch(r"[0-9]{8}", corp) or not is_stock_code(stock):
                raise ValueError("DART company CSV contains invalid corporate or stock code")
            if (corp in corps and corps[corp]["stock_code"] != stock
                    or stock in stocks and stocks[stock] != corp):
                raise ValueError("DART company CSV contains conflicting identities")
            corps[corp] = {"corp_code": corp, "stock_code": stock}
            stocks[stock] = corp
    return [corps[corp] for corp in sorted(corps)]


def _company(payload: dict, reference: dict) -> dict:
    if (payload.get("corp_code") != reference["corp_code"]
            or any(not isinstance(payload.get(field), str) for field in FIELDS)
            or not payload["corp_name"].strip() or payload["corp_cls"] not in {"Y", "K", "N", "E"}
            or payload["stock_code"] not in {"", reference["stock_code"]}):
        raise DartAPIError("DART malformed company or mismatched corporate identity")
    if payload["est_dt"]:
        try:
            if not re.fullmatch(r"[0-9]{8}", payload["est_dt"]):
                raise ValueError
            datetime.strptime(payload["est_dt"], "%Y%m%d")
        except ValueError:
            raise DartAPIError("DART invalid company establishment date") from None
    if payload["acc_mt"] and payload["acc_mt"] not in {f"{month:02d}" for month in range(1, 13)}:
        raise DartAPIError("DART invalid company accounting month")
    return {"corp_code": reference["corp_code"], **{field: payload[field] for field in FIELDS},
            "reference_stock_code": reference["stock_code"]}


def _stored(path: Path) -> dict:
    companies = {}
    for row in read_rows(path):
        corp, stock = row.get("corp_code"), row.get("reference_stock_code")
        if not isinstance(corp, str) or not re.fullmatch(r"[0-9]{8}", corp) or not is_stock_code(stock):
            raise ValueError("DART invalid saved company identity")
        _company(row, {"corp_code": corp, "stock_code": stock})
        try:
            _kst(datetime.fromisoformat(row["collected_at"]))
        except (KeyError, TypeError, ValueError):
            raise ValueError("DART invalid saved company timestamp") from None
        if corp in companies:
            raise ValueError("DART duplicate saved company")
        companies[corp] = row
    return companies


def _quota(path: Path) -> dict:
    quota = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"days": {}}
    if not isinstance(quota, dict) or not isinstance(quota.get("days"), dict):
        raise ValueError("DART invalid company quota")
    for day, entry in quota["days"].items():
        if (not re.fullmatch(r"[0-9]{8}", day) or not isinstance(entry, dict)
                or type(entry.get("requests")) is not int or entry["requests"] < 0
                or type(entry.get("provider_blocked")) is not bool):
            raise ValueError("DART invalid company daily quota")
        datetime.strptime(day, "%Y%m%d")
    return quota


class DartCompanyCollector:
    def __init__(self, api_key=None, session=None, clock=None, data_dir=DEFAULT_DATA_DIR, *,
                 sleeper=None, request_interval=0.25):
        if (isinstance(request_interval, bool) or not math.isfinite(request_interval)
                or request_interval <= 0):
            raise ValueError("DART company request interval must be positive and finite")
        self.api_key = api_key
        self.session = session
        self.clock = clock if clock is not None else lambda: datetime.now(KST)
        self.sleeper = sleeper if sleeper is not None else time.sleep
        self.data_dir = Path(data_dir)
        self.request_interval = request_interval

    def collect(self, corp_codes_path: str | Path, *, max_requests=3000, execute=False, stock_codes=None) -> dict:
        """Plan by default. max_requests is a per-KST-day ceiling across reruns.

        Status 020 blocks this collector for the remainder of that KST day.
        Other provider/transport/validation failures propagate without losing
        successful companies. No-data (013) is an error, not a completed company.
        """
        if type(max_requests) is not int or max_requests < 1:
            raise ValueError("DART company max_requests must be a positive integer")
        references = load_corp_codes(corp_codes_path, stock_codes=stock_codes)
        directory = self.data_dir / "reference" / "dart_company"
        if not execute:
            return self._collect(references, directory, max_requests, execute=False)
        if not isinstance(self.api_key, str) or not self.api_key.strip():
            raise ValueError("DART API key is required for company execution")
        with file_lock(directory / ".collect.lock"):
            return self._collect(references, directory, max_requests, execute=True)

    def _collect(self, references, directory, max_requests, *, execute):
        path, quota_path = directory / "companies.jsonl", directory / "_quota.json"
        companies, quota = _stored(path), _quota(quota_path)
        for reference in references:
            previous = companies.get(reference["corp_code"])
            if previous is not None and previous["reference_stock_code"] != reference["stock_code"]:
                raise ValueError("DART saved company conflicts with reference CSV")
        pending = [row for row in references if row["corp_code"] not in companies]
        day = _kst(self.clock()).strftime("%Y%m%d")
        entry = quota["days"].get(day, {"requests": 0, "provider_blocked": False})
        budget = 0 if entry["provider_blocked"] else max(0, max_requests - entry["requests"])
        summary = {"dry_run": not execute, "corps": len(references),
                   "completed": len(references) - len(pending), "pending": len(pending),
                   "planned_requests": min(len(pending), budget), "requests": 0, "saved": 0,
                   "remaining": len(pending), "quota_day": day, "quota_used": entry["requests"],
                   "max_requests": max_requests, "stop_reason": "dry_run" if not execute else "complete",
                   "source_url": COMPANY_URL, "data_path": str(path)}
        if not execute:
            return summary
        session = self.session
        try:
            for reference in pending:
                day = _kst(self.clock()).strftime("%Y%m%d")
                entry = quota["days"].get(day, {"requests": 0, "provider_blocked": False})
                if entry["provider_blocked"] or entry["requests"] >= max_requests:
                    summary.update(quota_day=day, quota_used=entry["requests"],
                                   stop_reason="provider_limit" if entry["provider_blocked"] else "daily_limit")
                    break
                self.sleeper(self.request_interval)
                # Recheck after throttling, which may have crossed KST midnight.
                day = _kst(self.clock()).strftime("%Y%m%d")
                entry = quota["days"].setdefault(day, {"requests": 0, "provider_blocked": False})
                if entry["provider_blocked"] or entry["requests"] >= max_requests:
                    summary.update(quota_day=day, quota_used=entry["requests"],
                                   stop_reason="provider_limit" if entry["provider_blocked"] else "daily_limit")
                    break
                if session is None:
                    session = requests.Session()
                entry["requests"] += 1
                atomic_write(quota_path, json.dumps(quota) + "\n")
                summary.update(requests=summary["requests"] + 1,
                               quota_day=day, quota_used=entry["requests"])
                try:
                    response = session.get(COMPANY_URL,
                        params={"crtfc_key": self.api_key, "corp_code": reference["corp_code"]},
                        timeout=20, allow_redirects=False)
                    if not 200 <= response.status_code < 300:
                        raise DartAPIError("DART company HTTP failure")
                except Exception:
                    raise DartAPIError("DART company transport failure") from None
                try:
                    payload = read_dart_payload(response)
                except DartAPIError as exc:
                    if str(exc) != "DART provider error status=020":
                        raise
                    entry["provider_blocked"] = True
                    atomic_write(quota_path, json.dumps(quota) + "\n")
                    summary["stop_reason"] = "provider_limit"
                    break
                if payload["status"] == "013":
                    raise DartAPIError("DART company has no data for requested corporation")
                row = _company(payload, reference)
                row = {field: value.replace(self.api_key, "[REDACTED]") for field, value in row.items()}
                row.update(collected_at=_kst(self.clock()).isoformat(), source_url=COMPANY_URL)
                companies[reference["corp_code"]] = row
                write_rows(path, [companies[corp] for corp in sorted(companies)])
                summary["saved"] += 1
                summary["completed"] += 1
                summary["remaining"] -= 1
        finally:
            if self.session is None and session is not None:
                session.close()
        return summary
