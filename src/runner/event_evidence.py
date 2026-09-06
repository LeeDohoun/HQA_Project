"""Deterministic, provenance-preserving event packets from already dated documents."""
from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

EVENT_EVIDENCE_VERSION = "event-evidence-v2"
MAX_TEXT = 2400
MAX_SOURCES = 4
TEXT_SCOPES = {"document", "available_fragments", "structured_fields", "summary"}
_KST = ZoneInfo("Asia/Seoul")
_PATTERNS = (
    ("regulatory_risk", r"거래정지|상장폐지|관리종목|불성실공시|횡령|배임|감사의견(?:거절|부적정)|과징금|영업정지"),
    ("convertible_bond", r"전환사채|신주인수권부사채|교환사채"),
    ("capital_raise", r"유상증자|무상증자"),
    ("buyback", r"자기주식(?:취득|처분|소각)|자사주(?:매입|소각|취득|처분)"),
    ("merger", r"합병결정|합병계약|분할결정|주식교환|영업양수|영업양도"),
    ("contract", r"단일판매[ㆍ·]?공급계약|(?:공급|수주)계약(?:체결|해지|취소)"),
    ("dividend", r"배당결정|배당락|현금[ㆍ·]?현물배당"),
    ("earnings", r"영업이익|순이익|잠정실적|실적발표|영업.*실적|매출액.*손익구조"),
)


def _normalized(value: str) -> str:
    return " ".join(unicodedata.normalize("NFC", value).split())


def _timestamp(value: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError("event timestamp must be an aware ISO string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("event timestamp must include a timezone")
    return parsed.astimezone(timezone.utc)


def _document(row: dict) -> dict:
    if not isinstance(row, dict):
        raise ValueError("event document must be an object")
    for name in ("source_id", "source_type", "url", "title", "text", "source_text_hash"):
        if not isinstance(row.get(name), str) or not row[name].strip():
            raise ValueError(f"event document requires nonempty {name}")
    if row["source_type"] not in {"dart", "news", "general_news"}:
        raise ValueError("unsupported event source_type")
    if not re.fullmatch(r"[0-9a-f]{64}", row["source_text_hash"]):
        raise ValueError("source_text_hash must be the original SHA-256 digest")
    if type(row.get("truncated")) is not bool:
        raise ValueError("event document requires boolean truncated")
    if "original_characters" in row and (type(row["original_characters"]) is not int
                                        or row["original_characters"] < len(row["text"])):
        raise ValueError("original_characters cannot be less than supplied text length")
    published, available = _timestamp(row.get("published_at")), _timestamp(row.get("available_at"))
    if published > available:
        raise ValueError("event cannot be available before publication")
    meta = row.get("metadata", {})
    if not isinstance(meta, dict):
        raise ValueError("event document metadata must be an object")
    meta = dict(meta)
    scope = row.get("text_scope", meta.get("evidence_scope", "document"))
    if not isinstance(scope, str) or scope not in TEXT_SCOPES:
        raise ValueError("unsupported text_scope")
    for flag in ("is_correction", "is_withdrawal", "has_correction"):
        if flag in row and flag not in meta:
            meta[flag] = row[flag]
        if flag in meta and type(meta[flag]) is not bool:
            raise ValueError(f"{flag} must be boolean")
    precision = row.get("published_at_precision", meta.get("published_at_precision", "unknown"))
    if precision not in {"date", "datetime", "unknown"}:
        raise ValueError("unsupported published_at_precision")
    links = meta.get("supersedes_source_ids", [])
    if not isinstance(links, list) or any(not isinstance(item, str) or not item for item in links):
        raise ValueError("supersedes_source_ids must be explicit source ID strings")
    return {**row, "metadata": meta, "published_at": published.isoformat(),
            "available_at": available.isoformat(), "published_at_precision": precision, "text_scope": scope}


def _category(row: dict) -> str:
    title = re.sub(r"\s", "", row["title"])
    # Promotional/forecast headlines are not observed corporate actions.
    if row["source_type"] != "dart" and re.search(r"기대|전망|예상|가능성|수혜|관련주|목표주가", title):
        return "other"
    return next((kind for kind, pattern in _PATTERNS if re.search(pattern, title)), "other")


def _facts(row: dict) -> list[dict]:
    meta = row["metadata"]
    fields = meta.get("structured_row")
    if fields is None:
        return []
    receipt = meta.get("rcept_no")
    if (row["source_type"] != "dart" or not isinstance(fields, dict) or not receipt
            or meta.get("structured_rcept_no") != receipt or fields.get("rcept_no") != receipt
            or not isinstance(meta.get("structured_endpoint"), str) or not meta["structured_endpoint"]):
        raise ValueError("structured facts require an exact provider receipt match")
    preserved = {}
    if any(not isinstance(key, str) for key in fields):
        raise ValueError("structured provider facts require string field names")
    for key, value in sorted(fields.items()):
        if not isinstance(key, str) or not isinstance(value, (str, int, float, bool, type(None))):
            raise ValueError("structured provider facts must have scalar values")
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("structured provider facts must be finite")
        if key in {"status", "message", "crtfc_key"} or (isinstance(value, str) and len(value) > 400):
            continue
        if len(preserved) < 32:
            preserved[key] = value
    return [{"source_id": row["source_id"], "endpoint": meta["structured_endpoint"],
             "rcept_no": receipt, "fields": preserved, "omitted_fields_count": len(fields) - len(preserved)}]


def _group_fingerprint(row: dict, identity: tuple) -> tuple:
    # Headline variants may share a syndicated full body, but summaries and unseen tails cannot prove it.
    flags = tuple(bool(row["metadata"].get(flag)) for flag in ("is_correction", "is_withdrawal", "has_correction"))
    if (row["source_type"] not in {"news", "general_news"} or row["text_scope"] != "document"
            or row["truncated"] or len(_normalized(row["text"])) < 200
            or any(flags)
            or re.search(r"정정|철회|해명|오보|부인|해지|취소|아니|않|못|없|적자|흑자|증가|감소|상승|하락|확대|축소", row["title"])):
        return ("exact_document", *identity, flags)
    headline_numbers = tuple(re.findall(r"[+\-$₩€£]?\d[\d,.]*(?:\s*[가-힣%]+)?", _normalized(row["title"])))
    return ("full_body_syndication", identity[0], _category(row), headline_numbers,
            _normalized(row["text"]), row["text_scope"])


def build_event_evidence(documents: list[dict], stock_code: str) -> list[dict]:
    """No LLM extraction, inferred amounts, fuzzy deduplication, or time filtering."""
    if not isinstance(documents, list):
        raise ValueError("event documents must be a list")
    if not isinstance(stock_code, str) or not re.fullmatch(r"[0-9]{6}", stock_code):
        raise ValueError("event stock_code must contain six digits")
    groups, identities = {}, {}
    for raw in documents:
        row = _document(raw)
        row["_validated_facts"] = _facts(row)
        if row["text_scope"] == "structured_fields" and not row["_validated_facts"]:
            raise ValueError("structured_fields requires verified provider facts")
        fingerprint = (_timestamp(row["published_at"]).astimezone(_KST).date().isoformat(),
                       _normalized(row["title"]), _normalized(row["text"]),
                       row["source_text_hash"] if row["truncated"] else "", row["text_scope"])
        prior = identities.setdefault(row["source_id"], fingerprint)
        if prior != fingerprint:
            raise ValueError(f"conflicting_source_document:{row['source_id']}")
        groups.setdefault(_group_fingerprint(row, fingerprint), []).append(row)
    events = []
    for fingerprint, members in sorted(groups.items()):
        members.sort(key=lambda row: (row["source_type"] != "dart", row["source_id"], row["available_at"]))
        unique = {}
        for row in members:
            unique.setdefault(row["source_id"], row)
        candidates = list(unique.values())
        # Retain provenance for both timing anchors as well as the preferred text.
        required = [candidates[0], min(candidates, key=lambda row: row["available_at"]),
                    max(candidates, key=lambda row: row["available_at"])]
        selected = {row["source_id"]: row for row in required}
        for row in candidates:
            if len(selected) == MAX_SOURCES:
                break
            selected.setdefault(row["source_id"], row)
        sources = list(selected.values())
        primary = sources[0]
        category = _category(primary)
        correction = any(row["metadata"].get("is_correction", False)
                         or bool(re.search(r"\[(?:기재|첨부|첨부추가)?정정\]", row["title"])) for row in members)
        withdrawal = any(row["metadata"].get("is_withdrawal", False)
                         or bool(re.search(r"\[철회\]|철회신고서|철회결정", row["title"])) for row in members)
        has_correction = any(row["metadata"].get("has_correction", False) for row in members)
        links = sorted({link for row in members for link in row["metadata"].get("supersedes_source_ids", [])})
        flags = [flag for flag, active in (("correction", correction), ("withdrawal", withdrawal),
                 ("subsequent_correction_reported", has_correction),
                 ("regulatory_review", category == "regulatory_risk"),
                 ("dilution_review", category == "convertible_bond" or
                  (category == "capital_raise" and "유상증자" in primary["title"])),
                 ("contract_termination", bool(re.search(r"계약\s*(?:해지|취소)", primary["title"])))) if active]
        event_id = "event:" + hashlib.sha256(json.dumps([stock_code, fingerprint], ensure_ascii=False).encode()).hexdigest()
        events.append({"event_id": event_id, "stock_code": stock_code, "event_type": category,
            "deduplication_basis": fingerprint[0],
            "title": primary["title"], "text": primary["text"][:MAX_TEXT],
            "text_scope": primary["text_scope"],
            "text_source_id": primary["source_id"], "text_truncated": primary["truncated"] or len(primary["text"]) > MAX_TEXT,
            "published_at": min(row["published_at"] for row in members),
            "published_at_precision": "date" if any(row["published_at_precision"] == "date" for row in members)
                else "unknown" if any(row["published_at_precision"] == "unknown" for row in members) else "datetime",
            "available_at": min(row["available_at"] for row in members),
            "updated_at": max(row["available_at"] for row in candidates),
            "source_ids": [row["source_id"] for row in sources],
            "sources": [{key: row[key] for key in ("source_id", "source_type", "url", "title", "published_at", "available_at",
                       "published_at_precision", "source_text_hash", "truncated", "original_characters", "text_scope") if key in row}
                        for row in sources],
            "source_count": len(unique), "omitted_sources_count": max(0, len(unique) - MAX_SOURCES),
            "structured_facts": [fact for row in sources for fact in row["_validated_facts"]],
            "is_correction": correction, "is_withdrawal": withdrawal, "has_correction": has_correction,
            "unlinked_correction": correction and not links, "supersedes_source_ids": links,
            "risk_flags": flags})
    return events


def select_event_evidence(events: list[dict], max_events: int = 8, as_of: datetime | None = None) -> list[dict]:
    """A 30-day attention window prioritizes evidence; it does not expire obligations."""
    if type(max_events) is not int or max_events < 1:
        raise ValueError("max_events must be a positive integer")
    if as_of is not None and (not isinstance(as_of, datetime) or as_of.tzinfo is None or as_of.utcoffset() is None):
        raise ValueError("event selection as_of must be an aware datetime")
    return sorted(events, key=lambda event: (as_of is not None and
                  timedelta(0) <= as_of - _timestamp(event["available_at"]) <= timedelta(days=30),
                  bool(event["risk_flags"]), event["event_type"] != "other",
                  _timestamp(event["updated_at"]), event["event_id"]), reverse=True)[:max_events]
