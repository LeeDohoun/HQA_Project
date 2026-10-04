"""B001 disclosure-category event study; local coverage dry-run is the default.

Holding windows reuse event_study's vectorized implementation. Controls draw in
bounded batches, retaining event order, entry-only pools and missing-exit counts.
No collector, live runner, environment loader or holdout price file is opened.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import fcntl
import json
from pathlib import Path
import resource
import time

import exchange_calendars as calendars
import numpy as np
import pandas as pd

from backtesting import cost_model, event_study, experiment_registry, holdout, signal_eval
from backtesting.experiment_registry import PROJECT_ROOT
from backtesting.experiments import common, d001
from src.ingestion.krx_benchmarks import validate_benchmark_record
from src.strategies import _runner_module


EXPERIMENT_ID = "B001_disclosure_category"
CATEGORIES = ("contract", "buyback", "dividend", "earnings", "capital_raise",
              "convertible_bond", "regulatory_risk", "merger")
POSITIVE = {"contract", "buyback", "dividend"}
NEGATIVE = {"capital_raise", "convertible_bond", "regulatory_risk"}
HORIZONS = (1, 3, 5, 20)
N_CONTROLS = 200
PRICE_START, PRICE_END = "20221201", "20251231"
CUTOFF = pd.Timestamp("2026-01-01")
MARKET_INDEX_NAMES = {"KOSPI": "코스피", "KOSDAQ": "코스닥"}
LISTING_COLUMNS = ("rcept_no", "rcept_dt", "stock_code", "corp_name", "corp_cls", "report_nm", "rm")
GROSS_METRIC, NET_METRIC = "mean_abnormal", "mean_net"
NET_AT_COST_1_5_METRIC = "mean_net_at_cost_1_5"
_category = _runner_module("event_evidence")._category
INTERPRETATIONS = {
    "note": "정의는 실제 결과를 보기 전(2026-10-05)에 확정. 사전등록의 8개 유형과 방향을 고정하며 보조 기간으로 판정을 바꾸지 않습니다.",
    "criteria": {
        "events": {"metric": "overall.count; design.count; by_year",
                   "definition": "접수일 2023~2025의 필터·중복 제거 후 유효 이벤트 수. 설계는 접수일 2023~2024, 검증은 접수일 2025입니다. 설계의 유효 5세션 이벤트가 300건 이상이어야 하며 월별 36회 기준은 쓰지 않습니다."},
        "gross": {"metric": GROSS_METRIC,
                  "definition": "이벤트 동일가중 평균 총초과수익 = 종목 수정 수익률 − 소속 시장 지수 수익률. 종목은 다음 세션 시가부터 h번째 세션 종가, 지수는 진입 직전 세션 종가부터 실제 평가일 종가이므로 밤사이 지수 변동을 포함하는 근사식입니다."},
        "net": {"metric": NET_METRIC,
                "definition": "이벤트 동일가중 평균 순초과수익 = 총초과수익 − cost_model 왕복 비용. 진입 시가·시장·연도와 진입 전 ADV를 사용합니다. 음의 유형은 회피 필터이므로 총초과수익으로 판정합니다."},
        "cost_sensitivity": {"metric": "cost_sensitivity['1.0'/'1.5'/'2.0'].mean_net; " + NET_AT_COST_1_5_METRIC,
                             "definition": "동일 이벤트에 비용 배수 1·1.5·2를 적용한 평균 순초과수익과 날짜 군집 t. 기존 비용 모델대로 세금은 배수에서 제외합니다. 양의 유형은 1.5배 평균도 0보다 커야 합니다."},
        "date_clustered_t": {"metric": "overall.t_stat; overall.net_t_stat; overall.date_count",
                             "definition": "진입일별 이벤트 초과수익 평균을 먼저 계산한 뒤, 그 날짜 평균들의 평균 / (표본 표준편차 / 날짜 수의 제곱근). 보고된 성과 평균은 이벤트 가중, t는 날짜 가중입니다. 표준편차 0 또는 날짜 2개 미만이면 t는 미정입니다."},
        "threshold": {"metric": "judgement_inputs.required_t_stat; trial_count_after_run",
                      "definition": "required_t_stat과 사전등록 t 기준 중 큰 값. 이번 실행의 8개 대장 행을 포함해 20회를 넘으면 최소 3입니다. 양의 유형은 순수익 t > 기준, 음의 유형은 총수익 t < −기준입니다."},
        "random_control": {"metric": "random_control.gross/net; judgement_inputs.random_control_share",
                           "definition": "같은 진입일의 거래 가능·상한가 제외 종목 풀에서 복원 추출한 200세트. event_study와 동일하게 청산/벤치마크 누락 종목도 풀에 남기고 뽑힌 뒤 제외 건수를 보고합니다. 양의 유형은 순초과수익, 나머지는 총초과수익을 비교합니다. share는 대조군 평균 >= 실제 평균인 유효 세트 비율(동점 포함)입니다."},
        "control_tails": {"metric": "random_control.share_of_controls; gross/net.p05/p50/p95",
                          "definition": "양의 유형은 share <= 0.05, 음의 유형은 share >= 0.95: 실제 값이 대조군의 95% 이상보다 낮거나 같아야 합니다. 분위수는 유효 대조군 평균의 5·50·95백분위입니다. 실패한 세트는 count와 valid_events_per_draw에 드러납니다."},
        "validation_sign": {"metric": "judgement_inputs.validation_year_same_sign; design_mean; validation_mean",
                            "definition": "접수일 기준 2025 평균과 설계 2023~2024 평균의 부호를 비교합니다. 양의 유형은 순초과수익, 음의 유형과 방향 미정 유형은 총초과수익입니다. 어느 구간이 비면 미정입니다."},
        "direction_only": {"metric": "judgement_inputs.abs_t_stat; direction",
                           "definition": "earnings·merger는 총초과수익 |t|와 관측 부호를 보고합니다. |t| > 기준 및 해당 부호의 대조군 꼬리 기준을 충족할 때만 direction을 기록합니다. 설계 300건 미달은 insufficient, 그 외 판정은 direction_only이며 신호로 채택하지 않습니다."},
        "liquidity": {"metric": "avg_trading_value_20d; liquidity_buckets",
                      "definition": "진입 직전 세션까지 검증된 거래대금 20개가 모두 있어야 합니다. 종가 >= 1,000원, ADV >= 1억원. 비용 모델의 4구간(1억 미만/1~10억/10~100억/100억 이상)별 동일 통계를 보고하며 첫 구간은 필터 때문에 비어 있습니다. 대조군 비용 ADV는 기존 event_study의 짧은 이력·close*volume 대체 규칙을 그대로 사용합니다."},
        "execution": {"metric": "excluded; unadjusted_fallback; stale_exit; halted_after_entry; limit_down_exit; no_later_price",
                      "definition": "missing_exit=last_close, limit_fill=exclude의 기존 보유기간 계산을 재사용합니다. 수정 일수익률을 연결하며 누락 시 기존 원주가 대체를 집계합니다. 거래 없는 청산은 기간 내 마지막 가격으로 평가하며 실제 매도 체결을 뜻하지 않습니다. 이후 행도 없으면 no_later_price 제외(확인된 상장폐지 건수가 아님). 보류 구간에 닿는 예정 청산은 평가 전 제외합니다."},
        "secondary": {"metric": "horizons['1'/'3'/'20']; by_year; liquidity_buckets",
                      "definition": "1·3·20세션, 연도별·유동성별 결과는 진단용입니다. 5세션 전체 결과만 판정하며 보조 결과로 유형·기간·방향을 선택하지 않습니다."},
    },
}


def _registration(repo_root):
    fields = common.load_preregistration(EXPERIMENT_ID, repo_root=repo_root)
    if fields["variants_planned"] != len(CATEGORIES) or fields["uses_holdout"] or fields["uses_llm"]:
        raise ValueError("B001 requires its eight fixed categories, no holdout and no LLM")
    return fields


def _session_calendar():
    # Later calendar metadata identifies forbidden horizons; later prices are never loaded.
    return calendars.get_calendar("XKRX", start="2022-12-01", end="2026-02-28").sessions


def _counts(frame):
    counts = frame.category.value_counts()
    return {name: int(counts.get(name, 0)) for name in (*CATEGORIES, "other")}


def _keep(frame, mask, name, counts):
    result = frame.loc[mask].copy()
    counts[name] = _counts(result)
    return result


def load_listings(data_dir, *, repo_root=PROJECT_ROOT):
    directory = Path(data_dir) / "disclosures/dart_full/list"
    if not directory.is_dir():
        raise ValueError(f"listing directory does not exist: {directory}")
    holdout.guard_period("20230101", "20251231", repo_root=repo_root)
    rows, files = [], 0
    for path in sorted(directory.glob("*/*.jsonl")):
        if not path.stem.isdigit() or len(path.stem) != 8 or not "20230101" <= path.stem <= "20251231":
            continue
        files += 1
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                row = json.loads(line)
                rows.append({name: row[name] for name in LISTING_COLUMNS})
    return pd.DataFrame(rows, columns=LISTING_COLUMNS), files


def build_events(listings):
    """Sequential listing filters, with every category counted before/after each."""
    if not set(LISTING_COLUMNS).issubset(listings):
        raise ValueError("listing rows require " + ", ".join(LISTING_COLUMNS))
    frame = listings.copy()
    for name in ("rcept_no", "rcept_dt", "corp_name", "corp_cls", "report_nm", "rm"):
        if not frame[name].map(lambda value: isinstance(value, str)).all():
            raise ValueError(f"listing {name} must be a string")
    if not frame.rcept_no.str.fullmatch(r"[0-9]{14}").all():
        raise ValueError("rcept_no must contain 14 digits")
    frame["available_at"] = pd.to_datetime(frame.rcept_dt, format="%Y%m%d", errors="raise")
    frame["category"] = frame.report_nm.map(lambda title: _category({"title": title, "source_type": "dart"}))
    counts = {"raw": _counts(frame)}
    frame = _keep(frame, frame.available_at.between("2023-01-01", "2025-12-31"), "period", counts)
    frame = _keep(frame, frame.corp_cls.isin(("Y", "K")), "listed_market", counts)
    frame = _keep(frame, ~frame.report_nm.str.contains("정정", regex=False), "no_corrections", counts)
    # rm '정' describes a subsequent correction, not a correction filing itself.
    withdrawal = frame.report_nm.str.contains("철회", regex=False) | frame.rm.str.contains("철", regex=False)
    frame = _keep(frame, ~withdrawal, "no_withdrawals", counts)
    codes = frame.stock_code.fillna("")
    if not codes.map(lambda value: isinstance(value, str)).all():
        raise ValueError("stock_code must be a string or missing")
    frame["stock_code"] = codes.str.strip()
    frame = _keep(frame, frame.stock_code.ne(""), "stock_code", counts)
    frame = _keep(frame, frame.stock_code.str.endswith("0"), "common_share", counts)
    frame = _keep(frame, ~frame.corp_name.str.contains("스팩|SPAC", case=False, regex=True), "no_spac", counts)
    frame = _keep(frame, frame.category.isin(CATEGORIES), "registered_category", counts)
    frame = frame.sort_values("rcept_no", kind="stable").drop_duplicates(["stock_code", "rcept_dt", "category"])
    counts["deduplicated"] = _counts(frame)
    frame["event_id"] = frame.stock_code + ":" + frame.rcept_dt + ":" + frame.category + ":" + frame.rcept_no
    return frame.reset_index(drop=True), counts


def filter_events(events, prices, sessions, *, repo_root=PROJECT_ROOT):
    """Vectorized common.universe_filter rules at the exact session before entry."""
    common.guard_prices(prices, repo_root=repo_root)
    price_days = common._sessions(prices)
    if prices.empty or prices.index.has_duplicates or (price_days >= CUTOFF).any():
        raise ValueError("B001 requires unique, nonempty prices strictly before 2026")
    required = {"stock_name", "market", "open", "close", "volume", "trading_value", "calendar_status"}
    if not required.issubset(prices):
        raise ValueError(f"missing B001 price columns: {sorted(required - set(prices.columns))}")
    if prices.stock_name.isna().any() or not prices.stock_name.map(lambda name: isinstance(name, str) and bool(name.strip())).to_numpy(dtype=bool).all():
        raise ValueError("stock_name is required to exclude SPACs")
    days = signal_eval._dates(sessions).sort_values().unique()
    if not price_days.isin(days).all():
        raise ValueError("price dates must belong to the supplied exchange calendar")
    frame = events.copy()
    entry = days.searchsorted(frame.available_at, side="right")
    frame["entry_date"] = days.to_numpy()[np.minimum(entry, len(days) - 1)]
    frame["previous_date"] = days.to_numpy()[np.maximum(entry - 1, 0)]
    counts = {"before_price_filters": _counts(frame)}
    frame = _keep(frame, entry < len(days), "entry_session", counts)
    frame = _keep(frame, frame.available_at >= days[0], "previous_session", counts)
    index = pd.MultiIndex.from_arrays([frame.previous_date, frame.stock_code], names=prices.index.names)
    current = prices.reindex(index)
    value = prices.trading_value.where(prices.calendar_status.eq("verified") & np.isfinite(prices.trading_value))
    wide = value.unstack("stock_code").reindex(days[days < CUTOFF])
    adv = wide.rolling(20, min_periods=20).mean().stack()
    frame["avg_trading_value_20d"] = adv.reindex(index).to_numpy()
    frame["previous_close"] = current.close.to_numpy()
    frame["previous_market"] = current.market.to_numpy()
    frame["previous_stock_name"] = current.stock_name.to_numpy()
    frame["previous_calendar_status"] = current.calendar_status.to_numpy()
    frame = _keep(frame, index.isin(prices.index), "previous_price", counts)
    frame = _keep(frame, frame.previous_calendar_status.eq("verified"), "verified_calendar", counts)
    frame = _keep(frame, frame.previous_market.isin(("KOSPI", "KOSDAQ")), "price_market", counts)
    frame = _keep(frame, ~frame.previous_stock_name.str.contains("스팩|SPAC", case=False, regex=True), "price_no_spac", counts)
    frame = _keep(frame, np.isfinite(frame.previous_close) & frame.previous_close.ge(1000), "previous_close", counts)
    frame = _keep(frame, np.isfinite(frame.avg_trading_value_20d) & frame.avg_trading_value_20d.ge(1e8), "adv20", counts)
    frame["bucket"] = np.searchsorted(cost_model.TRADING_VALUE_BUCKETS, frame.avg_trading_value_20d, side="right")
    return frame.reset_index(drop=True), counts


def load_benchmarks(data_dir, *, repo_root=PROJECT_ROOT):
    """Latest broad-index episodes only; future closes and sector indices are unused."""
    holdout.guard_period(PRICE_START, PRICE_END, repo_root=repo_root)
    path = Path(data_dir) / "market_context/benchmarks.jsonl"
    latest = {}
    with path.open(encoding="utf-8") as handle:
        fcntl.flock(handle, fcntl.LOCK_SH)
        for line in handle:
            if not line.endswith("\n"):
                raise ValueError("benchmark JSONL has an unterminated record")
            row = json.loads(line)
            if not "2022-12-01" <= row["trade_date"] <= "2025-12-31":
                continue
            if row["index_name"] != MARKET_INDEX_NAMES.get(row["series"]):
                continue
            row = validate_benchmark_record(row)
            key = row["trade_date"], row["series"]
            previous = latest.get(key)
            if previous and (row["available_at"] < previous["available_at"] or
                             row["available_at"] == previous["available_at"] and row["version"] != previous["version"]):
                raise ValueError("benchmark revisions are out of order or conflicting")
            latest[key] = row
    return pd.DataFrame([{"trade_date": pd.Timestamp(day), "market": market, "close": row["close"]}
                         for (day, market), row in sorted(latest.items())], columns=["trade_date", "market", "close"])


def _benchmark_frame(benchmarks, *, repo_root=PROJECT_ROOT):
    if not {"trade_date", "market", "close"}.issubset(benchmarks):
        raise ValueError("benchmarks require trade_date, market, close")
    frame = benchmarks[["trade_date", "market", "close"]].copy()
    frame["trade_date"] = signal_eval._dates(frame.trade_date)
    if not frame.empty:
        holdout.guard_period(frame.trade_date.min().date(), frame.trade_date.max().date(), repo_root=repo_root)
    if (frame.trade_date >= CUTOFF).any() or frame.duplicated(["trade_date", "market"]).any():
        raise ValueError("benchmark keys must be unique and strictly before 2026")
    frame["close"] = pd.to_numeric(frame.close, errors="raise").astype(float)
    if not frame.market.isin(MARKET_INDEX_NAMES).all() or not (np.isfinite(frame.close) & frame.close.gt(0)).all():
        raise ValueError("broad market benchmarks require finite positive closes")
    return frame.set_index(["trade_date", "market"]).close


def _horizon_plan(events, sessions, h):
    days = signal_eval._dates(sessions).sort_values().unique()
    entries = days.get_indexer(events.entry_date)
    targets = entries + h - 1
    reason = np.full(len(events), None, dtype=object)
    reason[(entries < 0) | (targets >= len(days))] = "incomplete_calendar_horizon"
    exits = days.to_numpy()[np.clip(targets, 0, len(days) - 1)]
    reason[(reason == None) & (exits >= CUTOFF.to_datetime64())] = "holdout_horizon"
    return exits, reason


def _entry_inventory(events, prices):
    index = pd.MultiIndex.from_arrays([events.entry_date, events.stock_code], names=prices.index.names)
    bars = prices.reindex(index)
    reason = np.full(len(events), None, dtype=object)
    reason[~index.isin(prices.index)] = "missing_entry_price"
    tradable = np.isfinite(bars.open) & bars.open.gt(0) & np.isfinite(bars.volume) & bars.volume.gt(0)
    reason[(reason == None) & ~tradable.to_numpy()] = "untradable_entry"
    base = bars.base_price.to_numpy() if "base_price" in bars else np.full(len(events), np.nan)
    base = np.where(np.isnan(base), events.previous_close, base)
    limited = np.isfinite(base) & (base > 0) & (bars.open.to_numpy() >= base * (1 + signal_eval.PRICE_LIMIT_THRESHOLD))
    reason[(reason == None) & limited] = "limit_up_entry"
    return bars, reason


def _coverage(benchmarks, sessions):
    days = signal_eval._dates(sessions)
    days = days[days < CUTOFF]
    return {market: {"sessions": int(benchmarks.loc[benchmarks.market.eq(market), "trade_date"].isin(days).sum()),
                     "required_sessions": len(days),
                     "first": str(benchmarks.loc[benchmarks.market.eq(market), "trade_date"].min().date()) if benchmarks.market.eq(market).any() else None,
                     "last": str(benchmarks.loc[benchmarks.market.eq(market), "trade_date"].max().date()) if benchmarks.market.eq(market).any() else None}
            for market in MARKET_INDEX_NAMES}


def dry_run(*, data_dir=PROJECT_ROOT / "data", repo_root=PROJECT_ROOT):
    _registration(repo_root)
    sessions = _session_calendar()
    listings, listing_files = load_listings(data_dir, repo_root=repo_root)
    events, counts = build_events(listings)
    prices = d001._load_prices(Path(data_dir) / "market/krx_daily", PRICE_START, PRICE_END, repo_root=repo_root)
    events, price_counts = filter_events(events, prices, sessions, repo_root=repo_root)
    counts.update(price_counts)
    path = Path(data_dir) / "market_context/benchmarks.jsonl"
    benchmarks = load_benchmarks(data_dir, repo_root=repo_root) if path.is_file() else pd.DataFrame(columns=["trade_date", "market", "close"])
    benchmark = _benchmark_frame(benchmarks, repo_root=repo_root)
    bars, entry_reason = _entry_inventory(events, prices)
    price_days = common._sessions(prices)
    guarded_days = sessions[sessions < CUTOFF]
    missing = guarded_days[~guarded_days.isin(price_days)]
    horizons = {}
    for h in HORIZONS:
        exits, reason = _horizon_plan(events, sessions, h)
        horizon_counts = {"before": _counts(events)}
        for label in ("holdout_horizon", "incomplete_calendar_horizon"):
            horizon_counts[label] = _counts(events.loc[reason == label])
        # Every scheduled holding session must have a price archive: no shifted windows.
        positions = sessions.get_indexer(events.entry_date)
        missing_prefix = np.r_[0, (~sessions.isin(price_days) & (sessions < CUTOFF)).cumsum()]
        absent = missing_prefix[np.minimum(positions + h, len(sessions))] - missing_prefix[positions] > 0
        reason[(reason == None) & absent] = "missing_holding_sessions"
        reason[(reason == None) & (entry_reason != None)] = entry_reason[(reason == None) & (entry_reason != None)]
        for label in ("missing_holding_sessions", "missing_entry_price", "untradable_entry", "limit_up_entry"):
            horizon_counts[label] = _counts(events.loc[reason == label])
        planned = reason == None
        start_index = pd.MultiIndex.from_arrays([events.previous_date, bars.market.to_numpy()])
        end_index = pd.MultiIndex.from_arrays([exits, bars.market.to_numpy()])
        covered = np.isfinite(benchmark.reindex(start_index).to_numpy(dtype=float)) & np.isfinite(benchmark.reindex(end_index).to_numpy(dtype=float))
        horizon_counts["planned"] = _counts(events.loc[planned])
        horizon_counts["benchmark_covered"] = _counts(events.loc[planned & covered])
        horizons[str(h)] = horizon_counts
    return {"experiment_id": EXPERIMENT_ID, "data_dir": str(Path(data_dir)), "listing_files": listing_files,
            "price_sessions": len(price_days), "price_rows": len(prices), "read_start": "2022-12-01", "read_end": "2025-12-31",
            "missing_price_sessions": [day.date().isoformat() for day in missing], "filter_counts": counts,
            "benchmark_file_exists": path.is_file(), "benchmark_coverage": _coverage(benchmarks, sessions),
            "horizons": horizons}


def _window_outcomes(data, window, benchmark):
    days = data["sessions"]
    closes = benchmark.unstack("market").reindex(index=days, columns=list(MARKET_INDEX_NAMES)).to_numpy(dtype=float)
    markets = np.where(data["market"] == "KOSPI", 0, 1)
    previous = np.vstack([np.full((1, 2), np.nan), closes[:-1]])
    starts = previous[np.arange(len(days))[:, None], markets]
    ends = closes[window["exit_position"], markets]
    reason = window["reason"]
    reason[0, reason[0] == None] = "missing_previous_session"
    missing = ~np.isfinite(starts) | ~np.isfinite(ends) | (starts <= 0) | (ends <= 0) | ~np.isin(data["market"], list(MARKET_INDEX_NAMES))
    reason[(reason == None) & missing] = "missing_benchmark"
    abnormal = np.full(starts.shape, np.nan)
    valid = reason == None
    abnormal[valid] = window["gross"][valid] - (ends[valid] / starts[valid] - 1)
    return abnormal, reason


def _control_pool(data, window, abnormal, reason, adv, entries):
    """Flatten entry-only pools once per horizon; exits never select candidates."""
    unique = np.unique(entries)
    selected = data["entry_tradable"][unique] & ~data["limit_up"][unique]
    row, column = np.nonzero(selected)
    entry = unique[row]
    sizes = np.zeros(len(data["sessions"]), dtype=int)
    sizes[unique] = selected.sum(axis=1)
    offsets = np.r_[0, sizes.cumsum()[:-1]]
    gross = abnormal[entry, column]
    net = np.full(len(entry), np.nan)
    net_reason = reason[entry, column].copy()
    liquidity = adv[entry - 1, column]
    valid = (net_reason == None) & np.isfinite(liquidity)
    net_reason[(net_reason == None) & ~np.isfinite(liquidity)] = "missing_liquidity"
    net[valid] = gross[valid] - cost_model.round_trip_cost_vectorized(
        data["open"][entry[valid], column[valid]], data["market"][entry[valid], column[valid]],
        data["sessions"].year.to_numpy()[entry[valid]], liquidity[valid])
    return {"sizes": sizes, "offsets": offsets, "gross": gross, "net": net,
            "gross_reason": reason[entry, column], "net_reason": net_reason,
            **{name: window[name][entry, column] for name in signal_eval._RETURN_FLAGS}}


def _random_controls(pool, entries, actual, rng, n_controls):
    means = {metric: [] for metric in ("gross", "net")}
    counts = {metric: [] for metric in means}
    exclusions = {metric: Counter() for metric in means}
    flags = {metric: Counter() for metric in means}
    for left in range(0, n_controls, 16):
        size = min(16, n_controls - left)
        # Row-major draws match run_event_study's draw -> event RNG order.
        draws = rng.integers(pool["sizes"][entries], size=(size, len(entries))) + pool["offsets"][entries]
        for metric in means:
            values = pool[metric][draws]
            valid = pool[f"{metric}_reason"][draws] == None
            totals = np.where(valid, values, 0).sum(axis=1)
            numbers = valid.sum(axis=1)
            averages = np.full(size, np.nan)
            np.divide(totals, numbers, out=averages, where=numbers > 0)
            means[metric].extend(averages)
            counts[metric].extend(numbers.tolist())
            reasons, amounts = np.unique(pool[f"{metric}_reason"][draws][~valid], return_counts=True)
            exclusions[metric].update(dict(zip(reasons, amounts.astype(int).tolist())))
            for name in signal_eval._RETURN_FLAGS:
                flags[metric][name] += int((pool[name][draws] & valid).sum())
    return {metric: {**signal_eval._control_summary(means[metric], actual[metric]),
                     "means": [float(value) if np.isfinite(value) else None for value in means[metric]],
                     "valid_events_per_draw": counts[metric], "excluded": dict(exclusions[metric]),
                     "no_later_price": exclusions[metric]["no_later_price"], **flags[metric]}
            for metric in means}


def _statistics(rows):
    return event_study._aggregate(rows.to_dict("records"))


def _category_result(rows, excluded):
    overall = _statistics(rows)
    overall["no_later_price"] = excluded.get("no_later_price", 0)
    costs = {}
    for multiplier in (1.0, 1.5, 2.0):
        net = rows.abnormal - rows[f"cost_{multiplier}"]
        costs[str(multiplier)] = {"mean_net": signal_eval._summary(net)["mean"],
                                 "net_t_stat": signal_eval._summary(net.groupby(rows.entry_date).mean())["t_stat"]}
    by_year = {str(year): _statistics(rows.loc[rows.available_at.dt.year.eq(year)]) for year in (2023, 2024, 2025)}
    buckets = {}
    for number, upper in enumerate(cost_model.TRADING_VALUE_BUCKETS):
        buckets[str(number)] = {**_statistics(rows.loc[rows.bucket.eq(number)]),
                               "lower_inclusive": 0 if number == 0 else cost_model.TRADING_VALUE_BUCKETS[number - 1],
                               "upper_exclusive": upper if np.isfinite(upper) else None}
    saved = rows.copy()
    for column in ("available_at", "entry_date", "exit_date", "scheduled_exit_date"):
        saved[column] = saved[column].dt.strftime("%Y-%m-%d")
    return {"overall": overall, "cost_sensitivity": costs, "by_year": by_year, "liquidity_buckets": buckets,
            "design": _statistics(rows.loc[rows.available_at.lt("2025-01-01")]),
            "excluded": excluded, "excluded_count": sum(excluded.values()), "events": saved.to_dict("records")}


def evaluate_events(events, prices, benchmarks, sessions, *, n_controls=N_CONTROLS, seed=0, repo_root=PROJECT_ROOT):
    """Vectorized event_study outcomes with B001's filters and forbidden horizons."""
    common.guard_prices(prices, repo_root=repo_root)
    signal_eval._positive_int(n_controls, "n_controls")
    benchmark = _benchmark_frame(benchmarks, repo_root=repo_root)
    days = signal_eval._dates(sessions).sort_values().unique()
    price_days = common._sessions(prices)
    expected = days[(days >= price_days.min()) & (days <= price_days.max())]
    if not price_days.equals(expected) or (price_days >= CUTOFF).any():
        raise ValueError("missing exchange price sessions would shift holding windows")
    data = signal_eval._holding_data(prices.sort_index(), "exclude")
    adv = signal_eval._adv(prices).unstack("stock_code").reindex(index=data["sessions"], columns=data["codes"]).to_numpy()
    output = {name: {"horizons": {}} for name in CATEGORIES}
    rngs = {name: np.random.default_rng(seed + number) for number, name in enumerate(CATEGORIES)}
    for h in HORIZONS:
        exits, planned_reason = _horizon_plan(events, days, h)
        entries = data["sessions"].get_indexer(events.entry_date)
        stocks = data["codes"].get_indexer(events.stock_code)
        reason = planned_reason.copy()
        reason[(reason == None) & (entries < 0)] = "missing_entry_session"
        reason[(reason == None) & (stocks < 0)] = "missing_entry_price"
        window = signal_eval._holding_window(data, h, "last_close")
        abnormal, outcome_reason = _window_outcomes(data, window, benchmark)
        existing = reason == None
        reason[existing] = outcome_reason[entries[existing], stocks[existing]]
        valid = reason == None
        rows = events.loc[valid, ["event_id", "stock_code", "category", "available_at", "entry_date", "avg_trading_value_20d", "bucket"]].copy()
        entry, stock = entries[valid], stocks[valid]
        rows["gross"] = window["gross"][entry, stock]
        rows["abnormal"] = abnormal[entry, stock]
        rows["exit_date"] = data["sessions"].to_numpy()[window["exit_position"][entry, stock]]
        rows["scheduled_exit_date"] = exits[valid]
        for flag in signal_eval._RETURN_FLAGS:
            rows[flag] = window[flag][entry, stock]
        rows["price_basis"] = np.where(rows.unadjusted_fallback, "raw", "adjusted_chain")
        for multiplier in (1.0, 1.5, 2.0):
            rows[f"cost_{multiplier}"] = cost_model.round_trip_cost_vectorized(
                data["open"][entry, stock], data["market"][entry, stock], data["sessions"].year.to_numpy()[entry],
                rows.avg_trading_value_20d.to_numpy(), multiplier=multiplier)
        rows["cost"] = rows["cost_1.0"]
        rows["net"] = rows.abnormal - rows.cost
        pool = _control_pool(data, window, abnormal, outcome_reason, adv, entry)
        for name in CATEGORIES:
            category_rows = rows.loc[rows.category.eq(name)]
            excluded = dict(Counter(reason[(events.category.eq(name)).to_numpy() & ~valid]))
            result = _category_result(category_rows, excluded)
            controls = _random_controls(pool, entry[rows.category.eq(name)],
                                        {"gross": result["overall"]["mean_abnormal"], "net": result["overall"]["mean_net"]},
                                        rngs[name], n_controls)
            metric = "net" if name in POSITIVE else "gross"
            result["random_control"] = {"metric": metric, "share_of_controls": controls[metric]["share_of_controls"], **controls}
            output[name]["horizons"][str(h)] = result
        del window, abnormal, outcome_reason, pool
    return output


def _threshold(fields, trial_count, repo_root):
    criteria = fields["pass_criteria"]["design_validation"]
    threshold = max(criteria["core_t_stat_gt"], experiment_registry.required_t_stat(EXPERIMENT_ID, repo_root=repo_root))
    if trial_count > 20:
        threshold = max(threshold, criteria["core_t_stat_gt_when_trials_gt_20"])
    return float(threshold)


def judgement_inputs(fields, category, result, trial_count, *, repo_root=PROJECT_ROOT):
    metric = NET_METRIC if category in POSITIVE else GROSS_METRIC
    overall, design = result["overall"], result["design"]
    validation = result["by_year"]["2025"][metric]
    design_mean = design[metric]
    same_sign = bool(np.sign(validation) == np.sign(design_mean)) if validation is not None and design_mean is not None else None
    t_stat = overall["net_t_stat" if category in POSITIVE else "t_stat"]
    threshold = _threshold(fields, trial_count, repo_root)
    share = result["random_control"]["share_of_controls"]
    tail = min(0.05, fields["pass_criteria"]["design_validation"]["random_control_top_percent"] / 100)
    direction = None
    if t_stat is not None and share is not None and overall[metric] is not None:
        if overall[metric] > 0 and t_stat > threshold and share <= tail:
            direction = "positive"
        elif overall[metric] < 0 and t_stat < -threshold and share >= 1 - tail:
            direction = "negative"
    return {"design_events": design["count"], GROSS_METRIC: overall[GROSS_METRIC], NET_METRIC: overall[NET_METRIC],
            NET_AT_COST_1_5_METRIC: result["cost_sensitivity"]["1.5"][NET_METRIC],
            "gross_t_stat": overall["t_stat"], "net_t_stat": overall["net_t_stat"], "t_stat": t_stat,
            "abs_t_stat": abs(overall["t_stat"]) if overall["t_stat"] is not None else None,
            "random_control_share": share, "validation_year_same_sign": same_sign,
            "design_mean": design_mean, "validation_mean": validation,
            "trial_count_after_run": trial_count, "required_t_stat": threshold, "direction": direction}


def judge_category(fields, category, results, *, repo_root=PROJECT_ROOT):
    if category not in CATEGORIES:
        raise ValueError("category must be one of the eight preregistered variants")
    criteria = fields["pass_criteria"]["design_validation"]
    minimum = criteria["minimum_observations"]["events"]
    if results["design_events"] < minimum:
        return "insufficient", [f"design events {results['design_events']} < {minimum}"]
    if category not in POSITIVE | NEGATIVE:
        return "direction_only", ["undecided category; record supported direction only, never adopt a signal"]
    names = ["t_stat", "random_control_share", NET_METRIC if category in POSITIVE else GROSS_METRIC]
    if category in POSITIVE:
        names.append(NET_AT_COST_1_5_METRIC)
    missing = [f"{name} is undefined" for name in names if results[name] is None or not np.isfinite(results[name])]
    if results["validation_year_same_sign"] is None:
        missing.append("validation-year sign is unmeasured")
    if missing:
        return "insufficient", missing
    threshold = _threshold(fields, results["trial_count_after_run"], repo_root)
    tail = min(0.05, criteria["random_control_top_percent"] / 100)
    failures = []
    if category in POSITIVE:
        if results[NET_METRIC] <= criteria["net_performance_gt"]:
            failures.append(f"{NET_METRIC} must exceed 0")
        if results["t_stat"] <= threshold:
            failures.append(f"net t_stat must exceed {threshold:g}")
        if results["random_control_share"] > tail:
            failures.append(f"random_control_share must be <= {tail:g}")
        if results[NET_AT_COST_1_5_METRIC] <= criteria["net_performance_at_cost_multiplier_1_5_gt"]:
            failures.append(f"{NET_AT_COST_1_5_METRIC} at cost x1.5 must exceed 0")
    else:
        if results[GROSS_METRIC] >= 0:
            failures.append(f"{GROSS_METRIC} must be negative")
        if results["t_stat"] >= -threshold:
            failures.append(f"gross t_stat must be below {-threshold:g}")
        if results["random_control_share"] < 1 - tail:
            failures.append(f"random_control_share must be >= {1 - tail:g}")
    if not results["validation_year_same_sign"]:
        failures.append("2025 sign differs from design")
    return ("fail", failures) if failures else ("pass", ["all direction-specific preregistered criteria satisfied"])


def write_results(payload, *, repo_root=PROJECT_ROOT):
    """B001 event summary, finite JSON and exclusive UTC files (common's policy)."""
    _registration(repo_root)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    lines = [f"# 실험 결과: {EXPERIMENT_ID}", "", INTERPRETATIONS["note"], "",
             "| 유형 | 판정 | 설계 이벤트 | 전체 이벤트 | 총초과수익 | 순초과수익 | 총 t | 순 t | 대조군 share | 방향 |",
             "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |"]
    for name, category in payload["categories"].items():
        values = category["horizons"]["5"]["overall"]
        inputs = category["judgement_inputs"]
        lines.append(f"| {name} | {category['verdict']} | {inputs['design_events']} | {values['count']} | {values['mean_abnormal']} | {values['mean_net']} | {values['t_stat']} | {values['net_t_stat']} | {inputs['random_control_share']} | {inputs['direction']} |")
    lines += ["", *[f"{name}: " + "; ".join(category["reasons"]) for name, category in payload["categories"].items()]]
    lines += ["", "## interpretations (판정 기준 해석)", "", "| 지표 | 사용 지표 | 정의 |", "| --- | --- | --- |"]
    for name, item in payload["interpretations"]["criteria"].items():
        lines.append(f"| {name} | {item['metric']} | {item['definition']} |")
    lines += ["", "연도별·유동성별·보조 기간·비용 민감도·제외 사유·대조군 세트는 JSON에 기록합니다.", "",
              f"실행 시간: {payload['performance']['runtime_seconds']:.3f}초; 최고 RSS: {payload['performance']['peak_memory_mib']:.1f} MiB.", "",
              *[f"- {note}" for note in payload["assumptions"]]]
    directory = Path(repo_root) / "research/experiments" / EXPERIMENT_ID / "results"
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    json_path, md_path = directory / f"{stamp}.json", directory / f"{stamp}.md"
    with json_path.open("x", encoding="utf-8") as json_file:
        with md_path.open("x", encoding="utf-8") as md_file:
            json_file.write(encoded)
            md_file.write("\n".join(lines) + "\n")
    return json_path, md_path


def run_experiment(*, data_dir=PROJECT_ROOT / "data", repo_root=PROJECT_ROOT, listings=None, prices=None, benchmarks=None, sessions=None):
    started = time.perf_counter()
    fields = _registration(repo_root)
    sessions = _session_calendar() if sessions is None else sessions
    if listings is None:
        listings, _ = load_listings(data_dir, repo_root=repo_root)
    events, counts = build_events(listings)
    if prices is None:
        prices = d001._load_prices(Path(data_dir) / "market/krx_daily", PRICE_START, PRICE_END, repo_root=repo_root)
    events, price_counts = filter_events(events, prices, sessions, repo_root=repo_root)
    counts.update(price_counts)
    if benchmarks is None:
        benchmarks = load_benchmarks(data_dir, repo_root=repo_root)
    benchmark = _benchmark_frame(benchmarks, repo_root=repo_root)
    required_days = signal_eval._dates(sessions)
    required_days = required_days[required_days < CUTOFF]
    keys = pd.MultiIndex.from_product([required_days, list(MARKET_INDEX_NAMES)])
    if benchmark.reindex(keys).isna().any():
        raise ValueError("B001 execution requires complete KOSPI/KOSDAQ benchmark coverage before 2026")
    categories = evaluate_events(events, prices, benchmarks, sessions, repo_root=repo_root)
    trial_count = experiment_registry.trial_count(EXPERIMENT_ID, repo_root=repo_root) + len(CATEGORIES)
    for name, result in categories.items():
        inputs = judgement_inputs(fields, name, result["horizons"]["5"], trial_count, repo_root=repo_root)
        verdict, reasons = judge_category(fields, name, inputs, repo_root=repo_root)
        result.update(judgement_inputs=inputs, verdict=verdict, reasons=reasons)
    payload = {"experiment_id": EXPERIMENT_ID, "categories": categories, "filter_counts": counts,
               "benchmark_coverage": _coverage(benchmarks, sessions), "interpretations": INTERPRETATIONS,
               "performance": {"runtime_seconds": time.perf_counter() - started,
                               "peak_memory_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024},
               "assumptions": ["설계·검증 연도는 접수일 기준이며 날짜 전용 접수일 다음 세션 시가에 진입합니다.",
                               "가격은 KRX 최신 저장 관측, 벤치마크는 정확한 코스피·코스닥 광역 지수 최신 관측입니다. 수집 시각을 과거 관측 시각으로 주장하지 않습니다.",
                               "실제 이벤트는 common.universe_filter의 완전한 20세션 ADV 규칙을 따릅니다. 필터는 진입 직전 세션에서 계산합니다.",
                               "대조군은 event_study의 진입 가능 전체 종목 풀과 복원 추출 규칙을 따릅니다. 미래 청산이나 유동성으로 풀을 좁히지 않습니다.",
                               "2026년 가격·지수값을 평가하지 않습니다. 늦은 2025년 공시는 기간별로 보류 구간 도달 건수를 보고하며 제외합니다.",
                               "전체 시장 가격 세션 누락은 청산 시점을 바꾸므로 실행을 거부합니다. dry-run 벤치마크 건수는 예정 청산 기준이며 stale 평가일 coverage와 다를 수 있습니다.",
                               "신호 성능은 이 실행에서만 측정합니다. direction_only 유형은 매수·회피 신호로 채택하지 않습니다."]}
    # Serialize everything before publishing or appending any category trial.
    json.dumps(payload, ensure_ascii=False, allow_nan=False)
    for name, result in categories.items():
        experiment_registry.record_trial(EXPERIMENT_ID, name, result["judgement_inputs"], result["verdict"],
                                         note="5-session category trial; " + "; ".join(result["reasons"]), repo_root=repo_root)
    write_results(payload, repo_root=repo_root)
    return payload


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="print filters and benchmark coverage (default)")
    mode.add_argument("--execute", action="store_true", help="evaluate local data and record eight category trials")
    parser.add_argument("--data-dir", type=Path, default=PROJECT_ROOT / "data")
    args = parser.parse_args(argv)
    if args.execute:
        result = run_experiment(data_dir=args.data_dir)
        for name, category in result["categories"].items():
            print(f"{name}: {category['verdict']}; " + "; ".join(category["reasons"]))
    else:
        plan = dry_run(data_dir=args.data_dir)
        print(f"{EXPERIMENT_ID} dry-run")
        print(f"Data directory: {plan['data_dir']}")
        print(f"Guarded read span: {plan['read_start']}..{plan['read_end']}; listing files={plan['listing_files']}; price sessions={plan['price_sessions']}; rows={plan['price_rows']}")
        labels = (*CATEGORIES, "other")
        print("Filter counts (remaining after each step): " + ", ".join(labels))
        for step, counts in plan["filter_counts"].items():
            print(f"{step}: " + ", ".join(str(counts[name]) for name in labels))
        print(f"Missing exchange price sessions: {len(plan['missing_price_sessions'])}")
        print(f"Benchmark file exists: {plan['benchmark_file_exists']}")
        for market, coverage in plan["benchmark_coverage"].items():
            print(f"{market}: {coverage['sessions']}/{coverage['required_sessions']} sessions; first={coverage['first']}; last={coverage['last']}")
        for h, inventory in plan["horizons"].items():
            print(f"Horizon {h} (planned / scheduled benchmark coverage / holdout exclusions): " + ", ".join(
                f"{name}={inventory['planned'][name]}/{inventory['benchmark_covered'][name]}/{inventory['holdout_horizon'][name]}" for name in CATEGORIES))
            for reason in ("incomplete_calendar_horizon", "missing_holding_sessions", "missing_entry_price", "untradable_entry", "limit_up_entry"):
                print(f"  {reason}: " + ", ".join(str(inventory[reason][name]) for name in CATEGORIES))
        print("Coverage uses scheduled exits; stale exit valuation coverage is checked on execute. No returns, random draws, registry rows or result files written.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
