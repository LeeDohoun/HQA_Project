-- Read-only audit. Every violations value should be zero.
BEGIN READ ONLY;
SELECT 'comment_count_mismatch' AS issue, count(*) AS violations FROM posts p
WHERE p.comment_count <> (SELECT count(*) FROM comments c WHERE c.post_id=p.id AND c.deleted_at IS NULL)
UNION ALL
SELECT 'invalid_inquiry_status', count(*) FROM posts
WHERE board_type='INQUIRY' AND (inquiry_status IS NULL OR inquiry_status NOT IN ('OPEN','RESOLVED','REJECTED'))
UNION ALL
SELECT 'invalid_stock_code', count(*) FROM posts
WHERE (board_type='STOCK' AND (stock_code IS NULL OR btrim(stock_code)='')) OR (board_type<>'STOCK' AND stock_code IS NOT NULL)
UNION ALL
SELECT 'invalid_inquiry_fields', count(*) FROM posts
WHERE board_type<>'INQUIRY' AND (inquiry_status IS NOT NULL OR target_post_id IS NOT NULL OR admin_reply IS NOT NULL)
UNION ALL
SELECT 'invalid_inquiry_target', count(*) FROM posts p JOIN posts target ON target.id=p.target_post_id
WHERE p.board_type<>'INQUIRY' OR p.user_id<>target.user_id OR target.board_type='INQUIRY'
UNION ALL
SELECT 'orphan_post', count(*) FROM posts p LEFT JOIN users u ON u.id=p.user_id WHERE u.id IS NULL
UNION ALL
SELECT 'orphan_comment', count(*) FROM comments c LEFT JOIN posts p ON p.id=c.post_id LEFT JOIN users u ON u.id=c.user_id WHERE p.id IS NULL OR u.id IS NULL
UNION ALL
SELECT 'invalid_post_deletion', count(*) FROM posts WHERE (deleted_at IS NULL)<>(deleted_by IS NULL)
UNION ALL
SELECT 'invalid_comment_deletion', count(*) FROM comments WHERE (deleted_at IS NULL)<>(deleted_by IS NULL);
COMMIT;
