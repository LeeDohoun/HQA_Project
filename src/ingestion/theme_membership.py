from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from src.config.settings import get_data_dir


@dataclass(frozen=True)
class ThemeMembership:
    theme_key: str
    stock_name: str
    stock_code: str
    first_seen_at: str
    last_seen_at: str = ""
    last_observed_at: str = ""
    source: str = "local_corpus_inferred"
    membership_confidence: float = 0.0
    evidence_count: int = 0
    evidence_source_counts: Dict[str, int] = field(default_factory=dict)
    notes: str = ""


class ThemeMembershipStore:
    """Persist point-in-time theme membership evidence as JSONL."""

    def __init__(self, data_dir: str | Path | None = None):
        self.data_dir = Path(data_dir) if data_dir else get_data_dir()
        self.root = self.data_dir / "raw" / "theme_membership"

    def get_path(self, theme_key: str) -> Path:
        return self.root / f"{theme_key}.jsonl"

    def get_meta_path(self, theme_key: str) -> Path:
        return self.root / f"{theme_key}.meta.json"

    def load_memberships(self, theme_key: str) -> List[ThemeMembership]:
        path = self.get_path(theme_key)
        if not path.exists():
            return []

        rows: List[ThemeMembership] = []
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                stock_code = str(row.get("stock_code") or "").strip()
                stock_name = str(row.get("stock_name") or "").strip()
                first_seen = _normalize_date(row.get("first_seen_at"))
                if not stock_code or not stock_name or not first_seen:
                    continue
                rows.append(
                    ThemeMembership(
                        theme_key=str(row.get("theme_key") or theme_key),
                        stock_name=stock_name,
                        stock_code=stock_code,
                        first_seen_at=first_seen,
                        last_seen_at=_normalize_date(row.get("last_seen_at")),
                        last_observed_at=_normalize_date(row.get("last_observed_at")),
                        source=str(row.get("source") or "local_corpus_inferred"),
                        membership_confidence=float(row.get("membership_confidence") or 0.0),
                        evidence_count=int(row.get("evidence_count") or 0),
                        evidence_source_counts=dict(row.get("evidence_source_counts") or {}),
                        notes=str(row.get("notes") or ""),
                    )
                )
        rows.sort(key=lambda item: (item.first_seen_at, item.stock_code))
        return rows

    def save_memberships(
        self,
        theme_key: str,
        memberships: Iterable[ThemeMembership],
        *,
        theme_name: str = "",
        method: str = "local_corpus_inferred",
    ) -> List[ThemeMembership]:
        self.root.mkdir(parents=True, exist_ok=True)
        ordered = sorted(
            memberships,
            key=lambda item: (item.first_seen_at or "9999-99-99", item.stock_code),
        )
        path = self.get_path(theme_key)
        with path.open("w", encoding="utf-8") as f:
            for row in ordered:
                f.write(json.dumps(asdict(row), ensure_ascii=False) + "\n")

        meta = {
            "theme_key": theme_key,
            "theme_name": theme_name or theme_key,
            "membership_count": len(ordered),
            "method": method,
            "storage_format": "jsonl",
            "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "notes": (
                "Point-in-time membership evidence. local_corpus_inferred rows are "
                "not official historical theme membership records."
            ),
        }
        with self.get_meta_path(theme_key).open("w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
        return ordered


def is_membership_active(row: ThemeMembership, as_of_date: str) -> bool:
    as_of = _normalize_date(as_of_date)
    if not as_of:
        return False
    first_seen = _normalize_date(row.first_seen_at)
    last_seen = _normalize_date(row.last_seen_at)
    if first_seen and first_seen > as_of:
        return False
    if last_seen and last_seen < as_of:
        return False
    return True


def active_membership_codes(rows: Iterable[ThemeMembership], as_of_date: str) -> set[str]:
    return {row.stock_code for row in rows if is_membership_active(row, as_of_date)}


def _normalize_date(value: Optional[str]) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    match = re.search(r"(\d{4})[-/.]?(\d{2})[-/.]?(\d{2})", text)
    if not match:
        return ""
    return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
