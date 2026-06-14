# -- sources/hl_hip3.py --
# Hyperliquid HIP-3 perp dex registry and meta fetcher.
#
# Public API
# ----------
# build_coin_index(meta)            dict[str, int]
# extract_ctx(coin, index, ctxs)    dict | None
# fetch_perp_dexs()                 list[dict]
# fetch_hip3_meta(dex)              tuple[dict, list[dict]]
# build_registry(db_path, dex)      None

import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any

import requests

import config

log = logging.getLogger(__name__)


def build_coin_index(meta: dict) -> dict[str, int]:
    """Return {coin_name: universe_index} from a metaAndAssetCtxs meta dict."""
    return {
        entry["name"]: i
        for i, entry in enumerate(meta.get("universe", []))
        if "name" in entry
    }


def extract_ctx(
    coin: str,
    index: dict[str, int],
    ctxs: list[dict],
) -> dict[str, Any] | None:
    """
    Return the ctx dict for a coin using the pre-built index.
    Returns None if the coin is not in the index or index is out of range.
    """
    idx = index.get(coin)
    if idx is None:
        return None
    if idx >= len(ctxs):
        log.warning("extract_ctx: index %d out of range for %s (ctxs len %d)", idx, coin, len(ctxs))
        return None
    return ctxs[idx]


def fetch_perp_dexs() -> list[dict]:
    """Fetch all deployed HIP-3 perp dexs from the HL info API."""
    resp = requests.post(
        config.HL_INFO_URL,
        json={"type": "perpDexs"},
        headers={"Content-Type": "application/json"},
        timeout=10,
    )
    resp.raise_for_status()
    return [d for d in resp.json() if d is not None]


def fetch_hip3_meta(dex: str) -> tuple[dict, list[dict]]:
    """
    Fetch metaAndAssetCtxs for a single dex.
    Returns (meta_dict, ctxs_list).
    """
    resp = requests.post(
        config.HL_INFO_URL,
        json={"type": "metaAndAssetCtxs", "dex": dex},
        headers={"Content-Type": "application/json"},
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    return data[0], data[1]


def build_registry(db_path: str = config.DB_PATH, dex: str = config.HIP3_DEX) -> None:
    """
    Upsert one row per HIP-3 market into hip3_markets.
    Deployer and oracle_updater come from perpDexs where available.
    """
    dexs = fetch_perp_dexs()
    dex_meta = next((d for d in dexs if d.get("name") == dex), None)
    if dex_meta is None:
        raise ValueError(f"Dex '{dex}' not found in perpDexs response")

    deployer       = dex_meta.get("deployer")
    oracle_updater = dex_meta.get("oracleUpdater")

    meta, _ctxs = fetch_hip3_meta(dex)
    now = datetime.now(timezone.utc).isoformat()

    with sqlite3.connect(db_path) as conn:
        rows_upserted = 0
        for entry in meta.get("universe", []):
            coin = entry.get("name")
            if not coin or not coin.startswith(f"{dex}:"):
                continue

            pyth_feed_id = config.PYTH_FEED_IDS.get(coin)
            max_leverage = entry.get("maxLeverage")

            conn.execute("""
                INSERT INTO hip3_markets
                    (coin, dex, display_name, asset_class, deployer, oracle_updater,
                     max_leverage, pyth_feed_id, first_seen, last_seen)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(coin) DO UPDATE SET
                    last_seen      = excluded.last_seen,
                    max_leverage   = excluded.max_leverage,
                    pyth_feed_id   = COALESCE(excluded.pyth_feed_id, hip3_markets.pyth_feed_id),
                    deployer       = COALESCE(excluded.deployer, hip3_markets.deployer),
                    oracle_updater = COALESCE(excluded.oracle_updater, hip3_markets.oracle_updater)
            """, (
                coin,
                dex,
                coin.split(":", 1)[1],
                None,
                deployer,
                oracle_updater,
                max_leverage,
                pyth_feed_id,
                now,
                now,
            ))
            rows_upserted += 1

        conn.commit()

    log.info("build_registry: upserted %d markets for dex '%s'", rows_upserted, dex)
