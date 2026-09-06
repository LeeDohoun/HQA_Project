# Event Evidence and Observed Price Response

This extends the Luna PAPER runtime with deterministic event preparation. It adds
no model calls, broker operation, sentiment score or automatic buy
rule. Company analysis remains shared; account-specific decisions remain private.
Collection and revision hardening is documented in [Data Cleansing](data-cleansing.md).

## Data Path

```text
Existing DART/news collectors
  -> raw document revisions with first-observed timestamps
  -> canonical corpus, retaining source versions and stock associations
  -> latest known documents at the analysis cutoff
  -> bounded event packets
  -> Analyst: events / Quant: financial facts / Chartist: observed reactions
  -> account-specific RiskManager

Standalone KRX index collection + explicit benchmark mappings
  -> market/sector comparisons on the same stock observation dates

All eligible latest-known DART documents, before event attention caps
  -> disclosed corporate-action dates and price-basis review
  -> Chartist context and deterministic new-entry guard
```

The existing price-only candidate selection is unchanged. This work improves the
evidence supplied for selected candidates and holdings, not the ranking weights.

## Event Evidence

- Categories cover earnings, contracts, capital increases, convertible securities,
  buybacks, mergers, dividends and regulatory risk. Classification uses explicit
  title patterns; unrecognized or promotional headlines remain `other`.
- Categories indicate review topics, not positive/negative investment judgments.
  News claims are not upgraded to company-confirmed facts.
- Exact normalized title/body duplicates on the same Korean publication date share
  an event. Full-body news syndication may also share an event when headline facts
  are compatible; corrections and opposing/numerically different claims remain separate.
  Different bodies remain separate, and truncated prefixes cannot prove
  duplication. Semantic clustering of differently worded articles is not performed.
- Each stock receives at most eight events, a 2,400-character primary excerpt per
  event and four source references. Omitted references and excerpt truncation are
  explicit. Recent availability within 30 days precedes risk flags and materiality;
  older evidence can fill remaining slots. This is not an obligation expiry rule.
- Source-age gates remain 400 days for ordinary DART evidence and seven days for
  news. Recognized corporate-action disclosures are retained beyond that age so
  unresolved risks and future disclosed action dates are not silently expired. Source
  URL, publication precision, availability, content hash and revision-bound source
  IDs remain attached. A corpus chunk is not counted as a separate event.
- The DART collector preserves receipt-matched structured provider fields, including
  paid-in capital increases and own-share acquisitions. Strings such as reported
  amounts retain their provider representation. This path does not infer amounts,
  units or earnings surprises from prose. Financial ratios remain the responsibility
  of the existing verified numerical financial snapshot.
- Verified structured-only records are labeled `structured_fields`, never narrative
  body extraction. Title-only/error pages without verified fields remain unusable.
  Invalid receipt matches fail; the collector never substitutes a different report.
- An original report marked as having a subsequent correction is distinct from a
  correction filing. Withdrawal flags survive ingestion. Correction targets are not
  guessed when no explicit link exists.

Raw storage preserves successive content/metadata changes, including an A -> B -> A
reversion, while unchanged recollection preserves first observation. Canonical dedupe
also retains these episodes. The analysis loader selects only the latest episode
known at its cutoff. Conflicting unversioned fragments fail instead of keeping stale
text as citable evidence.

## Observed Reactions

The event anchor is when this system could first use the supplied evidence version,
not an inferred exchange announcement time. Date-only DART publication dates remain
labeled as dates; collection time prevents retroactive use on that morning.

The code takes the last completed close strictly before availability and computes
returns to the first, third and fifth supplied completed bars strictly after it.
It also reports the latest observed return and first-post-event volume divided by
the preceding 20 supplied bars' mean volume. Bars exactly at the event timestamp
are excluded from both sides. Future bars cannot influence the result.

Insufficient bars, missing baseline or zero mean volume produce `null` values with
explicit status/gaps, not zero returns. Horizons count supplied bars; there is no
exchange-calendar completeness guarantee inside this standalone reaction function.
The local price loader now separately validates XKRX session completeness before
supplying bars. These are raw, unadjusted associations,
not causal effects. When dated benchmark observations and an applicable mapping
exist, the runtime also computes market/sector price-index return differences in
percentage points over the exact same stock baseline and endpoint dates. Missing
dates remain unavailable; there is no nearest-date substitution.

Disclosed corporate-action context is evaluated before the eight-event attention
cap. Known bonus issues, stock splits or reverse splits affecting the observed
price window can require price-basis review and block new BUY plans. This guard
does not disable existing holding protection or authorize an automatic sale.
Absence of a detected event does not certify adjusted prices or complete coverage.
Price adjustment factors, inferred ex-dates, intraday reaction and consensus-surprise
estimation remain unimplemented. See [Market Context Data](market-context-data.md)
for collection, storage, mapping requirements and the exact first-stage limits.

## Cost and Refresh

Event grouping and reaction calculations use code, not an additional extraction LLM.
The Analyst gets one excerpt per event instead of repeated corpus chunks. Quant gets
bounded disclosure facts instead of the entire structured provider response. Risk
gets at most three risk-prioritized event/reaction details, an omitted-event count
and the union of event risk flags, not another copy of full document/bar inputs.
Chartist retains all eight selected events and 1/3/5/latest index comparisons;
Risk uses the latest index comparison with omitted horizon labels. Price-basis
guards still use the full eligible corporate-action context before these limits.

Unchanged events and financial inputs reuse specialist results across users and
15-minute cycles. A newly completed price bar refreshes Chartist without rerunning
Analyst/Quant. A changed event refreshes its affected role inputs. Benchmark revision
and mapping provenance also participate in Chartist inputs; advancing the clock
alone does not create a new benchmark observation. Role input-token
limits still include prompts and schemas and are enforced by the existing provider
token counter; character limits do not guarantee that every packet fits. Rejection
is explicit. No live API cost/latency improvement is claimed by offline tests.

## Activation and Verification

Recollect and rebuild the existing pipeline to obtain new structured metadata.
Use `python -m scripts.data.collect` with explicit current date ranges,
`--reuse-saved-targets`, `--enabled-sources news,dart,financials,chart` and
`--update-mode append-new-stocks`. Its canonical corpus is rebuilt from retained raw
records. Its `overwrite` mode now replaces only derived indexes and preserves raw
observation history. News collection remains subject to source access rights.

Old data discarded by prior ingestion cannot be reconstructed by this change.
Recollected historical articles become available at recollection, not retroactively.
The current universe/price store is still not a historical point-in-time archive.
No collection, model generation or PAPER order is performed by these tests:

```bash
venv/bin/python -m pytest -q tests/test_dart_event_metadata.py tests/test_event_evidence.py tests/test_event_reaction.py tests/test_event_pipeline.py tests/test_shared_analysis.py tests/test_ingestion_pipeline_sources.py
```

Official source contracts: [DART disclosure list and correction remarks](https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS001&apiId=2019001),
[paid-in capital increases](https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS005&apiId=2020023),
[own-share acquisitions](https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS005&apiId=2020038).
