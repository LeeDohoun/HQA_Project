-- =====================================================================
-- V1__baseline.sql
--
-- Snapshot of the production-ish schema at the moment Flyway was introduced.
-- For existing databases (which already had these objects created by
-- Hibernate ddl-auto: update), this script is NOT executed — Flyway marks
-- it as the baseline via spring.flyway.baseline-on-migrate=true.
--
-- For brand new databases (CI, fresh dev machine), Flyway runs this end
-- to end to recreate the same starting point. Keep that path working: all
-- statements below must be idempotent (IF NOT EXISTS) and rerunnable.
--
-- Generated from pg_dump --schema-only of the hqa database, then cleaned
-- (no \restrict, no ownership/privilege noise).
-- =====================================================================

-- ── Types ────────────────────────────────────────────────────────────
DO $$ BEGIN
    CREATE TYPE public.userrole AS ENUM ('ADMIN', 'USER');
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

-- ── Tables ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.analysis_records (
    id character varying(255) NOT NULL,
    user_id character varying(255),
    task_id character varying(255),
    stock_name character varying(255) NOT NULL,
    stock_code character varying(255) NOT NULL,
    mode character varying(255),
    max_retries integer,
    status character varying(255),
    analyst_result text,
    quant_result text,
    chartist_result text,
    final_decision text,
    research_quality character varying(255),
    quality_warnings text,
    total_score double precision,
    action character varying(255),
    confidence double precision,
    errors text,
    created_at timestamp without time zone,
    completed_at timestamp without time zone,
    duration_seconds double precision,
    CONSTRAINT analysis_records_pkey PRIMARY KEY (id),
    CONSTRAINT ix_analysis_task_id UNIQUE (task_id)
);

CREATE TABLE IF NOT EXISTS public.chat_sessions (
    id character varying(36) NOT NULL,
    user_id character varying(36),
    session_id character varying(36),
    is_active boolean,
    created_at timestamp without time zone,
    updated_at timestamp without time zone,
    CONSTRAINT chat_sessions_pkey PRIMARY KEY (id)
);

CREATE TABLE IF NOT EXISTS public.chat_messages (
    id character varying(36) NOT NULL,
    session_id character varying(36) NOT NULL,
    role character varying(20) NOT NULL,
    content text NOT NULL,
    intent character varying(50),
    stocks json,
    created_at timestamp without time zone,
    CONSTRAINT chat_messages_pkey PRIMARY KEY (id)
);

CREATE TABLE IF NOT EXISTS public.error_logs (
    id character varying(255) NOT NULL,
    created_at timestamp(6) with time zone,
    detail oid,
    message character varying(255) NOT NULL,
    source character varying(255) NOT NULL,
    stock_code character varying(255),
    user_id character varying(255),
    CONSTRAINT error_logs_pkey PRIMARY KEY (id)
);

CREATE TABLE IF NOT EXISTS public.stock_cache (
    id character varying(255) NOT NULL,
    stock_code character varying(255) NOT NULL,
    stock_name character varying(255) NOT NULL,
    data_type character varying(255) NOT NULL,
    data json NOT NULL,
    expires_at timestamp without time zone NOT NULL,
    created_at timestamp without time zone,
    CONSTRAINT stock_cache_pkey PRIMARY KEY (id)
);

CREATE TABLE IF NOT EXISTS public.users (
    id character varying(255) NOT NULL,
    email character varying(255),
    name character varying(100),
    role character varying(255) NOT NULL,
    is_active boolean,
    created_at timestamp without time zone,
    updated_at timestamp without time zone,
    first_name character varying(255) NOT NULL,
    last_name character varying(255) NOT NULL,
    password character varying(255) NOT NULL,
    user_id character varying(255) NOT NULL,
    auto_trade_enabled boolean DEFAULT false NOT NULL,
    CONSTRAINT users_pkey PRIMARY KEY (id),
    CONSTRAINT uk6efs5vmce86ymf5q7lmvn2uuf UNIQUE (user_id),
    CONSTRAINT users_email_key UNIQUE (email)
);

CREATE TABLE IF NOT EXISTS public.user_credentials (
    id character varying(36) NOT NULL,
    user_id character varying(36) NOT NULL,
    google_api_key character varying(255),
    kis_app_key character varying(255),
    kis_app_secret character varying(512),
    kis_account_no character varying(50),
    created_at timestamp without time zone,
    updated_at timestamp without time zone,
    CONSTRAINT user_credentials_pkey PRIMARY KEY (id),
    CONSTRAINT user_credentials_user_id_key UNIQUE (user_id)
);

CREATE TABLE IF NOT EXISTS public.user_preferences (
    id character varying(255) NOT NULL,
    birth_date date NOT NULL,
    created_at timestamp(6) with time zone,
    investment_experience character varying(255) NOT NULL,
    investment_goal character varying(255) NOT NULL,
    investment_period_months integer NOT NULL,
    investment_type character varying(255) NOT NULL,
    leverage_allowed boolean NOT NULL,
    loss_action character varying(255) NOT NULL,
    loss_tolerance character varying(255) NOT NULL,
    monthly_investment bigint NOT NULL,
    occupation_type character varying(255) NOT NULL,
    target_return_rate integer NOT NULL,
    total_assets bigint NOT NULL,
    updated_at timestamp(6) with time zone,
    volatility_tolerance character varying(255) NOT NULL,
    user_id character varying(255) NOT NULL,
    CONSTRAINT user_preferences_pkey PRIMARY KEY (id),
    CONSTRAINT ukqy8dkrkc8b34dcgwoq2km43rd UNIQUE (user_id),
    CONSTRAINT user_preferences_investment_experience_check CHECK (((investment_experience)::text = ANY ((ARRAY['NONE'::character varying, 'BEGINNER'::character varying, 'INTERMEDIATE'::character varying, 'EXPERIENCED'::character varying, 'EXPERT'::character varying])::text[]))),
    CONSTRAINT user_preferences_investment_goal_check CHECK (((investment_goal)::text = ANY ((ARRAY['RETIREMENT'::character varying, 'HOME_PURCHASE'::character varying, 'VEHICLE_PURCHASE'::character varying, 'DEBT_REPAYMENT'::character varying, 'EDUCATION_FUND'::character varying, 'EMERGENCY_FUND'::character varying, 'TRAVEL'::character varying, 'WEDDING'::character varying, 'BUSINESS_STARTUP'::character varying, 'ASSET_GROWTH'::character varying, 'PASSIVE_INCOME'::character varying, 'TAX_OPTIMIZATION'::character varying, 'DONATION_FUND'::character varying, 'OTHER'::character varying])::text[]))),
    CONSTRAINT user_preferences_investment_type_check CHECK (((investment_type)::text = ANY ((ARRAY['STABLE'::character varying, 'MID_STABLE'::character varying, 'NEUTRAL'::character varying, 'MID_AGGRESSIVE'::character varying, 'AGGRESSIVE'::character varying])::text[]))),
    CONSTRAINT user_preferences_loss_action_check CHECK (((loss_action)::text = ANY ((ARRAY['SELL_IMMEDIATELY'::character varying, 'HOLD'::character varying, 'BUY_MORE'::character varying, 'SEEK_ADVICE'::character varying])::text[]))),
    CONSTRAINT user_preferences_loss_tolerance_check CHECK (((loss_tolerance)::text = ANY ((ARRAY['LEVEL_1'::character varying, 'LEVEL_2'::character varying, 'LEVEL_3'::character varying, 'LEVEL_4'::character varying, 'LEVEL_5'::character varying, 'LEVEL_6'::character varying])::text[]))),
    CONSTRAINT user_preferences_occupation_type_check CHECK (((occupation_type)::text = ANY ((ARRAY['EMPLOYEE'::character varying, 'BUSINESS_OWNER'::character varying, 'FREELANCER'::character varying, 'PUBLIC_SERVANT'::character varying, 'PROFESSIONAL'::character varying, 'FINANCE_WORKER'::character varying, 'RESEARCHER'::character varying, 'INFLUENCER'::character varying, 'STUDENT'::character varying, 'HOMEMAKER'::character varying, 'RETIRED'::character varying, 'UNEMPLOYED'::character varying, 'OTHER'::character varying])::text[]))),
    CONSTRAINT user_preferences_volatility_tolerance_check CHECK (((volatility_tolerance)::text = ANY ((ARRAY['VERY_LOW'::character varying, 'LOW'::character varying, 'MEDIUM'::character varying, 'HIGH'::character varying, 'VERY_HIGH'::character varying])::text[])))
);

CREATE TABLE IF NOT EXISTS public.user_secrets (
    id character varying(255) NOT NULL,
    created_at timestamp(6) with time zone,
    kis_account_no character varying(255),
    kis_app_key character varying(255),
    kis_app_secret character varying(255),
    updated_at timestamp(6) with time zone,
    user_id character varying(255) NOT NULL,
    kis_account_product_code character varying(255),
    kis_is_real boolean DEFAULT false NOT NULL,
    CONSTRAINT user_secrets_pkey PRIMARY KEY (id),
    CONSTRAINT uks2aw59680b30e26yj254wps8p UNIQUE (user_id)
);

-- ── Foreign keys ─────────────────────────────────────────────────────
-- Add only if missing; for fresh DBs these create the FK, for existing
-- DBs the duplicate_object guard makes the migration idempotent.
DO $$ BEGIN
    ALTER TABLE public.analysis_records
        ADD CONSTRAINT analysis_records_user_id_fkey
        FOREIGN KEY (user_id) REFERENCES public.users(id);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    ALTER TABLE public.chat_messages
        ADD CONSTRAINT chat_messages_session_id_fkey
        FOREIGN KEY (session_id) REFERENCES public.chat_sessions(id);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    ALTER TABLE public.chat_sessions
        ADD CONSTRAINT chat_sessions_user_id_fkey
        FOREIGN KEY (user_id) REFERENCES public.users(id);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    ALTER TABLE public.user_secrets
        ADD CONSTRAINT fk9qvjo1o7y1p8pbhvoaglnwyq1
        FOREIGN KEY (user_id) REFERENCES public.users(id);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    ALTER TABLE public.user_preferences
        ADD CONSTRAINT fkepakpib0qnm82vmaiismkqf88
        FOREIGN KEY (user_id) REFERENCES public.users(id);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    ALTER TABLE public.user_credentials
        ADD CONSTRAINT user_credentials_user_id_fkey
        FOREIGN KEY (user_id) REFERENCES public.users(id);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- ── Indexes ──────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS ix_analysis_records_status ON public.analysis_records USING btree (status);
CREATE INDEX IF NOT EXISTS ix_analysis_records_stock_code ON public.analysis_records USING btree (stock_code);
CREATE UNIQUE INDEX IF NOT EXISTS ix_analysis_records_task_id ON public.analysis_records USING btree (task_id);
CREATE INDEX IF NOT EXISTS ix_analysis_stock_date ON public.analysis_records USING btree (stock_code, created_at);
CREATE INDEX IF NOT EXISTS ix_chat_session_time ON public.chat_messages USING btree (session_id, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS ix_chat_sessions_session_id ON public.chat_sessions USING btree (session_id);
CREATE INDEX IF NOT EXISTS ix_el_created_at ON public.error_logs USING btree (created_at);
CREATE INDEX IF NOT EXISTS ix_el_user_id ON public.error_logs USING btree (user_id);
CREATE INDEX IF NOT EXISTS ix_stock_cache_stock_code ON public.stock_cache USING btree (stock_code);
CREATE INDEX IF NOT EXISTS ix_stock_cache_type ON public.stock_cache USING btree (stock_code, data_type);
