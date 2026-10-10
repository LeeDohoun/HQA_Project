"""HC002 phase2_ew characteristic controls: 사후 진단, 판정에 쓰지 않음.

Dry-run is the default. --execute evaluates local design/validation archives
and writes only diagnostics/matched_controls_<UTC timestamp>.json and .md.
"""
from __future__ import annotations

import argparse
import json
import resource
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np
import pandas as pd

from backtesting import holdout, signal_eval
from backtesting.diagnostics.matched_controls import CELLS, LABEL, matched_controls
from backtesting.experiment_registry import PROJECT_ROOT
from backtesting.experiments import common, hc002
from src.ingestion import krx_market
from src.research import industry_map


_OUTPUT = Path("research/experiments") / hc002.EXPERIMENT_ID / "diagnostics"


def _last_dates(price_files, repo_root):
    """One streaming identity/date pass preserves HC002 no_later_price policy.

    A month-window's last row cannot stand in for the stock's last row through
    2025. Only this small per-stock index survives the pass; prices do not.
    """
    if not price_files:
        raise ValueError("no price files in the guarded HC002 span")
    days = pd.DatetimeIndex([day for day, _ in price_files])
    holdout.guard_period(days.min().date(), days.max().date(), repo_root=repo_root)
    if days.max() >= hc002.hc001.CUTOFF:
        raise ValueError("diagnostic prices must precede 2026-01-01")
    latest = {}
    for day, path in price_files:
        with path.open(encoding="utf-8") as handle:
            for number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                row = json.loads(line)
                if row["trade_date"] != day.date().isoformat():
                    raise ValueError(f"price row does not match file date: {path.name}:{number}")
                code = row["stock_code"]
                if not isinstance(code, str) or not code:
                    raise ValueError(f"missing stock_code: {path.name}:{number}")
                latest[code] = max(day, latest.get(code, day))
    return pd.Series(latest, name="last_date", dtype="datetime64[ns]")


def _snapshot_features(prices, universe, sessions, profiles, data_dir):
    day = universe.decision_date.iloc[0]
    codes = pd.Index(universe.stock_code, name="stock_code")
    history = sessions[sessions <= day][-60:]
    past = prices.loc[prices.index.get_level_values("trade_date").isin(history)]
    returns = past.ret_1d.unstack("stock_code").reindex(index=history, columns=codes)
    verified = past.calendar_status.eq("verified").unstack("stock_code").reindex(index=history, columns=codes)
    returns = returns.where(verified.eq(True))
    vol = returns.std(ddof=1).where(returns.count().eq(60))
    # D001's projected loader omits market_cap. Read this one decision day's
    # actual KRX field instead of reconstructing size from current shares.
    caps = krx_market.load_prices(list(codes), day.date().isoformat(), day.date().isoformat(), data_dir,
                                  columns=("trade_date", "stock_code", "market_cap"))
    if "market_cap" not in caps:
        raise ValueError(f"missing KRX market_cap at decision close: {day.date().isoformat()}")
    current = prices.xs(day, level="trade_date").reindex(codes)
    features = universe[["decision_date", "stock_code", "avg_trading_value_20d"]].copy()
    features["feature_date"] = day
    features["market"] = current.market.to_numpy()
    features["market_cap"] = caps.xs(day, level="trade_date").market_cap.reindex(codes).to_numpy()
    features["volatility_60d"] = vol.reindex(codes).to_numpy()
    features["industry"] = [profiles.get(code, industry_map.UNCLASSIFIED) for code in codes]
    invalid = ~np.isfinite(features.market_cap) | ~np.isfinite(features.volatility_60d)
    if invalid.any():
        raise ValueError(f"missing market_cap or complete verified 60-session ret_1d history at "
                         f"{day.date().isoformat()}: {features.loc[invalid, 'stock_code'].tolist()}")
    return features


def dry_run(*, data_dir=PROJECT_ROOT / "data", repo_root=PROJECT_ROOT):
    plan = hc002.dry_run(data_dir=data_dir, repo_root=repo_root)
    plan.pop("variants")
    sessions = hc002.hc001._session_calendar()
    planned, _ = hc002.rebalance_calendar(sessions)
    files = hc002.hc001.d001._price_files(Path(data_dir) / "market/krx_daily", hc002.hc001.PRICE_START,
                                        hc002.hc001.PRICE_END, repo_root=repo_root)
    available = pd.DatetimeIndex([day for day, _ in files])
    covered = []
    for row in planned:
        position = sessions.get_loc(pd.Timestamp(row["decision_date"]))
        required = sessions[max(0, position - 59):position + 1 + hc002.HORIZON]
        if position >= 59 and required.difference(available).empty:
            covered.append(row["decision_date"])
    return {**plan, "diagnostic": "characteristic_matched_controls", "selection": "phase2_ew", "label": LABEL,
            "used_for_judgement": False, "draws": 200, "seed": 0, "cells": list(CELLS), "industry": True,
            "feature_lookback_sessions": 60, "holding_sessions": hc002.HORIZON,
            "feature_window_covered_months": covered,
            "incomplete_feature_windows": [row["decision_date"] for row in planned if row["decision_date"] not in covered],
            "output_pattern": str(Path(repo_root) / _OUTPUT / "matched_controls_<UTC timestamp>.{json,md}"),
            "execution_plan": [
                "2025년까지 종목별 마지막 관측일만 스트리밍하여 보존",
                "월별 최대 80세션 가격 창; HC002 universe_on_decision / forward_observations 재사용",
                "월말 시총·HC002 20세션 ADV·검증된 60세션 ret_1d 표본 표준편차",
                "업종→변동성 순서로 후보 5개 미만 셀 완화; 불가피한 중복 별도 집계",
                "200 draws, seed=0; 매칭 차이 및 회귀 국면 계수의 Bartlett NW lag 1",
                "--execute만 진단 JSON/한국어 MD 저장; 판정·대장 행 없음",
            ], "coverage_note": "파일 존재만 확인합니다. 종목별 특성의 완전성은 execute에서 검사하며 결측이면 명시적으로 중단합니다."}


def run_diagnostic(*, data_dir=PROJECT_ROOT / "data", repo_root=PROJECT_ROOT):
    """Compute without publishing; never call HC002.run_experiment or judge."""
    started = time.perf_counter()
    hc002._registration(repo_root)
    sessions = hc002.hc001._session_calendar()
    planned, excluded = hc002.rebalance_calendar(sessions)
    files = hc002.hc001.d001._price_files(Path(data_dir) / "market/krx_daily", hc002.hc001.PRICE_START,
                                        hc002.hc001.PRICE_END, repo_root=repo_root)
    available = pd.DatetimeIndex([day for day, _ in files])
    if not available.difference(sessions).empty:
        raise ValueError("price dates must be exchange sessions")
    last_dates = _last_dates(files, repo_root)
    profiles = industry_map.load_industry_map(Path(data_dir) / "reference/dart_company/companies.jsonl")
    features, outcomes, selections, counts, return_counts, stale = [], [], [], [], [], []
    for rebalance in planned:
        day = pd.Timestamp(rebalance["decision_date"])
        position = sessions.get_loc(day)
        # Preserve HC002's calendar exclusions, including its 20-session ADV.
        required = sessions[max(0, position - 19):position + 1 + hc002.HORIZON]
        missing = required.difference(available)
        if position < 19 or len(missing):
            excluded.append({**rebalance, "reason": "missing_price_sessions",
                             "missing_sessions": [date.date().isoformat() for date in missing]})
            continue
        window = sessions[max(0, position - 59):position + 1 + hc002.HORIZON]
        prices = hc002.hc001.d001._load_prices(Path(data_dir) / "market/krx_daily", window[0], window[-1], repo_root=repo_root)
        common.guard_prices(prices, repo_root=repo_root)
        # HC002 validates strings; a one-category map otherwise remains categorical.
        prices["stock_name"] = prices.stock_name.astype(object)
        history = prices.loc[prices.index.get_level_values("trade_date") <= day]
        with common.quarterly_view(data_dir, day, repo_root=repo_root) as financial_view:
            eligible, report = hc002.universe_on_decision(history, day, data_dir=financial_view, repo_root=repo_root)
        counts.append({**report, "decision_date": rebalance["decision_date"]})
        if eligible.empty:
            excluded.append({**rebalance, "reason": "no_eligible_signals"})
            continue
        universe = eligible.assign(decision_date=day, trade_date=pd.Timestamp(rebalance["trade_date"])).reset_index()
        snapshot = _snapshot_features(prices, universe, sessions, profiles, data_dir)
        observations = hc002.forward_observations(prices, universe, repo_root=repo_root, last_dates=last_dates)
        features.append(snapshot)
        outcomes.append(observations[["decision_date", "stock_code", "gross"]])
        selections.append(universe.loc[universe.phase.eq(2), ["decision_date", "stock_code"]])
        return_counts.append(signal_eval._return_counts(observations))
        stale.extend(signal_eval._stale_records(observations))
        # Drop this window before loading the next; retain only monthly tables.
        del prices, history, eligible, universe, observations
    if not features:
        raise ValueError("no eligible HC002 decision months in the guarded span")
    result = matched_controls(pd.concat(selections, ignore_index=True), pd.concat(features, ignore_index=True),
                              pd.concat(outcomes, ignore_index=True))
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    if peak >= 4096:
        raise MemoryError(f"diagnostic exceeded the 4 GiB peak RSS budget: {peak:.1f} MiB")
    return {**result, "experiment_id": hc002.EXPERIMENT_ID, "selected_variant": "phase2_ew",
            "period": {"design": "2017-05..2024-12", "validation": "2025-01..2025-11", "holdout_used": False},
            "calendar_exclusions": excluded, "universe_counts": counts,
            "return_counts": {**{name: sum(row[name] for row in return_counts) for name in (*signal_eval._RETURN_FLAGS, "no_later_price", "excluded_count")},
                              "excluded": {reason: sum(row["excluded"].get(reason, 0) for row in return_counts)
                                           for reason in sorted({reason for row in return_counts for reason in row["excluded"]})}},
            "stale_exits": stale,
            "performance": {"runtime_seconds": time.perf_counter() - started, "peak_memory_mib": peak,
                            "max_price_window_sessions": 60 + hc002.HORIZON},
            "assumptions": [
                "사후 진단, 판정에 쓰지 않음. 사전등록·기존 결과·registry.csv는 변경하지 않습니다.",
                "HC002와 같은 유니버스·2국면 선택 및 다음 시가→진입일 포함 20번째 종가의 ret_1d 체인·체결 제외를 사용합니다. 비용을 차감하지 않는 총수익입니다.",
                "특성·tercile은 미래 수익률 제외 전의 월말 유니버스에서 시장별로 계산합니다. 균형표의 유니버스와 수익률 비교는 HC002의 체결 가능 집합입니다.",
                "시총은 월말 KRX market_cap, ADV는 HC002의 20세션 평균 거래대금, 변동성은 월말 포함 검증된 60세션 ret_1d의 표본 표준편차입니다. 결측 특성은 대체하거나 종목을 몰래 제외하지 않고 중단합니다.",
                "업종은 industry_map의 현재 저장 분류이며 역사적 업종 변경을 재현하지 못합니다. 가격도 HC002처럼 최신 저장 버전으로, 당시 수집 시각까지 재현하는 것은 아닙니다.",
                "비복원 추출은 달마다 초기화합니다. 부족한 셀은 업종→변동성만 해제하며 시총·ADV·시장은 유지합니다. 남은 후보가 없으면 중단합니다.",
                "대조군 비율은 평균(대조군−유니버스) ≥ 평균(선정−유니버스)인 draw 비율입니다. 평균(선정−대조군) ≤ 0과 같으며 검정 p값이 아닙니다.",
                "NW는 인접한 달의 Bartlett lag 1, 자기공분산 분모 n, 유한표본 보정 없음입니다. 관측 공백을 인접 월로 연결하지 않으며 0분산 t와 식별 불가능한 회귀 계수는 null입니다.",
            ]}


def write_diagnostic(payload, *, repo_root=PROJECT_ROOT):
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    stats, regression = payload["difference"], payload["regression"]["phase2_coefficient"]

    def number(value):
        return "정의되지 않음" if value is None else f"{value:.6f}"

    lines = ["# HC002 phase2_ew 특성 매칭 대조군", "", f"**{LABEL}**", "",
             "설계 2017-05~2024-12 + 검증 2025-01~2025-11. 2026년 가격 미사용. 모든 수익률은 비용 전 비율입니다.", "",
             f"- 유효 월: {stats['count']}; 평균 매칭 차이: {number(stats['mean'])}; NW lag 1 t: {number(stats['t_stat'])}",
             f"- 실제 이상 대조군 draw 비율: {number(payload['random_control']['share_of_controls'])} (200 draws, seed=0)",
             f"- 회귀 국면 계수: 평균 {number(regression['mean'])}; NW lag 1 t {number(regression['t_stat'])}; 식별된 월 {regression['count']}",
             f"- 완화 (선정 종목×월): 업종 {payload['relaxations']['industry']}, 변동성 {payload['relaxations']['vol']}; 비복원 후보 소진 후 중복 슬롯 {payload['replacement_slots']}",
             f"- 수익률 제외: 유니버스 {payload['exclusions']['universe_returns']}, 선정 {payload['exclusions']['selection_returns']}; 선정 없는 월 {len(payload['exclusions']['skipped_months'])}", "",
             "| 의사결정일 | 선정 수 | 선정 총수익 | 매칭 대조군 총수익 (draw 평균) | 차이 | 업종 완화 | 변동성 완화 |",
             "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for row in payload["monthly"]:
        lines.append(f"| {row['decision_date']} | {row['selection_count']} | {number(row['selection_gross'])} | "
                     f"{number(row['matched_control_gross'])} | {number(row['difference'])} | {row['relaxations']['industry']} | {row['relaxations']['vol']} |")
    cohorts = ("selection", "universe", "matched_controls")
    lines += ["", "특성 균형 (월 동일가중, percentile은 0~1):", "",
              "| 특성 | 선정 | 유니버스 | 매칭 대조군 |", "| --- | ---: | ---: | ---: |"]
    if payload["balance"]:
        for label in ("size", "adv", "vol"):
            lines.append(f"| 평균 {label} percentile | " + " | ".join(number(payload["balance"][cohort][f"mean_{label}_percentile"]) for cohort in cohorts) + " |")
        for industry in payload["balance"]["universe"]["industry_shares"]:
            lines.append(f"| 업종 비중: {industry} | " + " | ".join(number(payload["balance"][cohort]["industry_shares"][industry]) for cohort in cohorts) + " |")
    lines += ["", f"최대 RSS: {payload['performance']['peak_memory_mib']:.1f} MiB; 실행 시간: {payload['performance']['runtime_seconds']:.3f}초.",
              "", "가정과 제한:", "", *[f"- {note}" for note in payload["assumptions"]]]
    directory = Path(repo_root) / _OUTPUT
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    paths = directory / f"matched_controls_{stamp}.json", directory / f"matched_controls_{stamp}.md"
    with paths[0].open("x", encoding="utf-8") as json_file:
        with paths[1].open("x", encoding="utf-8") as summary_file:
            json_file.write(encoded)
            summary_file.write("\n".join(lines) + "\n")
    return paths


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="plan and local archive coverage only (default)")
    mode.add_argument("--execute", action="store_true", help="compute and write only the post-registration diagnostic")
    parser.add_argument("--data-dir", type=Path, default=PROJECT_ROOT / "data")
    args = parser.parse_args(argv)
    if args.execute:
        payload = run_diagnostic(data_dir=args.data_dir)
        for path in write_diagnostic(payload):
            print(path)
    else:
        print(json.dumps(dry_run(data_dir=args.data_dir), ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
