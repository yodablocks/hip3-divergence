# -- config --
import os

HL_INFO_URL = "https://api.hyperliquid.xyz/info"
HERMES_URL  = "https://hermes.pyth.network/v2/updates/price/latest"

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

POLL_INTERVAL_SECS    = int(os.getenv("POLL_INTERVAL_SECS", "15"))
LAG_BPS_THRESHOLD     = float(os.getenv("LAG_BPS_THRESHOLD", "50"))
PREMIUM_BPS_THRESHOLD = float(os.getenv("PREMIUM_BPS_THRESHOLD", "100"))
STALE_SECS_THRESHOLD  = float(os.getenv("STALE_SECS_THRESHOLD", "120"))
DB_PATH               = os.getenv("DB_PATH", "hip3.db")
