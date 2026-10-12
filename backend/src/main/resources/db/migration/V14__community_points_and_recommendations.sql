ALTER TABLE public.users ADD COLUMN nickname varchar(255);
UPDATE public.users SET nickname = user_id;
ALTER TABLE public.users ALTER COLUMN nickname SET NOT NULL;
ALTER TABLE public.users ADD CONSTRAINT users_nickname_unique UNIQUE (nickname);
ALTER TABLE public.users ADD CONSTRAINT users_nickname_not_blank CHECK (length(btrim(nickname)) > 0);

ALTER TABLE public.posts ADD COLUMN recommendation_count integer NOT NULL DEFAULT 0;
ALTER TABLE public.posts ADD CONSTRAINT posts_recommendation_count_check CHECK (recommendation_count >= 0);
ALTER TABLE public.posts ADD CONSTRAINT posts_inquiry_no_recommendations CHECK (board_type <> 'INQUIRY' OR recommendation_count = 0);

CREATE TABLE public.post_recommendations (
    id varchar(255) PRIMARY KEY,
    post_id varchar(255) NOT NULL REFERENCES public.posts(id),
    user_id varchar(255) NOT NULL REFERENCES public.users(id),
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT post_recommendations_one_per_user UNIQUE (post_id, user_id)
);

-- The ledger is authoritative. Totals are sums, so concurrent awards cannot overwrite one another.
CREATE TABLE public.community_point_events (
    id varchar(255) PRIMARY KEY,
    user_id varchar(255) NOT NULL REFERENCES public.users(id),
    board_type varchar(16) NOT NULL CHECK (board_type IN ('FREE', 'STOCK')),
    reason varchar(32) NOT NULL,
    points integer NOT NULL,
    post_id varchar(255) NOT NULL REFERENCES public.posts(id),
    comment_id varchar(255) REFERENCES public.comments(id),
    recommendation_id varchar(255) REFERENCES public.post_recommendations(id),
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    reversed_at timestamptz,
    CONSTRAINT community_point_event_source_check CHECK (
        (reason = 'POST_CREATED' AND points = 10 AND comment_id IS NULL AND recommendation_id IS NULL)
        OR (reason = 'COMMENT_CREATED' AND points = 2 AND comment_id IS NOT NULL AND recommendation_id IS NULL)
        OR (reason = 'RECOMMEND_RECEIVED' AND points = 5 AND comment_id IS NULL AND recommendation_id IS NOT NULL)
    )
);
CREATE UNIQUE INDEX community_points_one_post_award ON public.community_point_events(post_id) WHERE reason = 'POST_CREATED';
CREATE UNIQUE INDEX community_points_one_comment_award ON public.community_point_events(comment_id) WHERE reason = 'COMMENT_CREATED';
CREATE UNIQUE INDEX community_points_one_recommendation_award ON public.community_point_events(recommendation_id) WHERE reason = 'RECOMMEND_RECEIVED';
CREATE INDEX community_points_active_user_board ON public.community_point_events(user_id, board_type) WHERE reversed_at IS NULL;
CREATE INDEX community_points_user_recent ON public.community_point_events(user_id, created_at DESC, id DESC);
CREATE INDEX community_points_post ON public.community_point_events(post_id) WHERE reversed_at IS NULL;

-- Give existing, visible contributions the same credit as newly written ones.
INSERT INTO public.community_point_events(id, user_id, board_type, reason, points, post_id, created_at)
SELECT md5('post:' || id), user_id, board_type, 'POST_CREATED', 10, id, created_at
FROM public.posts WHERE board_type IN ('FREE', 'STOCK') AND deleted_at IS NULL;
INSERT INTO public.community_point_events(id, user_id, board_type, reason, points, post_id, comment_id, created_at)
SELECT md5('comment:' || c.id), c.user_id, p.board_type, 'COMMENT_CREATED', 2, p.id, c.id, c.created_at
FROM public.comments c JOIN public.posts p ON p.id = c.post_id
WHERE p.board_type IN ('FREE', 'STOCK') AND p.deleted_at IS NULL AND c.deleted_at IS NULL;
