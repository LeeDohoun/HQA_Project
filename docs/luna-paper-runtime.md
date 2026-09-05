# Luna PAPER Runtime

## Operating Boundary

The runtime shares company-level Analyst, Quant and Chartist results, then makes
one account-specific RiskManager call. It screens up to 100 price-ranked candidates,
analyzes the top 20 plus every holding, and reviews at most five new candidates per
account. Entry sizing and order safety remain deterministic backend operations.

All production roles use `gpt-5.6-luna`. There is no automatic provider switch,
LLM retry, debate loop, fine-tuning or REAL order path. Explicit `ollama` and `mock`
settings remain development options; mock outputs are not PAPER acceptance data.

The 120-second analysis and 30-second monitoring p95 values are acceptance targets,
not measured API guarantees. The 30-second target covers quote acquisition,
condition evaluation and order request, not exchange fill time.

## Configuration

Use `.env.example` as the configuration reference. Supply credentials through the
local environment, never through source files, prompts or audit records:

- `OPENAI_API_KEY`: required by the OpenAI model factory.
- `HQA_INTERNAL_TOKEN`: identical nonblank secret in AI, backend and monitor.
- `BACKEND_INTERNAL_BASE_URL`: backend origin, normally `http://localhost:8000`.
- `AI_SERVER_URL`: AI origin, normally `http://localhost:8001`.
- `BACKEND_SIGNAL_URL`: required for account-specific plan publication; missing
  configuration fails explicitly. Account-free manual previews do not publish.
  After PAPER validation, use `http://localhost:8000/api/v1/internal/trading/signals`
  for host processes or `http://backend:8000/api/v1/internal/trading/signals` in Compose.
- Register each user's PAPER KIS account through the existing backend credential
  workflow. The AI service never receives broker credentials. REAL keys are rejected.

Run exactly one AI worker. Its concurrency ceiling is eight; request/token admission
defaults to 120 RPM and 200,000 TPM. Each generation also makes a token-count request
to reserve the full JSON-schema input cost. Configure limits from the actual OpenAI
project quota; conservative defaults can reject or delay a full cold-start burst.

Experts use low reasoning, summary uses none, and RiskManager uses medium. Output
ceilings include reasoning: experts 1,200, summary 800, RiskManager 12,000 tokens.
Truncated structured output fails validation; holdings are never silently omitted.

The budget uses UTC calendar months: ordinary analysis stops at the $90 operating
target; holding-priority work can use the remaining amount up to $100. Reservations
include worst-case input cache-write pricing and the complete output ceiling.
Unknown or interrupted requests keep their reservation, including across restarts
and month boundaries. Reconcile from provider usage before releasing uncertainty;
do not delete the ledger to resume work. Taxes and other applications are outside
this internal limit. A dedicated OpenAI project is recommended for accounting.

Persist `HQA_LLM_BUDGET_PATH` and `HQA_PAPER_AUDIT_PATH`. Audit records contain private
account context and exact supplied evidence, so keep the data volume access-limited.
Redis eviction cannot reset the budget or erase the prospective audit ledger.

## Data Requirements

The existing ingestion pipeline must provide theme targets, completed daily OHLCV,
canonical DART/news and financial snapshots. Initial price screening requires 151
daily records. Missing or stale prices, evidence and fundamentals produce explicit
errors rather than demo data or neutral scores.

Financial snapshots now retain collection time, DART receipt, CFS/OFS division,
currency and content version. Recollect old undated financial files before using
them for entry decisions. `as_of` in old financial files is a fiscal statement date,
not a publication date. The major-accounts API does not provide an exact publication
timestamp: it remains unknown, and the actual collection time is the conservative
earliest usable time. Corrected versions are retained instead of replacing history.
Ratios are computed from source amounts; KRW unit conversion is tested explicitly.

The live local-data adapter must not be used to reconstruct historical predictions
from today's unversioned universe or prices. Historical evaluation requires archived
point-in-time inputs injected into the same analysis service. Model pretraining
memory remains a separate limitation even with correctly filtered data.

## Execution and Recovery

Apply the backend's V9 migration before publishing v2 plans. It extends existing
signals and executions, persists plan receipts and daily account baselines, and adds
locking and identity constraints. Back up the database before migration.

Migration does not guess the broker binding for legacy plans. Reconcile old orders
and reissue validated plans against the actual PAPER account before enabling the
monitor. Duplicate active plans must be resolved before the unique index can apply.
One actual PAPER brokerage account is bound to one user; changing encryption keys
also requires explicit review of persisted account fingerprints.
Sharing one PAPER app key across multiple accounts or users is rejected because it
would invalidate per-account capacity estimates. Do not reset or fund the PAPER
account during an observation run: daily-loss baselines do not adjust for cash flows.

Versioned conditions are OR between groups and AND within each group's `all` list.
ENTRY buys; EXIT sells; REDUCE sells a deterministic fraction. INVALIDATION cancels
an unentered plan or exits managed holdings. An explicit held HOLD plan adopts or
updates protection without buying again. Backend account snapshots supply the
authoritative 20% concentration limit and entry eligibility.

Entry validity lasts at most 15 minutes from analysis. Existing positions remain
managed after that time. Duplicate and stale plan/trigger requests must not change
newer protection. Entry-only loss and price-drift gates do not block protective sells.

An accepted order is not a fill. The reconciliation worker queries cumulative fills,
protects partial fills, confirms cancellations, and preserves reservations for
uncertain submissions. An UNKNOWN order without a confirmed broker ID requires
operator investigation; never resubmit it by guessing the previous result.

REST monitoring uses per-account capacity admission. The initial conservative
configuration allows ten unique monitored symbols per account (one request/second,
0.5 requests/second reserved for account/order operations, 20-second quote cycle).
Existing holdings are never dropped to meet that capacity. Overload blocks new
entries and is reported as an unmet monitoring target. Verify actual KIS limits
before changing this configuration; no REAL or paid shared quote feed is assumed.
The relevant backend Spring properties and initial values are:

```properties
hqa.kis-paper-requests-per-second=1
hqa.paper-account-reserved-requests-per-second=0.5
hqa.paper-lifecycle-poll-ms=20000
hqa.paper-reconciliation-poll-ms=20000
```

The monitor also enumerates enabled accounts with no active plans, quotes every
observed holding, and records `uncovered_holdings` and `missing_protection` errors.
It never invents a plan or order to conceal missing protection.

Start the AI service after installing requirements:

Runtime jobs, task lookup, chat and query suggestions require the internal token;
the backend forwards it. Do not expose this token in browser configuration.

```bash
venv/bin/python -m uvicorn ai_server.app:app --host 127.0.0.1 --port 8001 --workers 1
```

Run the independent monitor only after PAPER account and order integration checks:

```bash
venv/bin/python -m src.runner.signal_monitor --once
venv/bin/python -m src.runner.signal_monitor
```

After those checks, run the scheduler as an HTTP client of the same AI service:

```bash
AI_SERVER_URL=http://localhost:8001 venv/bin/python -m src.runner.analysis_scheduler --forever
```

Docker's `analysis-scheduler` and `signal-monitor` are opt-in through the `paper`
profile (`docker compose --profile paper up`). Default Compose startup does not
activate these workers. Do not run multiple monitor instances as an SLO
workaround; backend idempotency is a safety boundary, not additional broker capacity.

## Verification

```bash
venv/bin/python -m pytest -q
mvn -f backend/pom.xml test
venv/bin/python -m scripts.evaluate_paper_runtime --audit data/paper_audit.sqlite3 --budget data/llm_budget.sqlite3
```

The evaluator reads SQLite in read-only mode and does not call any API. Supply
`--baseline-audit` to compare identically collected baseline observations. Report
completion rates and rejections alongside latency; refusing every request is not a
performance improvement. Synthetic load timings verify orchestration overhead only.

Required rollout gates are offline schema/concurrency tests, real PostgreSQL
transaction tests, PAPER quote/order/fill/cancel integration, then 20 trading days of
prospective observations with fixed prompt and configuration versions. Record actual
model identifiers; an alias alone cannot guarantee unchanged provider weights.

Evaluate fees, slippage, unfilled orders, net return, drawdown, turnover and sector
exposure against the existing numerical strategy and the same-universe buy-and-hold
baseline. Do not interpret low latency, direction accuracy or synthetic fills as
investment performance. REAL activation is outside this implementation.

## Observed Investment Comparison

```bash
venv/bin/python -m scripts.evaluate_paper_performance --input data/paper-comparison.json
```

This separate offline evaluator requires a JSON object with exactly three runs:
`strategy`, `numerical_baseline`, and `buy_and_hold`. It never generates a baseline,
retrieves prices, calls an LLM, or infers missing observations. Export and reconcile
actual PAPER fills and marked-to-market account equity before supplying this file;
automatic broker/database export is not implemented.

Each run has the following required fields:

- `universe`: unique six-digit stock codes, identical across the three runs.
- `currency`: one common three-letter currency code, normally `KRW`.
- `period`: `start` and `end` as timezone-aware ISO timestamps.
- `cost_assumptions`: common nonnegative JSON numbers `fee_bps` and `slippage_bps`.
- `equity_basis`: exactly `net_of_fees_and_slippage`.
- `cash_flows`: exactly `null`. Deposits, withdrawals, and any nonnull flow input
  are rejected; adjusted cash-flow returns are not implemented.
- `equity`: at least two strictly chronological observations, each containing
  `timestamp`, positive finite `net_equity`, and `positions`. The first and last
  timestamps must equal the period boundaries. All three runs require identical
  observation timestamps, allowing equivalent timezone offsets.
- Each position contains `stock_code`, nonblank `sector`, and finite nonnegative
  `market_value` in the run currency. List every observed long position once;
  use `[]` for actual cash-only observations. Sector classifications must agree.
- `fills`: chronological observed fills, each with unique `fill_id`, aware
  `timestamp`, in-universe `stock_code`, `side` (`BUY` or `SELL`), positive finite
  `notional`, and nonnegative finite `fees`. Use `[]` only when no fills occurred.
  Partial fills must have distinct IDs and incremental, not cumulative, notional.

Return is final net equity / initial net equity minus one. Fees and slippage must
already be included in net equity; reported fill fees are **not subtracted again**.
Maximum drawdown reuses the numerical backtest helper and is a nonpositive percent.
One-way turnover counts every BUY and SELL fill notional once, divided by arithmetic
mean observed net equity: buying 100 and selling 100 contributes 200, not 100.
Turnover is not annualized. Market and sector exposure are equally weighted means
of position market value / net equity at the aligned observations; missing sectors
and cash-only observations contribute zero, not missing samples. Irregularly spaced
observations are not duration-weighted. Benchmark excess returns are percentage-point
differences in net returns.

The evaluator cannot verify whether input observations are genuine, their universe
is point-in-time correct, or fills are complete. Unfilled/rejected order rates,
realized slippage attribution, between-observation drawdown, statistical significance,
and prospective profitability remain unmeasured. A successful computation is not a
PAPER integration or trading-performance acceptance result.

## Research Basis

- [OpenAI Luna documentation](https://developers.openai.com/api/docs/models/gpt-5.6-luna): model capabilities and pricing.
- [Expert Investment Teams](https://arxiv.org/html/2602.23330v1): computed inputs and narrowly defined specialist work.
- [Fin-Analyst](https://arxiv.org/html/2607.12233v1): short structured reports and unchanged filing reuse.
- [FinToolBench](https://arxiv.org/html/2603.08262v1): tool execution, source freshness and domain alignment.
- [Temporal Leakage](https://arxiv.org/html/2608.02985v1): limitations of retrospective LLM evaluation.
- [OpenDART major accounts](https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS003&apiId=2019016): receipt identifiers, statement division and fiscal-date fields.

These sources motivate the design; none establishes Luna's profitability on this
project's Korean stock universe.
