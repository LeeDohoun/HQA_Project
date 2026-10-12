-- PostgreSQL CHECK accepts UNKNOWN; require a non-null status explicitly.
ALTER TABLE public.posts DROP CONSTRAINT posts_inquiry_status_check;
ALTER TABLE public.posts ADD CONSTRAINT posts_inquiry_status_check CHECK (
    (board_type = 'INQUIRY' AND inquiry_status IS NOT NULL
        AND inquiry_status IN ('OPEN', 'RESOLVED', 'REJECTED'))
    OR (board_type <> 'INQUIRY' AND inquiry_status IS NULL)
);

ALTER TABLE public.posts DROP CONSTRAINT posts_stock_code_matches_board;
ALTER TABLE public.posts ADD CONSTRAINT posts_stock_code_matches_board CHECK (
    (board_type = 'STOCK' AND stock_code IS NOT NULL AND length(btrim(stock_code)) > 0)
    OR (board_type <> 'STOCK' AND stock_code IS NULL)
);

ALTER TABLE public.posts ADD CONSTRAINT posts_inquiry_fields_match_board CHECK (
    board_type = 'INQUIRY' OR (target_post_id IS NULL AND admin_reply IS NULL)
);

-- Soft deletion must record both when it happened and who performed it.
ALTER TABLE public.posts ADD CONSTRAINT posts_deletion_fields_match CHECK (
    (deleted_at IS NULL) = (deleted_by IS NULL)
);
ALTER TABLE public.comments ADD CONSTRAINT comments_deletion_fields_match CHECK (
    (deleted_at IS NULL) = (deleted_by IS NULL)
);
