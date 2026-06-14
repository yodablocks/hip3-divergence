# -- sources/hl_hip3_divergence.py --
# Price fetchers and spread computation for the HIP-3 three-price monitor.
#
# Public API
# ----------
# build_hermes_params(watchlist, feed_ids)  -> (params, coin_for_id)
# parse_hermes_price(parsed_entry)          -> (price, conf, publish_time)
# fetch_hermes_prices(watchlist, feed_ids)  -> dict[str, tuple]
# fetch_hl_prices(dex)                      -> tuple[dict[str,int], list[dict]]
# compute_spreads(pyth_px, hl_oracle_px, hl_mark_px) -> (oracle_lag_bps | None, mark_premium_bps | None)

import logging
from typing import Any

import requests

import config
from sources.hl_hip3 import build_coin_index, fetch_hip3_meta

log = logging.getLogger(__name__)


def build_hermes_params(
    watchlist: list[str],
    feed_ids: dict[str, str],
) -> tuple[list[tuple[str, str]], dict[str, str]]:
    """
    Build the query-param list for the Hermes batch endpoint and an inverse map.

    Returns
    -------
    params       : list of ("ids[]", hex_id) tuples for requests
    coin_for_id  : dict mapping hex_id -> coin name (for response parsing)
    """
    params: list[tuple[str, str]] = []
    coin_for_id: dict[str, str] = {}
    for coin in watchlist:
        feed_id = feed_ids.get(coin)
        if feed_id:
            params.append(("ids[]", feed_id))
            coin_for_id[feed_id] = coin
    return params, coin_for_id


def parse_hermes_price(
    parsed_entry: dict[str, Any],
) -> tuple[float, float, int]:
    """
    Convert a Hermes parsed price entry to (price, conf, publish_time).
    Applies the exponent: actual_price = int(price_str) * 10^expo.
    """
    px = parsed_entry["price"]
    expo = px["expo"]
    scale = 10 ** expo
    price = int(px["price"]) * scale
    conf  = int(px["conf"])  * scale
    return price, conf, int(px["publish_time"])


def fetch_hermes_prices(
    watchlist: list[str],
    feed_ids: dict[str, str],
) -> dict[str, tuple[float, float, int]]:
    """
    Batch fetch latest Pyth prices for all watchlist coins that have a feed id.

    Returns dict[coin] = (price, conf, publish_time).
    Coins without a feed id are absent from the result.
    Raises on network error -- caller must handle.
    """
    params, coin_for_id = build_hermes_params(watchlist, feed_ids)
    if not params:
        return {}

    resp = requests.get(config.HERMES_URL, params=params, timeout=10)
    resp.raise_for_status()
    data = resp.json()

    result: dict[str, tuple[float, float, int]] = {}
    for entry in data.get("parsed", []):
        feed_id = entry.get("id", "")
        coin = coin_for_id.get(feed_id)
        if coin is None:
            log.warning("fetch_hermes_prices: unexpected feed id %s in response", feed_id)
            continue
        result[coin] = parse_hermes_price(entry)

    return result


def fetch_hl_prices(dex: str) -> tuple[dict[str, int], list[dict]]:
    """
    Fetch one metaAndAssetCtxs call for the dex.
    Returns (coin_index, ctxs_list) where coin_index maps coin_name -> universe_idx.
    Raises on network error.
    """
    meta, ctxs = fetch_hip3_meta(dex)
    return build_coin_index(meta), ctxs


def compute_spreads(
    pyth_px: float | None,
    hl_oracle_px: float,
    hl_mark_px: float,
) -> tuple[float | None, float | None]:
    """
    Compute oracle_lag_bps and mark_premium_bps.

    oracle_lag_bps   = (hl_oracle_px - pyth_px)      / pyth_px      * 1e4
    mark_premium_bps = (hl_mark_px   - hl_oracle_px) / hl_oracle_px * 1e4

    Returns None for a spread when the denominator is zero or pyth_px is absent.
    """
    if pyth_px is not None and pyth_px != 0.0:
        oracle_lag: float | None = (hl_oracle_px - pyth_px) / pyth_px * 1e4
    else:
        oracle_lag = None

    if hl_oracle_px != 0.0:
        mark_premium: float | None = (hl_mark_px - hl_oracle_px) / hl_oracle_px * 1e4
    else:
        mark_premium = None

    return oracle_lag, mark_premium
