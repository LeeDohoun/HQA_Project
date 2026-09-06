ALTER TABLE public.trade_signals
    ADD COLUMN plan_version integer NOT NULL DEFAULT 1,
    ADD COLUMN account_mode varchar(16) NOT NULL DEFAULT 'PAPER',
    ADD COLUMN entry_valid_until timestamptz,
    ADD COLUMN planned_exit_at timestamptz,
    ADD COLUMN managed_quantity integer NOT NULL DEFAULT 0,
    ADD COLUMN account_binding varchar(255),
    ADD COLUMN analysis_as_of timestamptz,
    ADD COLUMN row_version bigint NOT NULL DEFAULT 0;

UPDATE public.trade_signals SET entry_valid_until = expires_at;

ALTER TABLE public.trade_signal_executions
    ADD COLUMN trigger_key varchar(512),
    ADD COLUMN trigger_type varchar(32),
    ADD COLUMN order_side varchar(8),
    ADD COLUMN stock_code varchar(32),
    ADD COLUMN order_organization varchar(128),
    ADD COLUMN reserved_cash bigint NOT NULL DEFAULT 0,
    ADD COLUMN account_binding varchar(255),
    ADD COLUMN row_version bigint NOT NULL DEFAULT 0;

UPDATE public.trade_signal_executions e SET
    stock_code = s.stock_code,
    order_side = CASE WHEN s.action LIKE '%BUY%' THEN 'BUY' ELSE 'SELL' END,
    trigger_type = CASE WHEN s.action LIKE '%BUY%' THEN 'ENTRY' ELSE 'EXIT' END
FROM public.trade_signals s WHERE s.id = e.signal_id;

CREATE UNIQUE INDEX ux_trade_execution_trigger ON public.trade_signal_executions(trigger_key)
    WHERE trigger_key IS NOT NULL;

CREATE TABLE public.paper_account_baselines (
    id varchar(255) PRIMARY KEY,
    user_id varchar(255) NOT NULL,
    trading_date date NOT NULL,
    baseline_equity bigint NOT NULL CHECK (baseline_equity > 0),
    captured_at timestamptz NOT NULL,
    source varchar(64) NOT NULL,
    UNIQUE(user_id, trading_date)
);

-- Deliberately fail migration if existing active duplicates need reconciliation.
CREATE UNIQUE INDEX ux_trade_signals_active_account_stock ON public.trade_signals(user_id, account_mode, stock_code)
    WHERE status IN ('WAITING_ENTRY', 'WAITING_EXIT', 'OPEN', 'ORDER_SUBMITTED', 'PARTIALLY_FILLED');
CREATE INDEX ix_trade_execution_pending_account ON public.trade_signal_executions(user_id, status);

CREATE TABLE public.paper_broker_accounts (
    account_binding varchar(255) PRIMARY KEY,
    credential_binding varchar(255) NOT NULL UNIQUE,
    user_id varchar(255) NOT NULL
);
CREATE UNIQUE INDEX ux_trade_signals_active_broker_stock ON public.trade_signals(account_binding, stock_code)
    WHERE account_binding IS NOT NULL
    AND status IN ('WAITING_ENTRY', 'WAITING_EXIT', 'OPEN', 'ORDER_SUBMITTED', 'PARTIALLY_FILLED');

CREATE TABLE public.trade_plan_receipts (
    id varchar(512) PRIMARY KEY,
    user_id varchar(255) NOT NULL,
    signal_id varchar(255) NOT NULL REFERENCES public.trade_signals(id)
);
INSERT INTO public.trade_plan_receipts(id, user_id, signal_id)
SELECT idempotency_key, user_id, id FROM public.trade_signals
WHERE idempotency_key IS NOT NULL AND idempotency_key <> '';
