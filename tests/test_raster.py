# Copyright (c) 2026 Woody McLendon
# SPDX-License-Identifier: MIT
import h3
import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin
from rasterio.windows import Window

from ingest.raster import raster_to_h3, strip_offsets


def _write(tmp_path, arr, nodata=-99999.0):
    path = tmp_path / "t.tif"
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
    return path


def test_mass_conserved_and_bad_pixels_skipped(tmp_path):
    arr = np.full((4, 3), -99999.0)
    arr[0, 0] = 10.0
    arr[1, 1] = 2.5
    arr[2, 2] = 0.0  # zero: skipped
    arr[3, 0] = -1.0  # negative: skipped
    arr[3, 2] = np.nan  # non-finite: skipped
    cells, pops, stats = raster_to_h3(_write(tmp_path, arr), 8, rows_per_window=2)
    assert stats.pixels_valid == 2
    assert stats.raster_total == 12.5
    assert stats.cell_total == 12.5
    assert pops.sum() == 12.5
    assert stats.windows == 2
    assert cells.dtype == np.uint64 and cells.size == stats.cells


def test_pixel_center_maps_to_expected_cell(tmp_path):
    arr = np.full((2, 2), -99999.0)
    arr[1, 0] = 7.0  # row 1, col 0 -> center lng 10.005, lat 5 - 0.015 = 4.985
    cells, pops, _ = raster_to_h3(_write(tmp_path, arr), 8)
    expected = h3.str_to_int(h3.latlng_to_cell(4.985, 10.005, 8))
    assert cells.tolist() == [expected]
    assert pops.tolist() == [7.0]


def test_neighbouring_pixels_sum_into_one_coarse_cell(tmp_path):
    arr = np.ones((2, 2)) * 3.0
    _cells, pops, stats = raster_to_h3(_write(tmp_path, arr), 3)  # res 3 cells ~ 100 km
    assert stats.cells == 1
    assert pops.tolist() == [12.0]


def test_rejects_projected_crs(tmp_path):
    path = tmp_path / "p.tif"
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=1,
        width=1,
        count=1,
        dtype="float32",
        crs="EPSG:3857",
        transform=from_origin(0, 0, 1000, 1000),
    ) as ds:
        ds.write(np.ones((1, 1), "float32"), 1)
    with pytest.raises(ValueError, match="geographic"):
        raster_to_h3(path, 8)


def test_clip_drops_pixels_outside_polygon(tmp_path):
    arr = np.ones((4, 4)) * 2.0  # 16 px, W=10..10.04, N=5..4.96
    # polygon covering the western half (columns 0-1 centers at 10.005, 10.015)
    poly = {
        "type": "Polygon",
        "coordinates": [[[10.0, 4.9], [10.02, 4.9], [10.02, 5.1], [10.0, 5.1], [10.0, 4.9]]],
    }
    _cells, pops, stats = raster_to_h3(_write(tmp_path, arr), 8, clip=[poly], rows_per_window=3)
    assert stats.pixels_valid == 8
    assert stats.pixels_clipped == 8
    assert stats.clipped_total == 16.0
    assert stats.cell_total == 16.0 == pops.sum()
    _c, _p, unclipped = raster_to_h3(_write(tmp_path, arr), 8)
    assert unclipped.pixels_valid == 16 and unclipped.pixels_clipped == 0


def _write_shuffled(tmp_path, arr, strip_rows, order):
    """A GeoTIFF whose strips sit in the file in `order` (strip indices), not row order.

    GDAL places each strip where it is first written, so writing them out of order is
    enough; this reproduces Meta HRSL files such as DZA's.
    """
    path = tmp_path / "shuffled.tif"
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=arr.shape[0],
        width=arr.shape[1],
        count=1,
        dtype="float64",
        crs="EPSG:4326",
        transform=from_origin(10.0, 5.0, 0.01, 0.01),
        nodata=float("nan"),
        blockysize=strip_rows,
    ) as ds:
        for s in order:
            r = s * strip_rows
            h = min(strip_rows, arr.shape[0] - r)
            ds.write(arr[r : r + h], 1, window=Window(0, r, arr.shape[1], h))
    return path


def _strips_out_of_order(path):
    with rasterio.open(path) as ds:
        offsets = strip_offsets(ds)
    return bool(np.any(np.diff(offsets) < 0))


@pytest.mark.parametrize("strip_rows", [1, 3])
@pytest.mark.parametrize("clipped", [False, True])
def test_file_order_read_matches_row_order(tmp_path, strip_rows, clipped):
    rng = np.random.default_rng(7)
    arr = rng.uniform(0.5, 9.0, (20, 9))
    arr[rng.random(arr.shape) < 0.4] = np.nan  # sparse, like HRSL
    n_strips = -(-arr.shape[0] // strip_rows)
    order = rng.permutation(n_strips).tolist()
    path = _write_shuffled(tmp_path, arr, strip_rows, order)
    assert _strips_out_of_order(path)
    clip = None
    if clipped:
        clip = [
            {
                "type": "Polygon",
                "coordinates": [
                    [[10.0, 4.8], [10.05, 4.8], [10.09, 5.0], [10.0, 5.0], [10.0, 4.8]]
                ],
            }
        ]
    kw = {"rows_per_window": 6, "clip": clip}
    c_row, p_row, s_row = raster_to_h3(path, 9, read_order="row", **kw)
    c_file, p_file, s_file = raster_to_h3(path, 9, read_order="file", **kw)
    c_auto, p_auto, _ = raster_to_h3(path, 9, **kw)
    assert c_row.tolist() == c_file.tolist() == c_auto.tolist()
    assert p_row.tolist() == p_file.tolist() == p_auto.tolist()
    for f in ("pixels_valid", "pixels_clipped", "clipped_total", "raster_total", "cell_total"):
        assert getattr(s_row, f) == getattr(s_file, f), f
    assert s_row.pixels_valid == np.count_nonzero(np.isfinite(arr)) - s_row.pixels_clipped
    if clipped:
        assert s_row.pixels_clipped > 0


def test_file_order_progress_counts_every_window(tmp_path):
    arr = np.ones((10, 4))
    path = _write_shuffled(tmp_path, arr, 1, [9, 3, 0, 7, 1, 5, 2, 8, 4, 6])
    seen = []
    _c, pops, stats = raster_to_h3(
        path, 8, rows_per_window=4, read_order="file", progress=lambda d, t: seen.append((d, t))
    )
    assert seen == [(1, 3), (2, 3), (3, 3)]
    assert stats.windows == 3 and pops.sum() == 40.0


def test_in_order_file_keeps_row_order_path(tmp_path):
    path = _write_shuffled(tmp_path, np.ones((6, 3)), 1, list(range(6)))
    assert not _strips_out_of_order(path)
    _c, pops, stats = raster_to_h3(path, 8, rows_per_window=4)  # auto: no rounding to strips
    assert stats.windows == 2 and pops.sum() == 18.0


def test_bad_read_order_rejected(tmp_path):
    with pytest.raises(ValueError, match="read_order"):
        raster_to_h3(_write(tmp_path, np.ones((2, 2))), 8, read_order="random")
