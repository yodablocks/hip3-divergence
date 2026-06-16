# -- config --
import os
from datetime import time as _time, timezone as _tz, datetime as _dt

HL_INFO_URL = "https://api.hyperliquid.xyz/info"
HERMES_URL  = "https://hermes.pyth.network/v2/updates/price/latest"
LAZER_URL   = "https://pyth-lazer.dourolabs.app/v1/latest_price"

HIP3_DEX       = os.getenv("HIP3_DEX", "xyz")
HIP3_WATCHLIST = os.getenv("HIP3_WATCHLIST", "xyz:SPCX,xyz:NVDA,xyz:TSLA,xyz:GOLD,xyz:SILVER").split(",")


def _load_pyth_feed_ids() -> dict[str, str]:
    # Env vars of the form PYTH_FEED_IDS_xyz_NVDA=<hex> -> coin "xyz:NVDA"
    ids: dict[str, str] = {}
    prefix = "PYTH_FEED_IDS_"
    for key, val in os.environ.items():
        if key.startswith(prefix) and val.strip():
            rest = key[len(prefix):]
            parts = rest.split("_", 1)
            if len(parts) == 2:
                coin = f"{parts[0]}:{parts[1]}"
                ids[coin] = val.strip()
    return ids


PYTH_FEED_IDS: dict[str, str] = _load_pyth_feed_ids()

PYTH_API_KEY = os.getenv("PYTH_API_KEY", "")


def _load_lazer_feed_ids() -> dict[str, int]:
    # Env vars of the form PYTH_LAZER_IDS_xyz_SPCX=99934 -> coin "xyz:SPCX": 99934
    ids: dict[str, int] = {}
    prefix = "PYTH_LAZER_IDS_"
    for key, val in os.environ.items():
        if key.startswith(prefix) and val.strip():
            rest = key[len(prefix):]
            parts = rest.split("_", 1)
            if len(parts) == 2:
                coin = f"{parts[0]}:{parts[1]}"
                try:
                    ids[coin] = int(val.strip())
                except ValueError:
                    pass
    return ids


LAZER_FEED_IDS: dict[str, int] = _load_lazer_feed_ids()

# Equity market schedule (UTC). Coins in this set use schedule-aware market_state.
# market_state = "fresh" during open hours, "closed" outside, "stale" if feed stops during open.
EQUITY_COINS: frozenset[str] = frozenset({"xyz:NVDA", "xyz:TSLA"})
_EQUITY_OPEN  = _time(15, 30, 0)   # 15:30 UTC
_EQUITY_CLOSE = _time(22, 0, 0)    # 22:00 UTC
_EQUITY_DAYS  = frozenset({0, 1, 2, 3, 4})  # Mon-Fri (weekday() values)


def is_equity_market_open(dt: _dt | None = None) -> bool:
    """Return True when US equity market is open (Mon-Fri 15:30-22:00 UTC)."""
    if dt is None:
        dt = _dt.now(_tz.utc)
    return dt.weekday() in _EQUITY_DAYS and _EQUITY_OPEN <= dt.time() < _EQUITY_CLOSE


POLL_INTERVAL_SECS    = int(os.getenv("POLL_INTERVAL_SECS", "15"))
LAG_BPS_THRESHOLD     = float(os.getenv("LAG_BPS_THRESHOLD", "50"))
PREMIUM_BPS_THRESHOLD = float(os.getenv("PREMIUM_BPS_THRESHOLD", "100"))
STALE_SECS_THRESHOLD  = float(os.getenv("STALE_SECS_THRESHOLD", "120"))
DB_PATH               = os.getenv("DB_PATH", "hip3.db")

# validity layer
HARDCODED_BOUNDS: dict[str, dict[str, float]] = {
    "xyz:SPCX":   {"lower": -0.10, "upper": 0.10},
    "xyz:NVDA":   {"lower": -0.10, "upper": 0.10},
    "xyz:TSLA":   {"lower": -0.10, "upper": 0.10},
    "xyz:GOLD":   {"lower": -0.10, "upper": 0.10},
    "xyz:SILVER": {"lower": -0.10, "upper": 0.10},
}
SEDA_LAG_THRESHOLD_BPS  = float(os.getenv("SEDA_LAG_THRESHOLD_BPS", "20"))
LAG_MOVE_THRESHOLD_BPS  = float(os.getenv("LAG_MOVE_THRESHOLD_BPS", "30"))
CATCH_UP_WINDOW         = int(os.getenv("CATCH_UP_WINDOW", "4"))
BOUND_PIN_THRESHOLD     = float(os.getenv("BOUND_PIN_THRESHOLD", "0.05"))
BOUNDS_REFRESH_INTERVAL = int(os.getenv("BOUNDS_REFRESH_INTERVAL", "120"))
