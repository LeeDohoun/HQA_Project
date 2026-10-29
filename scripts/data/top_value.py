#!/usr/bin/env python3
"""Daily universe: the KRX common stocks with the largest previous-session trading value.

Ranks every listed common stock in the KIS stock masters by reference price x previous-day
volume and writes the top N to raw/theme_targets/거래대금상위.jsonl, which the collection loop
collects like any other theme. Halted, liquidating, administrative-issue, investment-warning and
investment-risk stocks, SPACs and stocks without a DART corp code (no disclosures or financials,
so a BUY could never pass) are skipped. Every stock an auto-trade account holds is kept, so a
holding never loses its price history and specialist analysis by dropping out of the ranking.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.config.settings import get_data_dir, load_project_env

load_project_env()

THEME_KEY = "거래대금상위"
# Source: https://github.com/koreainvestment/open-trading-api/tree/main/stocks_info
# Each row is the code (9), standard code (12) and name, then a fixed-width tail.
MASTERS = {
    "KOSPI": ("https://new.real.download.dws.co.kr/common/master/kospi_code.mst.zip", 228),
    "KOSDAQ": ("https://new.real.download.dws.co.kr/common/master/kosdaq_code.mst.zip", 222),
}
# Offsets inside the tail. Checked against collected KIS bars: the previous-day volume matches
# exactly, while the reference price can differ from the last close by a few percent, so the
# product only orders liquidity.
FIELDS = {
    "KOSPI": {"group": (0, 2), "price": (41, 50), "halted": (60, 61), "liquidating": (61, 62),
              "administrative": (62, 63), "warning": (63, 65), "volume": (81, 93)},
    "KOSDAQ": {"group": (0, 2), "price": (36, 45), "halted": (55, 56), "liquidating": (56, 57),
               "administrative": (57, 58), "warning": (58, 60), "volume": (76, 88)},
}
COMMON_STOCK_GROUP = "ST"
# 00 none, 01 investment caution (common and mild), 02 investment warning, 03 investment risk.
BLOCKED_WARNINGS = {"02", "03"}


def parse_master(text: str, market: str) -> list[dict]:
    tail, fields = MASTERS[market][1], FIELDS[market]
    rows = []
    for line in text.splitlines():
        line += "\n"  # the official layout counts the tail from the end of the line, newline included
        if len(line) <= tail + 21:
            continue
        head, rest = line[:-tail], line[-tail:]
        value = {name: rest[start:end] for name, (start, end) in fields.items()}
        try:
            price, volume = int(value["price"]), int(value["volume"])
        except ValueError:
            continue
        rows.append({"stock_code": head[:9].strip(), "stock_name": head[21:].strip(), "market": market,
                     "group": value["group"], "halted": value["halted"], "liquidating": value["liquidating"],
                     "administrative": value["administrative"], "warning": value["warning"],
                     "trading_value": price * volume})
    return rows


def select_targets(rows: list[dict], corp_codes: dict[str, str], top: int, held: set[str]) -> list[dict]:
    eligible = [row for row in rows
                if row["group"] == COMMON_STOCK_GROUP and len(row["stock_code"]) == 6 and row["stock_code"].isdigit()
                and "Y" not in (row["halted"], row["liquidating"], row["administrative"])
                and row["warning"] not in BLOCKED_WARNINGS and "스팩" not in row["stock_name"]
                and row["stock_code"] in corp_codes and row["trading_value"] > 0]
    chosen = sorted(eligible, key=lambda row: (-row["trading_value"], row["stock_code"]))[:top]
    codes = {row["stock_code"] for row in chosen}
    by_code = {row["stock_code"]: row for row in rows}
    chosen += [by_code[code] for code in sorted(held - codes) if code in by_code]
    return [{"stock_name": row["stock_name"], "stock_code": row["stock_code"],
             "corp_code": corp_codes.get(row["stock_code"], ""), "trading_value": row["trading_value"]}
            for row in chosen]


def download_masters() -> list[dict]:
    rows = []
    for market, (url, _) in MASTERS.items():
        response = requests.get(url, timeout=60)
        response.raise_for_status()
        with ZipFile(BytesIO(response.content)) as archive:
            name = next(item for item in archive.namelist() if item.endswith(".mst"))
            rows += parse_master(archive.read(name).decode("cp949"), market)
    return rows


def held_codes() -> set[str]:
    """Stocks held by auto-trade accounts, read through the backend as the analysis does."""
    from src.runner.analysis_data import BackendAccountClient
    from src.runner.analysis_scheduler import BackendAutoTradeTargetClient

    users = [str(target["userId"]) for target in BackendAutoTradeTargetClient().fetch_targets()]
    client, held = BackendAccountClient(), set()
    for start in range(0, len(users), 10):  # the backend accepts ten accounts per request
        for snapshot in client.fetch_accounts(users[start:start + 10]).values():
            held |= {holding.stockCode for holding in snapshot.holdings if holding.quantity > 0}
    return held


def main() -> int:
    from src.ingestion.storage import atomic_write

    parser = argparse.ArgumentParser(description="Select the day's most traded stocks as a collection theme")
    parser.add_argument("--top", type=int, default=50)
    parser.add_argument("--data-dir", default=None)
    parser.add_argument("--corp-codes-csv", default="corp_codes.csv")
    args = parser.parse_args()
    if not 1 <= args.top <= 100:
        parser.error("--top must be between 1 and 100 (the analysis prefilters at most 100 stocks)")

    with open(args.corp_codes_csv, encoding="utf-8-sig", newline="") as handle:
        corp_codes = {row["stock_code"]: row["corp_code"] for row in csv.DictReader(handle)}
    # Without the holdings the list could drop a held stock, so a failure keeps yesterday's list.
    held = held_codes()
    targets = select_targets(download_masters(), corp_codes, args.top, held)

    path = Path(args.data_dir or get_data_dir()) / "raw" / "theme_targets" / f"{THEME_KEY}.jsonl"
    previous = set()
    if path.exists():
        previous = {json.loads(line)["stock_code"] for line in path.read_text(encoding="utf-8").splitlines() if line}
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, "".join(json.dumps({key: row[key] for key in ("stock_name", "stock_code", "corp_code")},
                                          ensure_ascii=False) + "\n" for row in targets))
    codes = {row["stock_code"] for row in targets}
    top = ", ".join(f"{row['stock_name']}({row['trading_value'] / 1e8:,.0f}억)" for row in targets[:5])
    print(f"[TOP_VALUE] {len(targets)} stocks (held kept: {len(held & codes)}), "
          f"+{len(codes - previous)} -{len(previous - codes)} vs previous; top: {top}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
