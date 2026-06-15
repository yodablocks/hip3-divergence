# -- tests/test_validity.py --
# Unit tests for validity.py. All DB tests use in-memory SQLite with the real schema.

import sqlite3
import sys
import os

import pytest

# ensure repo root is on the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import config
from validity import (
    compute_bound_proximity,
    compute_oracle_catching_up,
    compute_oracle_source,
    compute_signal_valid,
    upsert_bounds,
    write_validity_tick,
)


# ── helpers ──────────────────────────────────────────────────────────────

def _make_conn() -> sqlite3.Connection:
    """Open an in-memory DB and initialise the full schema."""
    conn = sqlite3.connect(":memory:")
    schema_path = os.path.join(os.path.dirname(__file__), "..", "db", "schema.sql")
    with open(schema_path) as f:
        conn.executescript(f.read())
    return conn


def _insert_lag_rows(conn: sqlite3.Connection, coin: str, lag_values: list[float]) -> None:
    """Insert synthetic hip3_prices rows with ascending timestamps."""
    for i, lag in enumerate(lag_values):
        ts = f"2026-06-15T10:{'00' if i < 10 else ''}:{i:02d}:00"
        conn.execute("""
            INSERT INTO hip3_prices
                (ts, coin, hl_oracle_px, hl_mark_px, oracle_lag_bps, mark_premium_bps,
                 funding, open_interest)
            VALUES (?, ?, 200.0, 200.1, ?, 0.5, 0.0001, 1000.0)
        """, (ts, coin, lag))
    conn.commit()


# ── compute_oracle_source ────────────────────────────────────────────────

def test_oracle_source_fresh():
    assert compute_oracle_source(10.0, "fresh", 5.0) == "pyth_live"

def test_oracle_source_fresh_ignores_lag():
    # Even large lag during "fresh" state should return pyth_live
    assert compute_oracle_source(10.0, "fresh", 300.0) == "pyth_live"

def test_oracle_source_stale_large_lag():
    assert compute_oracle_source(300.0, "stale", 250.0) == "seda_composite"

def test_oracle_source_stale_small_lag():
    # Stale but lag within threshold -> unknown
    assert compute_oracle_source(300.0, "stale", 5.0) == "unknown"

def test_oracle_source_stale_no_lag():
    # No oracle_lag_bps (coin has no Pyth feed)
    assert compute_oracle_source(300.0, "stale", None) == "unknown"

def test_oracle_source_no_pyth():
    # market_state None: no pyth feed at all
    assert compute_oracle_source(None, None, None) == "unknown"


# ── compute_oracle_catching_up ────────────────────────────────────────────

def test_catching_up_not_enough_rows():
    conn = _make_conn()
    _insert_lag_rows(conn, "xyz:NVDA", [10.0, 20.0])  # only 2 rows, window=4
    catching_up, direction, streak = compute_oracle_catching_up("xyz:NVDA", conn)
    assert catching_up is False
    assert direction is None
    assert streak == 0

def test_catching_up_upward():
    conn = _make_conn()
    # Each newer row has a higher lag: most-recent first after ORDER BY ts DESC
    # Insert oldest-to-newest: 10, 40, 80, 130 -> diffs newest-to-oldest all positive
    _insert_lag_rows(conn, "xyz:NVDA", [10.0, 40.0, 80.0, 130.0])
    catching_up, direction, streak = compute_oracle_catching_up("xyz:NVDA", conn)
    assert catching_up is True
    assert direction == "up"
    assert streak == 3

def test_catching_up_downward():
    conn = _make_conn()
    _insert_lag_rows(conn, "xyz:NVDA", [130.0, 80.0, 40.0, 10.0])
    catching_up, direction, streak = compute_oracle_catching_up("xyz:NVDA", conn)
    assert catching_up is True
    assert direction == "down"
    assert streak == 3

def test_catching_up_mixed_direction():
    conn = _make_conn()
    _insert_lag_rows(conn, "xyz:NVDA", [10.0, 50.0, 30.0, 80.0])
    catching_up, direction, streak = compute_oracle_catching_up("xyz:NVDA", conn)
    assert catching_up is False

def test_catching_up_below_move_threshold():
    # All moving up but total move < LAG_MOVE_THRESHOLD_BPS (30)
    conn = _make_conn()
    _insert_lag_rows(conn, "xyz:NVDA", [10.0, 15.0, 20.0, 25.0])
    catching_up, direction, streak = compute_oracle_catching_up("xyz:NVDA", conn)
    assert catching_up is False

def test_catching_up_different_coin_ignored():
    conn = _make_conn()
    _insert_lag_rows(conn, "xyz:GOLD", [10.0, 40.0, 80.0, 130.0])
    # Asking about NVDA which has no rows
    catching_up, direction, streak = compute_oracle_catching_up("xyz:NVDA", conn)
    assert catching_up is False


# ── compute_bound_proximity ────────────────────────────────────────────────

def test_bound_proximity_no_bounds_row():
    conn = _make_conn()
    proximity, pinned = compute_bound_proximity(200.0, 200.0, "xyz:NVDA", conn)
    assert proximity is None
    assert pinned is False

def test_bound_proximity_midband():
    conn = _make_conn()
    upsert_bounds(["xyz:NVDA"], [], conn)  # uses HARDCODED_BOUNDS +-10%
    conn.commit()
    # oracle=200, lower=180, upper=220, band=40; mark=200 -> proximity=0.5
    proximity, pinned = compute_bound_proximity(200.0, 200.0, "xyz:NVDA", conn)
    assert proximity == pytest.approx(0.5)
    assert pinned is False

def test_bound_proximity_near_upper():
    conn = _make_conn()
    upsert_bounds(["xyz:NVDA"], [], conn)
    conn.commit()
    # oracle=200, upper=220; mark=219 -> proximity=(219-180)/40 = 0.975
    proximity, pinned = compute_bound_proximity(219.0, 200.0, "xyz:NVDA", conn)
    assert proximity == pytest.approx(0.975)
    assert pinned is True

def test_bound_proximity_near_lower():
    conn = _make_conn()
    upsert_bounds(["xyz:NVDA"], [], conn)
    conn.commit()
    # oracle=200, lower=180; mark=181 -> proximity=(181-180)/40 = 0.025
    proximity, pinned = compute_bound_proximity(181.0, 200.0, "xyz:NVDA", conn)
    assert proximity == pytest.approx(0.025)
    assert pinned is True

def test_bound_proximity_clamped_above_1():
    conn = _make_conn()
    upsert_bounds(["xyz:NVDA"], [], conn)
    conn.commit()
    proximity, pinned = compute_bound_proximity(999.0, 200.0, "xyz:NVDA", conn)
    assert proximity == pytest.approx(1.0)
    assert pinned is True

def test_bound_proximity_clamped_below_0():
    conn = _make_conn()
    upsert_bounds(["xyz:NVDA"], [], conn)
    conn.commit()
    proximity, pinned = compute_bound_proximity(1.0, 200.0, "xyz:NVDA", conn)
    assert proximity == pytest.approx(0.0)
    assert pinned is True


# ── compute_signal_valid ──────────────────────────────────────────────────

def test_signal_valid_all_clear():
    assert compute_signal_valid(False, "pyth_live", False) is True

def test_signal_valid_catching_up():
    assert compute_signal_valid(True, "pyth_live", False) is False

def test_signal_valid_seda_composite():
    assert compute_signal_valid(False, "seda_composite", False) is False

def test_signal_valid_bound_pinned():
    assert compute_signal_valid(False, "pyth_live", True) is False

def test_signal_valid_unknown_source_is_valid():
    # unknown source alone does not invalidate the signal
    assert compute_signal_valid(False, "unknown", False) is True

def test_signal_valid_all_flags():
    assert compute_signal_valid(True, "seda_composite", True) is False


# ── upsert_bounds ─────────────────────────────────────────────────────────

def test_upsert_bounds_hardcoded():
    conn = _make_conn()
    upsert_bounds(["xyz:NVDA", "xyz:GOLD"], [], conn)
    conn.commit()
    row = conn.execute(
        "SELECT lower_bound_pct, upper_bound_pct, source FROM hip3_bounds WHERE coin='xyz:NVDA'"
    ).fetchone()
    assert row[0] == pytest.approx(-0.10)
    assert row[1] == pytest.approx(0.10)
    assert row[2] == "hardcoded"

def test_upsert_bounds_growth_mode():
    conn = _make_conn()
    universe = [
        {"name": "xyz:NVDA", "growthMode": "enabled"},
        {"name": "xyz:GOLD"},
    ]
    upsert_bounds(["xyz:NVDA", "xyz:GOLD"], universe, conn)
    conn.commit()
    nvda = conn.execute(
        "SELECT growth_mode FROM hip3_bounds WHERE coin='xyz:NVDA'"
    ).fetchone()
    gold = conn.execute(
        "SELECT growth_mode FROM hip3_bounds WHERE coin='xyz:GOLD'"
    ).fetchone()
    assert nvda[0] == "enabled"
    assert gold[0] is None

def test_upsert_bounds_idempotent():
    conn = _make_conn()
    upsert_bounds(["xyz:NVDA"], [], conn)
    upsert_bounds(["xyz:NVDA"], [], conn)
    conn.commit()
    count = conn.execute("SELECT COUNT(*) FROM hip3_bounds WHERE coin='xyz:NVDA'").fetchone()[0]
    assert count == 1


# ── write_validity_tick ───────────────────────────────────────────────────

def test_write_validity_tick_round_trip():
    conn = _make_conn()
    write_validity_tick("2026-06-15T10:00:00", "xyz:SPCX", {
        "oracle_catching_up": 0,
        "lag_direction":      None,
        "lag_streak":         0,
        "oracle_source":      "pyth_live",
        "bound_proximity":    0.5,
        "bound_pinned":       0,
        "signal_valid":       1,
    }, conn)
    conn.commit()
    row = conn.execute(
        "SELECT oracle_source, signal_valid, bound_proximity FROM hip3_validity"
        " WHERE coin='xyz:SPCX'"
    ).fetchone()
    assert row[0] == "pyth_live"
    assert row[1] == 1
    assert row[2] == pytest.approx(0.5)
