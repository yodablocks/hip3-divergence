# -- validity.py --
# Per-tick, per-coin validity flags for the HIP-3 divergence monitor.
#
# Public API
# ----------
# upsert_bounds(watchlist, meta_universe, db_conn)
# compute_oracle_source(pyth_stale_secs, market_state, oracle_lag_bps)
# compute_oracle_catching_up(coin, db_conn, window)
# compute_bound_proximity(hl_mark_px, hl_oracle_px, coin, db_conn)
# compute_signal_valid(oracle_catching_up, oracle_source, bound_pinned)
# write_validity_tick(ts, coin, flags, db_conn)

import logging
import sqlite3
from datetime import datetime, timezone

import config

log = logging.getLogger(__name__)


def upsert_bounds(
    watchlist: list[str],
    meta_universe: list[dict],
    db_conn: sqlite3.Connection,
) -> None:
    """
    Upsert one row per watchlist coin into hip3_bounds.
    Bound percentages come from HARDCODED_BOUNDS (API exposes no bound fields).
    growth_mode is read from the universe entry growthMode field when present.
    """
    # Build name -> universe entry map for growth_mode lookup
    universe_by_name = {e["name"]: e for e in meta_universe if "name" in e}
    now = datetime.now(timezone.utc).isoformat()

    for coin in watchlist:
        bounds = config.HARDCODED_BOUNDS.get(coin)
        entry = universe_by_name.get(coin, {})
        growth_mode = entry.get("growthMode")

        db_conn.execute("""
            INSERT INTO hip3_bounds (coin, lower_bound_pct, upper_bound_pct, growth_mode, source, updated_at)
            VALUES (?, ?, ?, ?, 'hardcoded', ?)
            ON CONFLICT(coin) DO UPDATE SET
                lower_bound_pct = excluded.lower_bound_pct,
                upper_bound_pct = excluded.upper_bound_pct,
                growth_mode     = excluded.growth_mode,
                updated_at      = excluded.updated_at
        """, (
            coin,
            bounds["lower"] if bounds else None,
            bounds["upper"] if bounds else None,
            growth_mode,
            now,
        ))

    log.debug("upsert_bounds: %d coins upserted", len(watchlist))


def compute_oracle_source(
    pyth_stale_secs: float | None,
    market_state: str | None,
    oracle_lag_bps: float | None,
) -> str:
    """
    Classify the current oracle data source.

    Returns "pyth_live" | "seda_composite" | "unknown"

    pyth_live:      market_state is "fresh" -- Hermes or Lazer is the live anchor
    seda_composite: market_state is "stale" or "closed" and |oracle_lag_bps| > SEDA_LAG_THRESHOLD_BPS
                    (oracle is diverging above the frozen Pyth price)
    unknown:        stale/closed but lag is small, or no Pyth data at all
    """
    if market_state == "fresh":
        return "pyth_live"
    if (
        market_state in ("stale", "closed")
        and oracle_lag_bps is not None
        and abs(oracle_lag_bps) > config.SEDA_LAG_THRESHOLD_BPS
    ):
        return "seda_composite"
    return "unknown"


def compute_oracle_catching_up(
    coin: str,
    db_conn: sqlite3.Connection,
    window: int = config.CATCH_UP_WINDOW,
) -> tuple[bool, str | None, int]:
    """
    Detect whether the oracle is in a directional catch-up move.

    Fetches the last `window` oracle_lag_bps values for the coin (most recent first).
    Returns (catching_up, direction, streak).

    catching_up: True when all values move the same direction and the total
                 change exceeds LAG_MOVE_THRESHOLD_BPS.
    direction:   "up" | "down" | None
    streak:      count of consecutive same-direction steps (0 when not catching up)
    """
    rows = db_conn.execute("""
        SELECT oracle_lag_bps
        FROM hip3_prices
        WHERE coin = ? AND oracle_lag_bps IS NOT NULL
        ORDER BY ts DESC
        LIMIT ?
    """, (coin, window)).fetchall()

    values = [r[0] for r in rows]

    if len(values) < window:
        return False, None, 0

    # values[0] is newest; compute differences newest-to-oldest
    # diff[i] = values[i] - values[i+1]  (positive = lag increased in that step)
    diffs = [values[i] - values[i + 1] for i in range(len(values) - 1)]

    if all(d > 0 for d in diffs):
        direction = "up"
    elif all(d < 0 for d in diffs):
        direction = "down"
    else:
        return False, None, 0

    total_move = abs(values[0] - values[-1])
    if total_move < config.LAG_MOVE_THRESHOLD_BPS:
        return False, None, 0

    return True, direction, len(diffs)


def compute_bound_proximity(
    hl_mark_px: float,
    hl_oracle_px: float,
    coin: str,
    db_conn: sqlite3.Connection,
) -> tuple[float | None, bool]:
    """
    Compute where mark_px sits within the discovery band.

    Returns (proximity, pinned).
    proximity: 0.0 = at lower bound, 1.0 = at upper bound, clamped.
    pinned: True when proximity < BOUND_PIN_THRESHOLD or > (1 - BOUND_PIN_THRESHOLD).
    Returns (None, False) when no bounds row exists for the coin.

    Uses hl_oracle_px as the reference price for the band calculation.
    """
    row = db_conn.execute("""
        SELECT lower_bound_pct, upper_bound_pct FROM hip3_bounds WHERE coin = ?
    """, (coin,)).fetchone()

    if row is None:
        return None, False

    lower_pct, upper_pct = row
    if lower_pct is None or upper_pct is None:
        return None, False

    lower_bound = hl_oracle_px * (1 + lower_pct)
    upper_bound = hl_oracle_px * (1 + upper_pct)
    band = upper_bound - lower_bound

    if band == 0:
        return None, False

    proximity = (hl_mark_px - lower_bound) / band
    proximity = max(0.0, min(1.0, proximity))
    pinned = proximity < config.BOUND_PIN_THRESHOLD or proximity > (1 - config.BOUND_PIN_THRESHOLD)

    return proximity, pinned


def compute_signal_valid(
    oracle_catching_up: bool,
    oracle_source: str,
    bound_pinned: bool,
) -> bool:
    """
    Composite validity flag. False when any failure mode is active.

    oracle_catching_up  -> FM1: lag signal unreliable during catch-up
    seda_composite      -> FM2: oracle not anchored to Pyth, lag signals invalid
    bound_pinned        -> FM3: mark at discovery boundary

    Note: mark_premium_bps is internal to HL and remains meaningful regardless.
    signal_valid gates lag-based and bound-based signals only.
    """
    if oracle_catching_up:
        return False
    if oracle_source == "seda_composite":
        return False
    if bound_pinned:
        return False
    return True


def write_validity_tick(
    ts: str,
    coin: str,
    flags: dict,
    db_conn: sqlite3.Connection,
) -> None:
    """Upsert one row into hip3_validity."""
    db_conn.execute("""
        INSERT INTO hip3_validity (
            ts, coin, oracle_catching_up, lag_direction, lag_streak,
            oracle_source, bound_proximity, bound_pinned, signal_valid
        ) VALUES (
            :ts, :coin, :oracle_catching_up, :lag_direction, :lag_streak,
            :oracle_source, :bound_proximity, :bound_pinned, :signal_valid
        )
        ON CONFLICT(ts, coin) DO UPDATE SET
            oracle_catching_up = excluded.oracle_catching_up,
            lag_direction      = excluded.lag_direction,
            lag_streak         = excluded.lag_streak,
            oracle_source      = excluded.oracle_source,
            bound_proximity    = excluded.bound_proximity,
            bound_pinned       = excluded.bound_pinned,
            signal_valid       = excluded.signal_valid
    """, {"ts": ts, "coin": coin, **flags})
