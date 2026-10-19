# BB002 implementation handoff — 2026-10-19

Implemented the registered d21 confirmation without changing either BB002 or
BB003 preregistration. This is a shared-period historical confirmation, not an
independent holdout. No protected execution or provider fetch was performed.

Changed source and tests:

- `backtesting/experiments/bb002.py`: separate fixed windows, BB002 reader and
  judge, metadata precheck, complete unprotected rehearsal, one-session execution,
  input/code rechecks, source hashes, and terminal abort recording.
- `backtesting/experiments/bb001.py`: optional period, archive-range and cutoff
  arguments only. Defaults and event, matching, valuation, cost, random-control
  and HAC arithmetic remain the BB001 implementation.
- `src/ingestion/dart_buyback.py`: explicit receipt ranges for listing reads,
  plans and fetches. Non-default structured responses live in
  `disclosures/dart_buyback/structured/FROM_TO/CORP.json`; historical default
  archives remain at their original paths. Receipt-named documents are shared
  and never replaced, and overlapping structured ranges must agree on original
  receipt values. Pagination, quota, provider status and document verification
  remain in use.
- `scripts/data/dart_buyback.py`: `plan` and `fetch` accept `--from` / `--to`;
  defaults remain 2016-06-01 through 2025-12-31. Fetch stays dry-run by default.
- `tests/test_experiments_bb002.py`: synthetic boundaries, repeat/coverage rules,
  ID propagation, missing horizon/source refusal, full HAC grid, judgment
  precedence, rehearsal isolation, single-session/abort behavior and mutation
  checks. Synthetic session scopes never invoke the real ledger claim.
- `tests/test_dart_buyback.py`: range, dry-run, original-receipt immutability and
  CLI coverage; the existing fake-provider CLI test also suppresses environment
  loading.

Judgment uses >=30 executed events, >=20 firms and >=20 entry dates, mean excess
>=0.009366926306807792, and cost x1.5 mean excess >0. The same rules are recomputed
with vanished positions on both sides at -100% gross, retaining costs.
Insufficiency takes precedence over a changed sensitivity verdict. HAC t,
entry-date cluster t, the 200 random controls, cost x2, March 6 receipt strata and
holding crossings, cancellation tags, gaps, side returns and annual intensity
terciles are reports only.

The execution lock covers precheck, rehearsal, pre-claim rechecks, the single
BB002 HoldoutSession, publication and the single `holdout_d21` registry row.
An exception after consumption records `holdout_aborted` through the repository's
captured-registration abort batch path, including hashes already read; the
session cannot be reopened. Missing files or uncollected sources refuse before
opening. Official unavailable/mismatching original responses remain counted
exclusions, as in BB001.

Validation completed:

```bash
ulimit -v 1450000
PYTHONDONTWRITEBYTECODE=1 GIT_OPTIONAL_LOCKS=0 OPENBLAS_NUM_THREADS=1 \
  OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 /usr/bin/time \
  venv/bin/python -B -m pytest -q -p no:cacheprovider \
  tests/test_experiments_bb002.py tests/test_experiments_bb001.py \
  tests/test_dart_buyback.py tests/test_backtesting_cost_model.py \
  --basetemp=/tmp/bb002-pytest
```

438 tests passed serially in 33.40 seconds; peak RSS was 221,204 KiB. No Git
add/commit operations were run. Both preregistrations, registry and real holdout
ledger have no diff. The shared virtual environment and data were read-only.

The actual local metadata precheck is saved in
[`validation/precheck.json`](validation/precheck.json). File access was instrumented
to reject protected price contents, environment files, network access and writes
in the main checkout. Findings:

- All 201 required official-session price files exist and are nonempty,
  2025-12-03 through 2026-09-30. Their protected contents/hashes were not opened.
- All 279 calendar-day listing archives needed for repeat coverage are covered,
  2025-09-25 through 2026-06-30. Additional 2025H2 listing requests are currently
  unnecessary.
- The fixed HAC grid is 2026-01-02 through E=2026-07-30: 141 official sessions,
  retaining dates without events. The shared calendar excludes June 3 and July 17.
- 140 title-qualified sample receipts represent 107 corporations and 139 distinct
  corporation/receipt-date targets. No 2026H1 structured archives or original
  documents have been collected. Precheck status is therefore `insufficient`,
  and execution refuses before a session.
- Remaining estimated requests: **139 structured + 140 document = 279**. This is
  a lower bound; pagination or transport retries can increase it. Local precheck,
  rehearsal and protected execution themselves make zero provider requests.

The concatenated BB001 `.part00` + `.part01` SHA-256 was verified, streaming the
compressed bytes without decompressing the comparison observations:

```text
7a56e0fb0026090c8d8587cde96327f40d509a982bf7a1d5e0698c471cee00ef
```

An actual 2025-01-02 through 2025-06-30 rehearsal, with vanish horizon
2025-09-30, completed the full calculation and JSON/Markdown output path under
[`rehearsal/20261019T003249155400Z.json`](rehearsal/20261019T003249155400Z.json),
using the same access restrictions. It took 46.48 seconds with peak RSS 401.75 MiB;
the JSON SHA-256 is
`0326d7dc01d43da34ac65303a552476dfdfcce870638556317a4b2858cd6f806`.
This is a pipeline
rehearsal, not a new BB001 judgment. It never accesses the registry or ledger.
Each protected execution performs a fresh successful rehearsal before opening.

Coordinator commands, after integrating this implementation into the canonical
checkout so the real registry and ledger are shared. These commands were not
used here to fetch data or execute the protected run. `DART_API_KEY` must be
configured for the coordinator's explicit fetch; the existing CLI loads project
environment settings only for that explicit action.

```bash
cd /home/are01/src/HQA_Project/HQA_Project
export BB002_DATA=/home/are01/src/HQA_Project/HQA_Project/data
export PYTHONDONTWRITEBYTECODE=1 GIT_OPTIONAL_LOCKS=0
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1
ulimit -v 1450000

# Local precheck; currently reports insufficient because sources are unfetched.
venv/bin/python -B -m backtesting.experiments.bb002 --precheck --data-dir "$BB002_DATA"

# Local request plan, then the coordinator's explicit download.
venv/bin/python -B scripts/data/dart_buyback.py plan --data-dir "$BB002_DATA" --from 2026-01-02 --to 2026-06-30
venv/bin/python -B scripts/data/dart_buyback.py fetch --data-dir "$BB002_DATA" --from 2026-01-02 --to 2026-06-30 --execute

# Repeat precheck after collection. Unavailable originals are counted exclusions.
venv/bin/python -B -m backtesting.experiments.bb002 --precheck --data-dir "$BB002_DATA"
venv/bin/python -B -m backtesting.experiments.bb002 --rehearsal --data-dir "$BB002_DATA"
venv/bin/python -B -m backtesting.experiments.bb002 --execute --data-dir "$BB002_DATA"
```

Omitting `--execute` from the fetch command performs only its dry-run plan.
