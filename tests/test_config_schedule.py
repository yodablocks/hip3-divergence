# -- tests/test_config_schedule.py --
# Tests for is_equity_market_open() schedule logic.

import sys
import os
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config import is_equity_market_open


def _utc(year, month, day, hour, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


# Monday 2026-06-15
def test_open_monday_during_session():
    assert is_equity_market_open(_utc(2026, 6, 15, 16, 0)) is True

def test_open_exactly_at_open():
    assert is_equity_market_open(_utc(2026, 6, 15, 15, 30)) is True

def test_closed_one_minute_before_open():
    assert is_equity_market_open(_utc(2026, 6, 15, 15, 29)) is False

def test_closed_exactly_at_close():
    # 22:00 is not inclusive
    assert is_equity_market_open(_utc(2026, 6, 15, 22, 0)) is False

def test_closed_after_close():
    assert is_equity_market_open(_utc(2026, 6, 15, 23, 0)) is False

def test_closed_before_open_same_day():
    assert is_equity_market_open(_utc(2026, 6, 15, 9, 0)) is False

# Saturday 2026-06-20
def test_closed_saturday():
    assert is_equity_market_open(_utc(2026, 6, 20, 16, 0)) is False

# Sunday 2026-06-21
def test_closed_sunday():
    assert is_equity_market_open(_utc(2026, 6, 21, 16, 0)) is False

# Friday close
def test_open_friday_during_session():
    assert is_equity_market_open(_utc(2026, 6, 19, 20, 0)) is True

def test_closed_friday_after_close():
    assert is_equity_market_open(_utc(2026, 6, 19, 22, 30)) is False
