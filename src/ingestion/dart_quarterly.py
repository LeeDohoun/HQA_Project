"""Offline planning and resumable OpenDART quarterly filing archives."""
from __future__ import annotations

import calendar
import csv
import hashlib
import json
import math
import re
from collections import defaultdict
from datetime import date, datetime
from itertools import groupby
from pathlib import Path

import pandas as pd
import requests

from src.utils.stock_codes import is_stock_code

from .dart_api import DartAPIError, read_dart_payload
from .dart_financials import DartFinancialStatementCollector
from .dart_poller import KST, _kst
from .storage import atomic_write, file_lock, read_rows, write_rows


DEFAULT_DATA_DIR = Path(__file__).resolve().parents[2] / "data"
DEFAULT_CORP_CODES = DEFAULT_DATA_DIR.parent.parent / "HQA_data_backup_20260913" / "corp_codes.csv"
DEFAULT_MAX_REQUESTS = 3000
URL = "https://opendart.fss.or.kr/api/fnlttMultiAcnt.json"
REPORTS = {"11013": 1, "11012": 2, "11014": 3, "11011": 4}
ACCOUNTS = {
    "revenue": DartFinancialStatementCollector.ACCOUNT_ALIASES["revenue"],
    "operating_income": DartFinancialStatementCollector.ACCOUNT_ALIASES["operating_profit"],
    "net_income": DartFinancialStatementCollector.ACCOUNT_ALIASES["net_income"],
}
COLUMNS = ["stock_code", "corp_code", "fiscal_quarter", *ACCOUNTS,
           "fs_div", "available_date", "rcept_no", "currency", "missing_reasons"]


def load_universe(*, corp_codes_path=DEFAULT_CORP_CODES, listing_dir=None,
                  universe_source=None) -> dict[str, list[str]]:
    """Union historical stock aliases, including delisted and alphanumeric codes.

    Tests can supply an iterable of corp_code/stock_code rows or a callable
    returning it. Aliases have no listing validity dates; they are identifiers,
    not evidence that a stock was tradable at a historical decision date.
    """
    if universe_source is None:
        listing_dir = Path(listing_dir) if listing_dir is not None else DEFAULT_DATA_DIR / "disclosures/dart_full/list"
        if not Path(corp_codes_path).is_file() or not listing_dir.is_dir():
            raise ValueError("DART quarterly universe source is missing")

        def sources():
            with Path(corp_codes_path).open(encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                if not {"corp_code", "stock_code"}.issubset(reader.fieldnames or []):
                    raise ValueError("DART invalid corporate-code CSV columns")
                yield from reader
            for path in sorted(listing_dir.rglob("*.jsonl")):
                yield from read_rows(path)

        rows = sources()
    else:
        rows = universe_source() if callable(universe_source) else universe_source
    companies = defaultdict(set)
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("stock_code"), str):
            raise ValueError("DART invalid universe row")
        stock = row["stock_code"].strip()
        if not stock:
            continue
        corp = row.get("corp_code")
        if not isinstance(corp, str) or not re.fullmatch(r"[0-9]{8}", corp) or not is_stock_code(stock):
            raise ValueError("DART invalid universe identifiers")
        companies[corp].add(stock)
    return {corp: sorted(companies[corp]) for corp in sorted(companies)}


def _read_json(path: Path, default: dict) -> dict:
    if not path.exists():
        return default
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError):
        raise ValueError("DART invalid quarterly checkpoint JSON") from None
    if not isinstance(value, dict):
        raise ValueError("DART quarterly checkpoint must be an object")
    return value


def _write_json(path: Path, value: dict) -> None:
    atomic_write(path, json.dumps(value, ensure_ascii=False, allow_nan=False) + "\n")


def _redact(value, api_key: str):
    if isinstance(value, dict):
        return {_redact(key, api_key): _redact(item, api_key) for key, item in value.items()
                if key.lower() not in {"crtfc_key", "api_key", "apikey", "authorization", "access_token"}}
    if isinstance(value, list):
        return [_redact(item, api_key) for item in value]
    if isinstance(value, str):
        return value.replace(api_key, "[REDACTED]")
    return value


def _filing_date(receipt) -> str:
    if not isinstance(receipt, str) or not re.fullmatch(r"[0-9]{14}", receipt):
        raise ValueError("DART invalid quarterly receipt number")
    try:
        return datetime.strptime(receipt[:8], "%Y%m%d").date().isoformat()
    except ValueError:
        raise ValueError("DART invalid quarterly receipt date") from None


def _amount(value):
    if value in (None, "", "-") or isinstance(value, bool):
        return None
    try:
        number = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _period_months(row: dict):
    dates = re.findall(r"[0-9]{4}[./-][0-9]{1,2}[./-][0-9]{1,2}", row.get("thstrm_dt") or "")
    if len(dates) != 2:
        return None
    try:
        start, end = [datetime.strptime(re.sub(r"[./]", "-", value), "%Y-%m-%d").date() for value in dates]
    except ValueError:
        return None
    if start > end or start.day != 1 or end.day != calendar.monthrange(end.year, end.month)[1]:
        return None
    return (end.year - start.year) * 12 + end.month - start.month + 1


def _sample_value(value):
    if isinstance(value, dict):
        return {_sample_value(key): _sample_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_sample_value(item) for item in value]
    if isinstance(value, str):
        return re.sub(r"https?://\S+", "[REDACTED_URL]", value)
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    return value


def _records(payload: dict, year: int, report: str, batch: list[str], universe: dict,
             diagnostics: dict) -> list[dict]:
    if payload["status"] == "013":
        return []
    rows = payload.get("list")
    if not isinstance(rows, list) or not rows or any(not isinstance(row, dict) for row in rows):
        raise DartAPIError("DART invalid quarterly account list")
    groups = defaultdict(list)
    for row in rows:
        checks = [
            ("corp_not_in_batch", "corp_code", not isinstance(row.get("corp_code"), str) or row["corp_code"] not in batch),
            ("fs_div", "fs_div", not isinstance(row.get("fs_div"), str) or row["fs_div"] not in {"CFS", "OFS"}),
            ("bsns_year", "bsns_year", str(row.get("bsns_year")) != str(year)),
            ("reprt_code", "reprt_code", row.get("reprt_code") != report),
            ("account_nm", "account_nm", not isinstance(row.get("account_nm"), str) or not row["account_nm"].strip()),
            ("currency_type", "currency", row.get("currency") is not None and not isinstance(row["currency"], str)),
            ("thstrm_dt_type", "thstrm_dt", row.get("thstrm_dt") is not None and not isinstance(row["thstrm_dt"], str)),
        ]
        failure = next(((reason, field) for reason, field, invalid in checks if invalid), None)
        if failure is not None:
            reason, field = failure
            counts = diagnostics["skipped_rows"]
            counts[reason] = counts.get(reason, 0) + 1
            samples = diagnostics["skipped_row_samples"].setdefault(reason, [])
            if len(samples) < 20:
                samples.append(_sample_value({"field": field, "type": type(row.get(field)).__name__,
                    "value": row.get(field), **{name: row.get(name) for name in ("corp_code", "reprt_code", "bsns_year")}}))
            continue
        try:
            _filing_date(row.get("rcept_no"))
        except ValueError:
            raise DartAPIError("DART malformed quarterly receipt") from None
        groups[(row["corp_code"], row["fs_div"], row["rcept_no"])].append(row)
    records = []
    normalize = DartFinancialStatementCollector._normalize_account_name
    for (corp, division, receipt), accounts in sorted(groups.items()):
        raw, conflicts = {}, {}
        for metric, aliases in ACCOUNTS.items():
            for alias in aliases:
                candidates = [row for row in accounts if normalize(row["account_nm"]) == normalize(alias)]
                if not candidates:
                    continue
                fields = ("account_nm", "thstrm_amount", "thstrm_add_amount", "thstrm_dt", "rcept_no", "currency", "fs_div")
                values = [{field: row.get(field) for field in fields} for row in candidates]
                if any(any(value[field] != values[0][field] for field in ("thstrm_dt", "rcept_no", "currency", "fs_div")) for value in values[1:]):
                    raise DartAPIError("DART conflicting quarterly accounts for one filing")
                if any(any(value[field] != values[0][field] for field in ("thstrm_amount", "thstrm_add_amount")) for value in values[1:]):
                    conflicts[metric] = alias
                else:
                    raw[metric] = values[0]
                break
        currencies = {row["currency"] for row in raw.values() if row.get("currency")}
        records.append({"corp_code": corp, "stock_codes": universe[corp], "bsns_year": year,
            "reprt_code": report, "fiscal_quarter": f"{year}Q{REPORTS[report]}",
            "fs_div": division, "rcept_no": receipt, "available_date": _filing_date(receipt),
            "currency": next(iter(currencies)) if len(currencies) == 1 else None, "raw_accounts": raw})
        if conflicts:
            records[-1]["conflicting_accounts"] = conflicts
    return records


def _derive(records: list[dict]) -> list[dict]:
    """Derive without mixing statement divisions, currencies or future filings.

    Q2/Q3 amount is a three-month value when its dated period spans three
    complete months, or the API supplies a separate cumulative add_amount.
    An explicit six/nine-month period takes precedence and denotes cumulative
    amount. Without either indicator its meaning is unknown. Cumulative-only
    Q2/Q3 subtract the previous quarter's YTD; Q4 subtracts Q3 YTD from annual.
    Missing add_amount may be reconstructed from known YTD plus a proved
    three-month amount. Each dependency is the latest same-division filing
    available on or before this receipt date; later corrections never leak.
    JSON null represents NaN, accompanied by a per-account missing reason.
    """
    history = defaultdict(lambda: defaultdict(list))
    derived = []
    for original in sorted(records, key=lambda row: (row["bsns_year"], REPORTS[row["reprt_code"]], row["available_date"], row["rcept_no"])):
        row = {**original, "cumulative": {}, "missing_reasons": {}, "derivation_inputs": {}}
        quarter = REPORTS[row["reprt_code"]]
        earlier = history[(row["corp_code"], row["bsns_year"], row["fs_div"])][quarter - 1]
        known = [value for value in earlier if value["available_date"] <= row["available_date"]]
        prior = max(known, key=lambda value: (value["available_date"], value["rcept_no"])) if known else None
        conflicts = row.get("conflicting_accounts", {})
        for metric in ACCOUNTS:
            account = row["raw_accounts"].get(metric)
            value = cumulative = None
            reason = ({"reason": "conflicting_accounts", "alias": conflicts[metric]}
                      if metric in conflicts else "missing_account")
            if account is not None:
                amount, added = _amount(account.get("thstrm_amount")), _amount(account.get("thstrm_add_amount"))
                months = _period_months(account)
                if quarter == 1:
                    value = cumulative = amount if amount is not None else added
                    reason = "missing_or_invalid_q1_amount"
                elif quarter == 4:
                    cumulative = amount if amount is not None else added
                    reason = "missing_or_invalid_annual_cumulative"
                else:
                    cumulative = added
                    if months == quarter * 3 and cumulative is None:
                        cumulative = amount
                    direct = months == 3 or added is not None and months != quarter * 3
                    value = amount if direct else None
                    reason = "ambiguous_thstrm_period" if amount is not None and cumulative is None and not direct else "missing_or_invalid_cumulative"
                if quarter > 1 and (value is None and cumulative is not None or value is not None and cumulative is None):
                    previous = prior["cumulative"].get(metric) if prior is not None else None
                    if previous is None:
                        reason = f"missing_q{quarter - 1}_cumulative"
                    else:
                        currency = account.get("currency")
                        previous_currency = prior["raw_accounts"].get(metric, {}).get("currency")
                        if not currency or not previous_currency:
                            reason = "missing_currency_for_derivation"
                        elif currency != previous_currency:
                            reason = "currency_mismatch"
                        else:
                            row["derivation_inputs"][metric] = prior["rcept_no"]
                            if value is None:
                                value = cumulative - previous
                            else:
                                cumulative = previous + value
            row[metric], row["cumulative"][metric] = value, cumulative
            if value is None:
                row["missing_reasons"][metric] = reason
        history[(row["corp_code"], row["bsns_year"], row["fs_div"])][quarter].append(row)
        derived.append(row)
    return derived


class _QuotaReached(Exception):
    pass


def _state(directory: Path) -> tuple[set[str], set[str]]:
    state = _read_json(directory / "_state.json", {"completed_batches": []})
    values = state.get("completed_batches")
    if not isinstance(values, list) or any(not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}_110(?:11|12|13|14)_[0-9a-f]{64}", value) for value in values):
        raise ValueError("DART invalid completed quarterly batches")
    for value in values:
        if not (directory / f"{'_'.join(value.split('_')[:2])}.jsonl").is_file():
            raise ValueError("DART completed quarterly archive is missing")
    skipped = state.get("batches_with_all_rows_skipped", [])
    if not isinstance(skipped, list) or any(not isinstance(value, str) or value not in values for value in skipped):
        raise ValueError("DART invalid skipped quarterly batches")
    return set(values), set(skipped)


def _quota(directory: Path) -> dict:
    quota = _read_json(directory / "_quota.json", {"days": {}})
    if not isinstance(quota.get("days"), dict):
        raise ValueError("DART invalid quarterly quota")
    for day, counter in quota["days"].items():
        try:
            date.fromisoformat(day)
        except (TypeError, ValueError):
            raise ValueError("DART invalid quarterly quota date") from None
        if (not isinstance(counter, dict) or type(counter.get("requests")) is not int
                or counter["requests"] < 0 or type(counter.get("provider_limited")) is not bool):
            raise ValueError("DART invalid quarterly quota counter")
    return quota


def _publish(directory: Path, year: int, report: str, records: list[dict]) -> int:
    archives = {code: read_rows(directory / f"{year}_{code}.jsonl") for code in REPORTS}
    stored = {(row["corp_code"], row["fs_div"], row["rcept_no"]): row for row in archives[report]}
    added = 0
    for row in records:
        key = row["corp_code"], row["fs_div"], row["rcept_no"]
        previous = stored.get(key)
        if previous is not None and (previous["raw_accounts"] != row["raw_accounts"]
                or previous.get("conflicting_accounts", {}) != row.get("conflicting_accounts", {})):
            raise DartAPIError("DART conflicting saved quarterly filing")
        if previous is None:
            archives[report].append(row)
            stored[key] = row
            added += 1
        else:
            previous["stock_codes"] = sorted(set(previous["stock_codes"]) | set(row["stock_codes"]))
    grouped = defaultdict(list)
    for row in _derive([row for rows in archives.values() for row in rows]):
        grouped[row["reprt_code"]].append(row)
    for code in REPORTS:
        path = directory / f"{year}_{code}.jsonl"
        if grouped[code] != archives[code] or code == report:
            write_rows(path, grouped[code])
    return added


def backfill(from_year: int, to_year: int, *, execute=False, max_requests=DEFAULT_MAX_REQUESTS,
             data_dir=DEFAULT_DATA_DIR, api_key=None, session=None, clock=None,
             corp_codes_path=DEFAULT_CORP_CODES, listing_dir=None, universe_source=None,
             retry_batches=None, batch_offset=0) -> dict:
    """Print a read-only plan by default; execution reserves each KST-day attempt.

    Completed batches, including 013, are skipped. Batch identity hashes its
    sorted corporate codes, so universe changes cannot reuse an index belonging
    to different companies. No retries are implicit. The endpoint has no receipt
    selector: preserve all returned/saved corrections, but this collector cannot
    recover an original version that OpenDART no longer returns. Future reports
    returning 013 are also completed. retry_batches lists completed batch IDs to
    refresh; an empty list or ["all"] refreshes all completed jobs in these years.
    batch_offset rotates request order for capped recurring refreshes without
    changing batch identities, filing versions or quota accounting.
    """
    if type(from_year) is not int or type(to_year) is not int or not 2015 <= from_year <= to_year <= 9999:
        raise ValueError("DART quarterly years must be ordered integers starting in 2015")
    if type(max_requests) is not int or max_requests < 1:
        raise ValueError("DART max_requests must be a positive integer")
    if type(batch_offset) is not int or batch_offset < 0:
        raise ValueError("DART batch_offset must be a nonnegative integer")
    directory = Path(data_dir) / "fundamentals/dart_quarterly"
    universe = load_universe(corp_codes_path=corp_codes_path,
        listing_dir=listing_dir if listing_dir is not None else Path(data_dir) / "disclosures/dart_full/list",
        universe_source=universe_source)
    codes = list(universe)
    batches = [codes[offset:offset + 100] for offset in range(0, len(codes), 100)]
    hashes = [hashlib.sha256(",".join(batch).encode()).hexdigest() for batch in batches]
    jobs = [(year, report, batch, f"{year}_{report}_{digest}") for year in range(from_year, to_year + 1)
            for report in REPORTS for batch, digest in zip(batches, hashes)]
    # A capped recurring refresh rotates so later reports/companies cannot starve.
    offset = batch_offset % len(jobs) if jobs else 0
    jobs = jobs[offset:] + jobs[:offset]
    completed, all_skipped = _state(directory)
    job_keys = {job[3] for job in jobs}
    if retry_batches is not None and (not isinstance(retry_batches, list)
            or any(not isinstance(key, str) for key in retry_batches)):
        raise ValueError("DART retry_batches must be a list of completed batch IDs")
    retry = (completed & job_keys if retry_batches in ([], ["all"]) else set(retry_batches or []))
    if not retry.issubset(completed & job_keys):
        raise ValueError("DART retry batches must be completed batches in the requested years and universe")
    plan = {"status": "dry_run", "dry_run": True, "from_year": from_year, "to_year": to_year,
            "companies": len(universe), "stock_codes": sum(map(len, universe.values())),
            "batches": len(batches), "reports": (to_year - from_year + 1) * 4,
            "requests": len(jobs), "pending_requests": len((job_keys - completed) | retry),
            "batches_with_all_rows_skipped": sorted(all_skipped & job_keys),
            "max_requests": max_requests, "data_dir": str(directory)}
    plan["batch_offset"] = offset
    if not execute:
        print(json.dumps(plan, ensure_ascii=False))
        return plan
    if not isinstance(api_key, str) or not api_key.strip():
        raise ValueError("DART API key is required for execution")
    now = clock if clock is not None else lambda: datetime.now(KST)
    owned_session = session is None
    summary = {**plan, "status": "ok", "dry_run": False, "requests_made": 0, "records_saved": 0,
               "conflicting_metrics": 0, "skipped_rows": {}, "skipped_row_samples": {}}
    with file_lock(directory / ".backfill.lock"):
        completed, all_skipped = _state(directory)
        quota = _quota(directory)
        if retry_batches in ([], ["all"]):
            retry = completed & job_keys
        try:
            for year, report, batch, key in jobs:
                if key in completed and key not in retry:
                    continue
                day = _kst(now()).date().isoformat()
                counter = quota["days"].setdefault(day, {"requests": 0, "provider_limited": False})
                if counter["provider_limited"]:
                    raise _QuotaReached("provider_status_020")
                if counter["requests"] >= max_requests:
                    raise _QuotaReached("daily_request_budget")
                counter["requests"] += 1
                _write_json(directory / "_quota.json", quota)
                summary["requests_made"] += 1
                try:
                    if session is None:
                        session = requests.Session()
                    response = session.get(URL, params={"crtfc_key": api_key, "corp_code": ",".join(batch),
                        "bsns_year": str(year), "reprt_code": report}, timeout=20)
                    response.raise_for_status()
                except Exception:
                    raise DartAPIError("DART quarterly transport failure") from None
                try:
                    payload = read_dart_payload(response)
                except DartAPIError as error:
                    if str(error) != "DART provider error status=020":
                        raise
                    counter["provider_limited"] = True
                    _write_json(directory / "_quota.json", quota)
                    raise _QuotaReached("provider_status_020") from None
                payload = _redact(payload, api_key)
                records = _records(payload, year, report, batch, universe, summary)
                summary["records_saved"] += _publish(directory, year, report, records)
                summary["conflicting_metrics"] += sum(len(row.get("conflicting_accounts", {})) for row in records)
                completed.add(key)
                retry.discard(key)
                if payload["status"] == "000" and not records:
                    all_skipped.add(key)
                else:
                    all_skipped.discard(key)
                _write_json(directory / "_state.json", {"completed_batches": sorted(completed),
                    "batches_with_all_rows_skipped": sorted(all_skipped)})
        except _QuotaReached as error:
            summary.update(status="quota_reached", reason=str(error))
        except DartAPIError as error:
            summary.update(status="error", error=_redact(str(error), api_key))
        finally:
            if owned_session and session is not None:
                session.close()
        summary["pending_requests"] = len((job_keys - completed) | retry)
        summary["batches_with_all_rows_skipped"] = sorted(all_skipped & job_keys)
        if summary["skipped_rows"]:
            with (directory / "_skipped_rows.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({name: summary[name] for name in (
                    "from_year", "to_year", "status", "requests_made", "skipped_rows",
                    "skipped_row_samples", "batches_with_all_rows_skipped")},
                    ensure_ascii=False, allow_nan=False) + "\n")
    return summary


def load_quarterly(as_of, *, data_dir=DEFAULT_DATA_DIR) -> pd.DataFrame:
    """Latest known CFS per corp/quarter, or OFS only if no CFS is known.

    as_of is the caller's trading-session date (aware timestamps use KST).
    Receipt dates must be strictly earlier, including weekend/holiday filings;
    the caller supplies real trading sessions, no weekday calendar is guessed.
    Keep missing CFS accounts missing rather than borrowing OFS values. Emit a
    row for every stock alias and expose receipt, currency and missing reasons.
    """
    session_date = pd.Timestamp(as_of)
    if pd.isna(session_date):
        raise ValueError("DART as_of trading session is required")
    if session_date.tzinfo is not None:
        session_date = session_date.tz_convert(KST)
    day = session_date.date().isoformat()
    directory = Path(data_dir) / "fundamentals/dart_quarterly"
    rows = []
    paths = sorted(directory.glob("[0-9][0-9][0-9][0-9]_110*.jsonl"))
    # Derivation dependencies are confined to a corporate business year. Keep
    # all years' output, but only one year's raw accounts/corrections in memory.
    for year, files in groupby(paths, key=lambda path: path.stem[:4]):
        records = []
        for path in files:
            for row in read_rows(path):
                if str(row["bsns_year"]) != year:
                    raise ValueError("DART quarterly archive business year disagrees with filename")
                if row["available_date"] != _filing_date(row["rcept_no"]):
                    raise ValueError("DART quarterly availability does not match receipt")
                if row["available_date"] < day:
                    records.append(row)
        latest = {}
        for row in _derive(records):
            key = row["corp_code"], row["fiscal_quarter"]
            rank = row["fs_div"] == "CFS", row["available_date"], row["rcept_no"]
            if key not in latest or rank > latest[key][0]:
                latest[key] = rank, row
        for key in sorted(latest):
            row = latest[key][1]
            rows.extend(tuple(stock if field == "stock_code" else row[field] for field in COLUMNS)
                        for stock in row["stock_codes"])
        del records, latest
    # Preserve the original corp/quarter order and each filing's alias order.
    rows.sort(key=lambda row: row[1:3])
    frame = pd.DataFrame(rows, columns=COLUMNS)
    for metric in ACCOUNTS:
        frame[metric] = pd.to_numeric(frame[metric]).astype(float)
    return frame


def _growth(current, previous):
    if pd.isna(current) or pd.isna(previous):
        return float("nan")
    if current < 0 and current < previous:
        return -200.0
    if previous == 0:
        return float("nan")
    return min(500.0, (current - previous) / abs(previous) * 100)


def yoy_signals(frame: pd.DataFrame) -> pd.DataFrame:
    """Apply vol1_2011_05.md conventions to standalone, same-quarter YoY (%).

    A new/larger loss is -200%; other growth uses abs(prior) as denominator,
    so loss reduction/return to profit is positive. Cap growth at 500%. A zero
    denominator or missing observation is NaN, except a new loss is -200%.
    Operating leverage means OI YoY > revenue YoY > 0. Exact phase rules, in
    priority order: 1 = positive revenue YoY, revenue >= all six consecutive
    quarters including this one, and OI YoY <= revenue YoY; 2 = operating
    leverage; 3 = positive revenue YoY, OI YoY < revenue YoY, and not phase 1;
    4 = revenue YoY < 0 and OI YoY < revenue YoY (faster income decline).
    Other/incomplete cases have nullable phase. Six quarters must all exist
    with revenue; ties qualify as a high. Phase 1/4 are explicit operational
    interpretations, since the source does not publish exact quantitative
    definitions. Input must already be point-in-time, one version per quarter;
    duplicate stock aliases are allowed only with identical financial values.
    """
    result = frame.copy()
    values = {}
    keys = []
    for row in frame.to_dict("records"):
        match = re.fullmatch(r"([0-9]{4})Q([1-4])", str(row["fiscal_quarter"]))
        if match is None:
            raise ValueError("DART invalid fiscal quarter in signals")
        period = int(match[1]) * 4 + int(match[2]) - 1
        key = row["corp_code"], period
        pair = row["revenue"], row["operating_income"]
        if key in values and any(not (pd.isna(left) and pd.isna(right)) and left != right for left, right in zip(values[key], pair)):
            raise ValueError("DART signals require one financial version per corporate quarter")
        values[key] = pair
        keys.append(key)
    revenue_yoy, income_yoy, phases = [], [], []
    for corp, period in keys:
        current = values[corp, period]
        previous = values.get((corp, period - 4), (float("nan"), float("nan")))
        revenue, income = [_growth(now, before) for now, before in zip(current, previous)]
        revenue_yoy.append(revenue)
        income_yoy.append(income)
        history = [values.get((corp, period - offset), (float("nan"),))[0] for offset in range(6)]
        high = all(pd.notna(value) for value in history) and current[0] >= max(history)
        phase = None
        if pd.notna(revenue) and pd.notna(income):
            if revenue > 0 and high and income <= revenue:
                phase = 1
            elif income > revenue > 0:
                phase = 2
            elif revenue > 0 and income < revenue:
                phase = 3
            elif revenue < 0 and income < revenue:
                phase = 4
        phases.append(phase)
    result["revenue_yoy"], result["operating_income_yoy"] = revenue_yoy, income_yoy
    result["operating_leverage"] = (result["operating_income_yoy"] > result["revenue_yoy"]) & (result["revenue_yoy"] > 0)
    result["phase"] = pd.array(phases, dtype="Int64")
    return result


def main() -> None:
    """Module CLI supports explicit refreshes without removing checkpoints."""
    import argparse
    import os

    from src.config.settings import get_project_root, load_project_env

    parser = argparse.ArgumentParser(description="Backfill point-in-time quarterly DART financials")
    parser.add_argument("--from-year", type=int, required=True)
    parser.add_argument("--to-year", type=int, required=True)
    parser.add_argument("--max-requests", type=int, default=DEFAULT_MAX_REQUESTS,
                        help="Request cap per KST day across runs (default: 3000)")
    parser.add_argument("--execute", action="store_true", help="Load environment, call DART and save progress")
    parser.add_argument("--retry-batches", nargs="*", metavar="BATCH_ID",
                        help="Re-request listed completed batch IDs; omit IDs or use all for all completed batches in these years")
    args = parser.parse_args()
    api_key, data_dir = None, DEFAULT_DATA_DIR
    if args.execute:
        load_project_env()
        api_key = os.getenv("DART_API_KEY")
        data_dir = Path(os.getenv("HQA_DATA_DIR", str(DEFAULT_DATA_DIR))).expanduser()
        if not data_dir.is_absolute():
            data_dir = get_project_root() / data_dir
    try:
        summary = backfill(args.from_year, args.to_year, execute=args.execute,
            max_requests=args.max_requests, api_key=api_key, data_dir=data_dir,
            retry_batches=args.retry_batches)
    except (ValueError, OSError, DartAPIError):
        print(json.dumps({"status": "error", "error": "DART quarterly configuration or archive is invalid"}))
        raise SystemExit(1) from None
    if args.execute:
        print(json.dumps(summary, ensure_ascii=False))
    if summary["status"] == "error":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
