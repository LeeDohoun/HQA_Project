-- =====================================================================
-- V3__stocks.sql
--
-- Replaces the hardcoded watchlist.json catalog with a real stocks table
-- populated from KIS's kospi_code.mst / kosdaq_code.mst dumps.
--
-- pg_trgm + GIN indexes power substring matching on Korean and English
-- names (e.g. "삼성" → "삼성전자", "samsung" → "Samsung Electronics").
-- =====================================================================

CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE IF NOT EXISTS public.stocks (
    code                 VARCHAR(12) PRIMARY KEY,
    name_ko              TEXT NOT NULL,
    name_en              TEXT,
    market               VARCHAR(16) NOT NULL,
    -- True while the stock is listed and tradable on the exchange. Flipped
    -- to false when KIS's daily master no longer includes the code (delisted
    -- / suspended). Rows are never deleted so historical analyses keep their
    -- foreign-key references.
    is_tradable          BOOLEAN NOT NULL DEFAULT TRUE,
    -- Manual whitelist for auto-trading. Default false so a newly ingested
    -- stock can never be auto-traded until an operator explicitly opts it in.
    auto_trade_eligible  BOOLEAN NOT NULL DEFAULT FALSE,
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_stocks_name_ko_trgm
    ON public.stocks USING GIN (lower(name_ko) gin_trgm_ops);

CREATE INDEX IF NOT EXISTS ix_stocks_name_en_trgm
    ON public.stocks USING GIN (lower(coalesce(name_en, '')) gin_trgm_ops);

CREATE INDEX IF NOT EXISTS ix_stocks_market
    ON public.stocks (market) WHERE is_tradable;

CREATE INDEX IF NOT EXISTS ix_stocks_auto_trade
    ON public.stocks (code) WHERE auto_trade_eligible AND is_tradable;
