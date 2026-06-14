# -- tests for hl_hip3 registry and meta parsing --
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from sources.hl_hip3 import build_coin_index, extract_ctx


SAMPLE_META = {
    "universe": [
        {"name": "xyz:XYZ100", "szDecimals": 4, "maxLeverage": 30},
        {"name": "xyz:NVDA",   "szDecimals": 2, "maxLeverage": 20},
        {"name": "xyz:SPCX",   "szDecimals": 2, "maxLeverage": 10},
        {"name": "xyz:GOLD",   "szDecimals": 2, "maxLeverage": 10},
    ]
}

SAMPLE_CTXS = [
    {"funding": "0.0001", "openInterest": "100.0",  "oraclePx": "29000.0", "markPx": "29010.0"},
    {"funding": "0.0002", "openInterest": "500.0",  "oraclePx": "135.0",   "markPx": "135.5"},
    {"funding": "0.0003", "openInterest": "1000.0", "oraclePx": "166.65",  "markPx": "166.7"},
    {"funding": "0.0004", "openInterest": "200.0",  "oraclePx": "3200.0",  "markPx": "3205.0"},
]


def test_build_coin_index_maps_names_to_indices():
    idx = build_coin_index(SAMPLE_META)
    assert idx["xyz:NVDA"] == 1
    assert idx["xyz:SPCX"] == 2
    assert idx["xyz:GOLD"] == 3


def test_build_coin_index_all_entries():
    idx = build_coin_index(SAMPLE_META)
    assert len(idx) == 4
    assert idx["xyz:XYZ100"] == 0


def test_extract_ctx_returns_correct_fields():
    idx = build_coin_index(SAMPLE_META)
    ctx = extract_ctx("xyz:SPCX", idx, SAMPLE_CTXS)
    assert ctx is not None
    assert ctx["oraclePx"] == "166.65"
    assert ctx["markPx"] == "166.7"
    assert ctx["funding"] == "0.0003"
    assert ctx["openInterest"] == "1000.0"


def test_extract_ctx_missing_coin_returns_none():
    idx = build_coin_index(SAMPLE_META)
    ctx = extract_ctx("xyz:MISSING", idx, SAMPLE_CTXS)
    assert ctx is None


def test_extract_ctx_index_out_of_range_returns_none():
    idx = {"xyz:NVDA": 99}
    ctx = extract_ctx("xyz:NVDA", idx, SAMPLE_CTXS)
    assert ctx is None


def test_build_coin_index_empty_universe():
    idx = build_coin_index({"universe": []})
    assert idx == {}
