# Copyright (c) 2026 Woody McLendon
# SPDX-License-Identifier: MIT
"""Offline checks for the outline-overlap audit. No data/ files needed."""

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import MultiPolygon, box

import scripts.audit_overlaps as ao


def _write(tmp_path, arr, name="t.tif", nodata=-99999.0):
    path = tmp_path / name
    transform = from_origin(10.0, 5.0, 0.01, 0.01)  # W=10, N=5, 0.01 deg pixels
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=arr.shape[0],
        width=arr.shape[1],
        count=1,
        dtype="float32",
        crs="EPSG:4326",
        transform=transform,
        nodata=nodata,
    ) as ds:
        ds.write(arr.astype("float32"), 1)
    return str(path)


def test_overlapping_outlines_make_one_pair_with_their_shared_area():
    found = ao.overlaps({"AAA": box(0, 0, 2, 2), "BBB": box(1, 1, 3, 3)})
    assert list(found) == [("AAA", "BBB")]
    assert found[("AAA", "BBB")].equals(box(1, 1, 2, 2))


def test_outlines_that_only_share_a_border_do_not_overlap():
    assert ao.overlaps({"AAA": box(0, 0, 1, 1), "BBB": box(1, 0, 2, 1)}) == {}


def test_overlaps_are_found_between_islands_of_a_multipolygon():
    islands = MultiPolygon([box(0, 0, 1, 1), box(5, 5, 6, 6)])
    found = ao.overlaps({"AAA": islands, "BBB": box(5.5, 5.5, 7, 7)})
    assert found[("AAA", "BBB")].equals(box(5.5, 5.5, 6, 6))


def test_pairs_with_keeps_only_pairs_that_include_those_countries():
    outlines = {"AAA": box(0, 0, 2, 2), "BBB": box(1, 1, 3, 3), "CCC": box(2.5, 2.5, 4, 4)}
    assert set(ao.overlaps(outlines)) == {("AAA", "BBB"), ("BBB", "CCC")}
    assert set(ao.overlaps(outlines, {"CCC"})) == {("BBB", "CCC")}


def test_raster_people_counts_pixels_whose_centre_is_inside(tmp_path):
    arr = np.full((4, 4), 1.0)  # pixel (r, c) centre: lng 10.005 + 0.01c, lat 4.995 - 0.01r
    arr[0, 0] = 5.0
    arr[0, 1] = -99999.0  # nodata
    arr[1, 0] = -1.0  # negative: not people
    path = _write(tmp_path, arr)
    # Takes in the centres of the top-left 2x2 pixels only (lng < 10.02, lat > 4.98).
    area = box(10.0, 4.981, 10.019, 5.0)
    assert ao.raster_people([path], {"x": area}) == {"x": 5.0 + 1.0}  # (0,0) and (1,1)


def test_raster_people_reads_in_row_chunks_and_ignores_rasters_elsewhere(tmp_path, monkeypatch):
    monkeypatch.setattr(ao, "ROWS_PER_READ", 1)
    path = _write(tmp_path, np.full((4, 4), 2.0))
    assert ao.raster_people([path], {"x": box(9.0, 4.0, 11.0, 6.0)}) == {"x": 32.0}
    assert ao.raster_people([path], {"x": box(20.0, 4.0, 21.0, 6.0)}) == {"x": 0.0}


def test_one_pass_over_several_areas_matches_reading_each_alone(tmp_path, monkeypatch):
    monkeypatch.setattr(ao, "ROWS_PER_READ", 3)
    rng = np.random.default_rng(1)
    path = _write(tmp_path, rng.integers(0, 9, size=(20, 20)).astype(float))
    areas = {
        "top-left": box(10.0, 4.9, 10.05, 5.0),
        "bottom-right": box(10.12, 4.8, 10.2, 4.86),
        "across": box(10.03, 4.85, 10.15, 4.95),
        "outside": box(30.0, 4.8, 30.1, 4.9),
    }
    together = ao.raster_people([path], areas)
    assert together == {k: ao.raster_people([path], {k: g})[k] for k, g in areas.items()}
    assert together["outside"] == 0.0 and all(v > 0 for k, v in together.items() if k != "outside")


def test_both_is_the_smaller_side_and_zero_when_a_side_was_not_read():
    assert ao.Overlap("AAA", "BBB", 1.0, 10.0, 4.0).both == 4.0
    assert ao.Overlap("AAA", "BBB", 1.0, 10.0, None).both == 0.0


@pytest.mark.parametrize(
    ("bbox", "expected"),
    [([0, 0, 1, 1], ["AAA"]), ([1.5, 1.5, 3, 3], ["BBB"]), ([0.5, 0.5, 2.5, 2.5], ["AAA", "BBB"])],
)
def test_near_finds_countries_whose_boxes_meet(bbox, expected):
    boxes = {"AAA": [0, 0, 1, 1], "BBB": [2, 2, 3, 3]}
    assert ao.near(bbox, boxes) == expected
