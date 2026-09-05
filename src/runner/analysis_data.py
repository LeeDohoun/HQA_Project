"""Point-in-time local evidence and authoritative backend account snapshots."""
from __future__ import annotations

import hashlib
import json
import math
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import requests
import yaml

from src.config.settings import get_data_dir
from src.runner.analysis_contracts import AccountSnapshot
from src.runner.theme_universe_loader import ThemeUniverseLoader

UTC = timezone.utc
KST = timezone(timedelta(hours=9))
FACTOR_VERSION = "leader-price-v1-ma150-annualized-vol"
PRICE_WEIGHTS = {"return_60d": .25 / .85, "return_20d": .20 / .85,
                 "trend_150d": .20 / .85, "volume_ratio_20d": .10 / .85,
                 "volatility_20d": .10 / .85}


def content_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     allow_nan=False, separators=(",", ":")).encode()).hexdigest()


def source_time(value: Any, *, daily_close: bool = False, naive_zone=KST) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("source timestamp is required")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    # Existing Korean ingestion stores naive local publication and OHLC dates.
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=naive_zone)
    if daily_close:
        parsed = parsed.astimezone(KST).replace(hour=15, minute=30, second=0, microsecond=0)
    return parsed.astimezone(UTC)


def read_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as handle:
        rows = []
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSONL: {path}:{line_number}") from exc
            if not isinstance(row, dict):
                raise ValueError(f"expected JSON object: {path}:{line_number}")
            rows.append(row)
        return rows


def price_features(rows: list[dict], as_of: datetime) -> tuple[dict, list[dict]]:
    by_date: dict[str, dict] = {}
    for row in rows:
        at = source_time(row.get("timestamp") or row.get("date"), daily_close=True)
        if at > as_of:
            continue
        normalized = {"available_at": at.isoformat()}
        meta = row.get("metadata") or {}
        if meta.get("source") == "krx" and meta.get("market") in {"KOSPI", "KOSDAQ"}:
            from src.ingestion.krx_chart import KrxChartCollector
            endpoint = (KrxChartCollector.KOSPI_DAILY_URL if meta["market"] == "KOSPI"
                        else KrxChartCollector.KOSDAQ_DAILY_URL)
            if meta.get("source_url") != endpoint or meta.get("price_basis") != "unadjusted":
                raise ValueError("unverified KRX price market/basis provenance")
            normalized.update(market=meta["market"], price_basis="unadjusted", source="krx", source_url=endpoint)
        for field in ("open", "high", "low", "close", "volume"):
            value = row.get(field)
            if value is None:
                raise ValueError(f"missing OHLCV field: {field}")
            number = float(str(value).replace(",", ""))
            if not math.isfinite(number) or number < 0 or (field != "volume" and number == 0):
                raise ValueError(f"invalid OHLCV field: {field}")
            normalized[field] = number
        if not normalized["low"] <= min(normalized["open"], normalized["close"]) <= max(normalized["open"], normalized["close"]) <= normalized["high"]:
            raise ValueError("inconsistent OHLC values")
        key = at.isoformat()
        if key in by_date:
            previous = by_date[key]
            if (any(previous[field] != normalized[field] for field in ("open", "high", "low", "close", "volume"))
                    or (previous.get("market") and normalized.get("market") and previous["market"] != normalized["market"])):
                raise ValueError("conflicting OHLCV rows for the same stock/date")
            normalized = {**previous, **normalized}
        by_date[key] = normalized
    known = [by_date[key] for key in sorted(by_date)][-300:]
    if len(known) < 150:
        raise ValueError(f"insufficient_price_history:{len(known)}<150")
    if as_of - source_time(known[-1]["available_at"]) > timedelta(days=4):
        raise ValueError("stale_daily_prices:older_than_4_calendar_days")
    frame = pd.DataFrame(known)
    close, volume = frame["close"], frame["volume"]
    if volume.tail(20).mean() <= 0:
        raise ValueError("zero_20d_volume")
    features = {"current_price": float(close.iloc[-1]), "history_days": len(known),
                "latest_timestamp": known[-1]["available_at"],
                "return_5d": float(close.iloc[-1] / close.iloc[-6] - 1),
                "return_20d": float(close.iloc[-1] / close.iloc[-21] - 1),
                "return_60d": float(close.iloc[-1] / close.iloc[-61] - 1),
                "trend_150d": float(close.iloc[-1] / close.tail(150).mean() - 1),
                "volume_ratio_20d": float(volume.iloc[-1] / volume.tail(20).mean()),
                "volatility_20d": float(close.pct_change().dropna().tail(20).std() * math.sqrt(252)),
                "avg_trading_value_20d": float((close * volume).tail(20).mean())}
    if any(not math.isfinite(features[key]) for key in PRICE_WEIGHTS):
        raise ValueError("nonfinite_price_factor")
    return features, known


def rank_price_candidates(candidates: list[dict]) -> list[dict]:
    unique = {row["stock_code"]: dict(row) for row in candidates}
    rows = [unique[code] for code in sorted(unique)]
    for row in rows:
        row["price_score"] = 0.0
    for key, weight in PRICE_WEIGHTS.items():
        values = [row["features"][key] for row in rows]
        if any(not math.isfinite(value) for value in values):
            raise ValueError(f"missing/nonfinite ranking factor:{key}")
        ranks = pd.Series(values, dtype="float64").rank(pct=True, ascending=key != "volatility_20d")
        for row, rank in zip(rows, ranks):
            row["price_score"] += 100 * weight * float(rank)
    return sorted(rows, key=lambda row: (-row["price_score"], row["stock_code"]))


class LocalAnalysisData:
    def __init__(self, *, data_dir: str | None = None, config_path: str = "config/watchlist.yaml"):
        self.data_dir = Path(data_dir) if data_dir else get_data_dir()
        with Path(config_path).open(encoding="utf-8") as handle:
            config = yaml.safe_load(handle)
        self.schedule = dict(config.get("schedule") or {})
        if self.schedule.get("timezone", "Asia/Seoul") != "Asia/Seoul":
            raise ValueError("Korean analysis schedule requires Asia/Seoul")
        self.filters = dict(config["trading"].get("theme_universe_filters") or {})

    def load_universe(self, as_of: datetime) -> tuple[list[dict], list[dict]]:
        if abs((datetime.now(UTC) - as_of).total_seconds()) > 60:
            raise ValueError("historical_replay_requires_versioned_price_and_universe_store")
        target_dir = self.data_dir / "raw" / "theme_targets"
        paths = sorted(target_dir.glob("*.jsonl"))
        if not paths:
            raise ValueError(f"missing_theme_targets:{target_dir}")
        stocks: dict[str, dict] = {}
        price_rows: dict[str, list[dict]] = {}
        errors = []
        for path in paths:
            key = path.stem
            for target in read_jsonl(path):
                code = ThemeUniverseLoader._stock_code(target)
                name = ThemeUniverseLoader._stock_name(target)
                if not code or len(code) != 6 or not code.isdigit() or not name:
                    raise ValueError(f"invalid theme target:{path}")
                stocks.setdefault(code, {"stock_code": code, "stock_name": name, "theme_keys": []})["theme_keys"].append(key)
            price_path = self.data_dir / "market_data" / key / "chart.jsonl"
            if not price_path.exists():
                errors.append({"theme_key": key, "stage": "price_data", "error": "missing_chart_file"})
                continue
            for row in read_jsonl(price_path):
                code = ThemeUniverseLoader._stock_code(row)
                if code:
                    price_rows.setdefault(code, []).append(row)
        candidates = []
        for code, stock in stocks.items():
            try:
                features, known = price_features(price_rows.get(code, []), as_of)
                stock.update(features=features, price_history=known)
                stock["entry_filter_errors"] = self._filter_errors(features)
                candidates.append(stock)
            except (ValueError, TypeError, OverflowError) as exc:
                errors.append({"stock_code": code, "stage": "price_data", "error": str(exc)})
        return candidates, errors

    def _filter_errors(self, features: dict) -> list[str]:
        errors = []
        if features["history_days"] < int(self.filters.get("min_history_days", 150)):
            errors.append("min_history_days")
        for setting, factor, minimum in (("min_avg_trading_value_20d", "avg_trading_value_20d", True),
                                        ("max_volatility_20d", "volatility_20d", False),
                                        ("max_return_5d", "return_5d", False),
                                        ("max_return_20d", "return_20d", False),
                                        ("min_trend_150d", "trend_150d", True)):
            if self.filters.get(setting) is not None:
                threshold = float(self.filters[setting])
                if (features[factor] < threshold) if minimum else (features[factor] > threshold):
                    errors.append(setting)
        return errors

    def load_technical(self, candidate: dict) -> dict:
        from src.tools.charts_tools import TechnicalAnalyzer
        frame = pd.DataFrame(candidate["price_history"]).rename(columns={
            "open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"})
        calculated = TechnicalAnalyzer(data_dir=str(self.data_dir))._calculate_all_indicators(frame).iloc[-1]
        fields = ("MA5", "MA20", "MA60", "MA150", "RSI", "MACD", "MACD_Signal", "MACD_Histogram",
                  "BB_Upper", "BB_Middle", "BB_Lower", "Stoch_K", "Stoch_D", "ATR", "Volume_MA20")
        values = {name: float(calculated[name]) if math.isfinite(calculated[name]) else None for name in fields}
        return {"indicators": values, "data_gaps": [f"undefined_indicator:{name}" for name, value in values.items() if value is None]}

    def load_market_context(self, candidate: dict, as_of: datetime) -> dict:
        from src.runner.market_context_data import load_benchmark_context
        return load_benchmark_context(self.data_dir, candidate, as_of)

    def load_evidence(self, candidate: dict, as_of: datetime) -> dict:
        from src.runner.corporate_actions import build_corporate_action_context, corporate_action_type
        from src.runner.event_evidence import build_event_evidence, select_event_evidence
        from src.runner.financial_snapshot import load_financial_snapshot
        documents, errors = {}, []
        conflicting_fragments = False
        for theme in candidate["theme_keys"]:
            path = self.data_dir / "canonical_index" / theme / "corpus.jsonl"
            if not path.exists():
                errors.append(f"missing_canonical_corpus:{theme}")
                continue
            for row in read_jsonl(path):
                if ThemeUniverseLoader._stock_code(row) != candidate["stock_code"]:
                    continue
                outer = row.get("metadata") or {}
                meta = {**(outer.get("metadata") or {}), **outer}
                kind = row.get("source_type") or meta.get("source_type")
                if kind not in {"dart", "news", "general_news"}:
                    continue
                try:
                    published = source_time(row.get("published_at") or meta.get("published_at"))
                    observed = source_time(row.get("collected_at") or meta.get("collected_at"), naive_zone=UTC)
                    available = max(published, observed)
                    if available > as_of:
                        continue
                    title = str(row.get("title") or meta.get("title") or "")
                    # Attention age does not resolve an outstanding corporate action.
                    if (as_of - published > timedelta(days=400 if kind == "dart" else 7)
                            and not (kind == "dart" and corporate_action_type(title))):
                        continue
                    url = row.get("url") or meta.get("url")
                    full_body = row.get("content") or meta.get("content")
                    body = full_body or row.get("text")
                    if not url or not body:
                        raise ValueError("evidence requires URL and content")
                    if kind == "dart" and meta.get("evidence_scope") == "structured_fields":
                        from src.ingestion.dart import DartDisclosureCollector
                        verified = DartDisclosureCollector.structured_fields_content(title, meta)
                        if not verified or full_body != verified:
                            raise ValueError("DART structured fields do not match verified provider content")
                    elif kind == "dart" and (meta.get("has_body") is False or meta.get("body_extracted") is False
                            or meta.get("wrapper_text_detected") is True or meta.get("mojibake_detected") is True
                            or meta.get("body_source") in {"title_fallback", "title_only"}):
                        raise ValueError("DART body quality is not suitable for event analysis")
                    source_id = str(row.get("doc_id") or meta.get("doc_id") or content_hash({"url": url}))
                    is_fragment = not full_body and "chunk_index" in meta
                    body_hash = hashlib.sha256(str(body).encode("utf-8")).hexdigest()
                    document = {"source_id": source_id, "source_type": kind, "url": url,
                                "published_at": published.isoformat(), "available_at": available.isoformat(),
                                "published_at_precision": meta.get("published_at_precision", "date" if kind == "dart" else "unknown"),
                                "title": title,
                                "metadata": {key: meta[key] for key in (
                                    "rcept_no", "rcept_dt", "remark", "is_correction", "has_correction", "is_withdrawal",
                                    "structured_endpoint", "structured_rcept_no", "structured_row", "structured_body_error_type",
                                    "supersedes_source_ids", "published_at_precision", "has_body", "body_source",
                                    "body_extracted", "evidence_scope") if key in meta},
                                "_full_body": str(body) if not is_fragment else None,
                                "_fragments": {}, "_fragment_times": {}}
                    document["_content_context"] = content_hash({"metadata": document["metadata"],
                        "title": document["title"], "published_at": document["published_at"]})
                    document["_version_context"] = content_hash({"content_context": document["_content_context"],
                        "provider_version": meta.get("version_id")})
                    key = (kind, url, document["_version_context"], "fragments" if is_fragment else body_hash)
                    existing = documents.setdefault(key, document)
                    existing["source_id"] = min(existing["source_id"], source_id)
                    if available.isoformat() < existing["available_at"]:
                        existing["available_at"] = available.isoformat()
                    if is_fragment:
                        index = int(meta["chunk_index"])
                        if index in existing["_fragments"] and existing["_fragments"][index] != body:
                            conflicting_fragments = True
                            raise ValueError("conflicting canonical chunks without a source version")
                        existing["_fragments"][index] = str(body)
                        existing["_fragment_times"][index] = min(available.isoformat(),
                            existing["_fragment_times"].get(index, available.isoformat()))
                except (ValueError, TypeError) as exc:
                    errors.append(f"invalid_evidence:{meta.get('doc_id', 'unknown')}:{exc}")
        if conflicting_fragments:
            raise ValueError("conflicting canonical chunks without a source version")
        for document in documents.values():
            full_body = document.pop("_full_body")
            fragments = document.pop("_fragments")
            fragment_times = document.pop("_fragment_times")
            body = full_body if full_body is not None else "\n[fragment]\n".join(fragments[key] for key in sorted(fragments))
            if full_body is None:
                document["available_at"] = max(fragment_times.values())
            digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
            document.update(text=body[:3500], source_text_hash=digest, truncated=len(body) > 3500,
                            original_characters=len(body), text_scope=(document["metadata"].get("evidence_scope", "document")
                                if full_body is not None else "available_fragments"))
            document["version"] = content_hash({"text_hash": digest, "context": document.pop("_version_context")})
            document["_content_version"] = content_hash({"text_hash": digest, "context": document.pop("_content_context")})
            document["source_id"] += ":" + document["version"][:16]
        latest, observations = {}, {}
        # Theme-local observation IDs must not reset an unchanged event's first availability.
        for document in sorted(documents.values(), key=lambda row: (row["available_at"], row["source_id"])):
            logical = (document["source_type"], document["url"])
            observed_key = (logical, document["available_at"])
            if observations.setdefault(observed_key, document["_content_version"]) != document["_content_version"]:
                raise ValueError("conflicting evidence revisions at the same availability time")
            previous = latest.get(logical)
            if previous is not None and document["_content_version"] == previous["_content_version"]:
                continue
            if previous is None or document["available_at"] > previous["available_at"]:
                latest[logical] = document
            else:
                raise ValueError("conflicting evidence revisions at the same availability time")
        for document in latest.values():
            document.pop("_content_version")
        corporate_actions = build_corporate_action_context(list(latest.values()), as_of)
        events = select_event_evidence(build_event_evidence(list(latest.values()), candidate["stock_code"]), as_of=as_of)
        selected_ids = {source_id for event in events for source_id in event["source_ids"]}
        selected = [doc for doc in latest.values() if doc["source_id"] in selected_ids]
        financial = load_financial_snapshot(self.data_dir, candidate["stock_code"], as_of)
        if not selected and financial["status"] != "ready":
            raise ValueError("no_dated_dart_or_news_evidence:" + ";".join(errors[:3]))
        return {"documents": selected, "events": events, "corporate_actions": corporate_actions, "data_gaps": errors,
                "financial_snapshot": financial}


class BackendAccountClient:
    def __init__(self, base_url: str | None = None, internal_token: str | None = None, timeout: int = 10):
        self.base_url = (base_url or os.getenv("BACKEND_INTERNAL_BASE_URL") or os.environ["BACKEND_BASE_URL"]).rstrip("/")
        self.token = internal_token if internal_token is not None else os.environ["HQA_INTERNAL_TOKEN"]
        if not self.token:
            raise ValueError("HQA_INTERNAL_TOKEN is required for account snapshots")
        self.timeout = timeout

    def fetch_accounts(self, user_ids: list[str]) -> dict[str, AccountSnapshot]:
        response = requests.post(f"{self.base_url}/api/v1/internal/trading/account-snapshots",
                                 json={"userIds": user_ids}, headers={"X-HQA-Internal-Token": self.token}, timeout=self.timeout)
        response.raise_for_status()
        rows = response.json()["snapshots"]
        result = {}
        for row in rows:
            if row.get("success") is not True:
                raise ValueError(f"account_snapshot_failed:{row.get('userId')}:{row.get('error')}")
            snapshot = AccountSnapshot.model_validate(row)
            age = datetime.now(UTC) - snapshot.capturedAt
            if age.total_seconds() < -5 or age > timedelta(seconds=60):
                raise ValueError(f"stale_account_snapshot:{snapshot.userId}")
            if snapshot.userId in result:
                raise ValueError("duplicate account snapshot")
            result[snapshot.userId] = snapshot
        if set(result) != set(user_ids):
            raise ValueError("account snapshot response does not match requested users")
        return result

    def fetch_prices(self, user_id: str, stock_codes: list[str]) -> dict[str, dict]:
        response = requests.post(f"{self.base_url}/api/v1/internal/market/price-snapshots",
                                 json={"userId": user_id, "stockCodes": stock_codes},
                                 headers={"X-HQA-Internal-Token": self.token}, timeout=self.timeout)
        response.raise_for_status()
        result = {}
        for row in response.json()["snapshots"]:
            value = row.get("currentPrice")
            if (row.get("success") is not True or isinstance(value, bool)
                    or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0):
                raise ValueError(f"price_snapshot_failed:{row.get('stockCode')}:{row.get('failureReason')}")
            if row.get("source") != "kis" or row["stockCode"] in result:
                raise ValueError("invalid or duplicate KIS quote source")
            at = source_time(row["snapshotAt"])
            age = (datetime.now(UTC) - at).total_seconds()
            if not -5 <= age <= 60:
                raise ValueError(f"stale_price_snapshot:{row['stockCode']}")
            result[row["stockCode"]] = {"source_id": f"quote:{row['stockCode']}:{at.isoformat()}",
                                        "current_price": row["currentPrice"], "available_at": at.isoformat(),
                                        "source": row["source"]}
        if set(result) != set(stock_codes):
            raise ValueError("price snapshot response does not match requested stocks")
        return result
