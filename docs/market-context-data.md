# Market Context Data

This first-stage extension supplies market/sector price-index comparisons and
disclosed corporate-action context to the existing shared analysis runtime. It
does not add another LLM role, change the price-only ranking weights, or collect
account-specific broker data. The parent flow is documented in
[Event Evidence and Observed Price Response](event-data-pipeline.md).

## Collect Shared Indices

`scripts/data/market_context.py` is a standalone collector, not a theme-pipeline
or trading-scheduler step. Run it from the repository root using the module entry:

```bash
venv/bin/python -m scripts.data.market_context --help
venv/bin/python -m scripts.data.market_context \
  --from-date 2026-08-01 --to-date 2026-09-04 \
  --series KOSPI KOSDAQ --data-dir ./data
```

The collection command makes real KRX requests and appends local observations; it
does not call a model or submit orders. Replace the example range deliberately.
Dates accept `YYYYMMDD` or `YYYY-MM-DD`. `--series` defaults to both series;
`--data-dir` defaults to the configured `HQA_DATA_DIR` (`./data` by default).
Project environment loading is shared with the other ingestion commands.

An issued key with the necessary KRX service approvals must be configured as
`KRX_OPEN_API_KEY`, or the existing alias `KRX_API_KEY`. The key is sent only in
the `AUTH_KEY` request header, not in stored source URLs. The collector uses these
approved-service endpoints, not the sample endpoints:

| Series | Endpoint |
| --- | --- |
| KOSPI | `https://data-dbg.krx.co.kr/svc/apis/idx/kospi_dd_trd` |
| KOSDAQ | `https://data-dbg.krx.co.kr/svc/apis/idx/kosdaq_dd_trd` |

Official service contracts: [KOSPI series](https://openapi.krx.co.kr/contents/OPP/USES/service/OPPUSES001_S2.cmd?BO_ID=EREKZauXnMmxyIlqzeDN),
[KOSDAQ series](https://openapi.krx.co.kr/contents/OPP/USES/service/OPPUSES001_S2.cmd?BO_ID=nimebcamqFNIPNcRrHoO).

The implemented collection gate rejects invocation before 08:00 Asia/Seoul,
including requests for older dates. The requested end date must be strictly before
today in Korea, even after today's close. The start must be on or after
2010-01-04. This conservative gate is not a guarantee that every response after
08:00 is complete or that a requested date is a trading session.

Each requested series/calendar date makes one request; market and industry index
rows returned by that request are retained. Redirects and non-2xx responses fail
before JSON parsing. Invalid response shapes, missing index identities, invalid
closes and conflicting duplicate rows fail explicitly. An empty `OutBlock_1` is
empty evidence, not a zero close or an inferred holiday. Collection does not retry
or maintain a cross-invocation response cache.

## Local Contracts

All paths below are relative to the configured data directory. Benchmark inputs
are shared company/market data, not per-account files.

### `market_context/benchmarks.jsonl`

The collector writes one JSON object per index/date observation episode:

| Field | Contract |
| --- | --- |
| `schema_version` | Integer `1` |
| `series`, `index_name` | `KOSPI` or `KOSDAQ`, plus exact provider `IDX_NM` |
| `trade_date` | `YYYY-MM-DD` |
| `close` | Finite positive JSON number from `CLSPRC_IDX` |
| `bar_at` | Aware timestamp representing the daily close at 15:30 KST |
| `available_at` | Actual aware collection-completion timestamp, not the trading date |
| `source_url` | Approved endpoint with the requested `basDd`, without credentials |
| `version`, `source_id` | Content SHA-256 and `krx-benchmark:` plus that digest |
| `price_basis` | `price_index` |

The archive validates input and existing records under a Linux file lock before
appending, then flushes and fsyncs. Unchanged consecutive observations retain their
first availability. Content changing A -> B -> A creates three episodes: the last
A reuses its content identity but has a new availability. Comparison selects the
latest episode known at its cutoff, not the first row with a matching hash.
Malformed archives and conflicting same-time revisions fail; this is not a
crash-recovery or automatic archive-repair facility.

### `market_context/benchmark_mappings.jsonl`

This is an explicit, source-backed input. The collector does not generate it.
Each row requires:

| Field | Contract |
| --- | --- |
| `schema_version`, `stock_code` | Integer `1`, six-digit stock code |
| `kind` | `market` or `sector` |
| `series`, `index_name` | Exact collected series and provider index name |
| `effective_from`, `effective_to` | Inclusive `YYYY-MM-DD` interval; `effective_to` must be present, with `null` allowed |
| `available_at` | Aware timestamp when this mapping evidence became available |
| `source_id`, `version` | Nonempty source and revision identifiers |
| `source_url` | Credential-free HTTPS URL supporting the mapping and its effective dates |

A sector mapping cannot be inferred from a theme, company name, article keyword,
or LLM judgment. It must identify the actual supported index relationship and its
effective interval. The referenced source must substantiate that relationship;
the loader validates the supplied fields but does not independently fetch or
certify the source page. There is no bundled production sector-mapping dataset.
Missing mappings remain unavailable rather than using an unrelated proxy.

For market context, verified KRX market metadata on every normalized price bar can
identify the broad-market index over that history's observed date interval.
Explicit market mappings use the broad index name (`코스피` or `코스닥`), not an
industry index. A supplied mapping that conflicts with verified stock-market
identity is rejected. Latest-known mapping selection does not authorize applying
today's industry membership retroactively: both stock comparison dates must be
inside the selected mapping's effective interval.

## Comparison Semantics

The comparator uses the stock reaction's exact Korean baseline date and each
endpoint date for horizons 1, 3 and 5, plus the latest observed endpoint. Both
benchmark `bar_at` and `available_at` must be at or before the analysis cutoff.
It never forward-fills, chooses a nearby date or substitutes market data for a
missing sector series. Missing dates and out-of-interval mappings produce explicit
statuses and `null` comparisons, not neutral returns.

```text
index_return_pct = (index_endpoint_close / index_baseline_close - 1) * 100
excess_return_pp = stock_raw_return_pct - index_return_pct
```

`excess_return_pp` is a percentage-point difference relative to a price index.
It is not causal attribution, beta-adjusted alpha, a total-return comparison or
proof that the event caused either price move. Stock prices remain unadjusted;
corporate-action and dividend limitations remain attached even when a comparison
is available. Exact mapping/bar source IDs and selected observation episodes are
retained for citations and cache provenance. Chartist and RiskManager receive
compact date/return/status/limitation projections, rounded to four decimal places
for display only. Chartist sees all selected events and comparison horizons;
RiskManager sees up to three risk-prioritized event details with the latest index
comparison. Omitted event counts, the union of selected-event risk flags and omitted
benchmark horizon labels are explicit. Full corporate-action guards run before
these attention limits. Repeated per-window source
lists and observation metadata stay out of model prompts. Each projection has a
content-addressed `benchmark-comparison:` source ID; configured runtime audit logs
retain the complete comparison in a `benchmark_context` record under that ID.
Models cite the derived comparison ID; its raw index and mapping source IDs stay
in the audit record. The comparison identity includes mapping versions and actual
observation episodes, and full calculation precision is retained in the audit.

## Disclosed Corporate Actions

Corporate-action context is built from all eligible latest-known per-stock DART
documents before the event attention cap. It uses the existing raw/canonical
document stores, not a new `market_context` calendar JSONL or an external calendar.
Recognized DART corporate-action documents bypass the normal 400-day age gate:
an old unresolved correction or a future disclosed action date must not expire
only because the announcement is old. Missing or never-ingested documents still
mean that coverage is incomplete; other DART and news evidence retain their age gates.

Titles identify bonus issues, paid-in capital increases, stock splits, reverse
splits and dividends as review topics. Structured date extraction currently covers
receipt-verified `fricDecsn` bonus-issue fields only:

| Provider field | Preserved date kind |
| --- | --- |
| `nstk_asstd` | Record date |
| `nstk_dlprd` | Expected new-share certificate delivery date |
| `nstk_lstprd` | Expected listing date |
| `bddd` | Board decision date |
| `nstk_dividrk` | Dividend accrual date |

These field meanings follow the [OpenDART bonus-issue guide](https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS005&apiId=2020024).
An accrual date is not an ex-dividend date. Record/listing dates are not inferred
ex-rights dates or certified price-adjustment dates. Other action categories may
have missing lifecycle dates; the code does not derive them from prose, trading
day offsets or another event. Corrections and withdrawals are linked only through
explicit source relationships, and unresolved links remain visible.

Known bonus-issue/split/reverse-split exposure through an in-window disclosure or
a relevant disclosed action date can emit `unverified_corporate_action_price_basis`
for the observed stock-price window. The runtime supplies
that review state to Chartist and RiskManager, and rejects a BUY plan with a
nonempty `price_safety.entry_block_reasons`, independently of model confidence.
Existing holding protection is not disabled by this new-entry guard; normal
protective HOLD/SELL plans and backend execution checks still apply. The guard
does not manufacture an order or a replacement price/stop. Missing provenance
without a detected action remains an explicit warning, not an adjustment
certificate or a blanket mechanical-action finding.

## Legacy Data and Limits

New KRX stock observations retain endpoint-backed market identity and unadjusted
price provenance. The raw OHLCV writer retains changed observation episodes,
including added provenance, without deleting earlier observations. The current
runtime selects the latest episode known at its cutoff. Legacy undated bars remain
raw but are quarantined from newly published price generations. Old or mixed
histories may not qualify for automatic market mapping. Use a separately verified,
dated mapping when needed; do not label old bars with today's membership.

This stage does not implement corporate-action adjustment factors, reconstructed
adjusted OHLCV, a complete exchange/holiday/action calendar, investor flows,
quarterly cash-flow ingestion, TTM financials, consensus surprises, or intraday
benchmark reactions. Versioned price observations do not reconstruct a historical
universe or certify the legacy backtesting loaders' point-in-time behavior.
Backfilled index observations become usable when collected, not retroactively on
their historical trading dates. See [backtesting limits](../backtesting/README.md).
