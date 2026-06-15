# -- collectors/hip3_collector.py --
# Poll loop for the HIP-3 three-price divergence monitor.
#
# Run:
#   python -m collectors.hip3_collector
#   python -m collectors.hip3_collector --interval 5 --once

import argparse
import logging
import signal
import sqlite3
import time
from datetime import datetime, timezone

import config
from sources.hl_hip3 import extract_ctx, fetch_hip3_meta
from sources.hl_hip3_divergence import (
    compute_spreads,
    fetch_hermes_prices,
    fetch_lazer_prices,
    fetch_hl_prices,
)
from validity import (
    compute_bound_proximity,
    compute_oracle_catching_up,
    compute_oracle_source,
    compute_signal_valid,
    upsert_bounds,
    write_validity_tick,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger(__name__)


def _init_db(db_path: str) -> None:
    with sqlite3.connect(db_path) as conn:
        with open("db/schema.sql") as f:
            conn.executescript(f.read())
        conn.commit()


def _write_price_row(conn: sqlite3.Connection, row: dict) -> None:
    conn.execute("""
        INSERT OR REPLACE INTO hip3_prices (
            ts, coin, pyth_px, pyth_conf, pyth_publish_time, pyth_stale_secs,
            hl_oracle_px, hl_mark_px, funding, open_interest,
            oracle_lag_bps, mark_premium_bps, market_state
        ) VALUES (
            :ts, :coin, :pyth_px, :pyth_conf, :pyth_publish_time, :pyth_stale_secs,
            :hl_oracle_px, :hl_mark_px, :funding, :open_interest,
            :oracle_lag_bps, :mark_premium_bps, :market_state
        )
    """, row)


def _write_event_row(
    conn: sqlite3.Connection,
    ts: str,
    coin: str,
    kind: str,
    value: float,
    threshold: float,
) -> None:
    conn.execute("""
        INSERT OR REPLACE INTO hip3_events (ts, coin, kind, value, threshold)
        VALUES (?, ?, ?, ?, ?)
    """, (ts, coin, kind, value, threshold))


def _market_state(pyth_stale_secs: float | None) -> str | None:
    if pyth_stale_secs is None:
        return None
    return "stale" if pyth_stale_secs > config.STALE_SECS_THRESHOLD else "fresh"


def tick(db_path: str) -> int:
    """
    Execute one collection tick.
    Returns the number of coins written. Logs and continues on partial failure.
    """
    ts = datetime.now(timezone.utc).isoformat()
    now_unix = time.time()
    rows_written = 0

    # -- one HL call for all coins --
    try:
        coin_index, ctxs = fetch_hl_prices(config.HIP3_DEX)
    except Exception as exc:
        log.error("tick: HL metaAndAssetCtxs failed: %s", exc)
        return 0

    # -- one Hermes batch call for coins with a Hermes feed id --
    pyth_prices: dict = {}
    try:
        pyth_prices = fetch_hermes_prices(config.HIP3_WATCHLIST, config.PYTH_FEED_IDS)
    except Exception as exc:
        log.warning("tick: Hermes fetch failed, continuing without pyth prices: %s", exc)

    # -- one Lazer batch call for coins with a Lazer feed id --
    if config.LAZER_FEED_IDS and config.PYTH_API_KEY:
        try:
            lazer = fetch_lazer_prices(
                config.HIP3_WATCHLIST, config.LAZER_FEED_IDS, config.PYTH_API_KEY
            )
            pyth_prices.update(lazer)
        except Exception as exc:
            log.warning("tick: Lazer fetch failed, continuing without Lazer prices: %s", exc)

    tick_rows: dict[str, dict] = {}

    with sqlite3.connect(db_path) as conn:
        # -- price pass --
        for coin in config.HIP3_WATCHLIST:
            ctx = extract_ctx(coin, coin_index, ctxs)
            if ctx is None:
                log.warning("tick: coin %s not found in HL meta, skipping", coin)
                continue

            hl_oracle_px  = float(ctx["oraclePx"])
            hl_mark_px    = float(ctx["markPx"])
            funding       = float(ctx["funding"])
            open_interest = float(ctx["openInterest"])

            pyth_px = pyth_conf = pyth_publish_time = pyth_stale_secs = None
            market_state = None

            if coin in pyth_prices:
                pyth_px, pyth_conf, pyth_publish_time = pyth_prices[coin]
                pyth_stale_secs = now_unix - pyth_publish_time
                market_state = _market_state(pyth_stale_secs)

            oracle_lag_bps, mark_premium_bps = compute_spreads(
                pyth_px, hl_oracle_px, hl_mark_px
            )

            row = dict(
                ts=ts,
                coin=coin,
                pyth_px=pyth_px,
                pyth_conf=pyth_conf,
                pyth_publish_time=pyth_publish_time,
                pyth_stale_secs=pyth_stale_secs,
                hl_oracle_px=hl_oracle_px,
                hl_mark_px=hl_mark_px,
                funding=funding,
                open_interest=open_interest,
                oracle_lag_bps=oracle_lag_bps,
                mark_premium_bps=mark_premium_bps,
                market_state=market_state,
            )

            _write_price_row(conn, row)
            tick_rows[coin] = row
            rows_written += 1

            log.info(
                "%s  oracle=%.4f  mark=%.4f  pyth=%s  lag=%s bps  premium=%s bps",
                coin,
                hl_oracle_px,
                hl_mark_px,
                f"{pyth_px:.4f}" if pyth_px is not None else "None",
                f"{oracle_lag_bps:.2f}" if oracle_lag_bps is not None else "None",
                f"{mark_premium_bps:.2f}" if mark_premium_bps is not None else "None",
            )

            # -- threshold events --
            if oracle_lag_bps is not None and abs(oracle_lag_bps) > config.LAG_BPS_THRESHOLD:
                _write_event_row(conn, ts, coin, "oracle_lag",
                                 oracle_lag_bps, config.LAG_BPS_THRESHOLD)
            if mark_premium_bps is not None and abs(mark_premium_bps) > config.PREMIUM_BPS_THRESHOLD:
                _write_event_row(conn, ts, coin, "mark_premium",
                                 mark_premium_bps, config.PREMIUM_BPS_THRESHOLD)
            if pyth_stale_secs is not None and pyth_stale_secs > config.STALE_SECS_THRESHOLD:
                _write_event_row(conn, ts, coin, "pyth_stale",
                                 pyth_stale_secs, config.STALE_SECS_THRESHOLD)

        # -- validity pass (after all price rows are written so catch-up can read history) --
        validity_summary: list[str] = []
        for coin, row in tick_rows.items():
            oracle_source = compute_oracle_source(
                row["pyth_stale_secs"], row["market_state"], row["oracle_lag_bps"]
            )
            catching_up, direction, streak = compute_oracle_catching_up(coin, conn)
            proximity, pinned = compute_bound_proximity(
                row["hl_mark_px"], row["hl_oracle_px"], coin, conn
            )
            valid = compute_signal_valid(catching_up, oracle_source, pinned)

            write_validity_tick(ts, coin, {
                "oracle_catching_up": int(catching_up),
                "lag_direction":      direction,
                "lag_streak":         streak,
                "oracle_source":      oracle_source,
                "bound_proximity":    proximity,
                "bound_pinned":       int(pinned),
                "signal_valid":       int(valid),
            }, conn)

            short = coin.split(":")[-1]
            validity_summary.append(f"{short} src={oracle_source} valid={valid}")

        if validity_summary:
            log.info("validity: %s", " | ".join(validity_summary))

        conn.commit()

    return rows_written


def main() -> None:
    parser = argparse.ArgumentParser(description="HIP-3 three-price divergence collector")
    parser.add_argument("--interval", type=int, default=config.POLL_INTERVAL_SECS,
                        help="Poll interval in seconds (default: %(default)s)")
    parser.add_argument("--once", action="store_true",
                        help="Run one tick and exit")
    parser.add_argument("--db", default=config.DB_PATH,
                        help="SQLite database path (default: %(default)s)")
    args = parser.parse_args()

    _init_db(args.db)
    log.info(
        "HIP-3 collector started: dex=%s watchlist=%s interval=%ds db=%s",
        config.HIP3_DEX, config.HIP3_WATCHLIST, args.interval, args.db,
    )

    def _refresh_bounds() -> None:
        try:
            meta, _ = fetch_hip3_meta(config.HIP3_DEX)
            with sqlite3.connect(args.db) as conn:
                upsert_bounds(config.HIP3_WATCHLIST, meta.get("universe", []), conn)
                conn.commit()
            log.info("bounds refreshed for %d coins", len(config.HIP3_WATCHLIST))
        except Exception as exc:
            log.warning("bounds refresh failed: %s", exc)

    _refresh_bounds()

    # SIGTERM from systemd: set flag, let the current tick finish cleanly.
    stop = False

    def _handle_signal(sig, _frame):
        nonlocal stop
        log.info("signal %s received, finishing current tick then stopping", sig)
        stop = True

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    tick_count = 0
    try:
        while not stop:
            written = tick(args.db)
            tick_count += 1
            log.info("tick complete: %d rows written", written)
            if args.once:
                break
            if tick_count % config.BOUNDS_REFRESH_INTERVAL == 0:
                _refresh_bounds()
            # Sleep in short increments so a signal wakes us promptly.
            for _ in range(args.interval):
                if stop:
                    break
                time.sleep(1)
    finally:
        log.info("HIP-3 collector stopped")


if __name__ == "__main__":
    main()
