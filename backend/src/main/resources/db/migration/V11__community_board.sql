-- 커뮤니티 게시판: 자유(FREE), 종목토론(STOCK), 문의(INQUIRY)를 한 테이블에서 board_type으로 구분한다.
-- 삭제는 soft delete만 사용한다. 댓글이 달린 글을 물리 삭제하면 댓글이 고아가 되기 때문이다.
CREATE TABLE public.posts (
    id varchar(255) NOT NULL,
    board_type varchar(16) NOT NULL,
    user_id varchar(255) NOT NULL,
    title varchar(160) NOT NULL,
    content text NOT NULL,
    -- STOCK 게시판 전용. 종목별 필터링에 쓴다.
    stock_code varchar(12),
    -- 살아있는 댓글 수. 삭제 제한 판정에서 매번 count 쿼리를 돌리지 않으려고 비정규화해 둔다.
    comment_count integer NOT NULL DEFAULT 0,
    -- INQUIRY 전용. 삭제 요청이면 대상 글을 가리킨다.
    target_post_id varchar(255),
    inquiry_status varchar(16),
    admin_reply text,
    deleted_at timestamptz,
    deleted_by varchar(255),
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    CONSTRAINT pk_posts PRIMARY KEY (id),
    CONSTRAINT fk_posts_user FOREIGN KEY (user_id) REFERENCES public.users(id),
    CONSTRAINT fk_posts_target FOREIGN KEY (target_post_id) REFERENCES public.posts(id),
    CONSTRAINT posts_board_type_check CHECK (board_type IN ('FREE', 'STOCK', 'INQUIRY')),
    CONSTRAINT posts_comment_count_check CHECK (comment_count >= 0),
    -- 종목토론방 글은 종목이 반드시 있고, 다른 게시판 글은 종목을 가질 수 없다.
    -- 한쪽만 걸면 자유글에 stock_code가 들어가 종목별 인덱스를 오염시킨다.
    CONSTRAINT posts_stock_code_matches_board CHECK (
        (board_type = 'STOCK' AND stock_code IS NOT NULL)
        OR (board_type <> 'STOCK' AND stock_code IS NULL)
    ),
    -- 문의글은 항상 처리 상태를 가진다. 그 외 게시판은 가지지 않는다.
    CONSTRAINT posts_inquiry_status_check CHECK (
        (board_type = 'INQUIRY' AND inquiry_status IN ('OPEN', 'RESOLVED', 'REJECTED'))
        OR (board_type <> 'INQUIRY' AND inquiry_status IS NULL)
    )
);

CREATE TABLE public.comments (
    id varchar(255) NOT NULL,
    post_id varchar(255) NOT NULL,
    user_id varchar(255) NOT NULL,
    content text NOT NULL,
    deleted_at timestamptz,
    deleted_by varchar(255),
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    CONSTRAINT pk_comments PRIMARY KEY (id),
    CONSTRAINT fk_comments_post FOREIGN KEY (post_id) REFERENCES public.posts(id),
    CONSTRAINT fk_comments_user FOREIGN KEY (user_id) REFERENCES public.users(id)
);

-- 목록 조회는 항상 "게시판별 + 미삭제 + 최신순"이라 부분 인덱스로 충분하다.
CREATE INDEX ix_posts_board_created ON public.posts(board_type, created_at DESC) WHERE deleted_at IS NULL;
CREATE INDEX ix_posts_stock_created ON public.posts(stock_code, created_at DESC) WHERE deleted_at IS NULL AND board_type = 'STOCK';
CREATE INDEX ix_posts_user ON public.posts(user_id, created_at DESC);
CREATE INDEX ix_posts_target ON public.posts(target_post_id) WHERE target_post_id IS NOT NULL;
CREATE INDEX ix_comments_post_created ON public.comments(post_id, created_at) WHERE deleted_at IS NULL;
