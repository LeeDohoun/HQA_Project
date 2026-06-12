CREATE TABLE IF NOT EXISTS watchlist_items (
    id VARCHAR(36) PRIMARY KEY,
    user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    stock_code VARCHAR(12) NOT NULL,
    stock_name TEXT NOT NULL,
    market VARCHAR(32) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
    CONSTRAINT uk_watchlist_user_stock UNIQUE (user_id, stock_code)
);

CREATE INDEX IF NOT EXISTS ix_watchlist_user_created_at
    ON watchlist_items(user_id, created_at DESC);
