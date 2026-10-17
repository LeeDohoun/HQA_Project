-- Read-only checks. Every count should be zero.
SELECT 'recommendation_count_mismatch' AS check_name, count(*) AS violations
FROM posts p WHERE p.recommendation_count <> (SELECT count(*) FROM post_recommendations r WHERE r.post_id=p.id)
UNION ALL
SELECT 'self_or_inquiry_recommendation', count(*) FROM post_recommendations r JOIN posts p ON p.id=r.post_id
WHERE r.user_id=p.user_id OR p.board_type='INQUIRY'
UNION ALL
SELECT 'point_board_mismatch', count(*) FROM community_point_events e JOIN posts p ON p.id=e.post_id
WHERE e.board_type<>p.board_type
UNION ALL
SELECT 'point_recipient_mismatch', count(*) FROM community_point_events e JOIN posts p ON p.id=e.post_id
LEFT JOIN comments c ON c.id=e.comment_id
WHERE (e.reason='COMMENT_CREATED' AND (c.user_id<>e.user_id OR c.post_id<>e.post_id))
   OR (e.reason IN ('POST_CREATED','RECOMMEND_RECEIVED') AND p.user_id<>e.user_id)
UNION ALL
SELECT 'active_points_for_deleted_content', count(*) FROM community_point_events e JOIN posts p ON p.id=e.post_id
LEFT JOIN comments c ON c.id=e.comment_id
WHERE e.reversed_at IS NULL AND (p.deleted_at IS NOT NULL OR c.deleted_at IS NOT NULL)
UNION ALL
SELECT 'missing_post_points', count(*) FROM posts p WHERE p.board_type IN ('FREE','STOCK') AND p.deleted_at IS NULL
AND NOT EXISTS(SELECT 1 FROM community_point_events e WHERE e.post_id=p.id AND e.reason='POST_CREATED' AND e.reversed_at IS NULL)
UNION ALL
SELECT 'missing_comment_points', count(*) FROM comments c JOIN posts p ON p.id=c.post_id
WHERE p.board_type IN ('FREE','STOCK') AND p.deleted_at IS NULL AND c.deleted_at IS NULL
AND NOT EXISTS(SELECT 1 FROM community_point_events e WHERE e.comment_id=c.id AND e.reason='COMMENT_CREATED' AND e.reversed_at IS NULL)
UNION ALL
SELECT 'missing_recommendation_points', count(*) FROM post_recommendations r JOIN posts p ON p.id=r.post_id
WHERE p.deleted_at IS NULL AND NOT EXISTS(SELECT 1 FROM community_point_events e WHERE e.recommendation_id=r.id AND e.reason='RECOMMEND_RECEIVED' AND e.reversed_at IS NULL);
