# pf001 execution contract (research side ↔ M8), derived from OP001 preregistration cb8e64a

The binding rules are in `preregistration.md`. This file states the interface M8 implements. Changes here that alter
preregistered rules are not allowed; interface details may be refined by agreement and recorded with a commit.

## 1. Inputs

| Period | Source | Proof |
| --- | --- | --- |
| 2026-11 (AV only) | FW001 sealed decision `forward/fw001/decisions/YYYYMM.json` → `rules.av_all.targets` and `trade_date` | `fw001.completion_proof(data_dir, "decisions", month)` returns non-None |
| from the first BB003 entry after 2026-12-01 | OP001 combined-target record `forward/op001/targets/YYYYMMDD.json`, produced by research code | `op001.completion_proof(data_dir, "targets", date)` with the FW001 marker semantics: private dir, kernel mtime sealed_at, 1 s bound, 08:59 deadline, no revoke |

Combined-target record fields (December onwards):
- `date`, `inputs` (FW001 decision hash, BB003 decision hashes)
- `sleeves.av.targets` [stock_code], `sleeves.av.capital_basis`
- `sleeves.bb.slots` [slot, rcept_no, stock_code, planned_entry_date, planned_exit_date, amount_rule]
- `algorithm` (code hash)
The record holds the target sets and amount rules. **Share quantities are not in it**: M8 computes them at execution
time (§2), so no future prices are needed.

AV-only month (November): AV sleeve capital = 80% of the bound account's T0 NAV, minus frozen holdings; equal-weight the
targets. M8 keeps a per-sleeve internal ledger from T0.

## 2. Execution stages (normal sessions)

1. Before 08:59: seal the input reference: target record hash, account snapshot, and the algorithm/config hashes. The
   timer starts at 08:57 and retries every 15-30 s until 08:58:59. If no valid seal exists before 08:59:00, the decision
   is late (preregistration §5): no new rebalance that day, existing holdings kept, logged as
   `input_not_sealed_before_deadline`.
2. 09:05-09:20 SELL stage: market orders (ORD_DVSN 01) for sells:
   - names outside the AV target set, with no band;
   - band breaches computed on AV sleeve NAV;
   - unsold carry-over sells.
   Reconcile fills and cancels before stage 3.
3. Quantity stage: read orderable cash (VTTC8908R) after reconciliation. Compute buy quantities:
   - per-name post-order combined weight ≤ 2% of NAV;
   - per-name daily external buy+sell total ≤ 1% of ADV20, including pending orders;
   - integer shares rounded down after the cost reserve;
   - BB slot amount = min(slot cash, 1% of last confirmed NAV);
   - if cash is short, scale all buy requests proportionally.
   Seal the computed quantities as an execution record.
4. 09:20-10:00 BUY stage: market buys.
5. 10:00: cancel unfilled buy remainders and reconcile. Buy remainders never carry over.
6. BB exits: closing auction window 15:20-15:29 on the planned 21st session. Unfilled sell quantity carries to the next
   session's same window.
7. Special sessions: official open +5..+20 min sell, +20..+60 min buy, +60 cancel; BB exits at official close −10..−1 min.
8. Same-direction orders across sleeves are aggregated; there are no internal transfers. Partial fills are allocated pro
   rata to requested quantity (floor), with leftover single shares going to AV, then BB by slot number.
9. Every stage must be idempotent and restart-safe (verified before activation). UNKNOWN orders are never resubmitted;
   reconcile first.

## 3. Risk profile `pf001`

- Disabled for pf001 and verified as disabled: the daily-loss 2% buy-block and the drawdown 10%/15% buy-block/review.
- Kept: kill switch, PAPER-account check, per-order cap, stale quote, holdings, duplicate/UNKNOWN and order-amount
  validations.
- Cancel-ratio limit: exempt only for scheduled BB exits and termination liquidation.
- **D = 25%**:
  - NAV = reconciled cash + actual holdings × official unadjusted close, computed daily. Reconciled cash = KIS D+2
    settlement cash (`prvs_rcdl_excc_amt`), so unsettled trades are netted; fees and taxes are counted once. The backend
    records it after the close, and Python recomputes it from the KRX official close the next morning. Python's value is
    binding; a mismatch blocks buys and raises an alert.
  - Breach = close NAV ≤ 0.75 × max(close NAV since T0, including the initial NAV).
  - On breach, write an immutable record and block all buys. From the next session, attempt to sell everything in each
    sell window; state is `liquidating` until fully reconciled, then cash until 2028-11-01.
  - Final status `terminated_drawdown`. No automatic resume.
  - Halted names: value at the last confirmed price with days elapsed. If required valuation data is missing, block new
    buys.

## 4. Visibility

The UI and reports show total account NAV, drawdown and indices only. Per-sleeve P&L, BB event results and comparator
models are stored but hidden until the later of the OP001 and BB003 final evaluations. Operational views (fills, slots,
errors) exclude P&L.

## 5. Activation conditions (all by 2026-11-01 23:59:59 KST, else OP001 = not_started)

- Backend on the lab; main PAPER account bound to `pf001` with cash only and no pending orders (user approval).
- Staged execution implemented; idempotence and restart verified, including at the maximum expected order count (~500
  buys) at 1 req/s.
- Risk profile verified.
- XO001 order test (10-27..29) and the OP001 readiness test (below) reviewed.
- `activation.json` sealed with code, config and account-identifier hashes.

## 6. OP001 readiness test (competition PAPER account, not the main account), plan fixed 2026-10-21, split revised 2026-10-21

- Days: 2026-10-27, 10-28, 10-29, 10-30. Stock 005930.
- M8 path: an intraday 1-share market buy (CONT_BUY) at 09:20:00. Record acceptance, order number, fill price/qty from the
  inquiry, and orderable cash before and after. At 15:20, M8 sells exactly its own 1 share (CLOSE phase).
- The limit-order cancel evidence comes from XO001's sealed order-test plan: a lower-limit limit buy at 09:10, cancelled
  at 09:11, on 10-27..29 on the same account. It is reused here and recorded as XO001 evidence.
- Each side flattens only its own quantity; no "sell all holdings" on the shared competition account.
- Purpose: to inform whether the activation conditions are met. These are not OP001 observations.
