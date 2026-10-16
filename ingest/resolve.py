# Copyright (c) 2026 Woody McLendon
# SPDX-License-Identifier: MIT
"""Polygon -> H3 cells at a resolution that fits the request: the Python reference for the
rule web/static.js implements in the browser (scripts/browser_parity.py checks the two agree)."""

from __future__ import annotations

from dataclasses import dataclass

import h3

from ingest.config import MIN_RESOLUTION

# What the automatic pick aims for. "Normal" detail lands here; it is a target, not a
# limit, so the resolution it chooses does not depend on the caller's ceiling.
AUTO_TARGET_CELLS = 4000
# Hard ceiling on one response. A continent at res 4 is ~33,000 cells and about 1.7 MB,
# which is the largest thing worth drawing; beyond that we refuse rather than ship
# tens of megabytes to a phone.
MAX_CELLS_LIMIT = 40000
MAX_CELLS_DEFAULT = MAX_CELLS_LIMIT
# How many levels finer than the automatic pick a caller may ask for.
MAX_DETAIL = 2


class TooManyCells(ValueError):
    pass


@dataclass(frozen=True)
class Resolved:
    resolution: int
    cells: list[str]  # H3 indexes as hex strings
    approx_area_km2: float


def _geometry(geojson: dict) -> dict:
    """Unwrap Feature/FeatureCollection to a single Polygon or MultiPolygon."""
    t = geojson.get("type")
    if t == "Feature":
        return _geometry(geojson["geometry"])
    if t == "FeatureCollection":
        feats = geojson.get("features", [])
        if len(feats) != 1:
            raise ValueError("FeatureCollection must contain exactly one feature")
        return _geometry(feats[0]["geometry"])
    if t not in ("Polygon", "MultiPolygon"):
        raise ValueError(f"geometry must be a Polygon or MultiPolygon, got {t!r}")
    return geojson


def polygon_area_km2(geometry: dict) -> float:
    """Rough area from a coarse H3 cover; only used to pick a resolution."""
    # Res 3 cells (~12,000 km^2) on the polygon, plus its boundary cells, over-estimate
    # small polygons badly, so refine: take the finest res whose cover stays small.
    for res in range(MIN_RESOLUTION, 10):
        cells = h3.geo_to_cells(geometry, res)
        n = len(cells)
        if n >= 20 or res == 9:
            return sum(h3.cell_area(c, "km^2") for c in cells) if n else 0.0
    return 0.0


def choose_resolution(area_km2: float, base_resolution: int, max_cells: int) -> int:
    """Finest resolution in [MIN_RESOLUTION, base] whose expected cell count fits."""
    for res in range(base_resolution, MIN_RESOLUTION - 1, -1):
        if area_km2 / h3.average_hexagon_area(res, "km^2") <= max_cells:
            return res
    return MIN_RESOLUTION


def auto_resolution(geometry: dict, base_resolution: int, area_km2: float) -> int:
    """The resolution "Normal" detail uses: finest whose real cover stays near the target.

    choose_resolution works from an estimated area, which undershoots on thin shapes, so
    the estimate is verified against an actual cover and stepped coarser if it was wrong.
    """
    resolution = choose_resolution(area_km2, base_resolution, AUTO_TARGET_CELLS)
    while resolution > MIN_RESOLUTION and len(h3.geo_to_cells(geometry, resolution)) > (
        AUTO_TARGET_CELLS
    ):
        resolution -= 1
    return resolution


def resolve(
    geojson: dict,
    base_resolution: int,
    *,
    resolution: int | None = None,
    detail: int = 0,
    max_cells: int = MAX_CELLS_DEFAULT,
) -> Resolved:
    """Cells whose centers lie inside the polygon, at an explicit or auto-chosen resolution.

    `detail` steps that many levels finer than the automatic pick, so "Fine" is always one
    level above "Normal" rather than a side effect of a larger cell budget. It stops at the
    source's base resolution: the pyramid only rolls coarser, so there is nothing below it
    and asking anyway would return no rows at all rather than more detail.
    """
    geometry = _geometry(geojson)
    max_cells = min(max_cells, MAX_CELLS_LIMIT)
    # Costs a polyfill per level, so it is measured once and shared with auto_resolution.
    area = polygon_area_km2(geometry)
    if resolution is not None:
        if not MIN_RESOLUTION <= resolution <= base_resolution:
            raise ValueError(f"resolution must be between {MIN_RESOLUTION} and {base_resolution}")
    else:
        if not 0 <= detail <= MAX_DETAIL:
            raise ValueError(f"detail must be between 0 and {MAX_DETAIL}")
        resolution = min(auto_resolution(geometry, base_resolution, area) + detail, base_resolution)
    cells = h3.geo_to_cells(geometry, resolution)
    if len(cells) > max_cells:
        # Never quietly answer at a different resolution than the one asked for: the caller
        # would get a coarser number with no way to tell, and "Fine" would silently become
        # "Normal" again. Say what happened and what would work instead.
        hint = (
            "Try a smaller area."
            if resolution <= MIN_RESOLUTION
            else "Try less detail, or a smaller area."
        )
        raise TooManyCells(
            f"That area needs {len(cells):,} cells at H3 resolution {resolution}, "
            f"over the {max_cells:,} cell limit. {hint}"
        )
    if not cells:
        # Tiny polygon with no cell center inside: use the cell containing its first vertex.
        ring = (
            geometry["coordinates"][0]
            if geometry["type"] == "Polygon"
            else geometry["coordinates"][0][0]
        )
        lng, lat = ring[0][:2]
        cells = [h3.latlng_to_cell(lat, lng, resolution)]
    return Resolved(resolution, sorted(cells), area)
