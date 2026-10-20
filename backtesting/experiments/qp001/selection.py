"""Decision-date grouping only; no prices after t or portfolio accounting."""
import math

import numpy as np
import pandas as pd


def group_targets(common):
    """Market C_t inverted-CDF thirds, then phase-2 and code-stable ROA half."""
    groups, selected, boundaries = {}, {}, {}
    for market in sorted(common.market.unique()):
        frame = common.loc[common.market.eq(market)]
        caps = frame.market_cap.to_numpy(dtype=float)
        if not np.isfinite(caps).all() or (caps <= 0).any():
            raise ValueError("C_t requires finite positive decision-date market_cap")
        edges = np.quantile(caps, [1 / 3, 2 / 3], method="inverted_cdf")
        boundaries[market] = edges.tolist()
        for code, row in frame.sort_index().iterrows():
            key = (market, bool(pd.notna(row.phase) and row.phase == 2), int(np.searchsorted(edges, row.market_cap, side="left")))
            groups.setdefault(key, set()).add(code)
    groups = dict(sorted(groups.items()))
    for key, codes in groups.items():
        ranked = common.loc[sorted(codes)].sort_values("roa", ascending=False, kind="stable")
        selected[key] = set(ranked.index[:math.ceil(len(codes) / 2)])
    weights = {key: len(codes) / len(common) for key, codes in groups.items()}
    return groups, selected, weights, boundaries

