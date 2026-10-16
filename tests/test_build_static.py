# Copyright (c) 2026 Woody McLendon
# SPDX-License-Identifier: MIT
"""The static build's data file and page wiring. No rasters needed: synthetic cells only."""

import sys
from pathlib import Path

import h3
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from build_packs import decode, encode, merge
from build_static import WEB

from ingest.pyramid import rollup


def pyramid(lat, lng, pop, base=9):
    cells = np.array([h3.str_to_int(h3.latlng_to_cell(lat, lng, base))], dtype=np.uint64)
    levels = rollup(cells, np.array([pop], dtype=np.float64), base, 3)
    levels[base] = (cells, np.array([pop]))
    return levels


def test_encode_round_trips_every_level():
    levels = merge([pyramid(3.87, 11.52, 1000.5), pyramid(4.36, 18.56, 250.0)])
    raw, sections = encode(levels)
    back = decode(raw, sections)
    assert [s["resolution"] for s in sections] == list(range(3, 10))
    for res, (cells, pops) in levels.items():
        assert np.array_equal(back[res][0], cells)
        assert np.allclose(back[res][1], pops)


def test_sections_are_sorted_so_the_browser_can_binary_search():
    levels = merge([pyramid(3.87 + i * 0.3, 11.52 + i * 0.7, 10.0) for i in range(20)])
    raw, sections = encode(levels)
    for res, (cells, _) in decode(raw, sections).items():
        assert np.all(cells[:-1] < cells[1:]), f"res {res} not strictly sorted"


def test_offsets_are_four_byte_aligned():
    """static.js views the file as Uint32Array/Float32Array, which throw on odd offsets."""
    levels = merge([pyramid(3.87 + i * 0.3, 11.52, 10.0) for i in range(7)])
    _, sections = encode(levels)
    assert all(s["offset"] % 4 == 0 for s in sections)


def test_merge_sums_a_cell_shared_by_two_countries():
    """A border cell appears in both countries' clipped output and must not be lost."""
    a, b = pyramid(5.9, 14.6, 100.0), pyramid(5.9, 14.6, 40.0)
    merged = merge([a, b])
    assert merged[9][0].size == 1
    assert merged[9][1][0] == pytest.approx(140.0)
    assert merged[3][1].sum() == pytest.approx(140.0)


def test_web_points_at_its_data_once():
    html = (WEB / "index.html").read_text()
    assert html.count('<meta name="tally-data" content="data/manifest.json">') == 1


def test_static_engine_loads_before_app():
    html = (WEB / "index.html").read_text()
    assert html.index("static.js?v=") < html.index("app.js?v=")


def test_web_carries_no_analytics():
    assert "cloudflareinsights" not in (WEB / "index.html").read_text()
