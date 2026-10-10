"""HI001 industry strength; local coverage is the default, execution is opt-in.

Daily files use KRX's latest stored episode per stock. Execution makes two
streaming passes: monthly index blocks with 20 warm-up sessions, then bounded
141-history/20-forward-session feature windows. No stock-by-full-history matrix
is retained. Classifications are frozen once per invocation, not reconstructed
historically. The first holding day replaces the index's close-to-close return
with a decision-cap-weighted mean of members' close/open - 1.
"""
from __future__ import annotations

import math

import argparse
from collections import deque
from itertools import groupby
import json
from pathlib import Path
import resource
import sys
import time

import numpy as np
import pandas as pd

from backtesting import cost_model, experiment_registry, signal_eval
from backtesting.experiment_registry import PROJECT_ROOT
from backtesting.experiments import common, d001, hf001
from src.research import industry_index, industry_map


EXPERIMENT_ID = "HI001_industry_strength"
VARIANTS = ("rs", "rs_breadth", "rs_breadth_topcap")
FEATURES = {"rs": ("rs60",), "rs_breadth": ("rs60", "breadth"),
            "rs_breadth_topcap": ("rs60", "breadth", "topcap")}
HORIZON = 20
HISTORY = 141  # 20 ADV warm-up + 120 daily index returns + baseline.
MIN_MEMBERS = 10
MIN_ADV = 1e8
N_CONTROLS = 200
MULTIPLIERS = (1.0, 1.5, 2.0)
NET_METRIC = "mean_monthly_net_excess"
NET_AT_COST_1_5_METRIC = "mean_monthly_net_excess_at_cost_1_5"
EXCLUDED_INDUSTRIES = (industry_map.UNCLASSIFIED, "지주회사")
INTERPRETATIONS = {
    "note": "정의는 실제 결과를 보기 전(2026-10-05)에 확정",
    "criteria": {
        "net_performance_gt": {
            "metric": f"judgement_inputs.{NET_METRIC}",
            "definition": "상위 3개 업종 동일가중 포트폴리오의 월별 비용 차감 수익률에서 같은 보유 구간의 전체시장 시가총액가중 수익률을 뺀 평균.",
        },
        "net_performance_at_cost_multiplier_1_5_gt": {
            "metric": f"judgement_inputs.{NET_AT_COST_1_5_METRIC}",
            "definition": "같은 월별 순초과수익 평균, 비용 모델 배수 1.5. 기존 비용 모델대로 매도 세금에는 배수를 적용하지 않습니다.",
        },
        "core_t_stat_gt": {
            "metric": "judgement_inputs.t_stat = monthly_excess.t_stat",
            "definition": "설계+검증의 유효 월별 순초과수익 평균 / (표본 표준편차 / 월 수의 제곱근). 순위 IC는 보조 지표입니다.",
        },
        "core_t_stat_gt_when_trials_gt_20": {
            "metric": "judgement_inputs.t_stat = monthly_excess.t_stat",
            "definition": "예정 대장 행까지 포함한 시도 수가 20을 초과하면 같은 월별 순초과수익 t에 강화 기준을 적용합니다.",
        },
        "random_control_top_percent": {
            "metric": "judgement_inputs.random_control_share",
            "definition": "매월 적격 업종 3개를 비복원 추출하는 200개 대조군 중 월별 순초과수익 평균이 실제 평균 이상인 비율. 대조군마다 이전 보유 업종과 동일한 교체 비용 규칙을 적용합니다.",
        },
        "minimum_observations": {
            "metric": "judgement_inputs.observations = monthly_excess.count",
            "definition": "월별 순초과수익 관측 수를 minimum_observations.rebalances와 비교합니다. 종목·업종 행 수나 IC 수를 사용하지 않습니다.",
        },
        "validation_year_same_sign": {
            "metric": "sign(validation_monthly_excess.mean) == sign(design_monthly_excess.mean)",
            "definition": "2025년 월별 순초과수익 평균과 2016~2024년 같은 평균의 부호가 같아야 합니다.",
        },
    },
}
ASSUMPTIONS = [
    "업종 분류는 실행 시작에 읽은 현재 DART 스냅샷입니다. 과거 업종 변경을 복원하지 못하며 분류 수집이 진행 중이면 미분류 비중이 높을 수 있습니다.",
    "2016-01~2025-11 월말을 사용합니다. 2025-12와 불완전한 20세션 보유 구간은 제외하고 2026 가격을 읽지 않습니다.",
    "공통 보통주·시장·검증된 20세션 ADV 필터를 재사용합니다. D001 전용 종가 1,000원 하한은 HI001 사전등록에 없으므로 적용하지 않습니다. 영문 SPAC/기업 인수 목적 명칭도 업종 지수 정책대로 제외합니다.",
    "rs60은 업종 60세션 누적 수익률에서 시장 누적 수익률을 뺀 차이입니다. breadth는 수정 수익률을 연쇄한 가격의 MA20 위 비율, topcap은 결정일 시총 상위 5개 수정 R60 중앙값에서 시장 R60을 뺀 값입니다.",
    "특징 입력은 월말 장 마감까지로 제한합니다. 현재 분류와 가격 파일의 최신 저장 개정치를 사용합니다.",
    "진입일 close/open-1을 결정일 구성 종목·시총 비중으로 평균하고 이후 19세션의 전일 시총 가중 업종 수익률을 연쇄합니다. 시가~첫 종가 구간은 이 근사로 계산하며 기업행위의 장중 영향은 복원하지 않습니다.",
    "상장폐지 관련 결측은 업종 지수 정책대로 마지막 관측 가격 이후 첫 결측 세션에 수익률 0, 다음 세션부터 영구 제외합니다. 당일 비중을 다시 정규화하지 않으며 손실을 만들어 넣지 않습니다.",
    "원주가 대체는 업종 지수의 close/전일 close 정책뿐입니다. 누락 종목·특징을 가짜 값으로 채우지 않습니다. 특징 계산의 지수 공백은 연결하지 않습니다.",
    "교체 비용은 신규 업종의 현재 결정일 바스켓과 이탈 업종의 직전 보유 결정일 바스켓 각각의 평균 왕복 비용에 포트폴리오 비중을 곱한 합입니다. 가격·ADV는 해당 결정일, 세율 날짜는 해당 바스켓의 진입일입니다. 유지 업종 비용과 업종 내부 비중 조정 비용은 0입니다.",
    "동일가중 보조 결과는 업종 내부 수익률과 구성 종목 비용을 동일가중합니다. 모든 결과의 벤치마크는 전체시장 시가총액가중이며 미분류·지주회사·소규모 업종도 포함합니다.",
    "bug fix after the 2026-10-10 run; first run recorded as is: 실제 선택과 200개 대조군의 후보는 결정일 구성 종목 수와 유한한 특징만으로 정합니다. 미선택 업종의 미래 수익률 결측 때문에 월 전체를 제외하던 조건을 제거했습니다. 결정일 후보가 top 수보다 적은 월은 insufficient_decision_time_features로 기록하고 이전 보유 상태를 유지합니다.",
    "bug fix after the 2026-10-10 run; first run recorded as is: 보유 중 구성원이 0이어서 생긴 지수 공백은 마지막 평가액을 유지(일 수익률 0)하고 업종별·선택 포트폴리오별·대조군별 세션 수를 기록합니다. 첫 결측 가격 유지 후 구성원을 제외하는 기존 정책의 전체 바스켓 소진 경우를 명시적으로 처리합니다. 유효 구성원이 있는 지수 결측은 대체하지 않습니다.",
    "bug fix after the 2026-10-10 run; first run recorded as is: 선택·대조군 추출 업종 또는 벤치마크의 수익률을 계산할 수 없으면 명확히 실패합니다. 미래 결측을 월 제외나 pandas 평균의 NaN 무시로 숨기지 않습니다.",
    "bug fix after the 2026-10-10 run; first run recorded as is: 사전등록이 매수 불가능 구성원 처리를 명시하지 않아 다른 실험의 진입 규칙을 적용합니다. 진입 시가 누락·0 이하, 거래량 누락·0 이하, 상한가 시가인 구성원은 진입 바스켓에서 제외하고 결정일 시총 비중을 재정규화합니다. 상한가는 base_price 우선, 없으면 직전 세션 원종가에 2015-06-15 전 15%, 이후 30%와 기존 0.5%p 호가 허용폭을 적용합니다. 진입 종가나 이후 가격은 매수 가능 판단에 사용하지 않습니다. 전원 제외 업종은 그달 수익률·비용 0인 현금으로 보유합니다. 실제 선택·무작위 대조군·교체 비용 바스켓에 동일하게 적용하고 업종-월별 제외 구성원과 현금 업종을 집계합니다. 이후 19세션 업종 지수 연쇄 규칙과 전체시장 벤치마크 진입 평가 규칙은 유지합니다.",
    "bug fix after the 2026-10-10 run; first run recorded as is: 같은 사유(사전등록의 매수 불가능 구성원 처리 미명시)로 전체시장 시가총액가중 벤치마크에도 동일한 진입 가능 규칙을 적용합니다. 진입 시가 누락·0 이하, 거래량 누락·0 이하, 상한가 시가인 구성원을 제외하고 나머지 결정일 시총 비중을 재정규화하며 벤치마크 월별 제외 수를 집계합니다. 위 이전 수정에서 유지했던 벤치마크 진입 규칙만 변경하며 이후 19세션 시장 지수 연쇄 규칙은 유지합니다.",
    "순위 동률은 평균 순위, 포트폴리오 점수 동률은 업종명 오름차순, 시총 동률은 종목코드 오름차순, 설계 t 동률은 사전등록 변형 표 순서로 해소합니다. 선택에는 2016~2024 순초과수익 t만 사용합니다.",
    "시장 국면은 결정일 시장 지수가 120세션 MA보다 높으면 above, 낮으면 below, 같으면 equal입니다. equal과 MA 결측도 별도로 보고하며 필터로 사용하지 않습니다.",
]


def _period(fields):
    periods = fields["data_periods"]
    return pd.Timestamp(periods["design"]["from"]), pd.Timestamp(periods["validation"]["to"])


def _load_day(day, path, mapping):
    """Bound parsing memory to one day and retain only required columns."""
    latest = {}
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
            values = {name: row.get(name) for name in
                      ("open", "close", "volume", "base_price", "market_cap", "trading_value", "stock_name", "market", "calendar_status")}
            values["ret_1d"] = None if row.get("change_rate_pct") is None else float(row["change_rate_pct"]) / 100
            values["industry"] = mapping.get(code, industry_map.UNCLASSIFIED)
            for name in ("stock_name", "market", "calendar_status", "industry"):
                if isinstance(values[name], str):
                    values[name] = sys.intern(values[name])
            latest[sys.intern(code)] = values
    frame = pd.DataFrame.from_dict(latest, orient="index").rename_axis("stock_code")
    # Keep wide membership/label matrices in consolidated NumPy blocks. Pandas
    # string extension columns otherwise dispatch comparisons once per stock.
    for name in ("stock_name", "market", "calendar_status", "industry"):
        frame[name] = frame[name].astype(object)
    for name in ("open", "close", "volume", "base_price", "market_cap", "trading_value", "ret_1d"):
        frame[name] = pd.to_numeric(frame[name], errors="raise").astype(float)
    frame["trade_date"] = day
    return frame.reset_index().set_index(["trade_date", "stock_code"]).sort_index()


def _inputs(data_dir, companies_path, fields, repo_root, prices=None):
    _, end = _period(fields)
    if prices is not None:
        common.guard_prices(prices, repo_root=repo_root)
        days = common._sessions(prices)
        if not len(days) or days[-1] > end or prices.index.has_duplicates:
            raise ValueError("HI001 requires unique prices in the design/validation read span")
        prices = prices.sort_index()
        return days, lambda: (prices.loc[[day]] for day in days), None
    mapping = industry_map.load_industry_map(companies_path)
    files = d001._price_files(data_dir, d001.PRICE_START, end, repo_root=repo_root)
    if not files:
        raise ValueError("no price observations in the guarded HI001 span")
    return pd.DatetimeIndex([day for day, _ in files]), lambda: (
        _load_day(day, path, mapping) for day, path in files), len(mapping)


def decision_schedule(days, fields):
    """December is counted as a holdout-boundary exclusion without opening it."""
    start, end = _period(fields)
    months = pd.period_range(start, end, freq="M")
    last = pd.Series(days, index=days.to_period("M")).groupby(level=0).max()
    decisions, skipped = [], []
    for month in months:
        if month >= pd.Period("2025-12", freq="M"):
            skipped.append({"month": str(month), "reason": "20_session_horizon_would_read_2026"})
        elif month not in last.index:
            skipped.append({"month": str(month), "reason": "no_price_sessions"})
        else:
            day = last.loc[month]
            if days.get_loc(day) + HORIZON >= len(days):
                skipped.append({"month": str(month), "reason": "incomplete_20_session_horizon"})
            else:
                decisions.append(day)
    return pd.DatetimeIndex(decisions), skipped


def member_universe(history, day, *, repo_root=PROJECT_ROOT):
    """Reuse the common filter, lifting only its D001-specific 1,000-won floor."""
    members = common.universe_filter(history, day, repo_root=repo_root)
    current = history.xs(day, level="trade_date")
    cheap = current.loc[np.isfinite(current.close) & current.close.gt(0) & current.close.lt(1000)]
    sessions = common._sessions(history)
    recent = sessions[sessions <= day][-20:]
    if not cheap.empty and len(recent) == 20:
        window = history.loc[history.index.get_level_values("trade_date").isin(recent)]
        values = window.trading_value.where(window.calendar_status.eq("verified")).unstack("stock_code").reindex(recent)
        values = values.where(np.isfinite(values))
        adv = values.mean().where(values.count() == 20)
        cheap = cheap.assign(avg_trading_value_20d=adv.reindex(cheap.index))
        keep = (cheap.index.str.endswith("0") & cheap.market.isin(("KOSPI", "KOSDAQ"))
                & cheap.calendar_status.eq("verified") & cheap.avg_trading_value_20d.ge(MIN_ADV))
        members = pd.concat([members, cheap.loc[keep]])
    return members.loc[~members.stock_name.str.contains(industry_index.SPAC_PATTERN, case=False, regex=True)].sort_index()


def _coverage(history, day, repo_root):
    members = member_universe(history, day, repo_root=repo_root)
    if members.market_cap.isna().any() or not np.isfinite(members.market_cap).all() or members.market_cap.le(0).any():
        raise ValueError("coverage requires positive observed market_cap")
    counts = members.groupby("industry").size()
    eligible = counts.loc[(counts >= MIN_MEMBERS) & ~counts.index.isin(EXCLUDED_INDUSTRIES)]
    capital = float(members.market_cap.sum())
    unknown = float(members.loc[members.industry.eq(industry_map.UNCLASSIFIED), "market_cap"].sum())
    return {"members": len(members), "market_cap": capital, "unmapped_market_cap": unknown,
            "mapped_cap_share": (capital - unknown) / capital if capital else None,
            "unmapped_cap_share": unknown / capital if capital else None,
            "industries_ge_10": {label: int(count) for label, count in eligible.items()}}


def dry_run(*, data_dir=PROJECT_ROOT / "data/market/krx_daily",
            companies_path=industry_map.DEFAULT_COMPANIES_PATH, repo_root=PROJECT_ROOT):
    fields = common.load_preregistration(EXPERIMENT_ID, repo_root=repo_root)
    days, factory, profiles = _inputs(data_dir, companies_path, fields, repo_root)
    dates, skipped = decision_schedule(days, fields)
    targets, buffer, coverage = set(dates), deque(maxlen=20), {}
    for frame in factory():
        buffer.append(frame)
        day = frame.index.get_level_values("trade_date")[0]
        if day in targets:
            coverage[str(day.to_period("M"))] = _coverage(pd.concat(buffer), day, repo_root)
    capital = sum(row["market_cap"] for row in coverage.values())
    unknown = sum(row["unmapped_market_cap"] for row in coverage.values())
    return {"experiment_id": EXPERIMENT_ID, "decision_start": "2016-01", "decision_end": "2025-11",
            "planned_months": len(pd.period_range(*_period(fields), freq="M")), "decision_months": 119,
            "design_months": 108, "validation_months": 11, "variants": list(VARIANTS),
            "read_start": pd.Timestamp(d001.PRICE_START).date().isoformat(), "read_end": _period(fields)[1].date().isoformat(),
            "data_dir": str(data_dir), "companies_path": str(companies_path), "profiles_found": profiles,
            "files": len(days), "first_file": days[0].date().isoformat(), "last_file": days[-1].date().isoformat(),
            "files_by_year": {str(year): int((days.year == year).sum()) for year in range(2015, 2026)},
            "covered_decision_months": len(coverage), "skipped_months": skipped,
            "months_with_three_industries": sum(len(row["industries_ge_10"]) >= 3 for row in coverage.values()),
            "zero_member_months": [month for month, row in coverage.items() if not row["members"]],
            "excluded_2026_count": sum(row["reason"] == "20_session_horizon_would_read_2026" for row in skipped),
            "mapped_cap_share": (capital - unknown) / capital if capital else None,
            "unmapped_cap_share": unknown / capital if capital else None, "coverage_by_month": coverage}


def _index_input(frame, dropped):
    dates = frame.index.get_level_values("trade_date")
    retirement = pd.Series(frame.index.get_level_values("stock_code").map(dropped), index=frame.index)
    keep = retirement.isna().to_numpy() | (dates <= pd.to_datetime(retirement).to_numpy())
    return frame.loc[keep & frame.market.isin(("KOSPI", "KOSDAQ")).to_numpy()].copy()


def stream_indices(factory):
    """Apply the index's permanent zero-and-drop policy across bounded blocks."""
    parts, tail, dropped = [], [], {}
    counts = {"members_dropped_no_row": 0, "raw_fallback": 0,
              "members_dropped_no_row_by_industry": {}, "raw_fallback_by_industry": {}}
    for _, batch in groupby(factory(), lambda frame: frame.index.get_level_values("trade_date")[0].to_period("M")):
        batch = list(batch)
        new_days = pd.DatetimeIndex([frame.index.get_level_values("trade_date")[0] for frame in batch])
        work = _index_input(pd.concat([*tail, *batch]), dropped)
        prepared, eligible = industry_index._prepare(work, industry_map.DEFAULT_COMPANIES_PATH, MIN_ADV)
        returns = prepared.ret_1d.unstack("stock_code")
        held = eligible.shift(1, fill_value=False)
        events = held & returns.isna()
        fallback = held & work.ret_1d.unstack("stock_code").isna() & returns.notna()
        labels = prepared.industry.unstack("stock_code").shift(1)
        for name, flags in (("members_dropped_no_row", events), ("raw_fallback", fallback)):
            flags = flags.loc[flags.index.isin(new_days)]
            for day, code in flags.stack().loc[lambda values: values].index:
                label = labels.loc[day, code]
                counts[name] += 1
                by_industry = counts[f"{name}_by_industry"]
                by_industry[label] = by_industry.get(label, 0) + 1
                if name == "members_dropped_no_row":
                    dropped[code] = day
        indices = industry_index.build_indices(work, min_trading_value_20d=MIN_ADV)
        parts.append(indices.loc[indices.index.get_level_values("trade_date").isin(new_days)])
        tail = [*tail, *batch][-20:]
    indices = pd.concat(parts).sort_index()
    for label in indices.index.get_level_values("industry").unique():
        for weighting in ("cap", "equal"):
            values = indices.xs(label, level="industry")[f"{weighting}_return"]
            indices.loc[(slice(None), label), f"{weighting}_index"] = industry_index._index_levels(values).to_numpy()
    indices.attrs.update(counts)
    return indices, dropped


def features_at_close(history, day, *, dropped=None, repo_root=PROJECT_ROOT, indices=None):
    """Use only decision-close data, optionally reusing the streamed cap returns."""
    common.guard_prices(history, repo_root=repo_root)
    history = history.loc[history.index.get_level_values("trade_date") <= day]
    work = _index_input(history, dropped or {})
    prepared, eligible = industry_index._prepare(work, industry_map.DEFAULT_COMPANIES_PATH, MIN_ADV)
    members = member_universe(work, day, repo_root=repo_root)
    members = members.loc[eligible.loc[day].reindex(members.index, fill_value=False)]
    index = industry_index.build_indices(work, min_trading_value_20d=MIN_ADV) if indices is None else indices.loc[
        indices.index.get_level_values("trade_date") <= day]
    market = index.xs(industry_index.ALL_MARKET, level="industry").cap_return.tail(60)
    market_growth = float((1 + market).prod()) if len(market) == 60 and market.notna().all() else np.nan
    market_return = market_growth - 1
    levels = industry_index._stock_levels(prepared)
    moving_average = levels.rolling(20, min_periods=20).mean().iloc[-1]
    returns = prepared.ret_1d.unstack("stock_code")
    rows = []
    for label, group in members.groupby("industry", sort=True):
        if label in EXCLUDED_INDUSTRIES or len(group) < MIN_MEMBERS:
            continue
        codes = group.index
        breadth = float((levels.iloc[-1][codes] > moving_average[codes]).mean()) if (
            levels.iloc[-1][codes].notna().all() and moving_average[codes].notna().all()) else np.nan
        top = group.market_cap.sort_values(ascending=False, kind="stable").head(5).index
        window = returns[top].tail(60)
        topcap = float(((1 + window).prod() - 1).median() - market_return) if (
            len(window) == 60 and window.notna().to_numpy().all()) else np.nan
        industry_returns = index.xs(label, level="industry").cap_return.reindex(returns.index).tail(60) if (
            label in index.index.get_level_values("industry")) else pd.Series(dtype=float)
        strength = float((1 + industry_returns).prod() - market_growth) if (
            len(industry_returns) == 60 and industry_returns.notna().all()) else np.nan
        rows.append({"industry": label, "rs60": strength, "breadth": breadth,
                     "topcap": topcap, "member_count": len(group)})
    result = pd.DataFrame(rows, columns=["industry", "rs60", "breadth", "topcap", "member_count"]).set_index("industry").astype(float)
    for name, columns in FEATURES.items():
        complete = result[list(columns)].where(np.isfinite(result[list(columns)])).dropna()
        result[name] = complete.rank(method="average", pct=True).mean(axis=1)
    return result, members


def holding_chain(indices, label, weighting, sessions):
    """An exhausted index basket retains its last value; live-member gaps do not."""
    if label in indices.index.get_level_values("industry"):
        original = indices.xs(label, level="industry")
        block = original.reindex(sessions)
        chain = block[f"{weighting}_return"].copy()
        # A missing industry row means the block contained no such industry.
        counts = block.member_count.where(sessions.isin(original.index), 0)
    else:
        chain = pd.Series(np.nan, index=sessions, dtype=float)
        counts = pd.Series(0, index=sessions)
    gaps = chain.isna() & counts.eq(0)
    return chain.mask(gaps, 0.0), int(gaps.sum())


def market_regime(indices, day):
    values = indices.xs(industry_index.ALL_MARKET, level="industry").cap_index.loc[:day].tail(120)
    if len(values) < 120 or values.isna().any():
        return "unmeasured"
    difference = values.iloc[-1] - values.mean()
    return "above" if difference > 0 else "below" if difference < 0 else "equal"


def entry_tradability(window, members, day, entry_day):
    """Check entry quotes against the preceding session's raw close only."""
    entry = window.xs(entry_day, level="trade_date").reindex(members.index)
    previous = window.xs(day, level="trade_date").close.reindex(members.index)
    base = entry.base_price.fillna(previous) if "base_price" in entry else previous
    limit_up = np.isfinite(base) & base.gt(0) & entry.open.ge(base * (1 + hf001.price_limit(entry_day) - 0.005))
    tradable = np.isfinite(entry.open) & entry.open.gt(0) & np.isfinite(entry.volume) & entry.volume.gt(0) & ~limit_up
    return tradable, limit_up


def holding_returns(window, indices, members, day, exit_day):
    """First-day intraday leg plus 19 adjusted index returns, including benchmark."""
    days = common._sessions(window)
    entry_day = days[days.get_loc(day) + 1]
    holding_days = days[(days > entry_day) & (days <= exit_day)]
    entry = window.xs(entry_day, level="trade_date").reindex(members.index)
    present = members.index.isin(window.xs(entry_day, level="trade_date").index)
    usable = np.isfinite(entry.open) & entry.open.gt(0) & np.isfinite(entry.close) & entry.close.gt(0)
    legs = pd.Series(0.0, index=members.index)
    legs.loc[present] = entry.loc[present, "close"] / entry.loc[present, "open"] - 1
    invalid = pd.Series(present & ~usable.to_numpy(), index=members.index)
    tradable, _ = entry_tradability(window, members, day, entry_day)
    groups = {label: group for label, group in members.groupby("industry")}
    groups[industry_index.ALL_MARKET] = members
    rows = {}
    for label, group in groups.items():
        basket_tradable = tradable.reindex(group.index)
        row = {"entry_excluded_members": int((~basket_tradable).sum()),
               "entry_cash_industry": bool(not basket_tradable.any())}
        group = group.loc[basket_tradable]
        for weighting in ("cap", "equal"):
            if group.empty:
                row[f"{weighting}_gross"] = 0.0
                row[f"{weighting}_index_gap_sessions"] = 0
                for multiplier in MULTIPLIERS:
                    row[f"{weighting}_cost_{multiplier}"] = 0.0
                continue
            chain, gap_count = holding_chain(indices, label, weighting, holding_days)
            weights = group.market_cap / group.market_cap.sum() if weighting == "cap" else pd.Series(1 / len(group), index=group.index)
            value = float((1 + np.dot(weights, legs.reindex(group.index))) * (1 + chain).prod() - 1) if (
                len(chain) == HORIZON - 1 and chain.notna().all() and not invalid.reindex(group.index).any()) else np.nan
            row[f"{weighting}_gross"] = value
            row[f"{weighting}_index_gap_sessions"] = gap_count
            for multiplier in MULTIPLIERS:
                costs = cost_model.round_trip_cost_vectorized(
                    group.close.to_numpy(), group.market.to_numpy(), entry_day.to_numpy(),
                    group.avg_trading_value_20d.to_numpy(), multiplier=multiplier)
                row[f"{weighting}_cost_{multiplier}"] = float(np.dot(weights, costs))
        rows[label] = row
    return pd.DataFrame.from_dict(rows, orient="index"), entry_day


def monthly_observations(days, factory, indices, dropped, dates, repo_root):
    """Retain at most 161 daily frames and small industry-level monthly tables."""
    source, buffer, position, rows = iter(factory()), deque(maxlen=HISTORY + HORIZON), -1, []
    for day in dates:
        target = days.get_loc(day) + HORIZON
        while position < target:
            buffer.append(next(source))
            position += 1
        window = pd.concat(buffer)
        history = window.loc[window.index.get_level_values("trade_date") <= day]
        features, members = features_at_close(history, day, dropped=dropped, repo_root=repo_root, indices=indices)
        returns, entry_day = holding_returns(window, indices, members, day, days[target])
        table = features.join(returns)
        benchmark = returns.loc[industry_index.ALL_MARKET, "cap_gross"]
        rows.append({"trade_date": day, "entry_date": entry_day, "exit_date": days[target], "table": table,
                     "benchmark": benchmark, "regime": market_regime(indices, day),
                     "benchmark_entry_excluded_members": int(returns.loc[industry_index.ALL_MARKET, "entry_excluded_members"]),
                     "coverage": _coverage(history, day, repo_root),
                     "holding_index_gap_sessions": returns.cap_index_gap_sessions.astype(int).to_dict()})
    return rows


def turnover_cost(selected, previous, costs, previous_costs):
    """Charge a full weighted round trip for each entering AND leaving industry."""
    current, old = set(selected), set(previous)
    return (sum(costs[label] for label in current - old) / len(current) if current else 0.0) + (
        sum(previous_costs[label] for label in old - current) / len(old) if old else 0.0)


def sample_control_portfolios(industries, rng, *, count=N_CONTROLS):
    labels = sorted(industries)
    if len(labels) < 3:
        raise ValueError("controls require at least three eligible industries")
    return [tuple(rng.choice(labels, size=3, replace=False)) for _ in range(count)]


def _portfolio_series(months, variant, top, weighting):
    previous, previous_costs, rows, skipped = (), {}, [], []
    for month in months:
        table = month["table"].loc[np.isfinite(month["table"][variant])]
        if len(table) < top:
            skipped.append({"month": str(month["trade_date"].to_period("M")), "reason": "insufficient_decision_time_features"})
            continue
        selected = tuple(table.sort_index().sort_values(variant, ascending=False, kind="stable").head(top).index)
        if not np.isfinite(month["benchmark"]):
            raise ValueError(f"{month['trade_date'].date()}: cannot value benchmark")
        gross = _selected_gross(table, selected, weighting, month["trade_date"])
        row = {"trade_date": month["trade_date"].date().isoformat(), "entry_date": month["entry_date"].date().isoformat(),
               "exit_date": month["exit_date"].date().isoformat(), "industries": list(selected),
               "gross": gross, "benchmark": float(month["benchmark"]), "regime": month["regime"],
               "benchmark_entry_excluded_members": month["benchmark_entry_excluded_members"],
               "holding_index_gap_sessions": _selected_gaps(table, selected, weighting),
               "entry_excluded_members_by_industry": table.loc[list(selected), "entry_excluded_members"].astype(int).to_dict(),
               "entry_cash_industries": list(table.loc[list(selected)].index[table.loc[list(selected), "entry_cash_industry"].astype(bool)])}
        for multiplier in MULTIPLIERS:
            costs = table[f"{weighting}_cost_{multiplier}"].to_dict()
            cost = turnover_cost(selected, previous, costs, previous_costs.get(str(multiplier), {}))
            row[f"cost_{multiplier}"] = cost
            row[f"net_{multiplier}"] = gross - cost
            row[f"excess_{multiplier}"] = gross - cost - month["benchmark"]
            previous_costs[str(multiplier)] = costs
        previous = selected
        rows.append(row)
    return rows, skipped


def _selected_gross(table, selected, weighting, day):
    values = table.loc[list(selected), f"{weighting}_gross"]
    if not np.isfinite(values).all():
        labels = ", ".join(values.index[~np.isfinite(values)])
        raise ValueError(f"{day.date()}: cannot value selected/drawn industries ({weighting}): {labels}")
    return float(values.mean())


def _selected_gaps(table, selected, weighting):
    return int(table.loc[list(selected), f"{weighting}_index_gap_sessions"].sum())


def _series(rows, column):
    return pd.Series([row[column] for row in rows], index=pd.DatetimeIndex([row["trade_date"] for row in rows]), dtype=float)


def _statistics(rows, column):
    values = _series(rows, column)
    return {**signal_eval._summary(values),
            "by_year": {str(year): signal_eval._summary(group) for year, group in values.groupby(values.index.year)}}


def select_variant(monthly_excess, fields):
    period = fields["data_periods"]["design"]
    start, end = pd.Timestamp(period["from"]), pd.Timestamp(period["to"])
    statistics = {name: signal_eval._summary(values.loc[(values.index >= start) & (values.index <= end)])
                  for name, values in monthly_excess.items()}
    valid = [name for name in VARIANTS if statistics[name]["t_stat"] is not None]
    return (max(valid, key=lambda name: statistics[name]["t_stat"]) if valid else None), statistics


def _controls(months, variant, actual_rows):
    measured = {pd.Timestamp(row["trade_date"]) for row in actual_rows}
    previous = [()] * N_CONTROLS
    previous_costs, totals, count = [{} for _ in range(N_CONTROLS)], np.zeros(N_CONTROLS), 0
    gaps = np.zeros(N_CONTROLS, dtype=int)
    excluded, cash = np.zeros(N_CONTROLS, dtype=int), np.zeros(N_CONTROLS, dtype=int)
    rng = np.random.default_rng(0)
    for month in months:
        if month["trade_date"] not in measured:
            continue
        table = month["table"].loc[np.isfinite(month["table"][variant])]
        costs = table["cap_cost_1.0"].to_dict()
        portfolios = sample_control_portfolios(table.index, rng)
        for number, selected in enumerate(portfolios):
            cost = turnover_cost(selected, previous[number], costs, previous_costs[number])
            totals[number] += _selected_gross(table, selected, "cap", month["trade_date"]) - cost - month["benchmark"]
            gaps[number] += _selected_gaps(table, selected, "cap")
            excluded[number] += int(table.loc[list(selected), "entry_excluded_members"].sum())
            cash[number] += int(table.loc[list(selected), "entry_cash_industry"].sum())
            previous[number], previous_costs[number] = selected, costs
        count += 1
    means = totals / count if count else np.array([])
    actual = signal_eval._summary(_series(actual_rows, "excess_1.0"))["mean"]
    return {**signal_eval._control_summary(means, actual), "monthly_observations": count,
            "mean_net_excess_by_control": means.tolist(), "seed": 0,
            "holding_index_gap_sessions_by_control": gaps.tolist(),
            "entry_excluded_members_by_control": excluded.tolist(),
            "entry_cash_industry_months_by_control": cash.tolist()}


def evaluate_variant(months, variant):
    rows, skipped = _portfolio_series(months, variant, 3, "cap")
    ic_rows = []
    for month in months:
        table = month["table"].loc[np.isfinite(month["table"][variant]) & np.isfinite(month["table"].cap_gross)]
        if len(table) < 3:
            continue
        scores, returns = signal_eval._rank_vector(table[variant]), signal_eval._rank_vector(table.cap_gross)
        if scores is not None and returns is not None:
            ic_rows.append({"trade_date": month["trade_date"].date().isoformat(), "ic": float(np.dot(scores, returns))})
    sensitivity = {str(multiplier): {"top_net": signal_eval._summary(_series(rows, f"net_{multiplier}"))["mean"],
                                    "top_net_excess": signal_eval._summary(_series(rows, f"excess_{multiplier}"))["mean"],
                                    "monthly_excess": _statistics(rows, f"excess_{multiplier}")}
                   for multiplier in MULTIPLIERS}
    secondary = {}
    for label, top, weighting in (("top_1", 1, "cap"), ("top_5", 5, "cap"), ("equal_weight", 3, "equal")):
        other, exclusions = _portfolio_series(months, variant, top, weighting)
        secondary[label] = {"monthly": other, "skipped_months": exclusions,
                            "cost_sensitivity": {str(multiplier): _statistics(other, f"excess_{multiplier}") for multiplier in MULTIPLIERS}}
    return {"monthly": rows, "monthly_excess": _statistics(rows, "excess_1.0"),
            "cost_sensitivity": sensitivity, "ic": {**_statistics(ic_rows, "ic"), "monthly": ic_rows},
            "random_control": _controls(months, variant, rows), "secondary": secondary,
            "entry_industry_months": [{"trade_date": month["trade_date"].date().isoformat(),
                                       "excluded_members_by_industry": month["table"].entry_excluded_members.astype(int).to_dict(),
                                       "cash_industries": list(month["table"].index[month["table"].entry_cash_industry.astype(bool)])}
                                      for month in months],
            "regime_split": {regime: _statistics([row for row in rows if row["regime"] == regime], "excess_1.0")
                             for regime in ("above", "below", "equal", "unmeasured")}, "skipped_months": skipped}


def _judgement_inputs(result, design, fields, trial_count):
    period = fields["data_periods"]["validation"]
    values = _series(result["monthly"], "excess_1.0")
    validation = signal_eval._summary(values.loc[values.index.to_series().between(
        pd.Timestamp(period["from"]), pd.Timestamp(period["to"]))])["mean"]
    same_sign = bool(np.sign(validation) == np.sign(design["mean"])) if validation is not None and design["mean"] is not None else None
    return {"observations": result["monthly_excess"]["count"], "t_stat": result["monthly_excess"]["t_stat"],
            NET_METRIC: result["cost_sensitivity"]["1.0"]["top_net_excess"],
            NET_AT_COST_1_5_METRIC: result["cost_sensitivity"]["1.5"]["top_net_excess"],
            "random_control_share": result["random_control"]["share_of_controls"],
            "validation_monthly_net_excess": validation, "validation_year_same_sign": same_sign,
            "trial_count_after_run": trial_count}


def _write_results(payload, repo_root):
    """Reuse exclusive publication; give HI001 its portfolio-primary summary."""
    json_path, summary_path = common.write_results(EXPERIMENT_ID, payload, repo_root=repo_root)
    lines = [f"# 실험 결과: {EXPERIMENT_ID}", "", f"- 선택 변형: {payload['selected_variant']}",
             f"- 최종 판정: {payload['verdict']}", "- 판정 사유: " + "; ".join(payload["reasons"]), "",
             "주 지표는 월별 상위 3개 업종 순초과수익이며 관측 단위는 월입니다. 선택에는 설계 기간만 사용합니다.", "",
             "| 변형 | 월 수 | 월별 순초과수익 평균 | t | 비용 1.5배 순초과수익 | 비용 2배 순초과수익 | 실제 이상 대조군 비율 | 보조 순위 IC 평균 |",
             "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for name, result in payload["variants"].items():
        primary, sensitivity = result["monthly_excess"], result["cost_sensitivity"]
        lines.append(f"| {name} | {primary['count']} | {primary['mean']} | {primary['t_stat']} | "
                     f"{sensitivity['1.5']['top_net_excess']} | {sensitivity['2.0']['top_net_excess']} | "
                     f"{result['random_control']['share_of_controls']} | {result['ic']['mean']} |")
    lines += ["", "## interpretations (판정 기준 해석)", "", INTERPRETATIONS["note"], "",
              "| 기준 | 사용 지표 | 정의 |", "| --- | --- | --- |"]
    for name, item in INTERPRETATIONS["criteria"].items():
        lines.append(f"| {name} | {item['metric']} | {item['definition']} |")
    lines += ["", "가정과 제한:", "", *[f"- {note}" for note in payload["assumptions"]], "",
              "월별 수익률·교체 비용·IC·대조군·보조 포트폴리오·연도별·국면별·제외 사유·분류 비중은 같은 이름의 JSON에 기록했습니다.",
              f"실행 시간: {payload['performance']['runtime_seconds']:.3f}초. "
              f"최대 RSS: {payload['performance']['peak_memory_mib']:.1f} MiB."]
    summary_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, summary_path


def run_experiment(*, data_dir=PROJECT_ROOT / "data/market/krx_daily",
                   companies_path=industry_map.DEFAULT_COMPANIES_PATH, repo_root=PROJECT_ROOT, prices=None):
    started = time.perf_counter()
    fields = common.load_preregistration(EXPERIMENT_ID, repo_root=repo_root)
    if fields["variants_planned"] != len(VARIANTS) or fields["uses_holdout"]:
        raise ValueError("HI001 requires its three preregistered variants and no holdout")
    days, factory, profiles = _inputs(data_dir, companies_path, fields, repo_root, prices)
    dates, skipped = decision_schedule(days, fields)
    indices, dropped = stream_indices(factory)
    months = monthly_observations(days, factory, indices, dropped, dates, repo_root)
    primary = {name: _portfolio_series(months, name, 3, "cap")[0] for name in VARIANTS}
    selected, design = select_variant({name: _series(rows, "excess_1.0") for name, rows in primary.items()}, fields)
    total_trials = experiment_registry.trial_count(EXPERIMENT_ID, repo_root=repo_root) + len(VARIANTS) + 1
    variants = {}
    pending = []
    for name in VARIANTS:
        result = evaluate_variant(months, name)
        inputs = _judgement_inputs(result, design[name], fields, total_trials)
        verdict, reasons = common.judge(fields, inputs, net_metric=NET_METRIC,
                                        net_at_cost_1_5_metric=NET_AT_COST_1_5_METRIC, repo_root=repo_root)
        reasons = [reason.replace("validation-year IC sign", "validation-year monthly net excess sign") for reason in reasons]
        result.update(design_monthly_excess=design[name], judgement_inputs=inputs, verdict=verdict, reasons=reasons,
                      skipped_month_count=len(result["skipped_months"]) + len(skipped),
                      unadjusted_fallback=indices.attrs["raw_fallback"], delisting_exclusions=indices.attrs["members_dropped_no_row"])
        variants[name] = result
        pending.append((name, inputs, verdict,
                        "selected variant" if name == selected else "unselected diagnostic; design-only selection"))
    verdict, reasons = (variants[selected]["verdict"], variants[selected]["reasons"]) if selected else (
        "insufficient", ["no variant has a defined design-period monthly net excess t statistic"])
    pending.append(("final", {"selected_variant": selected, **(variants[selected]["judgement_inputs"] if selected else {})},
                    verdict, "HI001 final verdict"))
    coverage = {str(month["trade_date"].to_period("M")): month["coverage"] for month in months}
    capital = sum(row["market_cap"] for row in coverage.values())
    unknown = sum(row["unmapped_market_cap"] for row in coverage.values())
    payload = {"experiment_id": EXPERIMENT_ID, "selected_variant": selected, "verdict": verdict, "reasons": reasons,
               "variants": variants, "common_skipped_months": skipped, "interpretations": INTERPRETATIONS,
               "coverage": {"profiles_found": profiles, "sessions": len(days), "coverage_by_month": coverage,
                            "unmapped_cap_share": unknown / capital if capital else None,
                            "excluded_2026_count": sum(row["reason"] == "20_session_horizon_would_read_2026" for row in skipped)},
               "counts": {**indices.attrs, "holding_index_gap_sessions_by_month": {
                   str(month["trade_date"].to_period("M")): month["holding_index_gap_sessions"] for month in months}},
               "assumptions": ASSUMPTIONS,
               "performance": {"runtime_seconds": time.perf_counter() - started,
                               "peak_memory_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024}}
    # Validate everything BEFORE any registry row is written: judgement inputs must be
    # finite; other non-finite report values are stored as null and listed.
    for _, inputs, _, _ in pending:
        json.dumps(inputs, allow_nan=False)
    nonfinite = []

    def _finite(value, path):
        if isinstance(value, dict):
            return {key: _finite(item, f"{path}.{key}") for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [_finite(item, f"{path}[{index}]") for index, item in enumerate(value)]
        if isinstance(value, float) and not math.isfinite(value):
            nonfinite.append(path)
            return None
        return value

    payload = _finite(payload, "$")
    payload["nonfinite_report_fields"] = nonfinite
    json.dumps(payload, ensure_ascii=False, allow_nan=False, default=str)
    for name, inputs, verdict_row, note in pending:
        experiment_registry.record_trial(EXPERIMENT_ID, name, inputs, verdict_row, repo_root=repo_root, note=note)
    _write_results(payload, repo_root)
    return payload


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="print plan and coverage (default)")
    mode.add_argument("--execute", action="store_true", help="run and append all three trials and final verdict")
    parser.add_argument("--data-dir", type=Path, default=PROJECT_ROOT / "data/market/krx_daily")
    parser.add_argument("--companies-path", type=Path, default=industry_map.DEFAULT_COMPANIES_PATH)
    args = parser.parse_args(argv)
    if args.execute:
        result = run_experiment(data_dir=args.data_dir, companies_path=args.companies_path)
        print(f"{EXPERIMENT_ID}: {result['verdict']}; selected={result['selected_variant']}")
        return 0
    plan = dry_run(data_dir=args.data_dir, companies_path=args.companies_path)
    print(f"{EXPERIMENT_ID} dry-run")
    print(f"Decision months: {plan['decision_start']}..{plan['decision_end']} ({plan['decision_months']}; design {plan['design_months']}; validation {plan['validation_months']})")
    print("Variants: " + ", ".join(plan["variants"]))
    print(f"Data directory: {plan['data_dir']}")
    print(f"Guarded read span: {plan['read_start']}..{plan['read_end']}")
    print(f"Price files: {plan['files']}; first={plan['first_file']}; last={plan['last_file']}")
    print("Files by year: " + ", ".join(f"{year}={count}" for year, count in plan["files_by_year"].items()))
    print(f"Profiles found (stock identities): {plan['profiles_found']}")
    print(f"Covered decision months (stored sessions): {plan['covered_decision_months']}/{plan['decision_months']}")
    print(f"Months with >=3 eligible industries: {plan['months_with_three_industries']}/{plan['decision_months']}")
    print("Zero-member months: " + (", ".join(plan["zero_member_months"]) or "none"))
    print(f"Excluded at 2026 boundary: {plan['excluded_2026_count']}")
    def share(value):
        return f"{value:.2%}" if value is not None else "unmeasured"
    print(f"Monthly-cap-weighted classification coverage: mapped={share(plan['mapped_cap_share'])}; 미분류={share(plan['unmapped_cap_share'])}")
    for month, coverage in plan["coverage_by_month"].items():
        labels = ", ".join(f"{label}={count}" for label, count in coverage["industries_ge_10"].items()) or "none"
        print(f"{month}: members={coverage['members']}; mapped={share(coverage['mapped_cap_share'])}; 미분류={share(coverage['unmapped_cap_share'])}; industries >=10 ({len(coverage['industries_ge_10'])}): {labels}")
    for row in plan["skipped_months"]:
        print(f"Excluded {row['month']}: {row['reason']}")
    print("Classification is a current snapshot; collection may still be running.")
    print("Coverage checks decision-day membership; execute also checks features, permanent member drops and holding windows.")
    print("No portfolios evaluated; no registry rows or result files written.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
