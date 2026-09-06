from __future__ import annotations

import json
import re
import csv
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List

from src.config.settings import get_data_dir
from .types import StockTarget
from .storage import atomic_write, write_rows


def load_corp_code_map(csv_path: str) -> dict[str, str]:
    if not csv_path or not Path(csv_path).exists():
        return {}
    mapping = {}
    with Path(csv_path).open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not {"stock_code", "corp_code"}.issubset(reader.fieldnames or []):
            raise ValueError("corporate code CSV requires stock_code and corp_code columns")
        for row in reader:
            stock, corp = (row.get("stock_code") or "").strip(), (row.get("corp_code") or "").strip()
            if not stock:
                continue
            if not re.fullmatch(r"[0-9]{6}", stock) or not re.fullmatch(r"[0-9]{8}", corp):
                raise ValueError("invalid stock or corporate code in corporate code CSV")
            if stock in mapping and mapping[stock] != corp:
                raise ValueError(f"conflicting corporate codes for stock:{stock}")
            mapping[stock] = corp
    return mapping


def make_theme_key(theme: str, fallback: str = "default") -> str:
    raw = (theme or fallback or "default").strip().lower()
    raw = re.sub(r"\s+", "_", raw)
    raw = re.sub(r"[^0-9a-zA-Z가-힣_()-]+", "_", raw)
    return raw or "default"


class ThemeTargetStore:
    """Persist theme-derived stock targets as JSONL snapshots."""

    def __init__(self, data_dir: str | None = None):
        self.data_dir = Path(data_dir) if data_dir else get_data_dir()
        self.root = self.data_dir / "raw" / "theme_targets"

    def get_path(self, theme_key: str) -> Path:
        return self.root / f"{theme_key}.jsonl"

    def get_meta_path(self, theme_key: str) -> Path:
        return self.root / f"{theme_key}.meta.json"

    def load_targets(self, theme_key: str) -> List[StockTarget]:
        path = self.get_path(theme_key)
        if not path.exists():
            return []

        deduped = {}
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                stock_name = str(row.get("stock_name", "") or "").strip()
                stock_code = str(row.get("stock_code", "") or "").strip()
                corp_code = str(row.get("corp_code", "") or "").strip()
                self._validate_target(stock_name, stock_code, corp_code)
                if stock_code in deduped and deduped[stock_code].corp_code != corp_code:
                    raise ValueError(f"conflicting saved corporate codes for stock:{stock_code}")
                deduped[stock_code] = StockTarget(
                    stock_name=stock_name,
                    stock_code=stock_code,
                    corp_code=corp_code,
                )

        return list(deduped.values())

    @staticmethod
    def _validate_target(name: str, stock: str, corp: str) -> None:
        if not name or not re.fullmatch(r"[0-9]{6}", stock):
            raise ValueError("target requires a name and six-digit stock code")
        if corp and not re.fullmatch(r"[0-9]{8}", corp):
            raise ValueError(f"invalid corporate code for stock:{stock}")

    def backfill_corp_codes(self, theme_key: str, targets: List[StockTarget], mapping: dict[str, str],
                            *, required: bool = False, theme_name: str = "") -> List[StockTarget]:
        resolved = []
        for target in targets:
            mapped = mapping.get(target.stock_code, "")
            self._validate_target(target.stock_name, target.stock_code, target.corp_code)
            if mapped and target.corp_code and mapped != target.corp_code:
                raise ValueError(f"corporate code mapping conflicts with saved target:{target.stock_code}")
            corp = target.corp_code or mapped
            if required and not corp:
                raise ValueError(f"missing DART corporate code for stock:{target.stock_code}")
            resolved.append(StockTarget(target.stock_name, target.stock_code, corp))
        if resolved != targets:
            self.save_targets(theme_key, resolved, theme_name=theme_name)
        return resolved

    def save_targets(
        self,
        theme_key: str,
        targets: Iterable[StockTarget],
        *,
        theme_name: str = "",
        mode: str = "overwrite",
    ) -> List[StockTarget]:
        if mode not in {"overwrite", "append"}:
            raise ValueError(f"unsupported mode: {mode}")

        self.root.mkdir(parents=True, exist_ok=True)
        merged = {}

        if mode == "append":
            for target in self.load_targets(theme_key):
                merged[target.stock_code] = target

        for target in targets:
            stock_name = (target.stock_name or "").strip()
            stock_code = (target.stock_code or "").strip()
            self._validate_target(stock_name, stock_code, (target.corp_code or "").strip())
            existing = merged.get(stock_code)
            corp_code = (target.corp_code or "").strip()
            if existing and existing.corp_code and corp_code and existing.corp_code != corp_code:
                raise ValueError(f"conflicting corporate codes for stock:{stock_code}")
            merged[stock_code] = StockTarget(
                stock_name=stock_name,
                stock_code=stock_code,
                corp_code=corp_code or (existing.corp_code if existing else ""),
            )

        ordered_targets = list(merged.values())
        path = self.get_path(theme_key)
        write_rows(path, [asdict(target) for target in ordered_targets])

        meta = {
            "theme_key": theme_key,
            "theme_name": theme_name or theme_key,
            "target_count": len(ordered_targets),
            "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "storage_format": "jsonl",
        }
        atomic_write(self.get_meta_path(theme_key), json.dumps(meta, ensure_ascii=False, indent=2))

        return ordered_targets
