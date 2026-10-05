# LH001 v1 inputs and outputs

The immutable preregistration in the parent directory defines this experiment.
`v1_common.txt` supplies the same source-only, no-tools and untrusted-data rules
for every arm. The other templates implement the probe, B's three-industry stage,
B/C numeric rankings and B/C business-text selections. The matching schemas
require ranked IDs, reasons of at most 200 characters, and confidence 1–5. C_num
also requires ten distinct avoid IDs. The probe schema requires four answers.
B_text and C_text have identical instructions and use `v1_text.schema.json`.
Anonymous and named calls use the same templates; only the supplied data differ.

Commit all `v1*` files before the first real call. The runner checks that they are
tracked in HEAD and unchanged, records their ordinary file SHA-256 hashes and
the model/CLI version, and refuses changes after that first call. A new design
requires a new experiment ID. This implementation task does not commit files or
run a model. Tests use synthetic inputs and injected subprocesses exclusively.

The implementation is `backtesting/experiments/lh001/`:

- `inputs.py`: close-known market/24-group/stock tables, strict-receipt financials,
  ADV caps, listing coverage, anonymous leak assertions, lazy business-text loader.
- `probe.py`: stored-data questions, month-seeded choices, scoring and clean start.
- `runner.py`: pinned Codex subprocess, fail-closed JSON events/schema/IDs,
  up to two invalid retries, timeout, frozen hashes, persistent locked 1,100-call cap.
- `arms.py`: stage order, three-repeat vote/rank aggregation and fixed text pools.
- `evaluate.py`: shared HC002 execution/cost kernels, ten-name random controls,
  paired NW lag-4 metrics, diagnostics, verdicts, shared results and registry writer.
- `__main__.py`: plans, source acquisition and stage checkpoints.
- `__init__.py`: fixed constants, protected-source guard and explicit assumptions.

Run from the repository root:

```bash
venv/bin/python -m backtesting.experiments.lh001 status
venv/bin/python -m backtesting.experiments.lh001 probe
venv/bin/python -m backtesting.experiments.lh001 screening
venv/bin/python -m backtesting.experiments.lh001 final
```

All are read-only plans by default. Add `--execute` only for an authorised real
stage. `--data-dir` selects a local archive root. `--limit-decisions N` labels
executed results `smoke`; it never writes registry rows or official stage state.
A final smoke still consumes the one holdout acquisition. No command downloads
reports, starts a collector, contacts an investment API or executes orders.
Re-running a completed official stage returns its saved result without new calls,
evaluation or registry rows.

Runtime artifacts live under `data/research/lh001/`: `inputs/` stores each rendered
input once; `calls/` stores content-addressed validated outputs and attempt event
summaries; `budget.json` counts every real attempt before launch, including failed
and interrupted calls; `manifest.json` freezes identity; `invalidated.json`
permanently stops an identity-changed run. Snapshots and stage JSON freeze source
data and execution results. Full prompt/event payloads are not copied into calls.
Result JSON/Markdown are published by `common.write_results`; every completed or
CLI-interrupted official stage appends five fixed-variant trial rows. Failed and
invalid model attempts additionally remain in the call cache. Cache hits do not
spend budget. Plans show an upper bound on stage cache hits because downstream
candidate inputs depend on earlier calls. Screening has 103 planned month ends
and 35 named diagnostics (344 base calls, rather than the approximate 340).

The final stage requires a completed non-smoke probe and screening and stored KRX
data through 2026-10-30. It prepares all source-derived inputs, returns and possible
business texts inside one `HoldoutSession("LH001_llm_hegemony_judge")`, then works
only with that hash-checked snapshot. A quota stop resumes calls without a second
protected source read or holdout claim. Interruption before snapshot publication
consumes the claim and cannot be restarted. HC002's monthly January–May linked
holdout is prepared in the same session only if its registry has a passing
`phase2_ew` row. Otherwise its variant is `not_applicable`.

Assumptions are also copied into every result. Review the full `ASSUMPTIONS` list
before real execution. The decisions needing particular coordinator attention are:

- The **single dedicated** `read_contamination_facts` function permits 2026
  benchmark rows, prior-month cap ranks and top-30 month-end closes before the
  holdout. It records that this is contamination measurement, not strategy
  evaluation; it deliberately does not call `guard_period` for protected months.
  Replace this function if that boundary is not accepted. Dry runs do not call it.
- B_num's ambiguous “universe”/“same 300” wording is conservatively interpreted as
  the full HC002 universe capped by ADV for random controls, regardless of B's
  selected sectors. C controls use the displayed A pool and text controls use 30.
- Both the 20-week and 300-stock/decision minimum fields are enforced; the
  statistical observation unit remains weekly. Fewer than 30 numeric candidates
  (40 for C's disjoint 30 ranked and 10 avoid IDs) give no selection. Three final
  repeats must all be valid; final ties use displayed IDs after count/mean rank.
  NW lag 4 uses calendar periods, preserving gaps from invalid/missing decisions.
- All source prices stop at the allowed exit; screening cannot open 2026 price
  files. October is exit-only, so no October decisions are introduced. Missing
  windows stay unavailable. Incomplete DART listing coverage gives unavailable
  counts; names/dates/titles are excluded from anonymous inputs.
  Listing dates cannot establish intraday availability, so disclosure windows use
  the 60 completed sessions before the decision day and omit its filings.
  Named screening diagnostics do not add disclosure titles. Current classifications and stored
  revisions cannot certify historical classification or publication vintages.
  A bounded presence-date inventory distinguishes no-later-row exclusions from
  temporary missing exits; it never contributes future prices to model inputs.
  This inventory stops at 2025-12-31 for screening, June 30 for linked HC002, and
  October 30 for final LH001.
- Prompts are sent through stdin (`codex exec ... -`) because a single command-line
  argument is limited to about 128 KiB on Linux, which long stock/title tables and
  30 Korean texts exceed. Nothing is truncated. The business-text module is supplied
  separately and is imported lazily; this experiment does not create it.

The legacy `ic` and `skipped_month_count` JSON fields only adapt the shared writer:
they alias paired excess statistics and skipped decisions, respectively. The LH001
Markdown replaces that writer's IC prose with the actual LH001 definitions. All
four LLM verdicts and their random ranks are reported, including screening failures
and insufficient observations. No best variant is selected after seeing results.
