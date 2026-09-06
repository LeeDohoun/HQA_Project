ALTER TABLE public.trade_signal_executions
    ADD COLUMN IF NOT EXISTS order_id character varying(128),
    ADD COLUMN IF NOT EXISTS order_type character varying(32),
    ADD COLUMN IF NOT EXISTS submitted_quantity integer,
    ADD COLUMN IF NOT EXISTS filled_quantity integer,
    ADD COLUMN IF NOT EXISTS average_fill_price bigint,
    ADD COLUMN IF NOT EXISTS submitted_at timestamp with time zone,
    ADD COLUMN IF NOT EXISTS filled_at timestamp with time zone,
    ADD COLUMN IF NOT EXISTS order_expires_at timestamp with time zone;

CREATE INDEX IF NOT EXISTS ix_trade_signal_executions_order_status
    ON public.trade_signal_executions (status, order_expires_at);
