"""LH001's fixed, offline-data experiment system (no orders or collectors)."""
from pathlib import Path
import os

from backtesting import holdout
from backtesting.experiment_registry import PROJECT_ROOT
from .config import ExperimentConfig, LH001, LH002

EXPERIMENT_ID = LH001.experiment_id
VARIANTS = ("b_num", "b_text", "c_num", "c_text", "hc002_holdout")
MODEL = "gpt-6-luna"
CODEX_VERSION = "codex-cli 0.160.0"
PROMPT_VERSION = "v1"
FINAL_END = "2026-10-30"
PROMPT_DIR = LH001.prompt_dir
ASSUMPTIONS = [
    "The dedicated probe reader may read 2026 market-level facts before opening the holdout. This is contamination measurement, not strategy evaluation; it does not call guard_period or consume a holdout claim. Reviewed and accepted by the coordinator on 2026-10-05; the read is limited to KOSPI month-end levels, sector index returns and top-30 stock month-end closes.",
    "B_num random controls use the entire HC002 universe capped at the top 300 by decision-close 20-session ADV, irrespective of the three selected industries. C_num uses the displayed capped A pool; text uses its fixed 30. The ambiguous B universe/same-300 wording remains a coordinator decision.",
    "All three final repeats must be valid; otherwise that arm has no selection. Exact count/mean-rank ties use the displayed ID in lexical order. Fewer than 30 numeric candidates, or fewer than 40 C candidates for disjoint ranked/avoid lists, produce no selection; candidate counts are never shortened.",
    "Date-only DART listings cannot establish publication before the decision-day close. Conservatively exclude the decision session: use the prior 60 completed sessions and calendar days between their first/last dates. Partial listing coverage makes counts unavailable, never presumed zeros; stored titles carry coverage.",
    "Input returns use complete ret_1d chains; missing windows remain unavailable. Volatility is daily sample standard deviation (not annualised); a 52-week window is 250 sessions and uses adjusted intraday highs. Breadth uses the HC002 eligible universe. Industry RS subtracts matching cap/EW all-market index returns.",
    "Stock-table trading value is decision-day turnover (percentile in anonymous mode). The 20-session ADV is retained separately for eligibility, candidate caps and execution costs, not substituted for the displayed daily value.",
    "Industry financial growth sums unique corporate current/prior pairs in the industry's latest known quarter; every eligible corporate member must have that quarter and a complete pair, otherwise growth is unavailable. Four-quarter trends show YoY and revenue/OI scaled by latest absolute revenue, never monetary amounts in anonymous mode.",
    "Current stored price revisions and company classifications are used, as in HC002; historical collection timestamps are not publication vintages. This does not reconstruct historical industry classifications. Financial receipt dates strictly precede the decision date.",
    "Probe stock returns are ratios of the two month-end closes, not strategy returns; stock splits can affect these facts. The two triples are disjoint draws from the prior month-end top-30 common stocks. Sector questions use KOSPI sector index rows, excluding index names beginning with 코스피/코스닥.",
    "Missing probe facts or invalid answers block clean-window approval; the probe needs all 21 months and four answers each. Zero estimated variance gives an undefined t, never an infinite passing t. NW uses Bartlett weights, lag 4, and no small-sample correction.",
    "Conservatively enforce both minimum_observations fields: 20 paired weekly decisions and 300 executable selected stock/decision observations. The t unit remains weekly; stock rows are not independent t observations. Screening uses the same lag-4 NW estimator. Forward dates are exit-price-only, so decisions end in September even when a later weekly exit could fit October 30.",
    "A quota-interrupted final run resumes only its hash-checked frozen snapshot prepared in the one HoldoutSession, with no protected source rereads or second holdout claim. If interrupted before snapshot publication, the claim is consumed and the run cannot be restarted.",
    "Smoke runs consume real-call budget if executed, but never append registry rows or overwrite official stage state. A final smoke run still consumes the one holdout claim. Text loaders are local only; no report is fetched by this experiment.",
    "Prompts are sent through stdin (`codex exec ... -`) because Linux's single-argument limit rejects long tables and business texts; nothing is truncated. A synthetic C_num format check (50 fake stocks) on 2026-10-05 returned schema-valid output with no tool events.",
    "A bounded stock-presence-date scan through the allowed stage price end preserves HC002's no-later-price exclusion versus temporary missing-exit last-close valuation. No closes from this scan enter LLM inputs; screening never scans 2026 files.",
]


def experiment_assumptions(config=LH001):
    if config == LH001:
        return ASSUMPTIONS
    notes = list(ASSUMPTIONS)
    notes[1] = "LH002 section 2 fixes B_num random controls to the entire HC002 universe capped at the top 300 by decision-close 20-session ADV, irrespective of selected industries. C_num uses the displayed capped A pool; text uses its fixed 30."
    notes[8] = "Probe stock returns are ratios of the two month-end closes, not strategy returns; stock splits can affect these facts. Five disjoint triples are drawn from the prior month-end top-30 common stocks. Two disjoint sector triples use KOSPI sector index rows, excluding names beginning with 코스피/코스닥, with pairwise separation of at least 3 percentage points."
    notes[9] = "LH002 probe v2 needs all 27 months and eight valid answers each. The 2024-01..06 positive control must pass a one-sided exact binomial test against 1/3 (p<0.05). A contaminated month has at least seven correct answers. The earliest January-May 2026 start must have no contaminated month through September and pooled p>=0.05. The 2025 months are boundary information only. Zero estimated variance gives an undefined t, never an infinite passing t; NW remains Bartlett lag 4 with no small-sample correction."
    return notes


def workspace(data_dir, config=LH001):
    return Path(data_dir) / config.workspace_subdir


def guard_data(start, end, *, repo_root=PROJECT_ROOT, config=LH001):
    """Never let a helper implicitly claim protected data outside a session."""
    import pandas as pd
    first, last = pd.Timestamp(start).date(), pd.Timestamp(end).date()
    if first <= holdout.HOLDOUT_END and last >= holdout.HOLDOUT_START:
        root = Path(repo_root).resolve()
        if not any(s._open and s._pid == os.getpid() and s.experiment_id == config.experiment_id and s.repo_root == root
                   and s.ledger_path == (root / holdout.LEDGER_PATH).resolve()
                   for s in holdout._SESSIONS.get()):
            raise ValueError(f"{config.name} protected source reads require an open HoldoutSession")
        holdout.guard_period(first, last, experiment_id=config.experiment_id, repo_root=repo_root)
    else:
        holdout.guard_period(first, last, repo_root=repo_root)
