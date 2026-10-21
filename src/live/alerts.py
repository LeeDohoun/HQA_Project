"""Channel-agnostic operator alerts for the strategy runtime.

Rules turn a daily summary, status payloads, the shadow ledger, incident files and
(optional) backend reads into Alert objects. Dedupe state lives in
<data_dir>/live/alerts/state.json; nothing is written without execute=True.
Channels are looked up by name; network channels (telegram, slack, ...) can be
registered later with register_channel, but none is implemented here.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Protocol

import requests

from src.ingestion.storage import atomic_write

from . import book

SEVERITIES = ("critical", "warning", "info")
WARNING_WINDOW = timedelta(days=7)
ALERT_DIR = Path("live/alerts")
CHANNELS_ENV = "HQA_ALERT_CHANNELS"
BATCH_PROBLEM_STATUSES = ("REJECTED", "PARTIAL", "HALTED")


@dataclass(frozen=True)
class Alert:
    severity: str
    code: str
    strategy_id: str | None
    message: str
    details: dict = field(default_factory=dict)
    dedupe_key: str = ""

    def __post_init__(self):
        if self.severity not in SEVERITIES:
            raise ValueError(f"unknown alert severity: {self.severity}")
        if not self.dedupe_key:
            object.__setattr__(self, "dedupe_key", f"{self.code}:{self.strategy_id or '-'}")

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class AlertInputs:
    """Everything the rules read; any part may be missing (None/empty)."""
    today: date
    when: str | None = None
    session: str | None = None
    summary: dict | None = None
    status: dict | None = None
    ledgers: dict = field(default_factory=dict)
    incident_files: dict = field(default_factory=dict)
    backend: dict | None = None
    calendar_warnings: list = field(default_factory=list)
    failure_unit: str | None = None


# ---------------------------------------------------------------- rules


def _critical(code, sid, message, details, key):
    return Alert("critical", code, sid, message, details, key)


def _warning(code, sid, message, details, key):
    return Alert("warning", code, sid, message, details, key)


def _steps(summary):
    return (summary or {}).get("steps") or []


def _step(summary, name):
    return next((s for s in _steps(summary) if s.get("step") == name), None)


def rule_status_errors(inputs, state):
    for text in (inputs.status or {}).get("errors") or []:
        sid = text.split(":", 1)[0] if ":" in text else None
        yield _critical("status_error", sid, f"상태 오류: {text}", {"error": text}, f"status_error:{text}")


def rule_shadow_incidents(inputs, state):
    """New ledger incidents and incident files since the last sent alert (cursor in state)."""
    seen = state.get("incidents_seen") or {}
    for sid, entries in sorted(inputs.ledgers.items()):
        through = (seen.get(sid) or {}).get("ledger_through") or ""
        for entry in entries:
            if entry.get("date", "") <= through:
                continue
            for incident in entry.get("incidents") or []:
                yield _critical("shadow_incident", sid, f"섀도 장부 사고 {sid} {entry['date']}: {incident}",
                                {"date": entry["date"], "incident": incident},
                                f"shadow_incident:{sid}:{entry['date']}:{incident}")
    for sid, rows in sorted(inputs.incident_files.items()):
        known = set((seen.get(sid) or {}).get("files") or [])
        for row in rows:
            name = row.get("_file") or f"{row.get('date')}_{row.get('kind')}"
            if name in known:
                continue
            yield _critical("incident_file", sid, f"운영 사고 기록 {sid} {row.get('date')}: {row.get('kind')}",
                            {"file": name, "kind": row.get("kind"), "detail": row.get("detail")},
                            f"incident_file:{sid}:{name}")


def rule_decision_not_final(inputs, state):
    step = _step(inputs.summary, "decision_seals")
    for row in ((step or {}).get("result") or {}).get("strategies") or []:
        if row.get("state") != "final":
            yield _critical("decision_not_final", row.get("strategyId"),
                            f"{row.get('month')} 결정이 진입일 아침까지 확정되지 않았습니다 ({row.get('state')})",
                            row, f"decision_not_final:{row.get('strategyId')}:{row.get('month')}:{row.get('state')}")


# Steps whose failure already has a specific alert rule.
SPECIFIC_STEP_RULES = ("decision_seals", "alerts")


def rule_step_failures(inputs, state):
    for step in _steps(inputs.summary):
        if step.get("status") == "failed" and step.get("step") not in SPECIFIC_STEP_RULES:
            yield _critical("daily_step_failed", None,
                            f"일일 작업 실패: {step.get('step')} — {step.get('error')}",
                            {"step": step.get("step"), "error": step.get("error"),
                             "runId": (inputs.summary or {}).get("runId")},
                            f"daily_step_failed:{(inputs.summary or {}).get('session')}:{step.get('step')}")


def rule_failure_unit(inputs, state):
    if inputs.failure_unit:
        yield _critical("systemd_unit_failed", None, f"{inputs.failure_unit} 실행 실패",
                        {"unit": inputs.failure_unit}, f"systemd_unit_failed:{inputs.failure_unit}")


def rule_backend(inputs, state):
    backend = inputs.backend or {}
    if not backend.get("configured"):
        return
    orders = backend.get("unknown_orders")
    if orders:
        ids = sorted(str(o.get("orderId")) for o in orders)
        yield _critical("unknown_orders", None, f"확인 필요(UNKNOWN) 주문 {len(orders)}건",
                        {"count": len(orders), "orders": orders}, "unknown_orders:" + ",".join(ids))
    for batch in backend.get("batches") or []:
        if batch.get("status") in BATCH_PROBLEM_STATUSES:
            sid = batch.get("strategyId")
            yield _critical("batch_problem", sid,
                            f"일괄 주문 {batch.get('status')}: {sid} {batch.get('sessionDate')} {batch.get('phase')}",
                            {k: batch.get(k) for k in ("batchId", "status", "sessionDate", "phase")},
                            f"batch_problem:{batch.get('batchId')}:{batch.get('status')}")
    for sid, risk in sorted((backend.get("risk") or {}).items()):
        if not isinstance(risk, dict):
            continue
        reasons = risk.get("reasons") or []
        if risk.get("buyAllowed") is False:
            yield _critical("risk_buy_blocked", sid, f"{sid} 매수 차단: {', '.join(reasons) or '사유 없음'}",
                            {"reasons": reasons, "drawdownPct": risk.get("drawdownPct"),
                             "dailyPnlPct": risk.get("dailyPnlPct")}, f"risk_buy_blocked:{sid}")
        if risk.get("fullReviewRequired"):
            yield _critical("risk_full_review", sid, f"{sid} 낙폭 기준 전체 검토 필요",
                            {"drawdownPct": risk.get("drawdownPct")}, f"risk_full_review:{sid}")
        if risk.get("halted"):
            yield _critical("trading_halted", sid, f"{sid} 중단 스위치가 켜져 있습니다",
                            {"reasons": reasons}, f"trading_halted:{sid}")


def rule_calendar(inputs, state):
    for text in inputs.calendar_warnings or []:
        yield _warning("calendar_review", None, f"거래소 달력 확인 필요: {text}", {"warning": text},
                       f"calendar_review:{text}")


def _plans(summary):
    step = _step(summary, "plans")
    return ((step or {}).get("result") or {}).get("plans") or []


def rule_plans(inputs, state):
    for plan in _plans(inputs.summary):
        sid, day, phase = plan.get("strategyId"), plan.get("sessionDate"), plan.get("phase")
        if (plan.get("funding") or {}).get("requires_same_auction_sell_proceeds"):
            yield _warning("funding_requires_sell_proceeds", sid,
                           f"{sid} {day} {phase} 매수 금액이 예수금을 넘습니다 (같은 동시호가 매도 대금 필요)",
                           plan.get("funding"), f"funding_requires_sell_proceeds:{sid}:{day}:{phase}")
        count = plan.get("advWarnings") or 0
        if count > 0:
            yield _warning("adv_warnings", sid, f"{sid} {day} {phase} 20일 평균 거래대금 1% 초과 주문 {count}건",
                           {"count": count}, f"adv_warnings:{sid}:{day}:{phase}")


def rule_stage(inputs, state):
    previous = state.get("stages") or {}
    for payload in (inputs.status or {}).get("strategies") or []:
        sid, stage = payload.get("strategyId"), payload.get("stage") or {}
        requested, effective = stage.get("requested"), stage.get("effective")
        if requested not in book.STAGES or effective not in book.STAGES:
            continue
        if book.stage_rank(effective) < book.stage_rank(requested) and previous.get(sid) != effective:
            yield _warning("stage_blocked", sid,
                           f"{sid} 실제 단계 {effective} (요청 {requested}): {', '.join(stage.get('reasons') or [])}",
                           {"requested": requested, "effective": effective, "reasons": stage.get("reasons") or []},
                           f"stage_blocked:{sid}:{effective}")


def rule_daily_summary(inputs, state):
    """Once per morning run: stage, NAV/return, sessions, incidents and next action per strategy."""
    if inputs.when != "morning" or not inputs.status:
        return
    rows = []
    plans = _plans(inputs.summary)
    for payload in inputs.status.get("strategies") or []:
        sid, shadow_view = payload.get("strategyId"), payload.get("shadow") or {}
        upcoming = [f"{p.get('sessionDate')} {p.get('phase')}" for p in plans if p.get("strategyId") == sid]
        rows.append({"strategyId": sid, "stage": (payload.get("stage") or {}).get("effective"),
                     "navKrw": shadow_view.get("navKrw"), "returnPct": shadow_view.get("returnPct"),
                     "sessions": shadow_view.get("sessions"), "incidents": shadow_view.get("incidents"),
                     "nextAction": upcoming[0] if upcoming else None})
    text = "; ".join(f"{r['strategyId']} {r['stage']} 순자산 {r['navKrw']} ({r['returnPct']}%) 사고 {r['incidents']}"
                     for r in rows)
    run = (inputs.summary or {}).get("runId") or inputs.today.isoformat()
    yield Alert("info", "daily_summary", None, f"일일 요약 {inputs.session}: {text}", {"strategies": rows},
                f"daily_summary:{inputs.session}:{run}")


RULES = (rule_status_errors, rule_shadow_incidents, rule_decision_not_final, rule_step_failures,
         rule_failure_unit, rule_backend, rule_calendar, rule_plans, rule_stage, rule_daily_summary)


def evaluate(inputs: AlertInputs, state: dict) -> list[Alert]:
    alerts = []
    for rule in RULES:
        alerts.extend(rule(inputs, state))
    return alerts


def advance_state(inputs: AlertInputs, state: dict) -> dict:
    """Cursor updates after a successful send: incidents seen and last effective stages."""
    state = json.loads(json.dumps(state))
    seen = state.setdefault("incidents_seen", {})
    for sid, entries in inputs.ledgers.items():
        if entries:
            item = seen.setdefault(sid, {})
            item["ledger_through"] = max(item.get("ledger_through") or "", entries[-1].get("date", ""))
    for sid, rows in inputs.incident_files.items():
        item = seen.setdefault(sid, {})
        names = set(item.get("files") or []) | {r.get("_file") or f"{r.get('date')}_{r.get('kind')}" for r in rows}
        item["files"] = sorted(names)
    stages = state.setdefault("stages", {})
    for payload in (inputs.status or {}).get("strategies") or []:
        effective = (payload.get("stage") or {}).get("effective")
        if effective:
            stages[payload.get("strategyId")] = effective
    return state


# ---------------------------------------------------------------- dedupe


def due(alerts, state, now: datetime) -> list[Alert]:
    """critical: once per KST day per key; warning: once per 7 days; info: always."""
    sent = state.get("sent") or {}
    result = []
    for alert in alerts:
        last = sent.get(alert.dedupe_key)
        if alert.severity != "info" and last:
            last = datetime.fromisoformat(last)
            if alert.severity == "critical" and last.astimezone(now.tzinfo).date() == now.date():
                continue
            if alert.severity == "warning" and now - last < WARNING_WINDOW:
                continue
        result.append(alert)
    return result


def state_path(data_dir) -> Path:
    return Path(data_dir) / ALERT_DIR / "state.json"


def load_state(data_dir) -> dict:
    path = state_path(data_dir)
    if not path.is_file():
        return {}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"alert state is garbled: {path}: {error}") from None
    if not isinstance(state, dict):
        raise ValueError(f"alert state is not an object: {path}")
    return state


def save_state(data_dir, state) -> None:
    atomic_write(state_path(data_dir), json.dumps(state, ensure_ascii=False, sort_keys=True, indent=2) + "\n")


# ---------------------------------------------------------------- channels


class Channel(Protocol):
    name: str

    def send(self, alerts: list[Alert]) -> dict: ...


class FileChannel:
    """Append JSONL to <data_dir>/live/alerts/YYYYMMDD.jsonl (always on with --execute)."""
    name = "file"
    is_record = True  # the local record, not a delivery to a person

    def __init__(self, data_dir, now: datetime, **_):
        self.path = Path(data_dir) / ALERT_DIR / f"{now:%Y%m%d}.jsonl"
        self.now = now

    def send(self, alerts):
        if alerts:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                for alert in alerts:
                    handle.write(json.dumps({"sent_at": self.now.isoformat(), **alert.to_dict()},
                                            ensure_ascii=False, sort_keys=True) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        return {"channel": self.name, "sent": len(alerts), "path": str(self.path)}


class StdoutChannel:
    """Dry-run preview: rendered lines are returned in the command's JSON output."""
    name = "stdout"
    is_record = True

    def __init__(self, *_, **__):
        pass

    def send(self, alerts):
        return {"channel": self.name, "sent": len(alerts),
                "preview": [f"[{a.severity}] {a.code} {a.strategy_id or '-'}: {a.message}" for a in alerts]}


SEVERITY_LABELS = {"critical": "긴급", "warning": "주의", "info": "정보"}
WHEN_LABELS = {"morning": "아침", "evening": "저녁"}


def render_messages(alerts, *, day, when, limit=4096) -> list[tuple[str, list]]:
    """Plain-text messages (critical first), each under `limit` chars: [(text, dedupe_keys)]."""
    ordered = sorted(alerts, key=lambda a: SEVERITIES.index(a.severity))
    counts = {s: sum(1 for a in alerts if a.severity == s) for s in SEVERITIES}
    header = (f"[HQA 전략] 긴급 {counts['critical']} · 주의 {counts['warning']} · 정보 {counts['info']} "
              f"({day} {WHEN_LABELS.get(when, '수시')})")
    room = limit - len(header) - 16  # leaves space for a " (i/n)" suffix and newlines
    chunks, lines, keys, size = [], [], [], 0
    for alert in ordered:
        line = f"{SEVERITY_LABELS[alert.severity]} | {alert.strategy_id or '전체'} | {alert.message}"
        if len(line) > room:
            line = line[:room - 1] + "…"
        if lines and size + len(line) + 1 > room:
            chunks.append((lines, keys))
            lines, keys, size = [], [], 0
        lines.append(line)
        keys.append(alert.dedupe_key)
        size += len(line) + 1
    if lines:
        chunks.append((lines, keys))
    total = len(chunks)
    return [((header + (f" ({i}/{total})" if total > 1 else "")) + "\n" + "\n".join(body), chunk_keys)
            for i, (body, chunk_keys) in enumerate(chunks, 1)]


class TelegramChannel:
    """Telegram Bot API sendMessage (plain text). The token is never logged, printed or stored."""
    name = "telegram"
    is_record = False
    API = "https://api.telegram.org"
    TOKEN_ENV, CHAT_ENV = "HQA_TELEGRAM_BOT_TOKEN", "HQA_TELEGRAM_CHAT_ID"
    TIMEOUT_SECONDS = 10
    MAX_RETRIES = 3
    MAX_RETRY_AFTER = 30

    @classmethod
    def check_env(cls, env):
        missing = [name for name in (cls.TOKEN_ENV, cls.CHAT_ENV) if not (env.get(name) or "").strip()]
        if missing:
            raise ValueError("telegram_not_configured:" + ",".join(missing))

    def __init__(self, data_dir=None, now=None, *, env=None, context=None, session=None, sleep=None):
        env = os.environ if env is None else env
        self.check_env(env)
        self._token = env[self.TOKEN_ENV].strip()
        self.chat_id = env[self.CHAT_ENV].strip()
        self.context = context or {}
        self.session = session or requests
        self.sleep = sleep or time.sleep

    def _redact(self, text) -> str:
        return str(text).replace(self._token, "<redacted>")

    def _post(self, text):
        url = f"{self.API}/bot{self._token}/sendMessage"
        body = {"chat_id": self.chat_id, "text": text, "disable_web_page_preview": True}
        for attempt in range(self.MAX_RETRIES + 1):
            response = self.session.post(url, json=body, timeout=self.TIMEOUT_SECONDS)
            try:
                payload = response.json()
            except ValueError:
                payload = {}
            if response.status_code == 429 and attempt < self.MAX_RETRIES:
                retry = (payload.get("parameters") or {}).get("retry_after", 1)
                retry = retry if isinstance(retry, (int, float)) and not isinstance(retry, bool) else 1
                self.sleep(min(max(float(retry), 0.0), self.MAX_RETRY_AFTER))
                continue
            if response.status_code >= 400 or payload.get("ok") is not True:
                raise RuntimeError(f"telegram_send_failed:HTTP {response.status_code}:"
                                   f"{payload.get('description') or 'no description'}")
            return
        raise RuntimeError("telegram_send_failed:rate_limited")

    def send(self, alerts):
        delivered = []
        if not alerts:
            return {"channel": self.name, "sent": 0, "messages": 0, "delivered": delivered}
        messages = render_messages(alerts, day=self.context.get("day"), when=self.context.get("when"))
        for index, (text, keys) in enumerate(messages):
            try:
                self._post(text)
            except Exception as error:  # noqa: BLE001 - report, never raise the token
                return {"channel": self.name, "sent": len(delivered), "messages": index, "delivered": delivered,
                        "error": self._redact(f"{type(error).__name__}: {error}")}
            delivered.extend(keys)
        return {"channel": self.name, "sent": len(delivered), "messages": len(messages), "delivered": delivered}


CHANNEL_REGISTRY: dict = {"file": FileChannel, "stdout": StdoutChannel, "telegram": TelegramChannel}


def register_channel(name: str, factory) -> None:
    """Add a channel (e.g. telegram, slack, discord, email, kakao) by name."""
    if not name or "," in name or name != name.strip().lower():
        raise ValueError(f"invalid alert channel name: {name!r}")
    CHANNEL_REGISTRY[name] = factory


def configured_channel_names(env=None) -> list[str]:
    """Names from HQA_ALERT_CHANNELS; an unknown name or a selected channel's missing
    configuration is an error at startup, never ignored."""
    env = os.environ if env is None else env
    raw = env.get(CHANNELS_ENV, "")
    names = [part.strip().lower() for part in raw.split(",") if part.strip()]
    unknown = [name for name in names if name not in CHANNEL_REGISTRY]
    if unknown:
        raise ValueError(f"unknown_alert_channel:{','.join(unknown)} (known: {','.join(sorted(CHANNEL_REGISTRY))})")
    names = list(dict.fromkeys(names))
    for name in names:
        check = getattr(CHANNEL_REGISTRY[name], "check_env", None)
        if check is not None:
            check(env)
    return names


def build_channels(data_dir, now, *, execute, env=None, context=None) -> list:
    env = os.environ if env is None else env
    names = configured_channel_names(env)
    if not execute:
        return [StdoutChannel()]
    return [CHANNEL_REGISTRY[name](data_dir, now, env=env, context=context or {})
            for name in dict.fromkeys(["file", *names]) if name != "stdout"]


def dispatch(inputs: AlertInputs, data_dir, now: datetime, *, execute=False, env=None) -> dict:
    """Evaluate, dedupe, send; with execute the dedupe state and cursors are saved."""
    context = {"day": inputs.session or now.date().isoformat(), "when": inputs.when}
    channels = build_channels(data_dir, now, execute=execute, env=env, context=context)
    state = load_state(data_dir)
    alerts = evaluate(inputs, state)
    pending = due(alerts, state, now)
    results, failed = [], False
    for channel in channels:
        try:
            result = channel.send(pending)
        except Exception as error:  # noqa: BLE001 - one channel must not hide others
            result = {"channel": getattr(channel, "name", "?"), "error": f"{type(error).__name__}: {error}"}
        failed = failed or "error" in result
        results.append(result)
    if execute:
        deliveries = [r for c, r in zip(channels, results) if not getattr(c, "is_record", False)]
        if deliveries:  # only alerts a person actually received are deduped; failed sends retry
            delivered = {key for r in deliveries for key in r.get("delivered") or []}
        else:
            delivered = {a.dedupe_key for a in pending} if not failed else set()
        if not failed:
            state = advance_state(inputs, state)
        sent = state.setdefault("sent", {})
        for alert in pending:
            if alert.dedupe_key in delivered:
                sent[alert.dedupe_key] = now.isoformat()
        save_state(data_dir, state)
    return {"evaluated": len(alerts), "sent": len(pending), "suppressed": len(alerts) - len(pending),
            "alerts": [a.to_dict() for a in pending], "channels": results, "status": "failed" if failed else "ok"}
