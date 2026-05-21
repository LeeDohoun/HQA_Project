-- =====================================================================
-- V4__user_secrets_text.sql
--
-- KIS credentials are now AES-GCM encrypted (Base64 of IV+ciphertext+tag,
-- prefixed with "enc:v1:"). A typical 180-char KIS app secret becomes
-- ~287 chars after encryption, overflowing the original varchar(255) and
-- causing INSERT/UPDATE to fail with SQLSTATE 22001
-- ("value too long for type character varying(255)").
--
-- Widen all encrypted KIS columns to TEXT. The entity is updated in
-- lockstep with columnDefinition="text" so Hibernate's validate mode
-- continues to pass.
-- =====================================================================

ALTER TABLE public.user_secrets
    ALTER COLUMN kis_app_key TYPE text,
    ALTER COLUMN kis_app_secret TYPE text,
    ALTER COLUMN kis_account_no TYPE text,
    ALTER COLUMN kis_account_product_code TYPE text;
