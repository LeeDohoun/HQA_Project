"""Compare merged shared accounting defaults with pre-merge code on fake prices."""
import ast
import copy
import json
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(root))
sys.path.insert(0, str(root / 'tests'))
from backtesting.experiments import av001, hc003
from test_experiments_hc003 import prices_for, selection
import pandas as pd

source = subprocess.run(['git', 'show', '0b978a9:backtesting/experiments/hc003.py'],
                        cwd=root, check=True, capture_output=True, text=True).stdout
nodes = [node for node in ast.parse(source).body if isinstance(node, (ast.ClassDef, ast.FunctionDef))
         and node.name in {'PriceBook', 'rebalance', 'simulate'}]
legacy = dict(vars(hc003))
exec(compile(ast.Module(body=nodes, type_ignores=[]), '<pre-merge shared defaults>', 'exec'), legacy)
days = hc003.session_calendar()
days = days[(days >= '2024-11-01') & (days <= '2025-06-30')]
prices = prices_for(days)
prices.loc[(pd.Timestamp('2025-01-02'), '000010'), ['open', 'close', 'ret_1d']] = [12000, 12000, .2]
plan = []
for month in pd.period_range('2025-01', '2025-06', freq='M'):
    current = days[days.to_period('M') == month]
    next_days = days[days.to_period('M') == month + 1]
    end = next_days[0] if len(next_days) else current[-1]
    prior = days[days < current[0]][-1]
    plan.append(selection(str(prior.date()), str(current[0].date()), str(end.date()),
                          {'000010': 2, '000020': 4}, month=str(month),
                          kind='open' if len(next_days) else 'close'))
rebalances = sorted({pd.Timestamp(row[k]) for row in plan for k in ('trade_date', 'end_date')})
windows = [prices.loc[prices.index.get_level_values('trade_date').to_period('Q') == quarter]
           for quarter in days.to_period('Q').unique()]
books = []
# Synthetic protected-date prices are never used; every day is in 2024/2025.
for cls in (legacy['PriceBook'], hc003.PriceBook):
    books.append(cls.from_windows(iter(copy.deepcopy(windows)), days, rebalances, days[-1],
                                 experiment_id=av001.EXPERIMENT_ID, repo_root=root))
for field in ('openings', 'close_snapshots', 'closings', 'zero_from', 'counts'):
    assert repr(getattr(books[0], field)) == repr(getattr(books[1], field)), field
kwargs = dict(experiment_id=av001.EXPERIMENT_ID, initial_state={'cash': 0, 'quantities': {'000010': 1}},
              initial_value=1)
old = legacy['simulate'](books[0], plan, 't_hold', **kwargs)
new = hc003.simulate(books[1], plan, 't_hold', **kwargs)
assert json.dumps(old, sort_keys=True) == json.dumps(new, sort_keys=True)
print(json.dumps({'passed': True, 'synthetic_months': len(plan), 'protected_data_read': False,
                  'book_snapshots_counts_equal': True, 'simulation_equal': True,
                  'old_code': '0b978a9', 'scope': 'PriceBook.from_windows, rebalance, simulate defaults'}))
