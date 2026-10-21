-- OP001 preregistration §5 "청산 예외 범위" (user-approved 2026-10-21): SELL-only batches that are a
-- scheduled BB exit or the D=25% termination liquidation are exempt from the cancel-ratio limit only.
ALTER TABLE public.strategy_batches ADD COLUMN exemption varchar(32)
    CHECK (exemption IS NULL OR exemption IN ('BB_SCHEDULED_EXIT', 'TERMINATION_LIQUIDATION'));
