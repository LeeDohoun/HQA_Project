-- Combined PAPER portfolio runtime (pf001): continuous-session phases, CONT_BUY cash sizing,
-- the submit-time live price, and close-to-close termination. V15 is already applied on the lab.

-- CONT_SELL / CONT_BUY phases ('CONT_SELL' does not fit varchar(8)).
DO $$
DECLARE c record;
BEGIN
    FOR c IN SELECT conname FROM pg_constraint
             WHERE conrelid = 'public.strategy_batches'::regclass AND contype = 'c'
               AND pg_get_constraintdef(oid) LIKE '%phase%'
    LOOP
        EXECUTE format('ALTER TABLE public.strategy_batches DROP CONSTRAINT %I', c.conname);
    END LOOP;
END $$;
ALTER TABLE public.strategy_batches ALTER COLUMN phase TYPE varchar(16);
ALTER TABLE public.strategy_batches ADD CONSTRAINT strategy_batches_phase_check
    CHECK (phase IN ('OPEN', 'CLOSE', 'CONT_SELL', 'CONT_BUY'));
-- Last reconciler pass that read the broker order list for the batch; CONT_BUY sizing waits for a pass
-- after the CONT_SELL cancels.
ALTER TABLE public.strategy_batches ADD COLUMN last_reconciled_at timestamptz;
-- Cash reserve for costs used by CONT_BUY sizing (null for other phases).
ALTER TABLE public.strategy_batches ADD COLUMN cost_reserve_rate double precision
    CHECK (cost_reserve_rate IS NULL OR (cost_reserve_rate >= 0 AND cost_reserve_rate < 0.05));

-- planned_quantity: the quantity as accepted; quantity becomes the sent quantity after sizing.
-- ref_price_at_submit: live price read just before a continuous-session order was sent.
ALTER TABLE public.strategy_batch_orders
    ADD COLUMN planned_quantity bigint CHECK (planned_quantity IS NULL OR planned_quantity > 0),
    ADD COLUMN ref_price_at_submit bigint CHECK (ref_price_at_submit IS NULL OR ref_price_at_submit > 0);
UPDATE public.strategy_batch_orders SET planned_quantity = quantity WHERE planned_quantity IS NULL;

-- One immutable sizing record per CONT_BUY batch, written once before its first order.
CREATE TABLE public.strategy_batch_sizing (
    batch_id uuid PRIMARY KEY REFERENCES public.strategy_batches(id),
    orderable_cash_krw bigint NOT NULL CHECK (orderable_cash_krw >= 0),
    read_at timestamptz NOT NULL,
    kis_fields text NOT NULL,
    required_krw bigint NOT NULL CHECK (required_krw >= 0),
    cost_reserve_rate double precision NOT NULL,
    scale_factor double precision NOT NULL CHECK (scale_factor >= 0 AND scale_factor <= 1),
    scaled boolean NOT NULL,
    quantities text NOT NULL
);

-- Close-of-session NAV per PAPER account and session, from two sources:
--   krx_official: the binding NAV posted by the Python runtime (reconciled cash + holdings at the official
--                 unadjusted KRX close, computed the next morning); termination is decided from these;
--   snapshot:     the backend's own KIS balance read after the close, kept as a cross-check.
CREATE TABLE public.strategy_nav_closes (
    account_binding varchar(255) NOT NULL,
    session_date date NOT NULL,
    source varchar(32) NOT NULL CHECK (source IN ('snapshot', 'krx_official')),
    -- A snapshot close whose D+2 settled cash was missing is stored with a null NAV (unavailable).
    nav_krw bigint CHECK (nav_krw > 0),
    -- Components, for the cross-check between the two sources (cash_krw is D+2 settled cash).
    cash_krw bigint,
    holdings_value_krw bigint,
    input_hash varchar(128),
    recorded_at timestamptz NOT NULL,
    PRIMARY KEY (account_binding, session_date, source),
    CHECK (nav_krw IS NOT NULL OR source = 'snapshot')
);

ALTER TABLE public.strategy_accounts
    ADD COLUMN activated_at timestamptz,
    ADD COLUMN activation_nav_krw bigint CHECK (activation_nav_krw IS NULL OR activation_nav_krw > 0),
    ADD COLUMN terminated_at timestamptz,
    ADD COLUMN termination_reason varchar(255);
