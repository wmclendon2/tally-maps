# Copyright (c) 2026 Woody McLendon
# SPDX-License-Identifier: MIT
"""One country, one source: clip -> H3 -> pyramid. Used by scripts/build_packs.py."""

from __future__ import annotations

import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from rich.console import Console

from ingest.boundaries import fetch_boundary, load_geometries
from ingest.config import MIN_RESOLUTION
from ingest.pyramid import rollup
from ingest.raster import RasterStats, _reduce, raster_to_h3


@dataclass
class IngestResult:
    country: str
    source: str
    resolution: int
    cells: np.ndarray
    pops: np.ndarray
    levels: dict[int, tuple[np.ndarray, np.ndarray]]
    stats: list[RasterStats]

    @property
    def total(self) -> float:
        return float(self.pops.sum())


RasterPath = str | Path


def is_geotiff_member(info: zipfile.ZipInfo) -> bool:
    """A GeoTIFF in a zip, not the AppleDouble '__MACOSX/._x.tif' stub a Mac zip adds
    beside it (Meta's USA tiles carry them; GDAL cannot open one)."""
    name = info.filename
    return (
        not info.is_dir()
        and name.lower().endswith((".tif", ".tiff"))
        and not name.startswith("__MACOSX/")
        and not name.rsplit("/", 1)[-1].startswith("._")
    )


def expand_rasters(paths: list[RasterPath]) -> list[str]:
    """Resolve inputs to things rasterio can open.

    A .zip is not extracted: each GeoTIFF inside becomes a GDAL virtual path
    ('zip://archive.zip!member.tif'), which reads as fast as the extracted
    file and avoids materialising the ~300x larger uncompressed raster.
    """
    out: list[str] = []
    for p in paths:
        sp = str(p)
        if "://" in sp:
            out.append(sp)
        elif sp.lower().endswith(".zip"):
            with zipfile.ZipFile(sp) as z:
                members = sorted(i.filename for i in z.infolist() if is_geotiff_member(i))
            if not members:
                raise RuntimeError(f"{sp}: no GeoTIFF inside")
            out += [f"zip://{sp}!{m}" for m in members]
        else:
            out.append(sp)
    return out


def bin_rasters(
    paths: list[RasterPath],
    resolution: int,
    *,
    clip: list[dict] | None,
    progress: Callable[[int, int], None] | None = None,
) -> tuple[np.ndarray, np.ndarray, list[RasterStats]]:
    """raster_to_h3 over several tiles of one country, merged into one cell set."""
    cell_chunks, pop_chunks, stats = [], [], []
    for path in expand_rasters(paths):
        c, p, s = raster_to_h3(path, resolution, clip=clip, progress=progress)
        cell_chunks.append(c)
        pop_chunks.append(p)
        stats.append(s)
    cells, pops = _reduce(np.concatenate(cell_chunks), np.concatenate(pop_chunks))
    return cells, pops, stats


def run_country(
    country: str,
    source: str,
    paths: list[RasterPath],
    resolution: int,
    *,
    clip: bool = True,
    boundary: Path | None = None,
    min_resolution: int = MIN_RESOLUTION,
    console: Console | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> IngestResult:
    """Compute base cells and the pyramid for one country. Touches no database."""
    console = console or Console()
    country = country.upper()
    geoms = None
    if clip:
        bpath = boundary or fetch_boundary(country)
        geoms = load_geometries(bpath)
        console.print(f"clipping to {bpath.name} ({len(geoms)} geometries)")
    cells, pops, stats = bin_rasters(paths, resolution, clip=geoms, progress=progress)
    levels = rollup(cells, pops, resolution, min_resolution)
    return IngestResult(country, source, resolution, cells, pops, levels, stats)
