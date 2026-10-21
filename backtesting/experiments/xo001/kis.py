"""Strict PAPER-only REST transport. No settings imports, dotenv, retry, or WS."""
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import http.client
import json
import os
from pathlib import Path
import re
import ssl
import time
from urllib.parse import urlencode, urlsplit

from . import KST, Refused, aware, now
from .storage import encoded, load, locked, mkdir, replace_private, sha

BASE = "https://openapivts.koreainvestment.com:29443"
TOKEN, APPROVAL = "/oauth2/tokenP", "/oauth2/Approval"
PRICE = "/uapi/domestic-stock/v1/quotations/inquire-price"
BOOK = "/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn"
ORDER = "/uapi/domestic-stock/v1/trading/order-cash"
CANCEL = "/uapi/domestic-stock/v1/trading/order-rvsecncl"
INQUIRY = "/uapi/domestic-stock/v1/trading/inquire-daily-ccld"
ALLOW = {("POST", TOKEN): {None}, ("POST", APPROVAL): {None},
         ("GET", PRICE): {"FHKST01010100"}, ("GET", BOOK): {"FHKST01010200"},
         ("POST", ORDER): {"VTTC0012U", "VTTC0011U"},
         ("POST", CANCEL): {"VTTC0013U"}, ("GET", INQUIRY): {"VTTC8001R"}}
ORDER_TRS = {"VTTC0012U", "VTTC0011U", "VTTC0013U"}
PRIVATE_FIELDS = {"access_token", "refresh_token", "token", "approval_key", "appkey", "app_key",
                  "appsecret", "app_secret", "secretkey", "secret_key", "authorization", "cano", "hts_id"}


class NotTransmitted(Refused):
    """The order HTTP call provably never began."""


def validate_request(method, url, tr_id=None):
    try:
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or parsed.hostname != "openapivts.koreainvestment.com"
                or parsed.port != 29443 or parsed.username or parsed.password or parsed.query or parsed.fragment
                or url != BASE + parsed.path or tr_id not in ALLOW.get((method, parsed.path), set())):
            raise Refused("request outside the exact XO001 PAPER allowlist")
    except ValueError:
        raise Refused("request outside the exact XO001 PAPER allowlist") from None
    return parsed.path


@dataclass(repr=False)
class Credentials:
    app_key: str = field(repr=False)
    app_secret: str = field(repr=False)
    cano: str = field(repr=False)
    product: str = field(repr=False)
    hts_id: str = field(repr=False)

    @classmethod
    def environment(cls):
        names = ("APP_KEY", "APP_SECRET", "CANO", "ACNT_PRDT_CD", "HTS_ID")
        values = [os.environ.get("XO001_KIS_PAPER_" + name) for name in names]
        if not all(isinstance(v, str) and v.strip() for v in values):
            raise Refused("missing required XO001 PAPER environment variables")
        if not re.fullmatch(r"\d{8}", values[2]) or not re.fullmatch(r"\d{2}", values[3]):
            raise Refused("invalid XO001 PAPER account identity")
        return cls(*values)

    def identifiers(self):
        return {"key_sha256": sha(self.app_key.encode()),
                "account_sha256": sha(encoded([self.cano, self.product, self.hts_id]))}


class Redactor:
    def __init__(self, credentials):
        self.secrets = {credentials.app_key, credentials.app_secret, credentials.cano,
                        credentials.hts_id, credentials.cano + "-" + credentials.product}

    def add(self, value):
        if value:
            self.secrets.add(value)

    def text(self, value):
        for secret in sorted(self.secrets, key=len, reverse=True):
            if secret:
                value = value.replace(secret, "[REDACTED]")
        # Scrub response echoes, including escaped JSON token fields.
        return re.sub(r'(?i)("(?:' + "|".join(sorted(PRIVATE_FIELDS)) + r')"\s*:\s*")[^"]*',
                      r'\1[REDACTED]', value)

    def clean(self, value):
        self._register(value)
        return self._clean(value)

    def _register(self, value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key.lower() in PRIVATE_FIELDS and isinstance(item, str):
                    self.add(item)
                self._register(item)
        elif isinstance(value, list):
            for item in value:
                self._register(item)

    def _clean(self, value):
        if isinstance(value, dict):
            return {self.text(key): "[REDACTED]" if key.lower() in PRIVATE_FIELDS else self._clean(item)
                    for key, item in value.items()}
        if isinstance(value, list):
            return [self._clean(v) for v in value]
        return self.text(value) if isinstance(value, str) else value


class HTTPS:
    """No proxy environment, redirect following, DNS override, or secondary host."""
    def request(self, method, url, *, headers, params=None, body=None, timeout=2., before_send=None):
        validate_request(method, url, headers.get("tr_id"))
        if headers.get("tr_id") in ORDER_TRS and before_send is None:
            raise NotTransmitted("order diagnostic requires a post-connect transmission guard")
        parsed = urlsplit(url)
        connection = http.client.HTTPSConnection(parsed.hostname, parsed.port,
                                               timeout=timeout, context=ssl.create_default_context())
        path = parsed.path + ("?" + urlencode(params) if params else "")
        request_body = encoded(body) if body is not None else None
        try:
            connection.connect()  # Complete DNS/TCP/TLS before the final authorization check.
            if before_send is not None:
                before_send()
            connection.request(method, path, body=request_body, headers=headers)
            response = connection.getresponse()
            raw = response.read(2_000_001)
            if len(raw) > 2_000_000:
                raise Refused("oversized PAPER response")
            return response.status, dict(response.getheaders()), raw
        finally:
            connection.close()


class Client:
    def __init__(self, credentials, root, rate, *, transport=None, clock=now, monotonic=time.monotonic):
        self.credentials, self.root, self.rate = credentials, Path(root), rate
        self.transport, self.clock, self.monotonic = transport or HTTPS(), clock, monotonic
        self.redactor = Redactor(credentials)
        self.owner = None
        self.connection_id = os.urandom(12).hex()
        self.sequence = 0

    def __enter__(self):
        digest = self.credentials.identifiers()["key_sha256"]
        directory = self.root / "auth"
        mkdir(directory)
        self.cache = directory / (digest + ".json")
        self._check_private(directory, directory=True)
        if self.cache.exists():
            self._check_private(self.cache)
        self.owner = locked(directory / (digest + ".owner"), nonblocking=True)
        self.owner.__enter__()
        return self

    def __exit__(self, *args):
        if self.owner:
            self.owner.__exit__(*args)
            self.owner = None

    @staticmethod
    def _check_private(path, directory=False):
        status = path.lstat()
        if path.is_symlink() or status.st_uid != os.geteuid() or status.st_mode & 0o777 != (0o700 if directory else 0o600):
            raise Refused("PAPER token cache must be owner-only")

    def _wire(self, method, path, tr_id, *, priority, deadline, params=None, body=None, token=None, continuation="", permit=None):
        url = BASE + path
        validate_request(method, url, tr_id)
        self.rate.acquire(priority, deadline)
        requested = aware(self.clock())
        if requested >= aware(deadline):
            raise NotTransmitted("request deadline passed after rate reservation")
        if tr_id in ORDER_TRS:
            from .diagnostic import OrderPermit
            if not isinstance(permit, OrderPermit) or permit.used:
                raise NotTransmitted("order diagnostic is OFF or intent permit invalid at transmission")
        def before_send():
            from .recorder import clock_evidence, clock_valid
            evidence = clock_evidence()
            at = aware(self.clock())
            if not clock_valid(evidence):
                raise NotTransmitted("order not transmitted: clock unverified after connect")
            if at + timedelta(seconds=evidence["error_bound_seconds"]) >= min(aware(deadline), aware(permit.end)):
                raise NotTransmitted("order not transmitted: deadline passed after connect")
            if not permit.consume(tr_id, body, at):
                raise NotTransmitted("order not transmitted: intent permit invalid after connect")

        headers = {"content-type": "application/json; charset=utf-8"}
        if tr_id:
            headers.update({"tr_id": tr_id, "custtype": "P", "appkey": self.credentials.app_key,
                            "appsecret": self.credentials.app_secret, "authorization": "Bearer " + token})
        if continuation:
            if path != INQUIRY or continuation != "N":
                raise Refused("continuation is only authorized for PAPER daily inquiry")
            headers["tr_cont"] = continuation
        if path == TOKEN:
            state = load(self.cache)
            state["last_attempt"] = requested.isoformat()
            replace_private(self.cache, state)
        started = self.monotonic()
        self.sequence += 1
        error, status, response_headers, raw, data = None, None, {}, b"", None
        transport_options = {"before_send": before_send} if tr_id in ORDER_TRS else {}
        try:
            status, response_headers, raw = self.transport.request(method, url, headers=headers,
                params=params, body=body, timeout=min(2., (aware(deadline) - requested).total_seconds()), **transport_options)
            if not 200 <= status < 300:
                error = "http_status_" + str(status)
            try:
                data = json.loads(raw)
                if not isinstance(data, dict):
                    error = "invalid_json_shape"
                    data = None
            except (ValueError, UnicodeError):
                error = "invalid_json"
            if data is not None and data.get("rt_cd") not in (None, "0"):
                error = "provider_rejection"
        except (OSError, TimeoutError, http.client.HTTPException):
            # Exception messages can contain URLs, headers or credentials. Never persist them.
            error = "transport_error"
        received = aware(self.clock())
        return {"requested_utc": requested.astimezone(timezone.utc).isoformat(),
                "received_utc": received.astimezone(timezone.utc).isoformat(),
                "requested_monotonic": started, "received_monotonic": self.monotonic(),
                "sequence": self.sequence, "connection_id": self.connection_id + f".{self.sequence:08d}",
                "path": path, "tr_id": tr_id, "http_status": status, "error": error,
                "response_headers": {k.lower(): v for k, v in response_headers.items()
                                     if k.lower() in {"tr_cont", "date", "content-type"}},
                "raw": raw.decode("utf-8", errors="replace"), "wire_sha256": sha(raw),
                "raw_encoding": "UTF-8; invalid bytes explicitly replaced", "data": data}

    def token(self, *, priority, deadline):
        if self.owner is None:
            raise Refused("PAPER client requires exclusive key ownership")
        self._check_private(self.cache) if self.cache.exists() else None
        state = load(self.cache) if self.cache.exists() else {}
        current = aware(self.clock())
        if state.get("token") and aware(state["expires_at"]) > current + timedelta(seconds=2):
            self.redactor.add(state["token"])
            return state["token"]
        if state.get("last_attempt") and (current - aware(state["last_attempt"])).total_seconds() < 60:
            raise Refused("PAPER token reissue guard: 60 seconds")
        # Persist before the call: timeout/crash/restart cannot bypass the 60s guard.
        state["last_attempt"] = current.isoformat()
        replace_private(self.cache, state)
        result = self._wire("POST", TOKEN, None, priority=priority, deadline=deadline,
            body={"grant_type": "client_credentials", "appkey": self.credentials.app_key,
                  "appsecret": self.credentials.app_secret})
        data = result["data"]
        if result["error"] or not data or not isinstance(data.get("access_token"), str) or not data["access_token"]:
            raise Refused("PAPER token request failed")
        token = data["access_token"]
        self.redactor.add(token)
        try:
            if data.get("access_token_token_expired"):
                expires = datetime.fromisoformat(data["access_token_token_expired"])
                expires = expires.replace(tzinfo=KST) if expires.tzinfo is None else expires
            elif type(data.get("expires_in")) in (int, float) and data["expires_in"] > 0:
                expires = aware(result["requested_utc"]) + timedelta(seconds=data["expires_in"])
            else:
                raise Refused("PAPER token response has no actual expiry")
        except (ValueError, TypeError, OverflowError):
            raise Refused("PAPER token response has no valid actual expiry") from None
        if aware(expires) <= aware(self.clock()):
            raise Refused("PAPER token already expired")
        replace_private(self.cache, {"token": token, "expires_at": aware(expires).isoformat(),
                                    "last_attempt": aware(result["requested_utc"]).isoformat()})
        return token

    def request(self, method, path, tr_id, *, priority="event", deadline, params=None, body=None, permit=None, continuation=""):
        validate_request(method, BASE + path, tr_id)
        if tr_id in ORDER_TRS:
            # Only the diagnostic can provide the intent-specific permit; no public bool override.
            from .diagnostic import OrderPermit
            if not isinstance(permit, OrderPermit) or permit.used:
                raise Refused("order diagnostic is OFF or intent permit invalid")
        if path in (TOKEN, APPROVAL):
            raise Refused("authentication is available only through its dedicated method")
        try:
            token = self.token(priority=priority, deadline=deadline)
        except Refused:
            if tr_id in ORDER_TRS:
                raise NotTransmitted("order not transmitted: authentication unavailable") from None
            raise
        from .rate import RateUnavailable
        try:
            result = self._wire(method, path, tr_id, priority=priority, deadline=deadline,
                                params=params, body=body, token=token, continuation=continuation, permit=permit)
        except RateUnavailable:
            if tr_id in ORDER_TRS:
                raise NotTransmitted("order not transmitted: request budget exhausted") from None
            raise
        return self.redactor.clean(result)

    def approval_probe(self, *, deadline):
        # Approval is tested only as a capability. No WebSocket is implemented/enabled.
        result = self._wire("POST", APPROVAL, None, priority="event", deadline=deadline,
            body={"grant_type": "client_credentials", "appkey": self.credentials.app_key,
                  "secretkey": self.credentials.app_secret})
        if result["data"]:
            self.redactor.add(result["data"].get("approval_key"))
        return self.redactor.clean(result)

    def quote(self, code, *, book, priority, deadline):
        if not re.fullmatch(r"[0-9A-Z]{6}", code):
            raise Refused("invalid KRX stock code")
        return self.request("GET", BOOK if book else PRICE, "FHKST01010200" if book else "FHKST01010100",
            priority=priority, deadline=deadline,
            params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code})
