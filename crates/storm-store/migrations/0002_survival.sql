-- Survival-strategy schema (sub-plan 3). pump.fun graduated-token tracking:
-- a graduation is detected, a feature snapshot is taken at T0+window, and an
-- outcome is recorded once the outcome window matures.

-- graduations: one row per discovered pump.fun graduation.
-- `mint` is UNIQUE — the idempotency key; re-discovering a token never inserts
-- a second row. `status` drives the collector lifecycle:
--   'pending_snapshot' -> 'snapshot_done' -> 'outcome_done'.
CREATE TABLE IF NOT EXISTS graduations (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    mint                   TEXT NOT NULL UNIQUE,
    pool_address           TEXT NOT NULL,
    bonding_curve_address  TEXT NOT NULL,
    graduation_slot        INTEGER NOT NULL,   -- getSlot value at detection (coarse provenance)
    detected_at            INTEGER NOT NULL,   -- Unix seconds; the collector's T0 approximation
    status                 TEXT NOT NULL DEFAULT 'pending_snapshot',
    created_at             INTEGER NOT NULL DEFAULT (unixepoch())
);

CREATE INDEX IF NOT EXISTS graduations_status ON graduations(status);

-- feature_snapshots: the storm_features FeatureVector for a graduation,
-- captured at T0+snapshot_window. One snapshot per graduation.
-- All raw u64 on-chain values are stored as TEXT (SQLite max integer is i64).
CREATE TABLE IF NOT EXISTS feature_snapshots (
    id                        INTEGER PRIMARY KEY AUTOINCREMENT,
    graduation_id             INTEGER NOT NULL UNIQUE REFERENCES graduations(id),
    snapshot_at               INTEGER NOT NULL,   -- Unix seconds the snapshot was taken
    -- liquidity group
    base_reserve              TEXT NOT NULL,      -- u64
    quote_reserve             TEXT NOT NULL,      -- u64
    lp_burned                 INTEGER NOT NULL,   -- 0 | 1
    pool_supply_fraction      REAL NOT NULL,
    -- contract-flags group
    mint_authority_present    INTEGER NOT NULL,   -- 0 | 1
    freeze_authority_present  INTEGER NOT NULL,   -- 0 | 1
    -- holder-distribution group
    visible_holder_count      INTEGER NOT NULL,
    top10_concentration       REAL NOT NULL,
    top20_concentration       REAL NOT NULL,
    creator_bag_fraction      REAL NOT NULL,
    -- bonding-curve-snapshot group
    curve_graduated           INTEGER NOT NULL,   -- 0 | 1
    curve_real_sol_reserves   TEXT NOT NULL,      -- u64
    curve_real_token_reserves TEXT NOT NULL,      -- u64
    curve_token_total_supply  TEXT NOT NULL,      -- u64
    -- deployer-signal group
    capped_signature_count    INTEGER NOT NULL,
    signature_count_capped    INTEGER NOT NULL,   -- 0 | 1
    oldest_signature_age_secs INTEGER,            -- nullable: None when unknown
    created_at                INTEGER NOT NULL DEFAULT (unixepoch())
);

-- outcomes: the recorded outcome for a graduation, taken once the outcome
-- window matured. One outcome per graduation.
CREATE TABLE IF NOT EXISTS outcomes (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    graduation_id  INTEGER NOT NULL UNIQUE REFERENCES graduations(id),
    outcome_at     INTEGER NOT NULL,   -- Unix seconds the outcome was checked
    survived       INTEGER NOT NULL,   -- 0 = rugged, 1 = survived
    base_reserve   TEXT NOT NULL,      -- u64: pool base reserve at the check
    quote_reserve  TEXT NOT NULL,      -- u64: pool quote (wSOL) reserve at the check
    created_at     INTEGER NOT NULL DEFAULT (unixepoch())
);

-- collector_state: tiny key/value heartbeat / progress table for the daemon.
CREATE TABLE IF NOT EXISTS collector_state (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at INTEGER NOT NULL DEFAULT (unixepoch())
);
