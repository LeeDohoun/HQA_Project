"""Fingerprint final artifacts and distinguish concurrent sibling registry changes."""
import csv
import hashlib
import io
import json
import shutil
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[5]
base = root / 'research/experiments/AV002_avoidance_holdout_recovery'
v = base / 'validation/merge'
def digest(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()
def git(*args):
    return subprocess.run(['git', *args], cwd=root, capture_output=True, text=True, check=True).stdout.strip()
r = json.loads((v / 'reproduce.json').read_text())
p = json.loads((v / 'preflight.json').read_text())
before = json.loads((v / 'protected_before.json').read_text())
after = {name: digest(Path(name)) if Path(name).exists() else None for name in before}
drift = {name: {'before': before[name], 'after': after[name]} for name in before if before[name] != after[name]}
main_registry = '/home/are01/src/HQA_Project/HQA_Project/research/experiments/registry.csv'
assert set(drift) == {main_registry}
original = (root / 'research/experiments/registry.csv').read_bytes()
current = Path(main_registry).read_bytes()
assert current.startswith(original)
csv.field_size_limit(sys.maxsize)
rows = list(csv.DictReader(io.StringIO(current.decode())))
old_rows = list(csv.DictReader(io.StringIO(original.decode())))
extra = rows[len(old_rows):]
assert len(extra) == 3 and all(row['experiment_id'] == 'EV001_earnings_filing_entry'
    and json.loads(row['metrics_json'])['stage'] == 'design_validation' for row in extra)
assert r['passed'] and r['state_matches_parent'] and r['state_sha256'] == 'b8daf99f7f8e27d06ecd5425be4952f6b6ecd6ff8d8949cb538ab5e9b27577da'
assert all(n == 103 for row in r['months_compared'].values() for n in row.values())
assert r['monthly_e_sha256'] == json.loads((base / 'validation/reproduce.json').read_text())['monthly_e_sha256']
assert p['passed'] and p['sessions'] == p['price_sessions'] == 141 and not p['contents_read']
for relative, sha in r['code_hashes'].items():
    assert digest(root / relative) == sha, relative
for relative, sha in r['parent']['sealed_files'].items():
    assert digest(root / relative) == sha, relative
assert git('rev-parse', 'HEAD') == '0b978a983357b48b8ad84e35b4bc91552c4044ad'
assert git('rev-parse', 'MERGE_HEAD') == 'bfc84ad768d4ffc86ca554d47c4886c988f58994'
assert git('hash-object', 'backtesting/append_only.py') == git('rev-parse', 'MERGE_HEAD:backtesting/append_only.py')
assert git('hash-object', 'src/runner/trading_calendar.py') == git('rev-parse', 'MERGE_HEAD:src/runner/trading_calendar.py')
assert not git('diff', 'HEAD', '--', 'research/experiments/AV002_avoidance_holdout_recovery/preregistration.md')
audits = {name: json.loads((v / (name + '.stderr.txt')).read_text())['audit']
          for name in ['preflight', 'reproduce', 'final_tests', 'final_batch_tests', 'append_only_final']}
for audit in audits.values():
    assert all(value == 0 for name, value in audit.items() if name != 'data_read_opens')
code_files = set(r['code_hashes']) | {'backtesting/append_only.py', 'backtesting/experiments/ev001.py', 'scripts/ops/research_run.sh'}
checks = {'passed': True, 'head': git('rev-parse', 'HEAD'), 'merge_head': git('rev-parse', 'MERGE_HEAD'),
    'state_sha256': r['state_sha256'], 'monthly_series_equal_previous_and_parent': True,
    'months_compared': r['months_compared'], 'protected_before': before, 'protected_after': after,
    'worktree_registry_and_both_ledgers_unchanged': True,
    'concurrent_sibling_registry_drift': {'hashes': drift, 'added_rows': [
        {k: row[k] for k in ['recorded_at', 'experiment_id', 'variant', 'verdict']} for row in extra],
        'stage': 'design_validation', 'original_prefix_preserved': True,
        'note': 'Sibling checkout is outside writable sandbox; guarded runs recorded zero write attempts. No AV002 rows added.'},
    'preregistration_unchanged': True, 'parent_seals_unchanged': True,
    'append_only_equals_incoming': True, 'calendar_equals_incoming': True,
    'execution_code_sha256': {name: digest(root / name) for name in sorted(code_files)},
    'audits': audits, 'tests': '422 passed, 1 skipped', 'reproduction_external_wall': '1:01:10',
    'reproduction_external_peak_rss_kib': 2926324,
    'live_staging': 'blocked: live Git directory is mounted read-only; coordinator must stage',
    'live_unmerged_paths': git('diff', '--name-only', '--diff-filter=U').splitlines(),
    'rehearsal': 'not rerun: default AV002 calculation unchanged; shared safety/I/O tested in temp repos'}
(v / 'final_checks.json').write_text(json.dumps(checks, indent=2) + '\n')
report = base / 'MERGE_REVIEW_REPORT.md'
s = report.read_text().replace('Final command outcomes and fingerprints are recorded below after completion.', '''| Check | Final result |
| --- | --- |
| AV002, AV001, ordering, registry, holdout, append-only, calendar suites | 422 passed, 1 skipped; 323.71 s |
| Real-data preflight | 141 sessions/files; none missing/unexpected; zero data-content opens |
| Real-data reproduction | Passed; 103 months for each S/O x three-variant series, exact equality |
| Full state SHA-256 | `b8daf99f7f8e27d06ecd5425be4952f6b6ecd6ff8d8949cb538ab5e9b27577da` |
| Reproduction external time / peak RSS | 1:01:10 / 2,926,324 KiB (2857.74 MiB) |
| Actual temporary ledger + batch/release and fsync checks | 15 passed; absent/empty/header/populated registries, normal and post-claim abort |
| Full append-only suite | 39 passed, including concurrent process appends |
| Synthetic pre-/post-merge shared-accounting defaults | Identical six-month snapshots, counts and simulation |

The skip is the existing optional HC003 archive comparison. An initial guarded
suite run had two harness failures because Python multiprocessing tried to put
its temporary backing files in `/dev/shm`. The launcher now directs those files
to the worktree; the full clean rerun above passed. No production change was
needed for those two failures.

Final preflight, reproduction and test audits recorded zero denied writes,
protected-price reads, `.env` opens and network attempts. Reproduction performed
36,243 non-protected data read opens. This worktree's registry and both real
holdout ledgers retain their initial hashes. All three parent seals and AV002
preregistration remain unchanged. `final_checks.json` verifies all 26 execution
and allowed-source fingerprints, reproduced state/monthly series, incoming append
helper/calendar identities, unchanged worktree heads and the audit counters.
No real holdout was claimed, no real registry row appended by this session, and
no worktree commit made.

**Concurrent sibling activity:** main's registry gained three EV001
`design_validation` rows at 2026-10-17 16:33:06 UTC. Its original byte prefix is
intact, and no AV002 row was added. Its hash changed from
`186f33016acc6bdb6263fbff68524f89fe921b13a84470544f5cc20d0c40b1ec` to
`0024fdb5fab875a72aad908378f95ba300af2ff6605a8a9ff194127c877ab3a0`.
That sibling checkout was read-only to this session; our guards recorded no
write attempts. This drift is preserved and reported, not copied into AV002 or
reverted. It does not affect the non-recorded reproduction, which uses the
parent's fixed trial count and sealed inputs. Both real holdout ledgers stayed
unchanged.

Outputs, external timing, clean suite logs, batch checks, the default-path
comparison and before/after checks are preserved under `validation/merge/`.
The original validation artifacts remain unchanged.''')
report.write_text(s)
for directory in [base / 'validation/merge_test_tmp', *(v / name for name in
        ['suite_tmp', 'final_suite_tmp', 'final_batch_tmp', 'final_added_tmp', 'append_final_tmp'])]:
    assert directory.resolve().is_relative_to(base / 'validation')
    if directory.exists():
        shutil.rmtree(directory)
print(json.dumps({'final_checks': 'passed', 'execution_sources_verified': len(code_files),
    'worktree_registry_and_both_ledgers_unchanged': True, 'concurrent_EV001_design_rows': len(extra), 'audits': audits}, indent=2))
