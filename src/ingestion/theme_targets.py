from __future__ import annotations

import json
import re
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List

from src.config.settings import get_data_dir
from .types import StockTarget


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
                if not stock_name or not stock_code:
                    continue
                deduped[stock_code] = StockTarget(
                    stock_name=stock_name,
                    stock_code=stock_code,
                    corp_code=corp_code,
                )

        return list(deduped.values())

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
            if not stock_name or not stock_code:
                continue
            merged[stock_code] = StockTarget(
                stock_name=stock_name,
                stock_code=stock_code,
                corp_code=(target.corp_code or "").strip(),
            )

        ordered_targets = list(merged.values())
        path = self.get_path(theme_key)
        with path.open("w", encoding="utf-8") as f:
            for target in ordered_targets:
                f.write(json.dumps(asdict(target), ensure_ascii=False) + "\n")

        meta = {
            "theme_key": theme_key,
            "theme_name": theme_name or theme_key,
            "target_count": len(ordered_targets),
            "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "storage_format": "jsonl",
        }
        with self.get_meta_path(theme_key).open("w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)

        return ordered_targets
