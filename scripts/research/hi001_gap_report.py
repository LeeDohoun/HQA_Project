"""Read-only HI001 gap diagnosis through 2025-12-31; JSON goes to stdout.

Build the streaming indices once, then inspect bounded monthly stock windows.
No portfolio performance, judgement, result publication or registry writes.
Run: venv/bin/python scripts/research/hi001_gap_report.py > /tmp/hi001_gaps.json
"""
from __future__ import annotations

import argparse
from collections import Counter, deque
import json
from pathlib import Path
import resource
import signal
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np
import pandas as pd

from backtesting.experiments import common, hi001
from src.research import industry_index, industry_map


def _dates(values):
    return [day.date().isoformat() for day in values]


def gap_report(*, data_dir=hi001.PROJECT_ROOT / "data/market/krx_daily",
               companies_path=industry_map.DEFAULT_COMPANIES_PATH,
               repo_root=hi001.PROJECT_ROOT, prices=None):
    started = time.perf_counter()
    fields = common.load_preregistration(hi001.EXPERIMENT_ID, repo_root=repo_root)
    days, factory, profiles = hi001._inputs(data_dir, companies_path, fields, repo_root, prices)
    decisions, skipped = hi001.decision_schedule(days, fields)
    reads = 0
    def source():
        nonlocal reads
        for frame in factory():
            reads += 1
            if resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024 > 2800:
                raise RuntimeError("HI001 diagnostic stopped before exceeding 3 GB RSS")
            if reads % 250 == 0:
                print(f"Read {reads} daily frames; elapsed {time.perf_counter() - started:.0f}s", file=sys.stderr, flush=True)
            yield frame
    indices, dropped = hi001.stream_indices(source)
    print(f"Indices constructed once ({len(days)} sessions)", file=sys.stderr, flush=True)
    labels = sorted(indices.index.get_level_values("industry").unique())
    buffer, position, rows, monthly = deque(maxlen=hi001.HISTORY + hi001.HORIZON), -1, [], []
    iterator = iter(source())
    for day in decisions:
        target = days.get_loc(day) + hi001.HORIZON
        while position < target:
            buffer.append(next(iterator))
            position += 1
        window = pd.concat(buffer)
        history = window.loc[window.index.get_level_values("trade_date") <= day]
        features, members = hi001.features_at_close(history, day, dropped=dropped, repo_root=repo_root, indices=indices)
        work = hi001._index_input(history, dropped)
        prepared, _ = industry_index._prepare(work, industry_map.DEFAULT_COMPANIES_PATH, hi001.MIN_ADV)
        stock_returns = prepared.ret_1d.unstack("stock_code")
        levels = industry_index._stock_levels(prepared)
        ma = levels.rolling(20, min_periods=20).mean().iloc[-1]
        returns, entry_day = hi001.holding_returns(window, indices, members, day, days[target])
        feature_days = days[days <= day][-60:]
        holding_days = days[days.get_loc(day) + 1:target + 1]
        table = features.join(returns)
        entry_rows = window.xs(entry_day, level="trade_date")
        tradable, limit_up = hi001.entry_tradability(window, members, day, entry_day)
        legacy_invalid, previous_policy_invalid = set(), set()
        for label in labels:
            group = members if label == industry_index.ALL_MARKET else members.loc[members.industry.eq(label)]
            block = indices.xs(label, level="industry").reindex(days)
            feature_gaps = block.cap_return.reindex(feature_days).isna()
            hold_block = block.reindex(holding_days)
            holding_gaps = hold_block.cap_return.isna()
            reasons = {name: [] for name in ("rs60", "breadth", "topcap", "gross")}
            excluded = label in hi001.EXCLUDED_INDUSTRIES
            if excluded or len(group) < hi001.MIN_MEMBERS:
                for name in ("rs60", "breadth", "topcap"):
                    reasons[name].append("excluded_industry" if excluded else "fewer_than_minimum_members")
            if feature_gaps.any():
                reasons["rs60"].append("index_gap_in_60_session_feature_window")
            market = indices.xs(industry_index.ALL_MARKET, level="industry").cap_return.reindex(feature_days)
            if market.isna().any():
                for name in ("rs60", "topcap"):
                    reasons[name].append("market_index_gap_in_60_session_feature_window")
            if len(feature_days) < 60:
                for name in ("rs60", "topcap"):
                    reasons[name].append("fewer_than_60_feature_sessions")
            bad_breadth = group.index[~np.isfinite(levels.iloc[-1].reindex(group.index)) | ~np.isfinite(ma.reindex(group.index))]
            top = group.market_cap.sort_values(ascending=False, kind="stable").head(5).index
            top_window = stock_returns.reindex(columns=top).tail(60)
            bad_top = top[top_window.isna().any().to_numpy()]
            if len(bad_breadth):
                reasons["breadth"].append("member_price_chain_or_ma20_not_finite")
            if len(bad_top):
                reasons["topcap"].append("topcap_member_gap_in_60_session_feature_window")
            entry = entry_rows.reindex(group.index)
            present = group.index.isin(entry_rows.index)
            bad_open = group.index[present & (~np.isfinite(entry.open) | entry.open.le(0))]
            bad_close = group.index[present & (~np.isfinite(entry.close) | entry.close.le(0))]
            if not len(group):
                reasons["gross"].append("no_decision_time_members")
            if len(bad_open):
                reasons["gross"].append("missing_or_invalid_entry_open")
            if len(bad_close):
                reasons["gross"].append("missing_or_invalid_entry_close")
            # Entry uses member open/close, not the entry day's index return.
            chain_gaps = holding_gaps.iloc[1:]
            if chain_gaps.any():
                reasons["gross"].append("index_gap_in_holding_window")
            gross = returns.loc[label, "cap_gross"] if label in returns.index else np.nan
            # Reconstruct the prior entry policy independently of the new rule.
            # Missing rows had zero legs; present invalid quotes failed valuation.
            old_chain, _ = hi001.holding_chain(indices, label, "cap", holding_days[1:])
            old_finite = bool(len(group) and not len(bad_open) and not len(bad_close)
                              and len(old_chain) == hi001.HORIZON - 1 and old_chain.notna().all())
            if not old_finite:
                previous_policy_invalid.add(label)
            if not old_finite or chain_gaps.any():
                legacy_invalid.add(label)
            excluded_codes = list(group.index[~tradable.reindex(group.index)])
            rows.append({"month": str(day.to_period("M")), "decision_date": day.date().isoformat(),
                         "entry_date": entry_day.date().isoformat(), "exit_date": days[target].date().isoformat(),
                         "industry": label, "member_count": len(group),
                         "excluded_industry": excluded, "reasons": reasons,
                         "features": {name: float(features.loc[label, name]) if label in features.index and np.isfinite(features.loc[label, name]) else None
                                      for name in ("rs60", "breadth", "topcap", *hi001.VARIANTS)},
                         "feature_index_gap_dates": _dates(feature_gaps.index[feature_gaps]),
                         "holding_index_gap_dates": _dates(holding_gaps.index[holding_gaps]),
                         "holding_gap_member_counts": {date.date().isoformat(): int(hold_block.loc[date, "member_count"]) if pd.notna(hold_block.loc[date, "member_count"]) else 0
                                                       for date in holding_gaps.index[holding_gaps]},
                         "breadth_members_with_nonfinite_chain_or_ma": list(bad_breadth),
                         "topcap_members_with_return_gaps": list(bad_top),
                         "missing_entry_rows": list(group.index[~present]),
                         "missing_entry_open_members": list(bad_open),
                         "zero_entry_open_members": list(group.index[present & entry.open.eq(0)]),
                         "nonfinite_entry_open_members": list(group.index[present & ~np.isfinite(entry.open)]),
                         "missing_entry_close_members": list(bad_close),
                         "entry_excluded_members": excluded_codes,
                         "entry_excluded_member_count": len(excluded_codes),
                         "zero_or_nonfinite_entry_volume_members": list(group.index[~np.isfinite(entry.volume) | entry.volume.le(0)]),
                         "limit_up_entry_members": list(group.index[limit_up.reindex(group.index)]),
                         "entry_cash_industry": bool(returns.loc[label, "entry_cash_industry"]) if label in returns.index else False,
                         "previous_policy_gross_finite": old_finite,
                         "legacy_gross_finite": label not in legacy_invalid, "policy_gross_finite": bool(np.isfinite(gross)),
                         "policy_gap_sessions_counted": int(returns.loc[label, "cap_index_gap_sessions"]) if label in returns.index else 0})
        status = {}
        benchmark_ok = industry_index.ALL_MARKET not in legacy_invalid
        for variant in hi001.VARIANTS:
            eligible = table.loc[np.isfinite(table[variant])]
            bad = sorted(set(eligible.index) & legacy_invalid)
            selected = list(eligible.sort_index().sort_values(variant, ascending=False, kind="stable").head(3).index)
            status[variant] = {"decision_time_eligible": list(eligible.index), "eligible_count": len(eligible),
                               "legacy_nonfinite_gross_industries": bad,
                               "legacy_month_accepted": len(eligible) >= 3 and benchmark_ok and not bad,
                               "previous_policy_month_computable_including_controls": bool(len(eligible) >= 3 and industry_index.ALL_MARKET not in previous_policy_invalid
                                                                                           and not (set(eligible.index) & previous_policy_invalid)),
                               "selected": selected,
                               "benchmark_gross_finite": bool(np.isfinite(returns.loc[industry_index.ALL_MARKET, "cap_gross"])),
                               "benchmark_entry_excluded_members": int(returns.loc[industry_index.ALL_MARKET, "entry_excluded_members"]),
                               "before_benchmark_entry_rule_month_computable_including_controls": bool(len(eligible) >= 3 and industry_index.ALL_MARKET not in previous_policy_invalid
                                                                                                      and np.isfinite(eligible.cap_gross).all()),
                               "policy_industry_baskets_computable_including_controls": bool(len(eligible) >= 3 and np.isfinite(eligible.cap_gross).all()),
                               "selected_entry_excluded_members": int(eligible.loc[selected, "entry_excluded_members"].sum()),
                               "selected_entry_cash_industries": list(eligible.loc[selected].index[eligible.loc[selected, "entry_cash_industry"].astype(bool)]),
                               "eligible_entry_excluded_members": int(eligible.entry_excluded_members.sum()),
                               "eligible_entry_cash_industries": list(eligible.index[eligible.entry_cash_industry.astype(bool)]),
                               "policy_month_computable_including_controls": bool(len(eligible) >= 3 and np.isfinite(returns.loc[industry_index.ALL_MARKET, "cap_gross"]) and np.isfinite(eligible.cap_gross).all())}
        monthly.append({"month": str(day.to_period("M")), "variants": status})
        print(f"Diagnosed {day:%Y-%m}; elapsed {time.perf_counter() - started:.0f}s", file=sys.stderr, flush=True)
    summary = {variant: {"legacy_accepted_months": sum(row["variants"][variant]["legacy_month_accepted"] for row in monthly),
                         "policy_computable_months": sum(row["variants"][variant]["policy_month_computable_including_controls"] for row in monthly),
                         "before_benchmark_entry_rule_computable_months": sum(row["variants"][variant]["before_benchmark_entry_rule_month_computable_including_controls"] for row in monthly),
                         "newly_computable_after_benchmark_entry_rule_months": sum(row["variants"][variant]["policy_month_computable_including_controls"] and not row["variants"][variant]["before_benchmark_entry_rule_month_computable_including_controls"] for row in monthly),
                         "benchmark_entry_excluded_members": sum(row["variants"][variant]["benchmark_entry_excluded_members"] for row in monthly),
                         "previous_policy_computable_months": sum(row["variants"][variant]["previous_policy_month_computable_including_controls"] for row in monthly),
                         "selected_entry_excluded_members": sum(row["variants"][variant]["selected_entry_excluded_members"] for row in monthly),
                         "selected_cash_industry_months": sum(len(row["variants"][variant]["selected_entry_cash_industries"]) for row in monthly),
                         "eligible_cash_industry_months": sum(len(row["variants"][variant]["eligible_entry_cash_industries"]) for row in monthly),
                         "newly_computable_months": sum(row["variants"][variant]["policy_month_computable_including_controls"] and not row["variants"][variant]["previous_policy_month_computable_including_controls"] for row in monthly),
                         "policy_industry_baskets_computable_months": sum(row["variants"][variant]["policy_industry_baskets_computable_including_controls"] for row in monthly),
                         "months_blocked_only_by_benchmark": sum(row["variants"][variant]["policy_industry_baskets_computable_including_controls"] and not row["variants"][variant]["benchmark_gross_finite"] for row in monthly),
                         "months_with_fewer_than_three_decision_time_signals": sum(row["variants"][variant]["eligible_count"] < 3 for row in monthly)}
               for variant in hi001.VARIANTS}
    counts = Counter(reason for row in rows for reason in row["reasons"]["gross"])
    return {"read_only": True, "read_end": days[-1].date().isoformat(), "profiles_found": profiles,
            "summary": summary, "gross_reason_industry_month_counts": dict(counts), "common_skipped_months": skipped,
            "months": monthly, "industry_months": rows, "index_member_policy_counts": indices.attrs,
            "runtime_seconds": time.perf_counter() - started,
            "peak_memory_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=hi001.PROJECT_ROOT / "data/market/krx_daily")
    parser.add_argument("--companies-path", type=Path, default=industry_map.DEFAULT_COMPANIES_PATH)
    args = parser.parse_args(argv)
    def timeout(signum, frame):
        raise RuntimeError("HI001 diagnostic exceeded its 30-minute limit")
    signal.signal(signal.SIGALRM, timeout)
    signal.alarm(1790)
    report = gap_report(data_dir=args.data_dir, companies_path=args.companies_path)
    signal.alarm(0)
    print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
