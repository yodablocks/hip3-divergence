# -- scripts/analyze_open.py --
# Analyze the equity open catch-up event from hip3_prices + hip3_validity.
#
# Usage:
#   python scripts/analyze_open.py [--db PATH] [--date YYYY-MM-DD]

import argparse
import sqlite3
from datetime import datetime, timezone


EQUITY_COINS = ["xyz:NVDA", "xyz:TSLA"]
COMMODITY_COINS = ["xyz:GOLD", "xyz:SILVER", "xyz:SPCX"]

# US equity open in UTC
OPEN_UTC = "15:30:00"


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def section(title: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print('=' * 60)


def analyze_pre_open_gap(conn: sqlite3.Connection, date: str) -> None:
    section(f"Pre-open gap -- {date} (before {OPEN_UTC} UTC)")

    for coin in EQUITY_COINS:
        rows = conn.execute("""
            SELECT ts, round(hl_oracle_px, 4) as oracle,
                   round(pyth_px, 4) as pyth,
                   round(oracle_lag_bps, 2) as lag,
                   market_state
            FROM hip3_prices
            WHERE coin = ?
              AND date(ts) = ?
              AND time(ts) < ?
            ORDER BY ts ASC
        """, (coin, date, OPEN_UTC)).fetchall()

        if not rows:
            print(f"\n  {coin}: no pre-open data")
            continue

        first = rows[0]
        last = rows[-1]
        lags = [r["lag"] for r in rows if r["lag"] is not None]
        peak_lag = max(lags, key=abs) if lags else None

        print(f"\n  {coin}  ({len(rows)} ticks)")
        print(f"    first tick  {first['ts']}  oracle={first['oracle']}  pyth={first['pyth']}  lag={first['lag']} bps")
        print(f"    last tick   {last['ts']}   oracle={last['oracle']}  pyth={last['pyth']}  lag={last['lag']} bps")
        print(f"    peak lag    {peak_lag} bps")
        print(f"    market_state throughout: {set(r['market_state'] for r in rows)}")


def analyze_catchup_window(conn: sqlite3.Connection, date: str) -> None:
    section(f"Catch-up window -- {date} (around {OPEN_UTC} UTC, +/- 30 min)")

    window_start = f"{date} 15:00:00"
    window_end   = f"{date} 16:00:00"

    for coin in EQUITY_COINS:
        rows = conn.execute("""
            SELECT p.ts,
                   round(p.hl_oracle_px, 4)   as oracle,
                   round(p.pyth_px, 4)         as pyth,
                   round(p.oracle_lag_bps, 2)  as lag,
                   round(p.mark_premium_bps, 2) as premium,
                   p.market_state,
                   v.oracle_catching_up,
                   v.lag_direction,
                   v.lag_streak,
                   v.oracle_source,
                   v.signal_valid
            FROM hip3_prices p
            LEFT JOIN hip3_validity v ON p.ts = v.ts AND p.coin = v.coin
            WHERE p.coin = ?
              AND p.ts >= ?
              AND p.ts <= ?
            ORDER BY p.ts ASC
        """, (coin, window_start, window_end)).fetchall()

        if not rows:
            print(f"\n  {coin}: no data in window")
            continue

        catching_up_ticks = [r for r in rows if r["oracle_catching_up"] == 1]
        max_streak = max((r["lag_streak"] for r in rows if r["lag_streak"]), default=0)

        # Find the tick where lag crossed below 20 bps after the open
        post_open = [r for r in rows if r["ts"] >= f"{date} 15:30:00"]
        resolution_tick = next(
            (r for r in post_open if r["lag"] is not None and abs(r["lag"]) < 20),
            None
        )

        # Find lag at open
        at_open = next((r for r in rows if r["ts"] >= f"{date} 15:30:00"), None)

        print(f"\n  {coin}  ({len(rows)} ticks in window)")

        if at_open:
            print(f"    at open  {at_open['ts']}  lag={at_open['lag']} bps  "
                  f"source={at_open['oracle_source']}  valid={at_open['signal_valid']}")
        if resolution_tick:
            print(f"    resolved {resolution_tick['ts']}  lag={resolution_tick['lag']} bps  "
                  f"(first tick below 20 bps)")
        else:
            print(f"    not resolved within window (lag still elevated at close)")

        print(f"    catch-up ticks (FM1 fired): {len(catching_up_ticks)}  max streak: {max_streak}")

        if catching_up_ticks:
            first_fm1 = catching_up_ticks[0]
            last_fm1  = catching_up_ticks[-1]
            print(f"    FM1 window: {first_fm1['ts']} -> {last_fm1['ts']}")

        print(f"\n    {'Timestamp':<30} {'Lag':>8} {'Premium':>9} {'Source':<16} {'Valid':>5} {'CatchUp':>7}")
        print(f"    {'-'*30} {'-'*8} {'-'*9} {'-'*16} {'-'*5} {'-'*7}")
        for r in rows:
            print(
                f"    {r['ts']:<30} "
                f"{str(r['lag']):>8} "
                f"{str(r['premium']):>9} "
                f"{str(r['oracle_source'] or ''):>16} "
                f"{str(r['signal_valid'] or ''):>5} "
                f"{str(r['oracle_catching_up'] or 0):>7}"
            )


def analyze_mark_behavior(conn: sqlite3.Connection, date: str) -> None:
    section(f"Mark premium during catch-up -- {date}")

    window_start = f"{date} 15:00:00"
    window_end   = f"{date} 16:00:00"

    for coin in EQUITY_COINS:
        rows = conn.execute("""
            SELECT p.ts,
                   round(p.oracle_lag_bps, 2)   as lag,
                   round(p.mark_premium_bps, 2)  as premium,
                   v.oracle_catching_up,
                   v.signal_valid
            FROM hip3_prices p
            LEFT JOIN hip3_validity v ON p.ts = v.ts AND p.coin = v.coin
            WHERE p.coin = ?
              AND p.ts >= ?
              AND p.ts <= ?
            ORDER BY p.ts ASC
        """, (coin, window_start, window_end)).fetchall()

        if not rows:
            continue

        pre  = [r for r in rows if r["ts"] < f"{date} 15:30:00"]
        post = [r for r in rows if r["ts"] >= f"{date} 15:30:00"]

        def avg(values):
            v = [x for x in values if x is not None]
            return round(sum(v) / len(v), 2) if v else None

        pre_premium  = avg([r["premium"] for r in pre])
        post_premium = avg([r["premium"] for r in post])
        pre_lag      = avg([r["lag"] for r in pre])
        post_lag     = avg([r["lag"] for r in post])

        print(f"\n  {coin}")
        print(f"    pre-open  avg lag={pre_lag} bps   avg mark_premium={pre_premium} bps  ({len(pre)} ticks)")
        print(f"    post-open avg lag={post_lag} bps  avg mark_premium={post_premium} bps  ({len(post)} ticks)")


def analyze_commodities(conn: sqlite3.Connection, date: str) -> None:
    section(f"Commodity baseline -- {date} (all day)")

    for coin in COMMODITY_COINS:
        rows = conn.execute("""
            SELECT round(avg(abs(oracle_lag_bps)), 2)  as mean_abs_lag,
                   round(max(abs(oracle_lag_bps)), 2)  as max_abs_lag,
                   round(avg(abs(mark_premium_bps)), 2) as mean_premium,
                   count(*) as ticks
            FROM hip3_prices
            WHERE coin = ? AND date(ts) = ?
        """, (coin, date)).fetchone()

        if rows and rows["ticks"]:
            print(f"  {coin:<14}  ticks={rows['ticks']}  "
                  f"mean|lag|={rows['mean_abs_lag']} bps  "
                  f"max|lag|={rows['max_abs_lag']} bps  "
                  f"mean_premium={rows['mean_premium']} bps")


def main() -> None:
    parser = argparse.ArgumentParser(description="HIP-3 equity open catch-up analysis")
    parser.add_argument("--db", default="hip3.db", help="SQLite DB path")
    parser.add_argument("--date", default="2026-06-15", help="Date to analyze (YYYY-MM-DD)")
    args = parser.parse_args()

    print(f"\nHIP-3 Open Catch-up Analysis")
    print(f"DB:   {args.db}")
    print(f"Date: {args.date}")

    with sqlite3.connect(args.db) as conn:
        conn.row_factory = sqlite3.Row
        analyze_pre_open_gap(conn, args.date)
        analyze_catchup_window(conn, args.date)
        analyze_mark_behavior(conn, args.date)
        analyze_commodities(conn, args.date)

    print("\n")


if __name__ == "__main__":
    main()
