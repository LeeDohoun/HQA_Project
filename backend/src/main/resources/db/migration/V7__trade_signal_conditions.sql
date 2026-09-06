ALTER TABLE public.trade_signals
    ADD COLUMN IF NOT EXISTS trade_plan_json text,
    ADD COLUMN IF NOT EXISTS condition_payload text,
    ADD COLUMN IF NOT EXISTS idempotency_key character varying(512);

CREATE UNIQUE INDEX IF NOT EXISTS ux_trade_signals_idempotency_key
    ON public.trade_signals (idempotency_key)
    WHERE idempotency_key IS NOT NULL AND idempotency_key <> '';
