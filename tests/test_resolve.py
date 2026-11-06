# Copyright (c) 2026 Woody McLendon
# SPDX-License-Identifier: MIT
"""The resolution rule (ingest/resolve.py) that web/static.js ports."""

import h3
import pytest

from ingest.config import MIN_RESOLUTION
from ingest.resolve import TooManyCells, choose_resolution, resolve

YAOUNDE = {  # ~20 x 20 km box around Yaoundé
    "type": "Polygon",
    "coordinates": [[[11.42, 3.77], [11.62, 3.77], [11.62, 3.97], [11.42, 3.97], [11.42, 3.77]]],
}
CAMEROON_BOX = {
    "type": "Polygon",
    "coordinates": [[[8.5, 1.7], [16.2, 1.7], [16.2, 13.0], [8.5, 13.0], [8.5, 1.7]]],
}


def test_choose_resolution_scales_with_area():
    assert choose_resolution(1.0, 9, 4000) == 9
    assert choose_resolution(600.0, 9, 4000) == 8  # 600 km2 / 0.105 km2 > 4000 at res 9
    assert choose_resolution(1e6, 9, 4000) == 5  # 3,968 res-5 cells just fits
    assert choose_resolution(2e6, 9, 4000) == 4
    assert choose_resolution(1e9, 9, 4000) == 3


def test_resolve_auto_and_explicit():
    r = resolve(YAOUNDE, 9)
    assert r.resolution == 8 and 0 < len(r.cells) <= 4000
    assert all(h3.get_resolution(c) == 8 for c in r.cells)
    r7 = resolve(YAOUNDE, 9, resolution=7)
    assert r7.resolution == 7 and len(r7.cells) < len(r.cells)
    with pytest.raises(ValueError):
        resolve(YAOUNDE, 9, resolution=10)


def test_resolve_feature_and_multipolygon_and_tiny():
    feat = {"type": "Feature", "properties": {}, "geometry": YAOUNDE}
    assert resolve(feat, 9, resolution=7).cells == resolve(YAOUNDE, 9, resolution=7).cells
    mp = {"type": "MultiPolygon", "coordinates": [YAOUNDE["coordinates"]]}
    assert resolve(mp, 9, resolution=7).cells == resolve(YAOUNDE, 9, resolution=7).cells
    tiny = {
        "type": "Polygon",
        "coordinates": [[[11.5, 3.8], [11.5001, 3.8], [11.5001, 3.8001], [11.5, 3.8]]],
    }
    r = resolve(tiny, 9, resolution=9)
    assert len(r.cells) == 1
    with pytest.raises(ValueError):
        resolve({"type": "Point", "coordinates": [0, 0]}, 9)


def test_over_budget_refuses_instead_of_answering_coarser():
    """Silently dropping a level would hand back a smaller number with no way to tell."""
    with pytest.raises(TooManyCells) as exc:
        resolve(CAMEROON_BOX, 9, max_cells=500)
    msg = str(exc.value)
    assert "cells at H3 resolution" in msg and "500 cell limit" in msg
    assert "less detail" in msg, "should say what would work instead"


def test_over_budget_at_the_floor_does_not_suggest_less_detail():
    with pytest.raises(TooManyCells) as exc:
        resolve(CAMEROON_BOX, 9, resolution=MIN_RESOLUTION, max_cells=1)
    assert "less detail" not in str(exc.value), "there is no coarser level to offer"


def test_fine_is_always_one_level_finer_than_normal():
    for geom in (YAOUNDE, CAMEROON_BOX):
        normal = resolve(geom, 9, detail=0)
        fine = resolve(geom, 9, detail=1)
        assert fine.resolution == normal.resolution + 1
        assert len(fine.cells) > len(normal.cells)


def test_detail_stops_at_the_base_resolution():
    """The pyramid only rolls coarser; res 10 rows do not exist, so asking is meaningless."""
    small = resolve(YAOUNDE, 9, detail=0)
    assert resolve(YAOUNDE, small.resolution, detail=1).resolution == small.resolution
    assert resolve(YAOUNDE, small.resolution, detail=2).resolution == small.resolution


def test_detail_is_ignored_when_a_resolution_is_given():
    assert resolve(YAOUNDE, 9, resolution=6, detail=1).resolution == 6


def test_normal_resolution_does_not_depend_on_the_cell_ceiling():
    """The old coupling is what made the toggle a no-op on a large shape."""
    base = resolve(CAMEROON_BOX, 9, detail=0).resolution
    assert resolve(CAMEROON_BOX, 9, detail=0, max_cells=40000).resolution == base


def _box(w, s, e, n):
    return {"type": "Polygon", "coordinates": [[[w, s], [e, s], [e, n], [w, n], [w, s]]]}


def test_resolve_counts_cells_past_180():  # F-39: a polyfill drops them with no error
    across = resolve(_box(179.5, -17, 180.5, -16), 9, resolution=5).cells
    west = h3.geo_to_cells(_box(179.5, -17, 180, -16), 5)
    east = h3.geo_to_cells(_box(-180, -17, -179.5, -16), 5)
    assert set(across) == set(west) | set(east)
    assert east  # the side the fill had been dropping


def test_resolve_wraps_shapes_wholly_past_180():
    expected = h3.geo_to_cells(_box(-179.8, -17, -179.2, -16), 5)
    assert resolve(_box(180.2, -17, 180.8, -16), 9, resolution=5).cells == sorted(expected)
    # Scrolled a world over to the west as well.
    expected = h3.geo_to_cells(_box(11.42, 3.77, 11.62, 3.97), 8)
    assert resolve(_box(-348.58, 3.77, -348.38, 3.97), 9, resolution=8).cells == sorted(expected)


def test_wrapping_leaves_the_edge_and_the_input_alone():
    box = _box(179.5, -17, 180.5, -16)
    resolve(box, 9, resolution=5)
    assert box == _box(179.5, -17, 180.5, -16)
    edge = _box(179.5, -17, 180, -16)
    assert resolve(edge, 9, resolution=5).cells == sorted(h3.geo_to_cells(edge, 5))
