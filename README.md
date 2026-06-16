# HIP-3 Divergence Monitor

Records and validates three prices for HIP-3 equity and commodity perps on Hyperliquid's `xyz` dex. Detects three distinct failure modes that make oracle-lag signals unreliable, and emits a `signal_valid` flag per tick.

## The three-price model

Every HIP-3 perp has three prices in flight simultaneously:

| Name | Source | Description |
|---|---|---|
| `pyth_px` | Pyth Hermes / Pyth Lazer | Real-world truth -- the live Pyth feed price |
| `hl_oracle_px` | HL info API | Pyth pushed through the xyz updater, subject to a ~1% throttle |
| `hl_mark_px` | HL info API | The traded price on the native book |

Two spreads carry the signal:

```
oracle_lag_bps   = (hl_oracle_px - pyth_px)      / pyth_px      * 10_000
mark_premium_bps = (hl_mark_px   - hl_oracle_px) / hl_oracle_px * 10_000
```

`oracle_lag_bps` measures the throttle gap between Pyth truth and the on-chain oracle. `mark_premium_bps` measures what traders pay above the oracle -- the book's premium or discount to the pushed price.

## What you see in practice

**Commodities (GOLD, SILVER):** Feed is always fresh. Both spreads sit within ~2 bps. The throttle is invisible when the feed is live and the book is deep.

**Equities (NVDA, TSLA) outside market hours:** The Hermes equity feed freezes at the last close. The xyz updater continues pushing an off-hours price from a SEDA composite source. The accumulated drift from Friday close to Monday open can reach 100-300+ bps -- a quantified pre-open gap that resolves at 15:30 UTC. During off-hours `market_state=closed`; at open it flips to `fresh` as the Hermes feed resumes.

**SPCX:** Anchored via Pyth Lazer (`Pyth.HL.SPCX/USDC`, feed ID 99934). All three prices and both spreads are live 24/7. During active trading the oracle tracks Lazer to within a few bps.

## Failure modes and the validity layer

Three conditions make `oracle_lag_bps` unreliable as a signal. The validity layer detects all three per tick and exposes a composite `signal_valid` flag:

**FM1 -- oracle catch-up:** After a fast price move the oracle lags and then chases Pyth in a directional streak. Signals read during the catch-up reflect the old price, not the new one. Detected by watching `oracle_lag_bps` across a rolling window of 4 ticks: if all steps move the same direction and the total move exceeds 30 bps, `oracle_catching_up=1`.

**FM2 -- SEDA composite source:** Outside equity market hours the xyz updater switches from Hermes to a SEDA composite oracle. The lag is not throttle noise -- it is a genuine divergence from a different price source. Detected by combining `market_state=stale|closed` with `|oracle_lag_bps| > 20 bps`. When active, `oracle_source="seda_composite"`. Equity coins show `market_state=closed` during predictable off-hours (Mon-Fri outside 15:30-22:00 UTC) and `market_state=stale` only when the feed stops unexpectedly during open hours.

**FM3 -- discovery bound pinning:** HIP-3 markets have +-10% discovery bounds relative to the oracle. When mark_px is pinned at the boundary, `mark_premium_bps` is a structural artifact, not a book signal. Detected by computing `bound_proximity` (0.0 = lower bound, 1.0 = upper bound) and flagging when proximity is within 5% of either edge.

`signal_valid = False` when any of FM1, FM2, or FM3 is active. `mark_premium_bps` remains meaningful regardless -- it is internal to HL and does not depend on the Pyth source.

## Quickstart

```bash
pip install -r requirements.txt

cp .env.example .env   # populate with feed IDs and API key

python -m collectors.hip3_collector --once   # one tick
python -m collectors.hip3_collector          # continuous
```

## Configuration

| Variable | Default | Description |
|---|---|---|
| `HIP3_DEX` | `xyz` | Dex name from `perpDexs` |
| `HIP3_WATCHLIST` | `xyz:SPCX,xyz:NVDA,xyz:TSLA,xyz:GOLD,xyz:SILVER` | Comma-separated coins |
| `PYTH_FEED_IDS_xyz_<COIN>` | -- | Hermes hex feed ID (e.g. `PYTH_FEED_IDS_xyz_NVDA=b107...`) |
| `PYTH_LAZER_IDS_xyz_<COIN>` | -- | Lazer numeric feed ID (e.g. `PYTH_LAZER_IDS_xyz_SPCX=99934`) |
| `PYTH_API_KEY` | -- | Pyth Lazer bearer token (required for any Lazer feed) |
| `POLL_INTERVAL_SECS` | `15` | Seconds between ticks |
| `LAG_BPS_THRESHOLD` | `50` | Emit event when `oracle_lag_bps` exceeds this |
| `PREMIUM_BPS_THRESHOLD` | `100` | Emit event when `mark_premium_bps` exceeds this |
| `STALE_SECS_THRESHOLD` | `120` | Mark state `stale` (or `closed` for equity coins outside market hours) beyond this age in seconds |
| `SEDA_LAG_THRESHOLD_BPS` | `20` | Minimum lag to classify source as `seda_composite` |
| `LAG_MOVE_THRESHOLD_BPS` | `30` | Minimum directional move across window to flag catch-up |
| `CATCH_UP_WINDOW` | `4` | Ticks to look back for catch-up detection |
| `BOUND_PIN_THRESHOLD` | `0.05` | Proximity fraction at which mark is considered pinned |
| `BOUNDS_REFRESH_INTERVAL` | `120` | Ticks between bounds registry refreshes |
| `DB_PATH` | `hip3.db` | SQLite database path |

**Two Pyth sources, one config pattern:**

- **Hermes** (`PYTH_FEED_IDS_xyz_<COIN>`): push oracle, hex feed ID, free. Used for NVDA, TSLA, GOLD, SILVER. Equity feeds freeze outside market hours.
- **Lazer** (`PYTH_LAZER_IDS_xyz_<COIN>`): pull oracle, numeric feed ID, requires API key. Used for SPCX. Runs 24/7.

If a coin has neither, its row still writes with HL prices and `mark_premium_bps` -- the Pyth columns and `oracle_lag_bps` are NULL.

**Adding a coin:** find its feed ID, add one line to `.env`, restart. No code change.

## Current watchlist

| Coin | Pyth source | Feed ID | Notes |
|---|---|---|---|
| xyz:SPCX | Lazer `Pyth.HL.SPCX/USDC` | `99934` | 24/7, growth mode market |
| xyz:NVDA | Hermes `Equity.US.NVDA/USD` | `b1073854...` | Freezes at US equity close |
| xyz:TSLA | Hermes `Equity.US.TSLA/USD` | `16dad506...` | Freezes at US equity close |
| xyz:GOLD | Hermes `Metal.XAU/USD` | `765d2ba9...` | Always fresh |
| xyz:SILVER | Hermes `Metal.XAG/USD` | `f2fb02c3...` | Always fresh |

## Schema

Five tables in SQLite:

**`hip3_markets`** -- registry, one row per coin.

**`hip3_prices`** -- one row per coin per tick.

```sql
ts                TEXT     -- ISO-8601 UTC
coin              TEXT     -- e.g. "xyz:NVDA"
pyth_px           REAL     -- NULL if no feed configured
pyth_conf         REAL     -- NULL for Lazer-sourced prices
pyth_publish_time INTEGER
pyth_stale_secs   REAL
hl_oracle_px      REAL
hl_mark_px        REAL
funding           REAL
open_interest     REAL
oracle_lag_bps    REAL     -- NULL if pyth_px is NULL
mark_premium_bps  REAL
market_state      TEXT     -- "fresh" | "stale" | "closed" | NULL
```

**`hip3_events`** -- one row when a spread or staleness threshold is crossed.

```sql
ts        TEXT
coin      TEXT
kind      TEXT    -- "oracle_lag" | "mark_premium" | "pyth_stale"
value     REAL
threshold REAL
```

**`hip3_bounds`** -- discovery band per coin, upserted on startup.

```sql
coin            TEXT     -- primary key
lower_bound_pct REAL     -- e.g. -0.10
upper_bound_pct REAL     -- e.g.  0.10
growth_mode     TEXT     -- "enabled" | NULL (from HL universe entry)
source          TEXT     -- "hardcoded" (API exposes no bound fields)
updated_at      TEXT
```

**`hip3_validity`** -- one row per coin per tick, written after hip3_prices.

```sql
ts                  TEXT
coin                TEXT
oracle_catching_up  INTEGER  -- 1 when FM1 is active
lag_direction       TEXT     -- "up" | "down" | NULL
lag_streak          INTEGER  -- consecutive same-direction ticks
oracle_source       TEXT     -- "pyth_live" | "seda_composite" | "unknown"
bound_proximity     REAL     -- 0.0 (lower) to 1.0 (upper), NULL if no bounds
bound_pinned        INTEGER  -- 1 when proximity < 0.05 or > 0.95
signal_valid        INTEGER  -- 0 when any failure mode is active
```

## Network calls per tick

At most three, regardless of watchlist size:

1. `POST /info` with `{"type": "metaAndAssetCtxs", "dex": "xyz"}` -- all coins in one response
2. `GET hermes.pyth.network/v2/updates/price/latest?ids[]=...` -- all Hermes IDs batched
3. `POST pyth-lazer.dourolabs.app/v1/latest_price` -- all Lazer IDs batched

Calls 2 and 3 are skipped when no coins have that feed type. Each failure is non-fatal: the tick logs a warning and continues with whatever data is available.

## Project layout

```
config.py                        env-driven config, HARDCODED_BOUNDS
validity.py                      FM1/FM2/FM3 detectors, signal_valid, upsert_bounds
sources/
  hl_hip3.py                     fetch_perp_dexs, fetch_hip3_meta, build_coin_index,
                                 extract_ctx, build_registry
  hl_hip3_divergence.py          fetch_hl_prices, fetch_hermes_prices, fetch_lazer_prices,
                                 parse_hermes_price, compute_spreads
collectors/
  hip3_collector.py              poll loop, price pass, validity pass, SIGTERM shutdown
db/
  schema.sql                     all five tables and indexes
scripts/
  dashboard.py                   live terminal dashboard -- validity, lag, premium per coin
  analyze_open.py                equity open catch-up analysis (pre-open gap, FM1 window,
                                 mark behavior, commodity baseline)
deploy/
  hip3-collector.service         systemd unit for Raspberry Pi
tests/
  test_hl_hip3.py                coin index and ctx resolution (6 tests)
  test_hl_hip3_divergence.py     price parsing and spread computation (8 tests)
  test_validity.py               validity layer -- all three FMs (28 tests)
```

## Analysis scripts

**`scripts/dashboard.py`** -- live terminal dashboard. Reads directly from the Pi DB, refreshes every 10 seconds, shows validity state per coin with color-coded lag and FM flags.

```
                 HIP-3 Dashboard   2026-06-16 06:08:18 UTC   last tick 17s ago
╔════════╦═════════╦═══════════╦═══════════╦═══════════╦══════════╦══════════╦═══════╦═════════╗
║ Coin   ║ State   ║ Source    ║    Oracle ║      Mark ║  Lag bps ║ Prem bps ║  Prox ║ Valid   ║
╠════════╬═════════╬═══════════╬═══════════╬═══════════╬══════════╬══════════╬═══════╬═════════╣
║ GOLD   ║ fresh   ║ pyth_live ║ 4316.1000 ║ 4316.0000 ║    -0.27 ║    -0.23 ║ 0.500 ║ ✓       ║
║ NVDA   ║ closed  ║ seda_cmp  ║  211.3300 ║  211.3900 ║   -58.44 ║    +2.84 ║ 0.501 ║ x FM2   ║
║ SILVER ║ fresh   ║ pyth_live ║   69.3410 ║   69.3510 ║    +0.57 ║    +1.44 ║ 0.501 ║ ✓       ║
║ SPCX   ║ fresh   ║ pyth_live ║  211.6500 ║  211.5800 ║    -2.10 ║    -3.31 ║ 0.498 ║ ✓       ║
║ TSLA   ║ closed  ║ seda_cmp  ║  405.1800 ║  405.1700 ║  -140.47 ║    -0.25 ║ 0.500 ║ x FM2   ║
╚════════╩═════════╩═══════════╩═══════════╩═══════════╩══════════╩══════════╩═══════╩═════════╝
```

```bash
# Run on the Pi directly
ssh marco@192.168.1.20 "/mnt/liqdata/venv/bin/python /mnt/liqdata/hip3-divergence/scripts/dashboard.py"

# Optional flags
#   --db PATH       override DB path
#   --refresh N     refresh interval in seconds (default: 10)
```

**`scripts/analyze_open.py`** -- equity open catch-up analysis. Reads from a local DB snapshot and prints four sections: pre-open gap (peak lag, first/last tick), catch-up window (FM1 ticks fired, resolution time), mark behavior (pre vs. post premium), and commodity baseline.

```bash
# Pull a consistent snapshot from the Pi first
ssh marco@192.168.1.20 "sqlite3 /mnt/liqdata/data/hip3.db '.backup /tmp/hip3_snapshot.db'" \
  && scp marco@192.168.1.20:/tmp/hip3_snapshot.db /tmp/hip3_analysis.db

# Run the analysis
python scripts/analyze_open.py --db /tmp/hip3_analysis.db --date 2026-06-16
```

## Running tests

```bash
python -m pytest tests/ -v
```

42 tests, no network calls.

## Collector flags

```
--interval N    Poll every N seconds (default: POLL_INTERVAL_SECS from env, or 15)
--once          Run one tick and exit
--db PATH       Override DB path
```

## Querying the data

```bash
# Latest prices and validity for all coins
sqlite3 hip3.db "
  SELECT p.coin, round(p.oracle_lag_bps,2), round(p.mark_premium_bps,2),
         p.market_state, v.oracle_source, v.signal_valid
  FROM hip3_prices p
  JOIN hip3_validity v ON p.ts = v.ts AND p.coin = v.coin
  ORDER BY p.ts DESC LIMIT 10;"

# Largest oracle lags in the last hour, valid signals only
sqlite3 hip3.db "
  SELECT p.ts, p.coin, round(p.oracle_lag_bps,2)
  FROM hip3_prices p
  JOIN hip3_validity v ON p.ts = v.ts AND p.coin = v.coin
  WHERE p.ts > datetime('now', '-1 hour')
    AND p.oracle_lag_bps IS NOT NULL
    AND v.signal_valid = 1
  ORDER BY abs(p.oracle_lag_bps) DESC LIMIT 20;"

# All threshold events
sqlite3 hip3.db "SELECT * FROM hip3_events ORDER BY ts DESC LIMIT 30;"

# Bound proximity over time for SPCX
sqlite3 hip3.db "
  SELECT ts, round(bound_proximity,3), bound_pinned
  FROM hip3_validity WHERE coin='xyz:SPCX'
  ORDER BY ts DESC LIMIT 20;"
```

## Dependencies

`requests`, `rich`, and `sqlite3` (stdlib). Python 3.12+. Pyth Lazer API key required for Lazer feeds (apply at pyth.network).
