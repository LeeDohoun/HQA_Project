-- =====================================================================
-- V5__trade_signals.sql
--
-- AI 서버가 생성한 사용자별 주도주 매매신호와 백엔드 집행 결과를 저장한다.
-- =====================================================================

CREATE TABLE IF NOT EXISTS public.trade_signals (
    id character varying(255) NOT NULL,
    user_id character varying(255) NOT NULL,
    source character varying(255) NOT NULL,
    strategy_profile character varying(255),
    theme_key character varying(255),
    theme_name character varying(255),
    stock_code character varying(255) NOT NULL,
    stock_name character varying(255) NOT NULL,
    action character varying(255) NOT NULL,
    leader_score integer,
    confidence integer,
    risk_level character varying(255),
    position_size character varying(255),
    signal_price bigint,
    stop_loss character varying(255),
    reason text,
    status character varying(255) NOT NULL,
    reject_reason character varying(255),
    raw_payload text,
    expires_at timestamp(6) with time zone,
    created_at timestamp(6) with time zone,
    updated_at timestamp(6) with time zone,
    executed_at timestamp(6) with time zone,
    CONSTRAINT trade_signals_pkey PRIMARY KEY (id)
);

CREATE TABLE IF NOT EXISTS public.trade_signal_executions (
    id character varying(255) NOT NULL,
    signal_id character varying(255) NOT NULL,
    user_id character varying(255) NOT NULL,
    status character varying(255) NOT NULL,
    quantity integer,
    order_price bigint,
    current_price bigint,
    price_drift_pct double precision,
    reject_reason character varying(255),
    kis_response text,
    executed_at timestamp(6) with time zone,
    CONSTRAINT trade_signal_executions_pkey PRIMARY KEY (id)
);

CREATE INDEX IF NOT EXISTS ix_trade_signals_status_created
    ON public.trade_signals (status, created_at);

CREATE INDEX IF NOT EXISTS ix_trade_signals_user_created
    ON public.trade_signals (user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS ix_trade_signal_executions_signal
    ON public.trade_signal_executions (signal_id);
