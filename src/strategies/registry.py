"""M5 candidate instances. Registration conveys no performance validation.

B1 is event driven: score=1 denotes membership in event_evidence's categories,
not an ordering between categories. Evaluate the receipt-level frame with
event_study(feature='category'), never a cross-sectional IC/ranking test. B2's
receipt-level feature is revenue_ratio_pct, evaluated by that event study.
For the common score/generate interface, B1/B2 select the latest usable event
per stock in a trailing 30-calendar-day window (not the maximum past feature).
Ties break by stock_code; weights remain unset for later deterministic sizing.
Corrections retain their own event IDs and become usable only at their own time.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import _runner_module
from .base import StrategySpec, _targets
from .data import PointInTimeData
from .factors import MomentumLowVolStrategy


class DisclosureCategoryStrategy:
    spec = StrategySpec(
        strategy_id="b1_disclosure_category", sleeve="B",
        thesis="Disclosure categories predict 1-20 session abnormal returns; event-study hypothesis",
        counterparty="Investors reacting slowly to disclosures",
        universe="Disclosed stocks, membership in existing event_evidence categories",
        inputs=("DART category; first_seen_at or next-session 09:00 KST for date-only disclosures",),
        decision_time="Event availability; execution strictly later", horizon_sessions=20, stage="candidate",
    )

    def score(self, as_of, data: PointInTimeData) -> pd.DataFrame:
        events = data.events_as_of(as_of, 30)
        if events.empty:
            return pd.DataFrame({"stock_code": pd.Series(dtype=str), "score": pd.Series(dtype=float)})
        categories = {category for category, _ in _runner_module("event_evidence")._PATTERNS}
        latest = events.drop_duplicates("stock_code", keep="last")
        result = latest[["stock_code"]].copy()
        result["score"] = latest.category.isin(categories).astype(float)
        return result.sort_values("stock_code", kind="stable").reset_index(drop=True)

    def generate(self, as_of, data: PointInTimeData, top_n: int):
        scores = self.score(as_of, data)
        return _targets(self.spec, as_of, scores.loc[scores.score > 0], top_n)


class ContractRatioStrategy:
    spec = StrategySpec(
        strategy_id="b2_contract_ratio", sleeve="B",
        thesis="Contract amount / reported revenue predicts 1-20 session abnormal returns",
        counterparty="Investors reacting slowly to contract size",
        universe="Stocks with a usable contract disclosure and a parsed revenue_ratio_pct",
        inputs=("DART contract revenue_ratio_pct; first_seen_at or next-session 09:00 KST",),
        decision_time="Event availability; execution strictly later", horizon_sessions=20, stage="candidate",
    )

    def score(self, as_of, data: PointInTimeData) -> pd.DataFrame:
        events = data.events_as_of(as_of, 30)
        if events.empty:
            return pd.DataFrame({"stock_code": pd.Series(dtype=str), "score": pd.Series(dtype=float)})
        latest = events.loc[events.category == "contract"].drop_duplicates("stock_code", keep="last")
        result = latest[["stock_code", "revenue_ratio_pct"]].rename(columns={"revenue_ratio_pct": "score"})
        # A newer unparsed correction does not resurrect the older known ratio.
        return result.loc[np.isfinite(result.score)].sort_values("stock_code", kind="stable").reset_index(drop=True)

    def generate(self, as_of, data: PointInTimeData, top_n: int):
        return _targets(self.spec, as_of, self.score(as_of, data), top_n)


STRATEGIES = {
    "d_momentum_lowvol": MomentumLowVolStrategy(),
    "b1_disclosure_category": DisclosureCategoryStrategy(),
    "b2_contract_ratio": ContractRatioStrategy(),
}
