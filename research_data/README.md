# QP001 OpenDART multi-company raw archive

Collector: `research_data/dart_multi_raw.py`. CLI: `scripts/qp001/dart_multi_raw.py`.
Production ingestion files and existing data are not modified by development.
Only the coordinator runs `fetch --execute`. Plan, default fetch, and link-check
do not load project environment files, create a session, or write data.

## Commands for the coordinator

Run from `/home/are01/src/HQA_Project/HQA_qp001`. The supplied worktree currently
has a regular `data/` directory without fundamentals; use the explicit existing
data root below. Do not depend on the worktree's nested `data/data` symlink.

```bash
ulimit -v 1400000
export PYTHONDONTWRITEBYTECODE=1
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1
QP001_DATA=/home/are01/src/HQA_Project/HQA_Project/data
QP001_CORPS=/home/are01/src/HQA_Project/HQA_data_backup_20260913/corp_codes.csv

venv/bin/python scripts/qp001/dart_multi_raw.py plan --data-dir "$QP001_DATA" --corp-codes "$QP001_CORPS" --from-year 2015 --to-year 2025

# Read-only preview; no key or network access.
venv/bin/python scripts/qp001/dart_multi_raw.py fetch --data-dir "$QP001_DATA" --corp-codes "$QP001_CORPS" --from-year 2015 --to-year 2025 --max-requests 500 --daily-cap 500

# COORDINATOR ONLY. Loads src.config.settings.load_project_env(), then DART_API_KEY.
venv/bin/python scripts/qp001/dart_multi_raw.py fetch --data-dir "$QP001_DATA" --corp-codes "$QP001_CORPS" --from-year 2015 --to-year 2025 --max-requests 500 --daily-cap 500 --execute

# All existing quarterly archive years, including 2026 if present.
venv/bin/python scripts/qp001/dart_multi_raw.py link-check --data-dir "$QP001_DATA"

# Focused coverage for the requested collection window.
venv/bin/python scripts/qp001/dart_multi_raw.py link-check --data-dir "$QP001_DATA" --from-year 2015 --to-year 2025
```

Verified offline on 2026-10-20: 3,995 corporations, 40 sorted batches of at most
100. All 40 batch hashes match the existing quarterly checkpoint. The plan is
**1,760 requests = 11 years × 4 reports × 40 batches**, or 160 per year. No
responses have been collected by this implementation. A 500/day cap requires at
least four KST calendar days, assuming no failed attempts. Repeating the command
on the same day consumes only the unused daily allowance. There are no implicit
retries or refreshes of completed requests.

Pre-collection link-check counted 163,799 rows in 2015–2025: 148,887 currently
listed, 14,864 delisted/non-exchange proxy, 48 unknown. All 163,799 are currently
unmatched because no raw responses have been fetched. The all-year default also
includes 9,918 rows in 2026 (173,717 total); 2026 is outside this fetch plan.

## Storage and resume contract

All output is inside `data/fundamentals/dart_multi_raw/`:

- `_universe.json`: immutable corp/stock aliases and ordered batch hashes,
  obtained with the existing `dart_quarterly.load_universe`. The existing
  quarterly checkpoint's batch hashes must match; source/universe drift fails.
- `blobs/<sha256>.json`: exact `response.content` bytes, including the complete
  original JSON, unused accounts and additional response fields. SHA-256 is over
  these exact bytes, not a reserialized object.
- `requests/<year>_<report>_<batch_sha256>.json`: immutable completed request
  metadata, with endpoint, all non-secret request params, KST fetch timestamp,
  HTTP status, DART status and response SHA-256. The API key is never persisted.
- `attempts/<request_key>_<attempt_id>.json`: immutable metadata for unsuccessful
  HTTP/provider responses, pointing to the same content-addressed blob store.
  A transport failure has no response bytes; its reserved attempt remains charged.
- `derived/<request_key>.jsonl`: reproducible balance-sheet filing rows, keyed by
  `(corp_code, bsns_year, reprt_code, fs_div, rcept_no)`. Each row keeps all returned
  `BS` accounts under `accounts`, including original `account_nm`, `currency`,
  `thstrm_amount`, `frmtrm_amount`, `fs_div`, `rcept_no` and other returned fields.
  `metric` identifies whitespace-normalized 자산총계, 부채총계 and 자본총계.
  Original amount strings are retained without floating-point rounding.
- `_quota.json`: per-KST-day reserved attempts and provider-limit flags.
  `.fetch.lock` serializes writers, quota reservation, and resume publication.

Raw blobs and completed request metadata are installed atomically without
overwriting. The same completed request key with different bytes is refused.
An identical response does not change the original fetch timestamp. Valid 013
responses are completed and have empty derived files. Only complete HTTP-success
000/013 responses claim a request key. Failures have distinct attempt IDs so a
later explicit run can retry pending work. Status 020 stops and blocks further
requests that KST day; 800 stops this run and leaves the request pending.

Every network attempt is reserved in the ledger **before** sending, including
timeouts, HTTP failures, 013, 020 and 800. `--max-requests` caps a single run even
across midnight; `--daily-cap` caps the ledger's total for each KST day across
runs. Keep the same daily cap on repeat invocations. No other collector's quota
is represented by this ledger. Interrupted runs may conservatively charge an
attempt that was reserved before the request actually reached DART. HTTP
redirects are disabled so one reserved attempt cannot silently follow redirects.

Completed raw responses are verified before skipping. If a run crashes between
raw publication and derivation, resuming rebuilds the derived file without
requesting that job again. A malformed account fails visibly while preserving
its complete raw response. Nothing is silently replaced with placeholder data.

## Linking and date interpretation

`link-check` reports matched/total/fraction overall, by business year, by current
listing group, and by both year and group. It includes every saved quarterly
filing version, including corrections. It verifies each response hash and
reproduces the derived rows before using them. A match requires an identical
five-field filing key **and nonempty identical currency**, with one finite
current-period total-assets value. Zero is a value; missing, non-finite or
conflicting assets do not count. It never borrows a different receipt, division,
currency, balance-sheet account or previous-period value. Missing filing versions,
missing assets, currency mismatches, invalid values and conflicts are reported
separately. Empty denominators have `fraction: null`.

Listing groups use the local company-profile snapshot: Y/K/N = listed (N is
KONEX), E = delisted/non-exchange proxy among historically traded firms.
Corporations without a usable profile remain `unknown`. Supplement membership
alone does not prove delisting, because its target set also includes KONEX and
unknown firms. This is a current classification, not a historical delisting-date
reconstruction. Source dates/provenance remain in the existing reference archives.

The endpoint has no receipt selector. Returned corrections are saved separately;
old versions that DART no longer returns may stay unmatched. Neither full raw
capture nor an exact join reconstructs those originals. Derived `available_date`
is the same receipt-prefix field as the existing quarterly archive. Experiments
must pass the same filing row to
`src.ingestion.dart_financial_date_rules.FilingDates.annotate`: S uses
`actual_public_date`, O uses `assumed_public_date`, with the existing stage and
decision-date gates. Fetch time must never substitute for a public filing date.
No profitability or preregistration coverage threshold is imposed here.

## Offline validation

```bash
ulimit -v 1400000
PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 venv/bin/python -m pytest -q -p no:cacheprovider research_data/tests/test_dart_multi_raw.py
```

Tests use fake sessions and temporary directories, prohibit real sessions,
outbound connections and environment loading, and run serially. Coverage includes
batching, archive-universe matching, immutable bytes/metadata, raw corruption,
resume after interrupted derivation, independent per-run and daily budgets,
KST rollover within/across runs, provider stops, malformed responses, exact
corrected-filing joins and S/O annotation, fs_div/currency mismatch, conflicting
assets, and listed/delisted/unknown coverage.

Validation on 2026-10-20: **62 tests passed**, serially. Observed test/CLI peak
RSS was below 100 MiB. The subprocess import test installs an audit hook before
project imports to reject `.env*` reads and network operations.
