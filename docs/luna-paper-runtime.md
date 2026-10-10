# Luna PAPER Runtime

## Operating Boundary

The runtime shares company-level Analyst, Quant and Chartist results, then makes
one account-specific RiskManager call. It screens up to 100 price-ranked candidates,
analyzes the top 20 plus every holding, and reviews at most five new candidates per
account. Entry sizing and order safety remain deterministic backend operations.

Production roles use one provider, chosen with `LLM_PROVIDER`: OpenAI `gpt-5.6-luna`
(`openai`, the default), Claude on API credits (`anthropic`, see [Claude](#claude)) or Claude on
the user's subscription (`claude_plan`, see [Claude subscription](#claude-subscription-claude_plan)).
There is no automatic provider switch, LLM retry, debate loop, fine-tuning or REAL
order path. Explicit `ollama` and `mock` settings remain development options; mock
outputs are not PAPER acceptance data.

The 120-second analysis and 30-second monitoring p95 values are acceptance targets,
not measured API guarantees. The 30-second target covers quote acquisition,
condition evaluation and order request, not exchange fill time.

The operator's start-up order, first-run integration checks, daily checks and
incident handling are in the [PAPER pre-flight checklist](paper-preflight-checklist.md)
(Korean).

## Configuration

Use `.env.example` as the configuration reference. Supply credentials through the
local environment, never through source files, prompts or audit records:

- `OPENAI_API_KEY`: required by the OpenAI model factory (`LLM_PROVIDER=openai`).
- `ANTHROPIC_API_KEY`: required by the Claude model factory (`LLM_PROVIDER=anthropic`).
- `CLAUDE_CODE_OAUTH_TOKEN`: the long-lived subscription token from `claude setup-token`
  (`LLM_PROVIDER=claude_plan`).
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

On Luna, experts use low reasoning, summary uses none, and RiskManager uses medium. Output
ceilings include reasoning: experts 1,200, summary 800, RiskManager 12,000 tokens
(Claude's are in [Claude](#claude)).
Truncated structured output fails validation; holdings are never silently omitted.
Input limits are 12,000 tokens for experts and 128,000 for the RiskManager
(`HQA_LLM_<ROLE>_MAX_INPUT_TOKENS`); spend follows the counted input, not the limit.
One RiskManager call carries every holding and up to five new stocks, about 4-5k tokens
a row. If the rows still exceed the limit by the conservative offline estimate, the
lowest-ranked new stocks are left out first (`omitted_candidates` with
`risk_manager_input_budget`); a holding is never left out, only shown with fewer of its
events. Each account result reports `risk_manager_input` (estimate and budget).

The budget uses UTC calendar months: ordinary analysis stops at the $90 operating
target; holding-priority work can use the remaining amount up to $100. Reservations
include worst-case input cache-write pricing and the complete output ceiling.
Unknown or interrupted requests keep their reservation, including across restarts
and month boundaries. Reconcile from provider usage before releasing uncertainty;
do not delete the ledger to resume work. Taxes and other applications are outside
this internal limit. A dedicated OpenAI project is recommended for accounting.

Reconcile with the operator tool, never by editing or deleting the ledger:

```bash
venv/bin/python -m scripts.llm_budget status
venv/bin/python -m scripts.llm_budget settle <request_id> --input-tokens N --output-tokens M
venv/bin/python -m scripts.llm_budget release <request_id>
venv/bin/python -m scripts.llm_budget acknowledge-overrun <request_id> --note "what was corrected"
```

`settle` takes the provider-reported usage for a `sent`/`unknown` request; zero usage releases a
request the provider never billed. A `reserved` request was never sent (its process stopped between
reservation and sending); `release` frees it once it is older than `--min-age-seconds` (default 600). An observed cost above its reservation blocks every call,
including holding protection, until `acknowledge-overrun` records the review (for example a
corrected price table). `GET /internal/status` (internal token) shows the budget snapshot,
unresolved requests, unreviewed overruns, pending calendar reviews, runtime task states and the
published generation per theme. The RiskManager has its own 180 s timeout, 300 s on Claude
(`HQA_LLM_RISK_MANAGER_TIMEOUT_SECONDS`), because a timed-out call is still billed.

Persist `HQA_LLM_BUDGET_PATH` and `HQA_PAPER_AUDIT_PATH`. Audit records contain private
account context and exact supplied evidence, so keep the data volume access-limited.
Redis eviction cannot reset the budget or erase the prospective audit ledger.

### Claude

`LLM_PROVIDER=anthropic` (alias `claude`) runs every role on the Claude Messages API through
the official `anthropic` SDK (`src/utils/claude_chat.py`), with the same admission queue,
input limits and budget ledger as Luna:

- **Models:** `HQA_CLAUDE_MODEL` for all roles, `HQA_CLAUDE_<ROLE>_MODEL` per role; one of
  `claude-opus-5-5` (default), `claude-sonnet-5-5`, `claude-haiku-5-5`. `ANTHROPIC_MODEL` is
  not read because Claude Code uses it. Audit records and cycle manifests name the model each
  role called (`manifest.role_models`).
- **Thinking and effort:** thinking is adaptive and always on (Claude Opus 5.5 cannot turn it
  off); `effort` sets its depth: experts and summary `low`, RiskManager `medium`
  (`HQA_CLAUDE_<ROLE>_EFFORT`). Thinking counts toward the output ceiling, and the answers are
  Korean, so the ceilings are larger: experts 4,000, summary 2,000, RiskManager 16,000 tokens.
  Timeouts are 120 s, RiskManager 300 s. A response that stops at the ceiling is billed and
  rejected.
- **Requests:** each call counts its input with the token-counting endpoint, including the
  structured-output schema, before reserving budget. The SDK moves schema constraints Claude
  does not enforce (lengths, ranges, patterns) into field descriptions, and the full schema is
  validated after settlement. No SDK retries. The key and `https://api.anthropic.com` are
  passed explicitly, so `ANTHROPIC_BASE_URL`, `ANTHROPIC_AUTH_TOKEN` and `ant` login profiles
  in the environment are ignored.
- **Refusals:** a safety-classifier decline is billed and rejected
  (`stop_reason: refusal`, with its category). On Opus 5.5 and Sonnet 5.5 the server-side
  fallback (`fallbacks: "default"`) first re-runs a declined request on Claude Opus 5 or Opus 4.8
  inside the same call; the reservation adds one attempt at Opus 5 rates, settlement prices
  every attempt at its own model's rates, and the answering model is logged.
  `HQA_CLAUDE_FALLBACKS=off` turns it off. Haiku 5.5 has no server-side fallback.
- **Ledger:** prices per model are in `src/utils/llm_budget.py`. Rejections before generation
  (400, 401, 403, 404, 413, 422, 429, 529) settle at zero; timeouts, dropped connections and
  other server errors stay `unknown` until reconciled. When the organization's credit balance
  runs out the API refuses calls ("credit balance is too low"); HQA reports that as
  `LLMBudgetExceeded`, counted in `budget_rejections` of the PAPER report.
- **Max and Team plan credits:** Claude Max 5x ($100), Max 20x ($200) and Team plans include
  monthly API credits. Link a Console organization on claude.ai (Settings > Billing > API
  credits) and create the key in that organization; no payment method is needed. Credits
  refresh and expire on the plan's billing cycle, while this ledger uses calendar months, so
  keep `HQA_LLM_OPERATING_TARGET_USD` within what one billing cycle provides. Without purchased
  credits, calls stop when the credits run out; nothing is charged to the plan. A linked
  organization is at least on the Start usage tier: set `HQA_LLM_RPM` and `HQA_LLM_TPM` from its
  limits (`scripts.claude_check --send` prints them).

`venv/bin/python -m scripts.claude_check` confirms the key and the role models, and counts every
specialist request the local data would produce against its role limit without generating
anything. `--send` adds one billed quant request and reports its usage, cost and latency.

### Claude subscription (`claude_plan`)

`LLM_PROVIDER=claude_plan` runs the same roles, models, efforts and limits on the user's Claude
subscription (Pro or Max) instead of API credits, through the logged-in Claude Code CLI
(`src/utils/claude_plan_chat.py`, `claude -p --output-format json`). Anthropic allows the Agent SDK
and `claude -p` on a subscription for the subscriber's own use; offering claude.ai login or
subscription limits to other people in a product needs Anthropic's approval, so this mode is for
the subscriber's own PAPER account on their own machine.

- **Login:** the CLI must be 2.1.205 or later (older ones silently drop a schema that uses
  `format`, such as the RiskManager's date-time fields) and logged in. For unattended runs, put the
  long-lived token from `claude setup-token` in `CLAUDE_CODE_OAUTH_TOKEN`; otherwise the CLI's own
  keychain login is used, which can expire. `HQA_CLAUDE_CLI` names another CLI path.
- **Isolation:** each call runs with `--safe-mode` (no CLAUDE.md, skills, plugins, hooks or MCP
  servers), `--tools ""`, `--setting-sources ""`, `--strict-mcp-config`, `--no-session-persistence`
  and the role's system prompt in place of Claude Code's, in an empty temporary directory. The
  child environment carries only basic variables and the token: no `ANTHROPIC_API_KEY` (which would
  bill API credits), `ANTHROPIC_BASE_URL`, HQA or broker secrets. Auto-update is off.
- **Answers:** `--json-schema` makes the CLI check the answer against the schema and ask again on
  a mismatch; the full pydantic schema is validated again afterwards. `CLAUDE_CODE_MAX_OUTPUT_TOKENS`
  carries the role's output ceiling. Errors, refusals, a stop at the ceiling, or a missing
  structured output reject the answer. A response from a model other than the requested one is
  logged (`served_models`).
- **Limits and accounting:** there is no token pre-count and no dollar ledger entry; the payloads
  are still fitted to the role input limits by the offline estimate. Calls go through the same
  admission queue, and the trace records their tokens and the CLI's API-equivalent cost. The
  subscription's 5-hour and weekly limits are shared with the user's claude.ai and Claude Code use;
  reaching them raises `LLMBudgetExceeded` (counted in `budget_rejections`) until they reset, and
  nothing is charged unless extra usage is turned on in claude.ai.
- **Admission:** the subscription publishes no token-per-minute limit, while HQA's own admission
  window defaults to 200,000 tokens a minute. A cold cycle's specialist burst (about 57 calls)
  exceeds it, and calls that wait more than `HQA_LLM_QUEUE_TIMEOUT_SECONDS` fail with
  `LLMQueueTimeout`, so raise `HQA_LLM_TPM` (for example to 2,000,000) on this provider.
- **Measured on 2026-10-10 (Pro, Claude Opus 5.5):** one full specialist pass over 20 stocks
  (57 calls, 6 at a time) took 122 s and used about 5% of the 5-hour session limit; one
  RiskManager call with six candidates took 30 s (22k input, 3k output tokens), about 0.3%.
  Every structured answer takes two CLI turns.
- **Where it runs:** on the host only; the Compose AI image has no Claude CLI.

`venv/bin/python -m scripts.claude_check --plan` shows the CLI version and its login as HQA starts
it; `--send` adds one real quant request on the subscription.

## Data Requirements

The existing ingestion pipeline must provide theme targets, completed daily OHLCV,
canonical DART/news and financial snapshots. Initial price screening requires 151
daily records. Missing or stale prices, evidence and fundamentals produce explicit
errors rather than demo data or neutral scores.

The [event data pipeline](event-data-pipeline.md) supplies disclosure/news event
packets, correction-aware source revisions and observed daily price/volume responses.
Its source-time rules, bounded role inputs and recollection requirements are documented
separately. Event importance is not a buy signal or evidence of causal price impact.

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

A reduction runs once for the same content, across reissued plan versions: a partial
fill, an order still working and a broker refusal count as done. One that sold
nothing because its limit order expired or was cancelled unfilled, was never sent, or
was confirmed not submitted by an operator may run again. A protective trigger
cancels a still-working entry BUY. A working SELL is kept when it belongs to the same
trigger (the monitor re-sends a condition that stays true) or when the new trigger is
a reduction; otherwise, such as a partial reduction or another exit group's order the
price has left, it is cancelled and the exit follows once the cancellation is
confirmed.

Entry validity lasts at most 15 minutes from analysis. Existing positions remain
managed after that time. Duplicate and stale plan/trigger requests must not change
newer protection. Entry-only loss and price-drift gates do not block protective sells.

An accepted order is not a fill. The reconciliation worker queries cumulative fills,
protects partial fills, confirms cancellations, and preserves reservations for
uncertain submissions. An UNKNOWN order without a confirmed broker ID requires
operator investigation; never resubmit it by guessing the previous result. Until it
is resolved every trigger of its plan, protective sells included, waits for it, and
the monitor reports the holding as `protection_blocked:order_without_broker_id`.
Look the order up in the KIS paper order history and record the finding:

```bash
venv/bin/python -m scripts.paper_orders unknown
venv/bin/python -m scripts.paper_orders adopt <execution_id> <ODNO> --note "where it was found"
venv/bin/python -m scripts.paper_orders not-submitted <execution_id> --note "how absence was confirmed"
```

The backend checks both against the KIS order history for the submission date
before changing anything: `adopt` needs that order number with the same stock, side
and quantity, not claimed by another execution, and reconciliation then resumes;
`not-submitted` is refused while KIS lists an order from that time that could be
this one, and releases the order's reservation.

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
It never invents a plan or order to conceal missing protection. A holding counts as
protected only by an OPEN plan the backend can sell; each uncovered holding carries
a `reason`: `no_active_plan`, `entry_fill_unrecorded` (an entry filled but not yet
reconciled, reported only after 60 s), `plan_conditions_unreadable`,
`protection_blocked:<reason>` (an UNKNOWN order or a reconciliation fault that needs
an operator), `plan_without_exit` or `partially_managed:<managed>/<held>` (shares
bought outside HQA). Capacity overload adds `monitor_capacity_exceeded:<n>/<cap>`.

The monitor follows the KRX calendar in `src/runner/trading_calendar.py`. Entries go
out only inside the verified session. On a weekday whose hours are unverified (a
pending KRX notice, or a calendar past its review horizon) protective triggers still
go out between 09:00 and 16:30 KST and the backend's own session check decides.
Outside the session the loop makes no backend or KIS calls; `--once` still evaluates
and reports, sending nothing. Protective triggers go out as soon as they are
evaluated, before any entry. Within a plan, price exits and invalidations are
evaluated first, then a due planned exit, then reductions. A rejection that the same
plan version and status cannot overcome (consumed entry or reduction, stale version,
wrong plan state) is not re-sent until the plan changes; the monitor passes over that
group and sends the plan's next matching one. A refused planned exit is retried after
60 s while price exits keep being evaluated; the plan's reductions wait with it, since
the backend would cancel a reduction's order for the full exit. An accepted trigger
rests 60 s while its order works, holding back the plan's other groups so they do not
cancel that order. Each poll report lists `session`, `deferred`, `quiet`, `rejections`
(refused entries are trading decisions, not monitoring failures) and `settled`.

The backend gates orders by its own calendar: weekdays 09:00-15:30 KST minus
`HQA_KRX_CLOSED_DATES`, whose default lists the Python calendar's weekday closures
through its review horizon (a test keeps them equal; unset or blank keeps the default,
a nonblank value replaces it), with official special sessions
in `HQA_KRX_SPECIAL_SESSIONS` (`YYYY-MM-DD@HH:MM-HH:MM`). When KRX publishes a
special-session notice, add it to both: `SPECIAL_CLOSES` in the Python calendar (open,
close, publication time and source URLs) and `HQA_KRX_SPECIAL_SESSIONS` for the
backend, e.g. `2026-11-19@10:00-16:30` for the 2027 CSAT day once its notice confirms.

Start the AI service after installing requirements:

Runtime jobs, task lookup, chat and query suggestions require the internal token;
the backend forwards it. Do not expose this token in browser configuration.

The dashboard submits selected stocks through authenticated `POST /api/v1/analysis`
or `/api/v1/analysis/bulk` (`mode=full`, `maxRetries=0`, at most 20 stocks).
An omitted bulk body selects the user's watchlist; an explicit empty list selects
no stocks. The backend invokes `POST /runtime/stock-preview` with `stock_code`.
This uses the current shared Analyst/Quant/Chartist pipeline and cache, without
account snapshots, RiskManager or orders. Stocks still need valid local price
history; missing inputs or failed specialists are reported explicitly.

Poll `GET /api/v1/analysis/{taskId}` for status and results. The frontend polls every
five seconds after the preceding request finishes; it does not use the removed
analysis SSE endpoints. Only the submitting user can read the task and history.
History lists the last stored state without depending on AI-server availability;
opening a task refreshes its runtime state.
Flyway V10 adds durable terminal-result storage to `analysis_records`. Runtime
jobs awaiting their first terminal poll remain in AI-server memory: if that server
restarts or evicts a finished job before it is stored, the backend records an
explicit failure. It does not rerun paid work automatically. Running jobs are
never evicted to admit new work; a full active queue returns HTTP 503.

Authenticated `/api/v1/internal/` requests bypass the public IP rate limit. KIS
per-account pacing and capacity limits continue to apply. Broker cancellation
rejection restores an order's open state, allowing the next fresh reconciliation
to reassess cancellation; an uncertain response keeps its reservation and pending
cancellation state. Dashboard order history lists each persisted execution,
including separate BUY, REDUCE and EXIT orders, with requested and filled quantity.

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
venv/bin/python -m backtesting paper-runtime --audit data/paper_audit.sqlite3 --budget data/llm_budget.sqlite3
```

`AnalysisRecordRepositoryTest` additionally checks terminal-result writes and owner
isolation against PostgreSQL. Set `HQA_TEST_DATABASE_URL` (a JDBC URL),
`HQA_TEST_DATABASE_USERNAME` and `HQA_TEST_DATABASE_PASSWORD` to an isolated test
database when running Maven. Flyway runs before these tests; test rows roll back.
`PaperTradeStorePostgresTest` uses the same settings to check that the account lock
serializes concurrent plan saves and trigger claims: one sell order per plan, no cash
reserved twice, one active plan per stock. Its calls commit like production, and it
removes its rows afterwards. Without that URL, the six PostgreSQL tests are skipped.

`PaperLifecycleSimulationTest` runs the real Python monitor and plan submitter
(`scripts/paper_lifecycle_sim.py`) against the real order lifecycle and store, with
the backend's JSON settings, in-memory repositories and a simulated KIS paper broker,
through stop, take-profit tier, planned-exit, entry, expiry, rate-limit and plan
publication scenarios. It needs no keys or
database and is skipped unless `HQA_SIM_PYTHON` names the project's Python, e.g.
`HQA_SIM_PYTHON=$PWD/../venv/bin/python mvn -q test -Dtest=PaperLifecycleSimulationTest`
from `backend/`. Its report is `backend/target/paper-lifecycle-sim.txt`.

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
venv/bin/python -m backtesting paper-performance --input data/paper-comparison.json
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
