# -- tests for hl_hip3_divergence spread and price parsing --
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from sources.hl_hip3_divergence import (
    parse_hermes_price,
    compute_spreads,
    build_hermes_params,
)


def test_parse_hermes_price_basic():
    parsed_entry = {
        "id": "abcd1234",
        "price": {
            "price": "20509869",
            "conf": "202094",
            "expo": -5,
            "publish_time": 1781294420,
        },
    }
    price, conf, publish_time = parse_hermes_price(parsed_entry)
    assert abs(price - 205.09869) < 0.0001
    assert abs(conf - 2.02094) < 0.0001
    assert publish_time == 1781294420


def test_parse_hermes_price_negative_expo():
    parsed_entry = {
        "id": "abcd",
        "price": {"price": "42174", "conf": "58", "expo": -3, "publish_time": 12345},
    }
    price, conf, _ = parse_hermes_price(parsed_entry)
    assert abs(price - 42.174) < 0.0001
    assert abs(conf - 0.058) < 0.0001


def test_compute_spreads_basic():
    oracle_lag, mark_premium = compute_spreads(
        pyth_px=100.0,
        hl_oracle_px=100.5,
        hl_mark_px=101.0,
    )
    # oracle_lag_bps = (100.5 - 100.0) / 100.0 * 1e4 = 50.0
    assert abs(oracle_lag - 50.0) < 0.001
    # mark_premium_bps = (101.0 - 100.5) / 100.5 * 1e4 ~= 49.75
    assert abs(mark_premium - 49.75) < 0.1


def test_compute_spreads_no_pyth():
    oracle_lag, mark_premium = compute_spreads(
        pyth_px=None,
        hl_oracle_px=100.5,
        hl_mark_px=101.0,
    )
    assert oracle_lag is None
    assert mark_premium is not None
    assert abs(mark_premium - 49.75) < 0.1


def test_compute_spreads_zero_oracle_px():
    oracle_lag, mark_premium = compute_spreads(
        pyth_px=None,
        hl_oracle_px=0.0,
        hl_mark_px=1.0,
    )
    assert mark_premium is None


def test_compute_spreads_zero_pyth():
    oracle_lag, mark_premium = compute_spreads(
        pyth_px=0.0,
        hl_oracle_px=1.0,
        hl_mark_px=1.0,
    )
    assert oracle_lag is None


def test_build_hermes_params_filters_to_known_feeds():
    watchlist = ["xyz:SPCX", "xyz:NVDA", "xyz:GOLD"]
    feed_ids = {"xyz:NVDA": "aaaa", "xyz:GOLD": "bbbb"}
    params, coin_for_id = build_hermes_params(watchlist, feed_ids)
    assert params == [("ids[]", "aaaa"), ("ids[]", "bbbb")]
    assert coin_for_id == {"aaaa": "xyz:NVDA", "bbbb": "xyz:GOLD"}


def test_build_hermes_params_empty_when_no_feeds():
    params, coin_for_id = build_hermes_params(["xyz:SPCX"], {})
    assert params == []
    assert coin_for_id == {}
