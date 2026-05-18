-- =====================================================================
-- V2__align_schema_with_entities.sql
--
-- The baseline (V1) reflects whatever Hibernate ddl-auto: update happened
-- to produce — including some types that don't match the JPA entities
-- anymore. This migration brings the schema in line with the entities so
-- that Hibernate's `validate` mode passes cleanly going forward.
--
-- Changes:
--   1. stock_cache.data : json NOT NULL → text
--      Entity stores a JSON-serialised string (@Column(columnDefinition="TEXT")).
--      Old `json` typed column required all writes to be valid JSON, which
--      we no longer enforce at the DB level. Use json::text to preserve
--      existing rows.
--
--   2. error_logs.detail : oid → text
--      Hibernate 6 maps @Lob String to `oid` (Large Object), but the code
--      uses it as a plain text payload. Convert via lo_get(detail) so
--      existing large-object rows are pulled into normal text.
--      If lo_get can't find the LOB (older orphan rows), fall back to NULL.
-- =====================================================================

-- 1) stock_cache.data : json → text
ALTER TABLE public.stock_cache
    ALTER COLUMN data TYPE text USING data::text;

-- 2) error_logs.detail : oid → text
--    Read the large object content, then drop the oid reference.
ALTER TABLE public.error_logs
    ALTER COLUMN detail TYPE text
    USING CASE
        WHEN detail IS NULL THEN NULL
        ELSE convert_from(lo_get(detail), 'UTF8')
    END;
