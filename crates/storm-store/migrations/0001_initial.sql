-- pools: one row per discovered DEX pool
CREATE TABLE IF NOT EXISTS pools (
    address           TEXT PRIMARY KEY,
    program_id        TEXT NOT NULL,
    dex               TEXT NOT NULL,            -- 'raydium-amm-v4' | 'orca-whirlpool' | …
    token_a_mint      TEXT NOT NULL,
    token_b_mint      TEXT NOT NULL,
    token_a_decimals  INTEGER NOT NULL,
    token_b_decimals  INTEGER NOT NULL,
    discovered_at     INTEGER NOT NULL DEFAULT (unixepoch())
);

-- prices: append-only price snapshots, foreign-keyed to pools.
CREATE TABLE IF NOT EXISTS prices (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    pool_address    TEXT NOT NULL REFERENCES pools(address),
    spot_price      TEXT NOT NULL,    -- Decimal stringified for portability
    reserve_a_raw   TEXT NOT NULL,    -- u64 stringified (sqlite max integer is i64)
    reserve_b_raw   TEXT NOT NULL,
    captured_at     INTEGER NOT NULL DEFAULT (unixepoch())
);

CREATE INDEX IF NOT EXISTS prices_pool_captured_at
    ON prices(pool_address, captured_at DESC);
