# -- scripts/dashboard.py --
# Live terminal dashboard for the HIP-3 three-price monitor.
#
# Run on the Pi (reads DB directly, no network calls):
#   /mnt/liqdata/venv/bin/python /mnt/liqdata/hip3-divergence/scripts/dashboard.py
#
# Or from Mac over SSH:
#   ssh user@pi "/mnt/liqdata/venv/bin/python /mnt/liqdata/hip3-divergence/scripts/dashboard.py"

import argparse
import sqlite3
import time
from datetime import datetime, timezone

from rich.console import Console
from rich.live import Live
from rich.table import Table
from rich.text import Text
from rich import box

DEFAULT_DB   = "/mnt/liqdata/data/hip3.db"
REFRESH_SECS = 10


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _fetch(conn: sqlite3.Connection) -> tuple[list[sqlite3.Row], str | None]:
    """Return latest validity+price row per coin, plus the most recent ts."""
    rows = conn.execute("""
        SELECT
            p.coin,
            p.ts,
            p.market_state,
            round(p.oracle_lag_bps,    2) AS lag,
            round(p.mark_premium_bps,  2) AS premium,
            round(p.hl_oracle_px,      4) AS oracle,
            round(p.hl_mark_px,        4) AS mark,
            v.oracle_source,
            v.oracle_catching_up,
            v.bound_pinned,
            v.signal_valid,
            round(v.bound_proximity,   3) AS proximity
        FROM hip3_prices p
        LEFT JOIN hip3_validity v ON p.ts = v.ts AND p.coin = v.coin
        WHERE p.ts = (
            SELECT max(ts) FROM hip3_prices WHERE coin = p.coin
        )
        ORDER BY p.coin
    """).fetchall()

    latest_ts = conn.execute("SELECT max(ts) FROM hip3_prices").fetchone()[0]
    return rows, latest_ts


def _lag_since(ts_str: str | None) -> str:
    if ts_str is None:
        return "no data"
    try:
        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        delta = int((datetime.now(timezone.utc) - ts).total_seconds())
        return f"{delta}s ago"
    except Exception:
        return "?"


def _source_short(source: str | None) -> str:
    if source == "pyth_live":
        return "pyth_live"
    if source == "seda_composite":
        return "seda_cmp"
    return source or "?"


def _fm_flags(row: sqlite3.Row) -> str:
    flags = []
    if row["oracle_catching_up"]:
        flags.append("FM1")
    if row["oracle_source"] == "seda_composite":
        flags.append("FM2")
    if row["bound_pinned"]:
        flags.append("FM3")
    return " ".join(flags)


def _state_color(state: str | None) -> str:
    if state == "fresh":   return "green"
    if state == "closed":  return "yellow"
    if state == "stale":   return "red"
    return "white"


def _valid_cell(row: sqlite3.Row) -> Text:
    flags = _fm_flags(row)
    if row["signal_valid"] == 1 or row["signal_valid"] is None and not flags:
        return Text("✓", style="bold green")
    return Text(f"✗ {flags}", style="bold red")


def _build_table(rows: list[sqlite3.Row], latest_ts: str | None) -> Table:
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    lag_str = _lag_since(latest_ts)

    table = Table(
        title=f"HIP-3 Dashboard   {now_utc}   last tick {lag_str}",
        box=box.DOUBLE_EDGE,
        show_header=True,
        header_style="bold cyan",
        title_style="bold white",
        expand=False,
    )

    table.add_column("Coin",    style="bold white", width=8)
    table.add_column("State",   width=8)
    table.add_column("Source",  width=10)
    table.add_column("Oracle",  justify="right", width=10)
    table.add_column("Mark",    justify="right", width=10)
    table.add_column("Lag bps", justify="right", width=9)
    table.add_column("Prem bps",justify="right", width=9)
    table.add_column("Prox",    justify="right", width=6)
    table.add_column("Valid",   width=10)

    for row in rows:
        coin_short = row["coin"].split(":")[-1]
        state      = row["market_state"] or "?"
        source     = _source_short(row["oracle_source"])
        lag        = f"{row['lag']:+.2f}" if row["lag"] is not None else "—"
        premium    = f"{row['premium']:+.2f}" if row["premium"] is not None else "—"
        prox       = f"{row['proximity']:.3f}" if row["proximity"] is not None else "—"
        oracle     = f"{row['oracle']:.4f}" if row["oracle"] is not None else "—"
        mark       = f"{row['mark']:.4f}" if row["mark"] is not None else "—"

        state_text = Text(state, style=_state_color(state))

        # Color lag: green near zero, yellow moderate, red large
        abs_lag = abs(row["lag"]) if row["lag"] is not None else 0
        lag_style = "green" if abs_lag < 10 else ("yellow" if abs_lag < 50 else "red")
        lag_text = Text(lag, style=lag_style)

        table.add_row(
            coin_short,
            state_text,
            source,
            oracle,
            mark,
            lag_text,
            premium,
            prox,
            _valid_cell(row),
        )

    return table


def main() -> None:
    parser = argparse.ArgumentParser(description="HIP-3 live dashboard")
    parser.add_argument("--db",      default=DEFAULT_DB,   help="SQLite DB path")
    parser.add_argument("--refresh", default=REFRESH_SECS, type=int,
                        help="Refresh interval in seconds (default: %(default)s)")
    args = parser.parse_args()

    console = Console()

    try:
        conn = _connect(args.db)
    except Exception as exc:
        console.print(f"[red]Cannot open DB: {exc}[/red]")
        return

    with Live(console=console, refresh_per_second=1, screen=True) as live:
        while True:
            try:
                rows, latest_ts = _fetch(conn)
                live.update(_build_table(rows, latest_ts))
            except Exception as exc:
                live.update(Text(f"Error: {exc}", style="red"))
            time.sleep(args.refresh)


if __name__ == "__main__":
    main()
