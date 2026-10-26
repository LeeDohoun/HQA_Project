"""Run `backtesting run` with entry at the next session's open instead of the signal-day close.

The engine decides on the as-of close and enters at that same close. This wrapper keeps the
decision unchanged and moves the fill to the open of the first tradable session after it;
stops and the trailing high-water mark start from that open, on that session's bar.
The engine computes realized = exit_price / signal_close - 1, so the returned exit price is
rescaled by signal_close / entry_open to make that ratio equal exit / entry_open - 1.
Repository code is not modified.
"""
import sys

import backtesting.leader_backtest as lb

_original = lb._simulate_exit


def _next_open_exit(*, df, entry_pos, planned_exit_pos, entry_price, exit_config, truncated=False):
    fill_pos = None
    for pos in range(entry_pos + 1, planned_exit_pos + 1):
        if float(df.iloc[pos]["Volume"] or 0.0) > 0:
            fill_pos = pos
            break
    if fill_pos is None:
        # No tradable session before the planned exit: the position is never opened.
        return {"exit_pos": entry_pos, "exit_price": float(entry_price), "exit_reason": "no_next_open_fill"}
    open_price = float(df.iloc[fill_pos]["Open"])
    if open_price <= 0:
        return {"exit_pos": entry_pos, "exit_price": float(entry_price), "exit_reason": "no_next_open_fill"}
    result = _original(
        df=df,
        entry_pos=fill_pos - 1,  # the loop starts at fill_pos, so the entry day's low can stop out
        planned_exit_pos=planned_exit_pos,
        entry_price=open_price,
        exit_config=exit_config,
        truncated=truncated,
    )
    result = dict(result)
    result["exit_price"] = float(result["exit_price"]) * float(entry_price) / open_price
    return result


lb._simulate_exit = _next_open_exit

if __name__ == "__main__":
    raise SystemExit(lb.main(sys.argv[1:]))
