Plan Type: work-plan
Workstream: main-branch-agent-runtime-port
Version: v01
Status: Implemented / Verified
Created: 2026-05-26 19:47:17 KST
Last Updated: 2026-05-26 19:56:22 KST
Supersedes: None
Related Plan: docs/agent-system-spec.md, plan/20260526-192623--work-plan--agent-system-spec--v01.md
Current Completion State: The main-based integration branch has the Python AI runtime port applied, focused verification has passed, and the final read-only diff review is complete.
Completed So Far: Created a clean `origin/main`-based worktree branch `port/main-agent-runtime-20260526`, copied this approved plan into that worktree, ported the Python AI runtime/config/script/test surfaces from `ai-data-main`, excluded backend/frontend/generated-data/log areas, added a default skip gate for live KIS paper tests, ported the small backtesting helper changes required by the proof-validation tests, ran the focused spec test set, ran adjacent scheduler/KIS/tracing/proof tests, ran Python compile checks, ran `git diff --cached --check`, and confirmed the final diff has no backend/frontend/generated artifact paths.
Remaining Work: None for the approved porting scope. Live KIS validation and fullstack backend/frontend build checks remain optional follow-up validation because those areas were intentionally not modified.
Current Blockers: None.
Next Step: Hand off concise branch, changed-scope, and verification notes.

# Scoreboard

Current Score: 50
Score Source: provisional
Last Updated: 2026-05-26 19:56:22 KST
Score Rationale: The requested main-branch port is implemented in a clean main-based worktree, focused Python verification passes, backend/frontend/generated-data areas remain untouched, and read-only diff review found no path-scope violation. The score remains capped at 50 because the user has not provided an explicit score and fullstack verification was intentionally not run.
What Improved: The port moved from approved plan to verified code. Python AI runtime files, configs, scripts, tests, proof-validation helpers, and safety gates are now present on the main-based branch. Live KIS checks are gated out of default pytest runs.
What Remains Unsatisfactory: No live KIS paper order validation was run, and backend/frontend builds were not run because those areas were intentionally left unmodified.
Actions To Raise Or Maintain Score: Review the final diff, run any desired fullstack checks separately if the branch will be used in a fullstack deployment, and run live KIS checks only with explicit credentials and `RUN_KIS_LIVE_TESTS=1`.
Score History:
- 2026-05-26 19:47:17 KST: provisional 46. Approved planning state before implementation; exact conflict surface and verification results are still unknown.
- 2026-05-26 19:55:08 KST: provisional 50. Main-based port implemented and focused Python verification passed; provisional score is capped pending explicit user feedback.
- 2026-05-26 19:56:22 KST: provisional 50. Final read-only diff review passed with no backend/frontend/generated-data path changes; score remains capped pending explicit user feedback.

# Objective

Port the HQA agent runtime described in `docs/agent-system-spec.md` from the current `ai-data-main` branch into a `main`-based integration branch. The port should make the Python AI agent system available on top of main without performing a broad branch merge and without dragging generated data, logs, experiment outputs, or unrelated local artifacts into the result.

The port should preserve the current main branch's backend and frontend areas as much as possible. Backend and frontend files should not be edited during the first implementation pass. If the AI runtime cannot be integrated or verified without changing a backend or frontend file, the work should pause before making that edit and report the concrete reason to the user. The user explicitly wants to approve that boundary crossing before it happens.

The intended result is a maintainable main-based branch that contains the Python agent/runtime feature set, its configuration and CLI/script entry points, and the focused tests needed to prove the port. It should not be a wholesale merge of `ai-data-main`, and it should not overwrite main-specific fullstack code.

# Confirmed Facts

The current working branch is `ai-data-main`, and the repository also has a local `main` branch plus `origin/main`. The current branch contains a generated `docs/agent-system-spec.md` that describes the HQA agent system and gives a main-branch porting strategy.

The specification states that `main.py`, `.env.example`, `.gitignore`, `README.md`, `src/agents/llm_config.py`, and `src/utils/kis_auth.py` are conflict-prone and should be manually merged rather than blindly overwritten.

The specification identifies high-priority Python AI runtime files to port first, including the theme paper trading runner, LLM decision engine, paper order guard, paper portfolio manager, paper position store, theme candidate filter, theme evidence builder, theme universe loader, portfolio context utility, `config/theme_trading.yaml`, `scripts/run_theme_paper_trading.py`, and related tests.

The specification also identifies existing extension files that may need careful merging: `src/agents/risk_manager.py`, `src/agents/theme_orchestrator.py`, `src/runner/theme_leader_trading_runner.py`, `src/runner/multi_theme_leader_trading_runner.py`, `src/runner/trade_executor.py`, `src/utils/parallel.py`, `prompts/risk_manager/decision.md`, and `config/watchlist.yaml`.

The specification explicitly excludes generated data and large artifacts from this port: `experiment_results/**`, `data/backtest_results/**`, `data/vector_stores/**`, `logs/**`, and `subagent-runs/**` should be kept out of the main integration change.

# Safe Assumptions

The safest working assumption is that the port should target a new branch or worktree based on `main` or `origin/main`, not the dirty current `ai-data-main` working tree. This avoids losing existing uncommitted local changes and makes the final diff easier to review.

The backend/frontend avoidance rule covers application server, frontend UI, and fullstack deployment files. Python AI runtime code under `src/`, Python scripts under `scripts/`, Python tests under `tests/`, Python-oriented configs under `config/`, prompt templates under `prompts/`, and root CLI wiring in `main.py` are inside the expected AI runtime porting surface unless a file also clearly belongs to the fullstack backend/frontend service.

If `main.py` exists on main and is still the Python AI CLI entry point, it may need minimal manual edits. This is allowed by the user's request and the spec, but it must be handled conservatively. It is not a backend or frontend file, but it is conflict-prone and should be patched only for the required options.

If `.env.example`, README, or `.gitignore` need updates, those changes should be documentation/config hygiene only and should be minimal. They should not become a broad documentation rewrite.

Focused verification should prioritize Python tests named in the spec. Backend and frontend builds should not be run as evidence that they were modified unless the port actually touches those areas. If they remain untouched, the final report can state that backend/frontend verification was not required by the actual diff.

# Non-Goals

Do not merge the entire `ai-data-main` branch into `main`.

Do not copy generated vector stores, raw/corpus/market data, order logs, trace files, paper trading snapshots, experiment results, or subagent run outputs into the main branch.

Do not rewrite backend APIs, frontend pages, database schema, Docker deployment, or fullstack build configuration unless a concrete AI runtime integration blocker is found and the user approves that broader edit.

Do not change real-money trading safety defaults. The port must preserve safe defaults: dry-run or disabled trading by default, paper account explicitness, and real trading only when both account type and allow-real-trading flags explicitly permit it.

Do not turn the port into a cleanup pass. Avoid formatting churn, broad refactors, or renaming files that are not required for compatibility with main.

Do not claim live KIS or LLM validation unless actually run. Local deterministic tests are the primary proof for this pass.

# Technical Approach

Use a clean main-based worktree or branch so that the existing dirty `ai-data-main` working tree is not disturbed. The preferred path is to create a sibling worktree from `main` or `origin/main` with a port branch name such as `port/main-agent-runtime`. This keeps the branch switch safe even if the current working tree has uncommitted generated data changes.

Once the main-based workspace exists, inspect its tree before editing. Confirm whether the Python AI package already exists on main, whether `src/`, `tests/`, `config/`, `scripts/`, and `prompts/` are present, and whether main contains backend/frontend directories that should be avoided. This inspection should drive the exact patch strategy.

For new files that do not exist on main, copy the current `ai-data-main` version if they are within the specification's high-priority runtime set and do not include generated data. For existing files, compare main and ai-data-main carefully and apply only the relevant changes. Preserve main-specific changes whenever they are unrelated to the AI runtime feature.

Use standard Git comparison and file inspection commands for branch differences. Prefer `rg`, `git diff`, `git show`, and `git status` for cheap targeted inspection. Avoid destructive commands. Do not reset or checkout over existing uncommitted user changes in the current working tree.

Use `apply_patch` for manual edits. If copying complete new files from the source branch into the main worktree is necessary, use normal non-destructive filesystem copy operations only for new files or for files whose entire replacement has been reviewed as appropriate. For conflict-prone existing files, prefer manual patching.

# Phased Work Sequence

## Phase 1: Prepare Clean Main Workspace

Confirm the current repository state and branch. Identify uncommitted changes in the existing `ai-data-main` worktree so they are not accidentally included or lost. Create a separate main-based integration workspace, preferably a Git worktree, using a clear branch name.

The success condition for this phase is a clean workspace based on main where the port can be implemented without touching the dirty source branch. If a worktree cannot be created due to branch naming conflicts or local Git state, use an alternate branch name or inspect whether an existing worktree already serves the same purpose. Do not force-delete user work.

## Phase 2: Inspect Main Structure And Conflict Surface

Read the main worktree's top-level layout, Python package structure, tests, scripts, configs, and any backend/frontend directories. Compare the porting specification's file list against what exists on main. Build a concrete file inventory with three groups: new files to add, existing Python AI files to merge, and conflict-prone files requiring manual patching.

If backend or frontend files appear required for the AI runtime to import or run, stop and report the blocker before editing those files. Examples of such blockers would include main moving Python runtime behind a backend API, removing the standalone CLI entirely, or requiring server route changes to expose the agent system. Merely discovering backend/frontend directories does not justify editing them.

## Phase 3: Add New Runtime Files

Add high-priority new Python runtime files from the spec if missing on main. These include the theme paper runner, LLM decision engine, paper order guard, paper portfolio manager, paper position store, theme candidate filter, theme evidence builder, theme universe loader, and portfolio context utility. Also add the dedicated script and theme trading configuration if absent.

Keep added files byte-for-byte close to the source branch unless main's package layout requires import adjustments. If an import adjustment is needed, make the smallest change and keep it local to the compatibility issue.

## Phase 4: Merge Existing Runtime Extensions

For existing AI runtime files, merge only the feature-bearing differences needed by the spec. The likely surfaces are Risk Manager portfolio context support, theme leader orchestration filters and context propagation, theme trading preview/execute runners, multi-theme ranking and signal quality behavior, trade executor safety extensions, and parallel utility support.

Preserve main behavior outside those surfaces. When main has newer code not present on `ai-data-main`, keep the main version and adapt the port around it. When `ai-data-main` has a targeted feature that main lacks, apply the minimal patch that introduces that behavior.

## Phase 5: Manual CLI And Config Merge

Patch `main.py` only after the core runtime imports and tests are in place. Add only the CLI options required by the spec and current runtime: theme trading preview/execute, report replay, multi-theme trading, loop scheduling, or theme paper script connectivity as applicable to main's CLI structure.

Patch `config/watchlist.yaml` only for AI runtime options that are necessary and safe by default. Ensure trading remains disabled or dry-run by default. Add `config/theme_trading.yaml` as a separate paper trading configuration, not as a replacement for watchlist mode.

Patch `.env.example`, `.gitignore`, and README only if the runtime port would otherwise be unclear or unsafe. If these files are not necessary for tests or safe operation, leave them untouched in the first pass.

## Phase 6: Add And Align Tests

Bring over the relevant tests listed in the spec. Prefer focused behavior tests over broad integration tests that require live external services. Tests should cover the LLM paper trading runner, theme orchestrator JSON parsing, Risk Manager cross validation and portfolio prompt context, multi-theme leader runner, theme leader trading runner, trade executor behavior, KIS paper configuration safety where feasible, and tracing if those surfaces are included.

Adjust tests only where main's existing test harness or package layout requires it. Do not weaken safety assertions or remove meaningful guard checks to make the port pass.

## Phase 7: Verification

Run syntax checks or compilation on changed Python files if useful. Run the minimum focused test set from the specification:

`pytest tests/test_theme_paper_trading.py tests/test_theme_orchestrator_json.py tests/test_risk_manager_cross_validation.py tests/test_multi_theme_leader_trading_runner.py tests/test_theme_leader_trading_runner.py tests/test_trade_executor.py`

Add adjacent tests if the actual port touches KIS auth, tracing, scheduler, or parser behavior. If tests fail due to missing dependencies on main, diagnose whether the dependency is already expected by the project. Fix narrow compatibility issues where possible. If a failure points to backend/frontend integration requirements, pause and report.

## Phase 8: Read-Only Review And Plan Update

After implementation and verification, perform a read-only review of the final diff. Confirm generated data and logs are absent. Confirm backend/frontend files are untouched unless the user approved otherwise. Confirm real trading defaults remain safe. Confirm test changes are meaningful and not broad skips.

Update this plan file with final status, completed work, remaining work, blockers, next step, and Scoreboard. The final response should summarize the branch/worktree used, changed files, verification commands, backend/frontend status, and residual risks.

# Key Decisions

Use a main-based branch or worktree instead of checking out main inside the dirty current working tree. This is the lowest-risk path because the current tree already contains modified generated data and untracked artifacts.

Treat `docs/agent-system-spec.md` as the porting guide, but use source code as the final authority where the spec and code differ. The spec says this directly, and code compatibility with main must be proven by tests.

Avoid backend and frontend edits unless a concrete blocker appears. This is a user constraint and a rollback-safety boundary. If a backend or frontend change becomes necessary, pause before editing and explain the exact file and reason.

Keep generated data out of the port. This avoids large diffs, GitHub file-size risk, noisy review, and difficult rollback.

Prefer additive runtime files and narrow merges over broad branch merge. This creates a reviewable integration branch and prevents unrelated main branch regressions.

# Risks And Mitigations

Risk: The main branch may have diverged heavily and lack the same Python runtime layout. Mitigation: inspect main first, then adapt only import paths or local compatibility points required for the AI runtime. Pause if fullstack files would need changes.

Risk: Blindly replacing existing files could erase main-specific fullstack or service work. Mitigation: manually diff conflict-prone files and apply only feature patches.

Risk: Generated data or logs could accidentally enter the final diff. Mitigation: inspect `git status --short` and `git diff --stat` repeatedly, and leave excluded paths untouched.

Risk: Trading behavior could become unsafe on main. Mitigation: preserve disabled/dry-run defaults, paper account explicitness, real-trading double gates, guard validation, current-price checks, and quantity checks.

Risk: Tests may pass locally but not cover backend/frontend integration. Mitigation: final report must explicitly state that backend/frontend areas were intentionally not modified and not validated unless touched.

Risk: External service tests could require credentials or network. Mitigation: run deterministic local tests first, avoid live KIS/LLM assertions, and record any skipped external validation honestly.

# Validation Plan

The first validation is structural: confirm the main-based branch contains only intended file additions and code/config/test patches. `git status --short` and `git diff --stat` should show no generated data, logs, experiment outputs, or subagent run artifacts.

The second validation is import and syntax safety: compile changed Python files or run test collection where practical. Import failures should be fixed before deeper test execution.

The third validation is focused behavior testing with the spec's test set. These tests should prove the paper trading runner path, Risk Manager portfolio context behavior, theme leader and multi-theme runner behavior, trade executor safety, and JSON parsing paths.

The fourth validation is manual read-only review. Inspect safety gates, default configs, prompt/report context handling, report replay, and generated-data exclusion.

# Definition Of Done

This task is done when there is a main-based integration branch or worktree containing the AI agent runtime port, backend/frontend files have not been modified without approval, generated data and logs are absent from the diff, focused Python tests have been run or clearly documented if blocked, and the final response tells the user exactly what changed and what remains risky.

If backend or frontend edits become necessary, the task is not done until the user has reviewed the reason and approved the expanded scope.

# Backend And Frontend Boundary Rule

Do not edit frontend files, UI assets, web routes, backend service controllers, backend API schemas, database migrations, or fullstack deployment files during the first implementation pass.

If a backend/frontend file appears in the diff by accident, revert that accidental change before finalizing unless the user explicitly approved it. If the AI runtime genuinely needs such a change, stop before editing and report the requirement in chat.

# Hidden Rules And Consistency Details

The Python AI runtime has several trust boundaries. Broker credentials and account identifiers must not be copied into prompts, reports, or tests. Portfolio context should be normalized and whitelisted. LLM decisions should remain advisory until code-level guards approve order intent. Report replay should avoid re-running LLM decisions so execution can be audited against a stable prior decision.

The port should maintain a clear distinction between analysis, decision, guard, and execution. Analyst, Quant, and Chartist provide evidence. Risk Manager weighs final decision context. ThemePaperRunner and portfolio manager translate accepted decisions into candidate order intents. PaperOrderGuard and TradeExecutor enforce executable safety rules.

The main branch may include fullstack concerns that are outside this AI runtime. The integration should leave those concerns stable. If later product work needs API endpoints or frontend controls for the paper trading system, that should be a follow-up plan with explicit backend/frontend scope.

The final diff should be easy to review and easy to roll back. A reviewer should be able to see that the patch adds or updates AI runtime behavior without needing to audit large data changes or unrelated web application changes.

# Open Questions

No blocking clarification is required before implementation. The user already clarified that backend and frontend should be avoided unless necessary, and that any necessary backend/frontend change must be reported first.

One open implementation question is whether to base the integration branch on local `main` or `origin/main`. The safe default is to inspect branch freshness and prefer the branch that best represents the current main target. If local main is stale and origin/main is available locally, use `origin/main` as the base for a new worktree branch.

Another open question is whether README or `.env.example` updates are necessary in this pass. The safe default is to skip them unless tests, runtime usability, or safety clarity requires a minimal update.
