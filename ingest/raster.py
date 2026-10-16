# Copyright (c) 2026 Woody McLendon
# SPDX-License-Identifier: MIT
"""Bin a population GeoTIFF into H3 cells.

Each populated pixel is assigned, whole, to the H3 cell containing its center.
Nodata, non-finite and non-positive pixels are skipped. Work proceeds window by
window so rasters with hundreds of millions of pixels never need to be resident
at once; each window is reduced to (cell, sum) pairs before the next is read.

Strip order matters because rasters are read straight out of their zip. Deflate
streams only decompress forwards, so every backward seek makes GDAL re-inflate from
an earlier snapshot. Some Meta HRSL GeoTIFFs store their strips out of row order
(DZA: 5,037 backward jumps over 68,400 strips), which made reading in row order
take 26 min for a file that inflates end to end in 88 s. When a stripped file is
out of order, strips are therefore read in file-offset order, and each window's
populated pixels are held until all of its strips have arrived. The result is
identical either way; only the read order changes.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import rasterio
from h3.api import numpy_int as h3
from rasterio.features import geometry_mask
from rasterio.windows import Window


@dataclass
class RasterStats:
    path: str
    crs: str
    width: int
    height: int
    nodata: float | None
    pixels_valid: int = 0
    pixels_clipped: int = 0
    clipped_total: float = 0.0
    raster_total: float = 0.0
    cells: int = 0
    cell_total: float = 0.0
    windows: int = 0
    bounds: tuple[float, float, float, float] = field(default=(0.0, 0.0, 0.0, 0.0))


def _row_windows(height: int, width: int, rows_per_window: int) -> Iterable[Window]:
    for row_off in range(0, height, rows_per_window):
        yield Window(0, row_off, width, min(rows_per_window, height - row_off))


def strip_offsets(ds) -> np.ndarray | None:
    """File offset of each strip, or None for a tiled raster (only strips are reordered).

    A sparse strip (never written, offset 0) costs no I/O and reads as nodata.
    """
    block_rows, block_cols = ds.block_shapes[0]
    if block_cols != ds.width:
        return None
    n = (ds.height + block_rows - 1) // block_rows
    return np.array(
        [int(ds.get_tag_item(f"BLOCK_OFFSET_0_{i}", "TIFF", bidx=1) or 0) for i in range(n)],
        dtype=np.int64,
    )


def _valid_pixels(block: np.ndarray, nodata: float | None) -> tuple[np.ndarray, np.ndarray]:
    valid = np.isfinite(block) & (block > 0)
    if nodata is not None:
        valid &= block != nodata
    return np.nonzero(valid)


def _reduce(cells: np.ndarray, pops: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Sum `pops` grouped by `cells`; returns sorted unique cells and their sums."""
    if cells.size == 0:
        return cells.astype(np.uint64), pops.astype(np.float64)
    uniq, inverse = np.unique(cells, return_inverse=True)
    sums = np.bincount(inverse, weights=pops, minlength=uniq.size)
    return uniq, sums


def pixels_to_cells(lats: np.ndarray, lngs: np.ndarray, resolution: int) -> np.ndarray:
    """H3 cell (uint64) for each lat/lng pair."""
    out = np.empty(lats.size, dtype=np.uint64)
    to_cell = h3.latlng_to_cell
    for i, (lat, lng) in enumerate(zip(lats.tolist(), lngs.tolist(), strict=True)):
        out[i] = to_cell(lat, lng, resolution)
    return out


def raster_to_h3(
    path: str | Path,
    resolution: int,
    *,
    rows_per_window: int = 512,
    clip: list[dict] | None = None,
    progress: Callable[[int, int], None] | None = None,
    read_order: str = "auto",
) -> tuple[np.ndarray, np.ndarray, RasterStats]:
    """Return (cell_ids uint64, population float64, stats) for a GeoTIFF.

    The raster must be in geographic WGS 84 coordinates (EPSG:4326), which both
    WorldPop and Meta HRSL products are. Pixel centers are computed from the
    geotransform, never stored. `clip` is a list of GeoJSON-like geometries in
    the same CRS; pixels whose center falls outside all of them are dropped and
    counted in stats.pixels_clipped / stats.clipped_total.

    `read_order` is "auto" (file order only when a stripped file stores its strips out of
    row order), "row" or "file"; see the module docstring. It never changes the result.
    """
    if read_order not in ("auto", "row", "file"):
        raise ValueError(f"read_order must be auto, row or file, not {read_order!r}")
    # GDAL virtual paths like 'zip://archive.zip!file.tif' must not be turned into Paths.
    path = path if isinstance(path, str) and "://" in path else Path(path)
    with rasterio.open(path) as ds:
        if ds.count != 1:
            raise ValueError(f"{path}: expected a single band, found {ds.count}")
        if ds.crs is None or not ds.crs.is_geographic:
            raise ValueError(f"{path}: expected a geographic CRS (EPSG:4326), got {ds.crs}")
        stats = RasterStats(
            path=str(path),
            crs=ds.crs.to_string(),
            width=ds.width,
            height=ds.height,
            nodata=ds.nodata,
            bounds=tuple(ds.bounds),
        )
        t = ds.transform
        nodata = ds.nodata
        offsets = strip_offsets(ds) if read_order != "row" else None
        if offsets is not None and read_order == "auto":
            stored = offsets[offsets > 0]
            if not np.any(np.diff(stored) < 0):
                offsets = None
        strip_rows = ds.block_shapes[0][0]
        if offsets is not None:
            # Whole strips per window, so a strip never straddles two windows.
            rows_per_window = -(-rows_per_window // strip_rows) * strip_rows
        windows = list(_row_windows(ds.height, ds.width, rows_per_window))
        cell_chunks: list[np.ndarray] = []
        pop_chunks: list[np.ndarray] = []

        def finish(win: Window, rows: np.ndarray, cols: np.ndarray, pops: np.ndarray) -> None:
            """Clip and bin one window's populated pixels (rows relative to the window)."""
            if clip is not None and rows.size:
                inside = geometry_mask(
                    clip,
                    out_shape=(win.height, win.width),
                    transform=ds.window_transform(win),
                    invert=True,
                )[rows, cols]
                if not inside.all():
                    stats.pixels_clipped += int((~inside).sum())
                    stats.clipped_total += float(pops[~inside].sum())
                    rows, cols, pops = rows[inside], cols[inside], pops[inside]
            if rows.size:
                stats.pixels_valid += rows.size
                stats.raster_total += float(pops.sum())
                # Pixel centers: offset by 0.5 within the window's absolute row/col.
                abs_rows = rows + win.row_off + 0.5
                abs_cols = cols + win.col_off + 0.5
                lngs = t.c + t.a * abs_cols + t.b * abs_rows
                lats = t.f + t.d * abs_cols + t.e * abs_rows
                c, p = _reduce(pixels_to_cells(lats, lngs, resolution), pops)
                cell_chunks.append(c)
                pop_chunks.append(p)
            stats.windows += 1
            if progress:
                progress(stats.windows, len(windows))

        if offsets is None:
            for win in windows:
                block = ds.read(1, window=win)
                rows, cols = _valid_pixels(block, nodata)
                finish(win, rows, cols, block[rows, cols].astype(np.float64))
        else:
            # Strips in file order; each window's pixels wait until its last strip is read.
            pending: dict[int, dict[int, tuple[np.ndarray, np.ndarray, np.ndarray]]] = {}
            for strip in np.argsort(offsets, kind="stable").tolist():
                row_off = strip * strip_rows
                block = ds.read(
                    1, window=Window(0, row_off, ds.width, min(strip_rows, ds.height - row_off))
                )
                rows, cols = _valid_pixels(block, nodata)
                w = row_off // rows_per_window
                win = windows[w]
                got = pending.setdefault(w, {})
                got[strip] = (
                    rows + (row_off - win.row_off),
                    cols,
                    block[rows, cols].astype(np.float64),
                )
                if len(got) == -(-win.height // strip_rows):
                    del pending[w]
                    parts = [got[k] for k in sorted(got)]  # row order, as the row path sees it
                    finish(win, *(np.concatenate([p[i] for p in parts]) for i in range(3)))

    if cell_chunks:
        cells, pops = _reduce(np.concatenate(cell_chunks), np.concatenate(pop_chunks))
    else:
        cells, pops = np.empty(0, dtype=np.uint64), np.empty(0, dtype=np.float64)
    stats.cells = int(cells.size)
    stats.cell_total = float(pops.sum())
    return cells, pops, stats
