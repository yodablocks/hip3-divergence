-- ── hip3-divergence schema ──────────────────────────────
-- three-price divergence monitor for HIP-3 perps
-- pyth (truth) vs hl oracle (throttled) vs hl mark (book)

-- ── registry ────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS hip3_markets (
    coin           TEXT PRIMARY KEY,   -- e.g. "xyz:SPCX"
    dex            TEXT NOT NULL,
    display_name   TEXT,
    asset_class    TEXT,               -- equity | commodity | index | preipo
    deployer       TEXT,
    oracle_updater TEXT,
    max_leverage   INTEGER,
    pyth_feed_id   TEXT,               -- full hermes hex
    first_seen     TEXT NOT NULL,
    last_seen      TEXT NOT NULL
);

-- ── three-price series ──────────────────────────────────
CREATE TABLE IF NOT EXISTS hip3_prices (
    ts                TEXT NOT NULL,
    coin              TEXT NOT NULL,
    pyth_px           REAL,
    pyth_conf         REAL,
    pyth_publish_time INTEGER,
    pyth_stale_secs   REAL,
    hl_oracle_px      REAL,
    hl_mark_px        REAL,
    funding           REAL,
    open_interest     REAL,
    oracle_lag_bps    REAL,    -- (hl_oracle_px - pyth_px) / pyth_px * 1e4
    mark_premium_bps  REAL,    -- (hl_mark_px - hl_oracle_px) / hl_oracle_px * 1e4
    market_state      TEXT,    -- fresh | stale | closed
    PRIMARY KEY (ts, coin)
);

CREATE INDEX IF NOT EXISTS idx_hip3_prices_coin_ts
    ON hip3_prices (coin, ts);

-- ── threshold events ────────────────────────────────────
CREATE TABLE IF NOT EXISTS hip3_events (
    ts        TEXT NOT NULL,
    coin      TEXT NOT NULL,
    kind      TEXT NOT NULL,   -- oracle_lag | mark_premium | pyth_stale
    value     REAL,
    threshold REAL,
    PRIMARY KEY (ts, coin, kind)
);

CREATE INDEX IF NOT EXISTS idx_hip3_events_coin_ts
    ON hip3_events (coin, ts);

-- ── validity layer ───────────────────────────────────────

-- discovery bounds registry (one row per coin, upserted on startup and periodically)
CREATE TABLE IF NOT EXISTS hip3_bounds (
    coin            TEXT PRIMARY KEY,
    lower_bound_pct REAL,   -- e.g. -0.10 for -10%
    upper_bound_pct REAL,   -- e.g.  0.10 for +10%
    growth_mode     TEXT,   -- "enabled" | null (from universe entry growthMode field)
    source          TEXT,   -- "hardcoded" (API exposes no bound fields)
    updated_at      TEXT NOT NULL
);

-- per-tick validity flags
CREATE TABLE IF NOT EXISTS hip3_validity (
    ts                  TEXT NOT NULL,
    coin                TEXT NOT NULL,

    -- FM1: oracle catch-up detector
    oracle_catching_up  INTEGER NOT NULL DEFAULT 0,
    lag_direction       TEXT,    -- "up" | "down" | null
    lag_streak          INTEGER DEFAULT 0,

    -- FM2: oracle source classifier
    oracle_source       TEXT NOT NULL,  -- "pyth_live" | "seda_composite" | "unknown"

    -- FM3: bound proximity
    bound_proximity     REAL,    -- 0.0 (at lower) to 1.0 (at upper)
    bound_pinned        INTEGER NOT NULL DEFAULT 0,

    -- composite
    signal_valid        INTEGER NOT NULL DEFAULT 1,

    PRIMARY KEY (ts, coin)
);

CREATE INDEX IF NOT EXISTS idx_hip3_validity_coin_ts
    ON hip3_validity (coin, ts);
