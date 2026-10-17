# AV002 merge review fixes and validation (2026-10-18 KST)

Starting HEAD: `0b978a983357b48b8ad84e35b4bc91552c4044ad`.
Incoming merge head: `bfc84ad768d4ffc86ca554d47c4886c988f58994`.
No commit was made. This report concerns implementation and read-only checks,
not a holdout result or independent validation.

## Accepted review findings

All four findings in the coordinator's `20_av002_reply.md` were accepted.

- P1: retain main's `append_only.locked_append`, stable sidecar locking and
  durable temp-file replacement. Adapt AV002's `record_trials` to its in-memory
  handle: `empty = not handle.getvalue()` and `io.SEEK_END`. Validate the whole
  batch before a single replacement. No file `stat()` before initial creation.
- P2-1: check `MemAvailable >= 5_000_000_000` immediately before constructing
  the claiming session, after reproduction, preflight, rehearsal, frozen inputs,
  the committed source manifest and final ordering checks. The existing outer
  execution lock remains held through pre-open work, claim, evaluation, result
  publication and batch append, including abort handling.
- P2-2: seal only the parent's execution-source inventory plus the explicit
  `ALLOWED_CODE_CHANGES` list. It includes the new append implementation and
  AV002 runner/successor/wrapper changes. Scope both Git change discovery and
  dirty checks to this set; unrelated changed/untracked Python/shell files do
  not affect the seal or committed-code gate.
- P2-3: flush/fsync both JSON (after the gzip trailer, if compressed) and summary
  files and fsync their directory before returning to registry publication.
  Flush/fsync the SHA-256 manifest and its directory before returning to claim.

The calendar test conflict retains main's real committed-preregistration fixture
and assertions; the obsolete mock-only side is removed. The merge retains main's
`bf7fe66` calendar narrowing and AV002's direct-successor/session locking changes.

## Rehearsal decision

The merge changes holdout **I/O and safety code**: crash-safe claims/appends,
strict ledger parsing and registry readers. These paths are tested with temporary
repositories and synthetic data, never the real ledger or protected prices.

It does not change AV002's default six-month calculation behavior. New HC003
`initial_chain` and `no_trade` options are not supplied by AV002; HF001 forward
extensions are not its evaluator; the cost/calendar changes preserve the dates
and default calculations used by AV002. The narrower pending special-session
gate concerns November 2026, after AV002's period. Filing-date filtering defaults
to `None` and max-lag calculation is equivalent. A six-month synthetic comparison
of pre-merge and merged `PriceBook.from_windows`, `rebalance`, and `simulate`
produced identical snapshots, counters and simulation output. Consequently the
long historical rehearsal was not rerun. This comparison is limited to defaults
and the synthetic fixture; it is not a new real-data rehearsal.

## Validation

Validation outputs are in `validation/merge/`; real-data commands explicitly use
`--data-dir /home/are01/src/HQA_Project/HQA_Project/data`. The audit launcher denies
network, `.env` opens, protected-price content reads, original-data mutations and
real ledger/registry writes. Tests use disposable repositories under this
worktree. The launcher permits `/dev/null` for standard subprocess I/O.

| Check | Final result |
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
The original validation artifacts remain unchanged.

## Staging handoff

The sandbox mounts the live worktree Git directory
`/home/are01/src/HQA_Project/HQA_Project/.git/worktrees/HQA_av002` read-only.
`git add` failed creating `index.lock` with `Read-only file system`.
The working files have both conflicts resolved, but the **live index still has
unmerged entries**. There is no permission escalation available in this session.

An isolated copy of the index and new objects lives under
`validation/merge/git_staging/`; it preserves incoming staged files and stages
these resolutions/fixes without changing the live Git metadata. It is reviewable
with the three Git environment variables shown in `validation/merge/isolated_git.sh`.
The coordinator must run `validation/merge/stage_for_coordinator.sh` in a writable
Git session to stage the final files in the actual merge, then review and commit.
