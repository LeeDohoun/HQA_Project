# LD002 lab installation (not installed by this change)

Use `hqa-server`, the research account also selected for LH003, and a separate
reviewed checkout at `/opt/hqa-ld002`. The `hqa-research` user/group and home in
these templates must match that account. Do not copy credentials into the code
root or units. Authenticate Codex interactively as that account before testing.
No EnvironmentFile, API key, collector change, or service start is performed here.

Prepare `/var/lib/hqa-ld002`, an existing shared `/run/hqa-heavy/run.lock` readable
by the account (preserve its inode), and a read-only `/srv/ld002-feed` transfer.
Its FW001 subtree must preserve completion-directory mtimes (e.g. `rsync -a`).
Only the decision unit needs the research account's existing Codex home writable.
MemoryMax is 1536 MiB, with low CPU/I/O weights and no swap-dependent claim.
Confirm aggregate lab capacity, timers, and actual child-process peaks first.

The non-H1 distributor is an explicit post-transfer step, never an implicit
daily raw-data query. See the implementation runbook for that command and its
full-inventory transfer manifest. Daily jobs read only the H1 seal and completed
distributions. A failed latest publication blocks older publications.

Daily official limit/halt and corporate-action data is an **unfulfilled feed
requirement**: provide immutable `status/YYYY-MM-DD.json` for every actual T, with
`day`, `coverage_verified: true`, `official_source`, `stocks` mapping every KRX
row code to boolean `halted`, `limit_up`, `limit_down`, and `corporate_actions`
(explicit `[]` for a verified no-action day). No price-limit approximation or
assumed absence of corporate actions is used. The current raw KRX loader does
not certify those fields. An absent sidecar refuses finalization, and the next
input is gated by `ledger_not_final`. This feed must be verified before activation.

After all section 10 evidence, the one-time H1 seal and activation are complete,
an operator can review the four units/four timers with `systemd-analyze verify`,
copy them into systemd's unit directory, reload, and enable the timers. Those
installation actions require a separate deployment instruction. Persistent timers
retry the same state; holidays/unverified calendar days never call the model.
No automatic restart loop refills a decision's two-attempt allowance.

The final evaluator's research-directory write access is for the canonical single
registry row. In a linked checkout, mount the actual canonical registry directory
and adjust that one ReadWritePaths entry; never create a competing local registry.
Test reboot/logout, attempt interruption, exact seal reuse, and finalizer
idempotence on the lab with synthetic data before activation. Preserve evidence
logs and hashes. Dry checks and development fixtures do not certify these gates.
The finalizer resumes the oldest unfinalized session, one per invocation; the
timers retry until it catches up. It never reconstructs missed LLM decisions.
Keep historical status/action sidecars so an outage can resume that exact day.
After the single final result is recorded, disable the three daily timers while
retaining state, completion evidence and the canonical registry row.
