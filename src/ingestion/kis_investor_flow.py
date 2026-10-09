"""Dedicated PAPER-data quotation collector; requests require explicit execution."""
from __future__ import annotations

import hashlib
import json
import math
import os
import pwd
import re
import stat
import time
from datetime import datetime, time as datetime_time, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo
from urllib.parse import urlsplit

import requests

from src.utils.stock_codes import is_stock_code
from . import krx_market
from .storage import atomic_write, file_lock, save_episodes

KST = ZoneInfo("Asia/Seoul")
PAPER_BASE_URL = "https://openapivts.koreainvestment.com:29443"
TOKEN_PATH = "/oauth2/tokenP"
INVESTOR_PATH = "/uapi/domestic-stock/v1/quotations/inquire-investor"
QUOTATION_ALLOWLIST = {INVESTOR_PATH: "FHKST01010900"}
DEFAULT_DATA_DIR = Path(__file__).resolve().parents[2] / "data"
MAX_ATTEMPTS = 3
# These message codes and investor fields require confirmation in the PAPER smoke test.
RATE_LIMIT_CODES = {"EGW00201", "EGW00133"}
TOKEN_REJECTION_CODES = {"EGW00121", "EGW00123"}
FLOW_FIELDS = {
    "individual_net_quantity": "prsn_ntby_qty",
    "foreign_net_quantity": "frgn_ntby_qty",
    "institution_net_quantity": "orgn_ntby_qty",
    "individual_net_value": "prsn_ntby_tr_pbmn",
    "foreign_net_value": "frgn_ntby_tr_pbmn",
    "institution_net_value": "orgn_ntby_tr_pbmn",
    "close": "stck_clpr",
}


class KisFlowError(RuntimeError):
    """Only locally defined reasons may reach logs or persisted failure lists."""

    def __init__(self, reason: str, *, retryable: bool = True):
        super().__init__(reason)
        self.reason, self.retryable = reason, retryable


def validate_request(method: str, url: str) -> None:
    """Reject every destination except PAPER token issuance and allowlisted GETs."""
    parts = urlsplit(url)
    if (parts.scheme != "https" or parts.netloc != "openapivts.koreainvestment.com:29443"
            or parts.query or parts.fragment):
        raise ValueError("KIS data requests require the dedicated PAPER domain")
    if method == "GET" and parts.path in QUOTATION_ALLOWLIST:
        return
    if method == "POST" and parts.path == TOKEN_PATH:
        return
    raise ValueError("KIS data request is outside the read-only quotation allowlist")


def _codes(stock_codes) -> list[str]:
    codes = list(stock_codes)
    if not codes or any(not is_stock_code(code) for code in codes):
        raise ValueError("investor flow requires six-character KRX stock codes")
    return list(dict.fromkeys(codes))


def load_stock_codes(data_dir: str | Path, supplied=None) -> list[str]:
    """Use the latest local full-market universe, or an explicit list if absent."""
    files = sorted((Path(data_dir) / "market" / "krx_daily").glob("*/*.jsonl"))
    if files:
        return _codes(row["stock_code"] for row in krx_market.load_universe(files[-1].stem, data_dir))
    if supplied is None:
        raise ValueError("no stored KRX universe; supply stock codes explicitly")
    return _codes(supplied)


def _kst(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("KIS collector clock requires a timezone")
    return value.astimezone(KST)


def _number(value, *, integer=False, positive=False) -> str:
    if (not isinstance(value, (str, int)) or isinstance(value, bool)
            or not re.fullmatch(r"[+-]?(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)(?:\.[0-9]+)?", str(value).strip())):
        raise KisFlowError("invalid_row", retryable=False)
    number = Decimal(str(value).strip().replace(",", ""))
    if (integer and number != number.to_integral_value()) or (positive and number <= 0):
        raise KisFlowError("invalid_row", retryable=False)
    text = format(number, "f")
    return (text.rstrip("0").rstrip(".") if "." in text else text) if number else "0"


def _cache_owner_uids() -> set[int]:
    owners = {0}
    try:
        owners.add(pwd.getpwnam("hqa").pw_uid)
    except KeyError:
        pass
    return owners


class KisInvestorFlowCollector:
    def __init__(self, app_key, app_secret, session=None, clock=None, sleeper=None,
                 data_dir=DEFAULT_DATA_DIR, requests_per_second=2.0):
        if (isinstance(requests_per_second, bool) or not math.isfinite(requests_per_second)
                or requests_per_second <= 0):
            raise ValueError("requests_per_second must be finite and positive")
        self.app_key, self.app_secret = app_key, app_secret
        self.session = session
        self.clock = clock if clock is not None else lambda: datetime.now(KST)
        self.sleeper = sleeper if sleeper is not None else time.sleep
        self.data_dir = Path(data_dir)
        self.requests_per_second = requests_per_second
        self._last_request_at = None

    def _throttle(self) -> None:
        now = _kst(self.clock())
        if self._last_request_at is not None:
            delay = 1 / self.requests_per_second - (now - self._last_request_at).total_seconds()
            if delay > 0:
                self.sleeper(delay)
        self._last_request_at = _kst(self.clock())

    def _request(self, method: str, path: str, *, throttle=True, **kwargs) -> dict:
        url = PAPER_BASE_URL + path
        validate_request(method, url)  # Before building any provider request.
        if throttle:
            self._throttle()
        if self.session is None:
            raise RuntimeError("KIS execution requires an active session")
        try:
            call = self.session.get if method == "GET" else self.session.post
            response = call(url, timeout=30, allow_redirects=False, **kwargs)
        except requests.RequestException:
            raise KisFlowError("network_error") from None
        if response.status_code == 429:
            self.requests_per_second /= 2
            raise KisFlowError("rate_limited")
        if response.status_code == 401:
            raise KisFlowError("token_rejected")
        try:
            body = response.json()
        except ValueError:
            raise KisFlowError("invalid_response") from None
        if not isinstance(body, dict):
            raise KisFlowError("invalid_response")
        message_code = body.get("msg_cd")
        if message_code is not None and not isinstance(message_code, str):
            raise KisFlowError("invalid_response")
        if message_code in RATE_LIMIT_CODES:
            self.requests_per_second /= 2
            raise KisFlowError("rate_limited")
        if message_code in TOKEN_REJECTION_CODES:
            raise KisFlowError("token_rejected")
        if response.status_code != 200:
            raise KisFlowError("http_error")
        return body

    def _cache_path(self) -> Path:
        # Tokens live outside the market/ops directories copied back to the PC.
        key_hash = hashlib.sha256(self.app_key.encode()).hexdigest()
        return self.data_dir / ".kis_tokens" / f"{key_hash}.json"

    def _prepare_cache(self, path: Path) -> None:
        owners = _cache_owner_uids()
        if os.geteuid() not in owners:
            raise ValueError("KIS token cache must be created by root or hqa")
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        directory = path.parent.lstat()
        if (not stat.S_ISDIR(directory.st_mode) or directory.st_uid not in owners
                or stat.S_IMODE(directory.st_mode) != 0o700):
            raise ValueError("KIS token directory must be root/hqa-owned and mode 0700")

    def _read_cache(self, path: Path, now: datetime) -> dict:
        if not path.exists() and not path.is_symlink():
            return {"request_day": now.date().isoformat(), "requests": 0,
                    "rejection_refresh_used": False, "rejected": False,
                    "access_token": None, "expires_at": None}
        info = path.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid not in _cache_owner_uids()
                or stat.S_IMODE(info.st_mode) != 0o600):
            raise ValueError("KIS token cache must be a root/hqa-owned regular 0600 file")
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
            day = datetime.strptime(state["request_day"], "%Y-%m-%d").date()
            valid = (day <= now.date() and type(state["requests"]) is int and 0 <= state["requests"] <= 2
                     and type(state["rejection_refresh_used"]) is bool and type(state["rejected"]) is bool)
            if state["access_token"] is not None:
                valid = valid and isinstance(state["access_token"], str) and bool(state["access_token"].strip())
                _kst(datetime.fromisoformat(state["expires_at"]))
            elif state["expires_at"] is not None:
                valid = False
            if not valid:
                raise ValueError
        except (ValueError, TypeError, KeyError):
            raise ValueError("invalid KIS token cache; daily request history must be preserved") from None
        if day != now.date():
            state.update(request_day=now.date().isoformat(), requests=0, rejection_refresh_used=False)
        return state

    def _access_token(self, rejected_token=None) -> str:
        validate_request("POST", PAPER_BASE_URL + TOKEN_PATH)
        path = self._cache_path()
        self._prepare_cache(path)
        with file_lock(path.with_suffix(".json.lock")):
            now = _kst(self.clock())
            state = self._read_cache(path, now)
            if rejected_token is not None and state["access_token"] == rejected_token:
                state["rejected"] = True
                atomic_write(path, json.dumps(state) + "\n")
            if (state["access_token"] is not None and not state["rejected"]
                    and _kst(datetime.fromisoformat(state["expires_at"])) > now):
                return state["access_token"]
            refresh = state["rejected"]
            if (state["requests"] >= (2 if refresh else 1)
                    or (refresh and state["rejection_refresh_used"])):
                raise KisFlowError("token_daily_limit", retryable=False)
            # A rate-limit wait may cross KST midnight; reserve on the request's day.
            self._throttle()
            request_at = _kst(self.clock())
            if request_at.date() != now.date():
                state = self._read_cache(path, request_at)
            now = request_at
            state.update(requests=state["requests"] + 1, access_token=None, expires_at=None)
            if refresh:
                state["rejection_refresh_used"] = True
            # Persist attempts before the POST, including unsuccessful issuance or crashes.
            atomic_write(path, json.dumps(state) + "\n")
            body = self._request("POST", TOKEN_PATH, throttle=False,
                headers={"content-type": "application/json"},
                json={"grant_type": "client_credentials", "appkey": self.app_key, "appsecret": self.app_secret})
            token = body.get("access_token")
            if not isinstance(token, str) or not token or any(char.isspace() for char in token):
                raise KisFlowError("invalid_token_response", retryable=False)
            try:
                expiries = []
                if "expires_in" in body:
                    seconds = int(_number(body["expires_in"], integer=True, positive=True))
                    expiries.append(now + timedelta(seconds=seconds))
                if "access_token_token_expired" in body:
                    expiries.append(datetime.strptime(body["access_token_token_expired"],
                                                      "%Y-%m-%d %H:%M:%S").replace(tzinfo=KST))
                if not expiries or min(expiries) <= now:
                    raise ValueError
            except (ValueError, TypeError, OverflowError, KisFlowError):
                raise KisFlowError("invalid_token_expiry", retryable=False) from None
            state.update(access_token=token, expires_at=min(expiries).isoformat(), rejected=False)
            atomic_write(path, json.dumps(state) + "\n")
            return token

    def _fetch_stock(self, code: str) -> list[dict]:
        token = self._access_token()
        while True:
            try:
                body = self._request("GET", INVESTOR_PATH,
                    headers={"authorization": "Bearer " + token, "appkey": self.app_key,
                             "appsecret": self.app_secret, "tr_id": QUOTATION_ALLOWLIST[INVESTOR_PATH],
                             "custtype": "P"},
                    params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code})
                break
            except KisFlowError as exc:
                if exc.reason != "token_rejected":
                    raise
                token = self._access_token(rejected_token=token)
        if str(body.get("rt_cd")) != "0":
            raise KisFlowError("provider_error")
        rows = body.get("output")
        if not isinstance(rows, list) or not rows:
            raise KisFlowError("empty_output")
        return rows

    def _rows(self, code: str, raw_rows: list[dict], now: datetime) -> tuple[list[dict], int, int]:
        rows, dates, provisional, skipped_invalid = [], {}, 0, 0
        for raw in raw_rows:
            try:
                day_text = raw["stck_bsop_date"]
                if not isinstance(day_text, str) or not re.fullmatch(r"[0-9]{8}", day_text):
                    raise ValueError
                day = datetime.strptime(day_text, "%Y%m%d").date()
                if day > now.date():
                    raise ValueError
                values, invalid = {}, False
                for field, source in FLOW_FIELDS.items():
                    value = raw[source]
                    try:
                        values[field] = _number(value)
                    except KisFlowError:
                        values[field] = value
                        invalid = True
                if day in dates and dates[day] != values:
                    raise ValueError
                duplicate = day in dates
                dates[day] = values
                if invalid:
                    skipped_invalid += 1
                    continue
                if duplicate:
                    continue
                if day == now.date() and now.time() < datetime_time(18, 30):
                    provisional += 1
                    continue
                row = {"stock_code": code, "trade_date": day.isoformat(),
                       **{field: _number(value, integer=field.endswith("quantity"), positive=field == "close")
                          for field, value in values.items()},
                       "source": "KIS_FHKST01010900", "net_value_unit": "provider_reported_unverified"}
            except (ValueError, TypeError, KeyError):
                raise KisFlowError("invalid_row", retryable=False) from None
            version = hashlib.sha256(json.dumps(row, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            rows.append({**row, "collected_at": now.isoformat(), "available_at": now.isoformat(), "version": version})
        return rows, provisional, skipped_invalid

    def collect_day(self, stock_codes, *, execute: bool = False) -> dict:
        """Append validated per-stock/date revisions; dry runs read neither keys nor caches."""
        codes = _codes(stock_codes)
        started = _kst(self.clock())
        summary = {"dry_run": not execute, "started_at": started.isoformat(), "stocks_planned": len(codes),
                   "stocks_attempted": 0, "stocks_ok": 0, "stocks_failed": 0,
                   "rows_saved": 0, "provisional_rows_skipped": 0, "failures": [],
                   "rows_skipped_invalid": 0, "stocks_no_valid_rows": 0, "no_valid_rows": []}
        if not execute:
            return summary
        if not all(isinstance(value, str) and value.strip() for value in (self.app_key, self.app_secret)):
            raise ValueError("KIS_DATA_APP_KEY and KIS_DATA_APP_SECRET are required for execution")
        validate_request("POST", PAPER_BASE_URL + TOKEN_PATH)
        validate_request("GET", PAPER_BASE_URL + INVESTOR_PATH)
        own_session = self.session is None
        if own_session:
            self.session = requests.Session()
        pending = {}
        try:
            for code in codes:
                summary["stocks_attempted"] += 1
                for attempt in range(MAX_ATTEMPTS):
                    try:
                        raw_rows = self._fetch_stock(code)
                        rows, provisional, skipped_invalid = self._rows(code, raw_rows, _kst(self.clock()))
                    except KisFlowError as exc:
                        if exc.retryable and attempt + 1 < MAX_ATTEMPTS:
                            self.sleeper(2 ** attempt)
                            continue
                        summary["failures"].append({"stock_code": code, "attempts": attempt + 1, "reason": exc.reason})
                        summary["stocks_failed"] += 1
                    else:
                        for row in rows:
                            day = row["trade_date"].replace("-", "")
                            pending.setdefault(day, []).append(row)
                        if not rows and skipped_invalid and not provisional:
                            summary["stocks_no_valid_rows"] += 1
                            summary["no_valid_rows"].append({"stock_code": code, "attempts": attempt + 1,
                                                             "reason": "no_valid_rows"})
                        else:
                            summary["stocks_ok"] += 1
                        summary["provisional_rows_skipped"] += provisional
                        summary["rows_skipped_invalid"] += skipped_invalid
                    break
            for day, rows in sorted(pending.items()):
                path = self.data_dir / "market" / "investor_flow" / day[:4] / f"{day}.jsonl"
                summary["rows_saved"] += save_episodes(path, rows,
                    identity=lambda item: item["stock_code"], revision=lambda item: item["version"])
            summary["completed_at"] = _kst(self.clock()).isoformat()
            atomic_write(self.data_dir / "market" / "investor_flow" / "_last_run.json",
                         json.dumps(summary, ensure_ascii=False) + "\n")
            return summary
        finally:
            if own_session:
                self.session.close()
                self.session = None
