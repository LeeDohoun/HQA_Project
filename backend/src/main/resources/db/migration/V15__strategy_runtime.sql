-- M8 strategy runtime: strategy-only PAPER accounts, rebalancing batches, order rows,
-- published status snapshots, account equity peaks and halt switches.
-- user_id holds the login id (users.user_id), as in every PAPER trading table.

CREATE TABLE public.strategy_accounts (
    user_id varchar(255) PRIMARY KEY REFERENCES public.users(user_id),
    strategy_id varchar(128) NOT NULL UNIQUE,
    stage varchar(16) NOT NULL CHECK (stage IN ('observe', 'shadow', 'paper')),
    enabled boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL
);

CREATE TABLE public.strategy_batches (
    id uuid PRIMARY KEY,
    strategy_id varchar(128) NOT NULL,
    user_id varchar(255) NOT NULL,
    account_binding varchar(255) NOT NULL,
    decision_hash varchar(64) NOT NULL,
    decision_date date NOT NULL,
    session_date date NOT NULL,
    phase varchar(8) NOT NULL CHECK (phase IN ('OPEN', 'CLOSE')),
    status varchar(16) NOT NULL CHECK (status IN ('QUEUED', 'SUBMITTING', 'SUBMITTED', 'COMPLETED', 'PARTIAL', 'REJECTED', 'HALTED')),
    idempotency_key varchar(64) NOT NULL UNIQUE,
    expected_nav_krw bigint,
    reasons text,
    created_at timestamptz NOT NULL,
    submitted_at timestamptz,
    completed_at timestamptz,
    row_version bigint NOT NULL DEFAULT 0
);
CREATE INDEX ix_strategy_batches_session_status ON public.strategy_batches(session_date, status);
CREATE INDEX ix_strategy_batches_strategy_created ON public.strategy_batches(strategy_id, created_at);
CREATE INDEX ix_strategy_batches_user_session ON public.strategy_batches(user_id, session_date);
-- One live batch per account, session and phase: a changed decision hash (new idempotency key)
-- must not queue every order a second time. Batches finalized as REJECTED do not count.
CREATE UNIQUE INDEX ux_strategy_batches_user_session_phase ON public.strategy_batches(user_id, session_date, phase)
    WHERE status <> 'REJECTED';

CREATE TABLE public.strategy_batch_orders (
    id uuid PRIMARY KEY,
    batch_id uuid NOT NULL REFERENCES public.strategy_batches(id),
    seq integer NOT NULL CHECK (seq > 0),
    stock_code varchar(32) NOT NULL,
    side varchar(8) NOT NULL CHECK (side IN ('BUY', 'SELL')),
    quantity bigint NOT NULL CHECK (quantity > 0),
    ref_price bigint NOT NULL CHECK (ref_price > 0),
    adv20_krw double precision,
    order_type varchar(16) NOT NULL,
    status varchar(24) NOT NULL CHECK (status IN ('PENDING', 'SUBMITTED', 'FILLED', 'PARTIALLY_FILLED', 'REJECTED', 'CANCELLED', 'UNKNOWN', 'SKIPPED')),
    broker_order_id varchar(64),
    order_org varchar(64),
    order_date date,
    filled_quantity bigint NOT NULL DEFAULT 0 CHECK (filled_quantity >= 0),
    average_fill_price numeric(20, 4),
    reject_reason varchar(255),
    kis_response text,
    submitted_at timestamptz,
    -- Set when a cancel request was sent; the cancel ratio counts these.
    cancel_requested_at timestamptz,
    updated_at timestamptz NOT NULL,
    UNIQUE (batch_id, stock_code, side),
    UNIQUE (batch_id, seq)
);
CREATE INDEX ix_strategy_batch_orders_status ON public.strategy_batch_orders(status);

CREATE TABLE public.strategy_status_snapshots (
    strategy_id varchar(128) PRIMARY KEY,
    payload text NOT NULL,
    generated_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL
);

CREATE TABLE public.account_equity_peaks (
    account_binding varchar(255) PRIMARY KEY,
    peak_equity_krw bigint NOT NULL CHECK (peak_equity_krw > 0),
    updated_at timestamptz NOT NULL
);

CREATE TABLE public.trading_halts (
    scope varchar(255) PRIMARY KEY,
    halted boolean NOT NULL,
    reason text,
    updated_at timestamptz NOT NULL
);
