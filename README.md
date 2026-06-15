# HIP-3 Divergence Monitor

Records three distinct prices for HIP-3 equity and commodity perps on Hyperliquid's `xyz` dex and tracks the gaps between them as a timestamped series.

## Why three prices

Every HIP-3 perp has three prices in flight simultaneously:

| Name | Source | Description |
|---|---|---|
| `pyth_px` | Pyth Hermes / Pyth Lazer | Real-world truth -- the live feed price |
| `hl_oracle_px` | HL info API | Pyth pushed through the xyz deployer updater, subject to a ~1% throttle |
| `hl_mark_px` | HL info API | The perp's traded price on the native book |

Two spreads carry the signal:

```
oracle_lag_bps   = (hl_oracle_px - pyth_px)      / pyth_px      * 10_000
mark_premium_bps = (hl_mark_px   - hl_oracle_px) / hl_oracle_px * 10_000
```

`oracle_lag_bps` measures the throttle and any off-hours oracle drift away from the real-world feed. `mark_premium_bps` measures what traders are willing to pay above the oracle -- the book's premium or discount to the pushed price.

## What you see in practice

**Commodities (GOLD, SILVER):** Feed is always fresh. Both spreads sit within ~0.1 bps. The throttle is invisible when the feed is live and the book is deep.

**Equities (NVDA, TSLA) outside market hours:** The Hermes equity feed freezes at the last close. The xyz updater continues pushing an off-hours price from a separate source. The accumulated drift between Friday's close and Monday's open can reach 100-300+ bps -- a quantified pre-open gap that resolves at the equity open.

**SPCX:** Anchored via Pyth Lazer (`Pyth.HL.SPCX/USDC`, feed ID 99934). All three prices and both spreads are live. During active trading the oracle tracks Lazer to within a few bps.

## Quickstart

```bash
pip install -r requirements.txt

# Copy and populate the env file
cp .env.example .env

# Run one tick
python -m collectors.hip3_collector --once

# Run continuously (15s default)
python -m collectors.hip3_collector
```

## Configuration

All config is environment-driven. Copy `.env.example` to `.env` and edit.

| Variable | Default | Description |
|---|---|---|
| `HIP3_DEX` | `xyz` | Dex name from `perpDexs` |
| `HIP3_WATCHLIST` | `xyz:SPCX,xyz:NVDA,xyz:TSLA,xyz:GOLD,xyz:SILVER` | Comma-separated coins to monitor |
| `PYTH_FEED_IDS_xyz_<COIN>` | -- | Hermes hex feed ID for a coin (e.g. `PYTH_FEED_IDS_xyz_NVDA=b107...`) |
| `PYTH_LAZER_IDS_xyz_<COIN>` | -- | Lazer numeric feed ID for a coin (e.g. `PYTH_LAZER_IDS_xyz_SPCX=99934`) |
| `PYTH_API_KEY` | -- | Pyth Lazer API key (required for any Lazer feed) |
| `POLL_INTERVAL_SECS` | `15` | Seconds between ticks |
| `LAG_BPS_THRESHOLD` | `50` | Emit event when `oracle_lag_bps` exceeds this |
| `PREMIUM_BPS_THRESHOLD` | `100` | Emit event when `mark_premium_bps` exceeds this |
| `STALE_SECS_THRESHOLD` | `120` | Emit event and mark state `stale` beyond this age |
| `DB_PATH` | `hip3.db` | SQLite database path |

**Two Pyth sources are supported per coin -- use whichever applies:**

- **Hermes** (`PYTH_FEED_IDS_xyz_<COIN>`): standard push oracle, hex feed ID, free. Used for NVDA, TSLA, GOLD, SILVER. Equity feeds freeze outside market hours.
- **Lazer** (`PYTH_LAZER_IDS_xyz_<COIN>`): pull oracle, numeric feed ID, requires API key. Used for SPCX (`Pyth.HL.SPCX/USDC`). Runs 24/7.

If a coin has neither, its row still writes with `hl_oracle_px`, `hl_mark_px`, `funding`, `open_interest`, and `mark_premium_bps` populated -- the pyth columns and `oracle_lag_bps` are NULL.

**Adding a coin:** find its feed ID (Hermes hex or Lazer numeric), add one line to `.env`, restart. No code change.

## Current watchlist

| Coin | Pyth source | Feed ID |
|---|---|---|
| xyz:SPCX | Lazer `Pyth.HL.SPCX/USDC` | `99934` |
| xyz:NVDA | Hermes `Equity.US.NVDA/USD` | `b1073854...` |
| xyz:TSLA | Hermes `Equity.US.TSLA/USD` | `16dad506...` |
| xyz:GOLD | Hermes `Metal.XAU/USD` | `765d2ba9...` |
| xyz:SILVER | Hermes `Metal.XAG/USD` | `f2fb02c3...` |

## Schema

Three tables in SQLite:

**`hip3_markets`** -- registry, one row per coin. Populated by `build_registry()`.

**`hip3_prices`** -- one row per coin per tick.

```sql
ts                TEXT    -- ISO-8601 UTC
coin              TEXT    -- e.g. "xyz:NVDA"
pyth_px           REAL    -- NULL if no feed configured for this coin
pyth_conf         REAL    -- NULL for Lazer-sourced prices (not returned by Lazer)
pyth_publish_time INTEGER
pyth_stale_secs   REAL
hl_oracle_px      REAL
hl_mark_px        REAL
funding           REAL
open_interest     REAL
oracle_lag_bps    REAL    -- NULL if pyth_px is NULL
mark_premium_bps  REAL
market_state      TEXT    -- "fresh" | "stale" | NULL
```

**`hip3_events`** -- one row when a spread or staleness threshold is crossed.

```sql
ts        TEXT
coin      TEXT
kind      TEXT    -- "oracle_lag" | "mark_premium" | "pyth_stale"
value     REAL
threshold REAL
```

## Network calls per tick

At most three, regardless of watchlist size:

1. `POST /info` with `{"type": "metaAndAssetCtxs", "dex": "xyz"}` -- all coins in one response
2. `GET hermes.pyth.network/v2/updates/price/latest?ids[]=...` -- all Hermes feed IDs batched
3. `POST pyth-lazer.dourolabs.app/v1/latest_price` -- all Lazer feed IDs batched

Calls 2 and 3 are skipped if no coins have that feed type configured. Each failure is non-fatal: the tick logs a warning and continues with whatever data is available.

## Project layout

```
config.py                        env-driven config
sources/
  hl_hip3.py                     fetch_perp_dexs, fetch_hip3_meta, build_coin_index,
                                 extract_ctx, build_registry
  hl_hip3_divergence.py          fetch_hl_prices, fetch_hermes_prices, fetch_lazer_prices,
                                 parse_hermes_price, compute_spreads, build_hermes_params
collectors/
  hip3_collector.py              poll loop, threshold events, clean SIGTERM shutdown
db/
  schema.sql                     hip3_markets, hip3_prices, hip3_events
deploy/
  hip3-collector.service         systemd unit for Raspberry Pi
tests/
  test_hl_hip3.py                coin index and ctx resolution
  test_hl_hip3_divergence.py     price parsing and spread computation
```

## Running tests

```bash
python -m pytest tests/ -v
```

14 tests, no network calls.

## Collector flags

```
--interval N    Poll every N seconds (default: POLL_INTERVAL_SECS from env, or 15)
--once          Run one tick and exit
--db PATH       Override DB path
```

## Querying the data

```bash
# Last 15 rows across all coins
sqlite3 hip3.db "
  SELECT coin, round(pyth_px,4), round(hl_oracle_px,4), round(hl_mark_px,4),
         round(oracle_lag_bps,2), round(mark_premium_bps,2), market_state
  FROM hip3_prices ORDER BY ts DESC LIMIT 15;"

# Largest oracle lags in the last hour
sqlite3 hip3.db "
  SELECT ts, coin, round(oracle_lag_bps,2)
  FROM hip3_prices
  WHERE ts > datetime('now', '-1 hour') AND oracle_lag_bps IS NOT NULL
  ORDER BY abs(oracle_lag_bps) DESC LIMIT 20;"

# All threshold events
sqlite3 hip3.db "SELECT * FROM hip3_events ORDER BY ts DESC LIMIT 30;"
```

## Dependencies

`requests` and `sqlite3` (stdlib). Python 3.12+. Pyth Lazer API key required for Lazer feeds (apply at pyth.network).
