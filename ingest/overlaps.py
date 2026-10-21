# Copyright (c) 2026 Woody McLendon
# SPDX-License-Identifier: MIT
"""Overlapping outlines split between their two countries, so no one is counted twice (F-30).

Each country is clipped to its own geoBoundaries outline, and neighbouring outlines (from
different national sources) disagree about where the border runs, so the people in the
strips between them were in both packs: 1.67 M across 161 pairs (scripts/audit_overlaps.py).

So where two countries' outlines overlap, the overlap is split along geoBoundaries CGAZ's
border (a global ADM0 set with no overlaps): the part inside A's CGAZ unit goes to A, the
part inside B's to B. The rest, a piece in neither unit or in a third one (a disputed area
CGAZ draws as its own unit), goes whole to the side whose raster holds more people there,
ties to the lower ISO3 code. So does the whole overlap when CGAZ has no unit for one side:
it folds Puerto Rico, Guam, the US Virgin Islands and others into their parent, whose
raster holds no one there (split along CGAZ, Puerto Rico's 3.6 M people would go to the
USA's pack, which has none of them). A country's clip is its own outline minus the pieces
it gives away; for a WorldPop country the outline is first grown about 10 km
(boundaries.CLIP_GROW_DEG), and the split cuts it at its built neighbours.

Who gets the rest needs both rasters, so scripts/split_overlaps.py decides once for every
overlapping pair, a < b, and records it in reference/overlap_split.json (checked in). A
build needs the outlines, that file and CGAZ (162 MB, downloaded once). A gives B the
overlap inside b's side, and B gives A the rest of the overlap, so the two clips never
overlap and together keep all of it. CGAZ's line inside the overlaps is not checked in
instead: the overlaps are long, detailed strips, and the line through them alone came to
22 MB. Clips are cached as data/boundaries/clip/<ISO3>_ADM0.geojson, redone when the
country's pairs change.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable, Iterable
from pathlib import Path

import numpy as np
import shapely
from shapely.geometry import Polygon, mapping
from shapely.geometry.base import BaseGeometry

from ingest.boundaries import CLIP_GROW_DEG, area_km2, clip_base, fetch_boundary
from ingest.config import BOUNDARY_DIR, DATA_DIR, ROOT
from ingest.fetch import get_bytes

# CGAZ as of geoBoundaries' 2024-04-20 update, its latest (checked 2026-10-20), pinned
# to that commit so every split is made along the same line.
CGAZ_URL = (
    "https://github.com/wmgeolab/geoBoundaries/raw/4004e96433a2bdc54cac2b88d7d2e694c89cb818/"
    "releaseData/CGAZ/geoBoundariesCGAZ_ADM0.gpkg"
)
CGAZ_SHA256 = "6bdeb27072cf3759edfe9d31e037ebaf2481d9e0fa5ad18c1036346e42b4c41a"
CGAZ_PATH = DATA_DIR / "cgaz" / "geoBoundariesCGAZ_ADM0.gpkg"
SPLIT_PATH = ROOT / "reference" / "overlap_split.json"
CLIP_DIR = BOUNDARY_DIR / "clip"
# b's side is worked out within this many degrees (~100 m) of the overlap's pieces' boxes:
# the rest of CGAZ's units never meets the overlap.
NEAR_DEG = 0.001
EMPTY = Polygon()


def polygonal(g: BaseGeometry) -> BaseGeometry:
    """The areal part of a geometry; shared edges and corners are not overlaps."""
    parts = [p for p in getattr(g, "geoms", [g]) if p.geom_type in ("Polygon", "MultiPolygon")]
    parts = [p for p in parts if not p.is_empty and p.area > 0]
    return shapely.union_all(parts) if parts else EMPTY


def fetch_cgaz(path: Path = CGAZ_PATH) -> Path:
    """geoBoundaries CGAZ ADM0 (CC BY 4.0, 162 MB), downloaded once and cached under data/."""
    if not path.exists():
        data = get_bytes(CGAZ_URL, timeout=1800)
        if hashlib.sha256(data).hexdigest() != CGAZ_SHA256:
            raise RuntimeError(f"{CGAZ_URL}: not the expected file (sha256 differs)")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return path


def gpkg_geometry(blob: bytes) -> BaseGeometry:
    """A GeoPackage geometry: "GP", version, flags, srs_id, an envelope whose size the flags
    give (none, xy, xyz, xym or xyzm), then plain WKB."""
    if blob[:2] != b"GP":
        raise ValueError("not a GeoPackage geometry")
    envelope = (0, 32, 48, 48, 64)[(blob[3] >> 1) & 7]
    return shapely.from_wkb(bytes(blob[8 + envelope :]))


def cgaz_units(codes: Iterable[str], path: Path | None = None) -> dict[str, BaseGeometry]:
    """CGAZ's units for these ISO3 codes, those it has, each made valid."""
    codes = sorted(set(codes))
    if not codes:
        return {}
    path = path or fetch_cgaz()
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as db:
        (table,) = db.execute(
            "SELECT table_name FROM gpkg_contents WHERE data_type = 'features'"
        ).fetchone()
        rows = db.execute(
            f'SELECT shapeGroup, geom FROM "{table}" WHERE shapeGroup IN '
            f"({', '.join('?' * len(codes))})",
            codes,
        ).fetchall()
    found: dict[str, list] = {}
    for code, blob in rows:
        g = gpkg_geometry(blob)
        found.setdefault(code, []).append(g if g.is_valid else g.buffer(0))
    return {k: shapely.union_all(v) for k, v in sorted(found.items())}


def pieces(g: BaseGeometry) -> list[BaseGeometry]:
    return [] if g.is_empty else list(getattr(g, "geoms", [g]))


def overlap_of(a: BaseGeometry, b: BaseGeometry) -> BaseGeometry:
    """Where two outlines overlap, polygon by polygon: only pieces whose boxes meet are
    intersected, so a coast of thousands of islands costs a box lookup each."""
    pa, pb = pieces(a), pieces(b)
    if not pa or not pb:
        return EMPTY
    i, j = shapely.STRtree(pb).query(pa, predicate="intersects")
    if not len(i):
        return EMPTY
    found = shapely.intersection(np.array(pa, dtype=object)[i], np.array(pb, dtype=object)[j])
    return polygonal(shapely.union_all([polygonal(g) for g in found]))


def rest_of(
    overlap: BaseGeometry, unit_a: BaseGeometry | None, unit_b: BaseGeometry | None
) -> BaseGeometry:
    """The part of an overlap CGAZ gives to neither side: all of it if a side has no unit."""
    if unit_a is None or unit_b is None:
        return overlap
    return polygonal(overlap.difference(unit_a).difference(unit_b))


def b_side(
    overlap: BaseGeometry,
    unit_a: BaseGeometry | None,
    unit_b: BaseGeometry | None,
    rest_to_a: bool,
) -> BaseGeometry:
    """Where b's side of a's and b's overlap lies, near it: b's CGAZ unit, or everything
    but a's when the rest is b's; nothing or everything when CGAZ lacks a side."""
    near = shapely.union_all(
        [shapely.box(*p.bounds).buffer(NEAR_DEG, join_style="mitre") for p in pieces(overlap)]
    )
    if near.is_empty or unit_a is None or unit_b is None:
        return EMPTY if rest_to_a else near
    return polygonal(near.intersection(unit_b) if rest_to_a else near.difference(unit_a))


def given_away(
    iso3: str,
    pairs: list[dict],
    outline: Callable[[str], BaseGeometry],
    units: dict[str, BaseGeometry],
) -> BaseGeometry:
    """Every piece of iso3's outline that a pair gives to the other side."""
    away = []
    for p in pairs:
        a, b = p["a"], p["b"]
        if iso3 not in (a, b):
            continue
        overlap = overlap_of(outline(a), outline(b))
        if overlap.is_empty:
            continue
        side = b_side(overlap, units.get(a), units.get(b), p["rest_to"] == a)
        to_b = polygonal(overlap.intersection(side))
        away.append(to_b if iso3 == a else polygonal(overlap.difference(to_b)))
    return polygonal(shapely.union_all(away)) if away else EMPTY


def clip_shape(
    iso3: str,
    pairs: list[dict],
    outline: Callable[[str], BaseGeometry],
    units: dict[str, BaseGeometry],
) -> BaseGeometry:
    """iso3's outline minus what it gives away."""
    away = given_away(iso3, pairs, outline, units)
    own = outline(iso3)
    return own if away.is_empty else polygonal(own.difference(away))


def load_split(path: Path = SPLIT_PATH) -> dict:
    return json.loads(path.read_text())


def pairs_of(iso3: str, split: dict) -> list[dict]:
    return [p for p in split["pairs"] if iso3 in (p["a"], p["b"])]


def split_key(iso3: str, split: dict) -> str:
    """What a country's clip depends on, besides the outlines: its pairs, CGAZ's file and
    how far its outline is grown."""
    doc = {
        "cgaz": split["cgaz"]["sha256"],
        "pairs": pairs_of(iso3, split),
        "grow": CLIP_GROW_DEG.get(iso3),
    }
    return hashlib.sha256(json.dumps(doc, sort_keys=True).encode()).hexdigest()[:16]


def clip_doc(iso3: str, geometry: BaseGeometry, key: str, source: dict) -> dict:
    """A clip as GeoJSON, keeping its outline's metadata (a Natural Earth outline's land
    area, which its density is measured by). A grown clip reaches out to sea, so its area
    is the plain outline's."""
    feature = next(iter(source.get("features") or [{}]), {})
    tally = {**source.get("tally", {}), "split": key}
    if iso3 in CLIP_GROW_DEG and "land_area_km2" not in tally:
        land = sum(area_km2(f["geometry"]) for f in source["features"] if f.get("geometry"))
        tally["land_area_km2"] = round(land, 1)
    return {
        "type": "FeatureCollection",
        "tally": tally,
        "features": [
            {
                "type": "Feature",
                "properties": {**feature.get("properties", {}), "clip": "F-30 overlap split"},
                "geometry": mapping(geometry),
            }
        ],
    }


def clip_path(iso3: str, split: dict | None = None, *, units: dict | None = None) -> Path:
    """The outline a country's raster is clipped to: its own (grown for CLIP_GROW_DEG),
    minus the overlaps it gives away. A country in no pair and not grown is clipped to its
    own outline file."""
    iso3 = iso3.upper()
    split = load_split() if split is None else split
    pairs = pairs_of(iso3, split)
    src = fetch_boundary(iso3)
    if not pairs and iso3 not in CLIP_GROW_DEG:
        return src
    key = split_key(iso3, split)
    path = CLIP_DIR / f"{iso3}_ADM0.geojson"
    if path.exists() and json.loads(path.read_text()).get("tally", {}).get("split") == key:
        return path
    cache: dict[str, BaseGeometry] = {}

    def outline(code: str) -> BaseGeometry:
        if code not in cache:
            cache[code] = clip_base(code)
        return cache[code]

    if units is None and pairs:
        if split["cgaz"]["sha256"] != CGAZ_SHA256:
            raise RuntimeError(f"{SPLIT_PATH.name} was made with another CGAZ file")
        units = cgaz_units({c for p in pairs for c in (p["a"], p["b"])})
    geometry = clip_shape(iso3, pairs, outline, units)
    doc = clip_doc(iso3, geometry, key, json.loads(src.read_text()))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc))
    tmp.replace(path)
    return path
