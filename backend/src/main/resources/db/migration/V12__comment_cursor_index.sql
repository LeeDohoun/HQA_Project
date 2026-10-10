-- Match the stable ordering used by bounded comment cursor queries.
CREATE INDEX ix_comments_post_cursor ON public.comments(post_id, created_at, id)
    WHERE deleted_at IS NULL;
