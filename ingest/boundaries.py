# Copyright (c) 2026 Woody McLendon
# SPDX-License-Identifier: MIT
"""Country boundaries from geoBoundaries (CC BY 4.0), used to clip rasters at ingestion.

Files are cached under data/boundaries/<ISO3>_ADM0.geojson and never committed.
The GeoJSON itself is served from GitHub's Git LFS media host, which throws
transient 503s under load, so fetches go through ingest.fetch and retry.

A few territories have no usable geoBoundaries outline; theirs are made from Natural
Earth (public domain) instead, see ADM0_FROM_NATURAL_EARTH.
"""

from __future__ import annotations

import json
from pathlib import Path

from ingest.config import BOUNDARY_DIR, DATA_DIR
from ingest.fetch import get_bytes

API = "https://www.geoboundaries.org/api/current/gbOpen/{iso3}/ADM{adm}/"

# Meta ships rasters for these, but geoBoundaries has no ADM0 for them (404): they are
# ADM1 units of another country there.
ADM0_FROM_PARENT = {
    "PRI": ("USA", 1, "Puerto Rico"),
}

NE10_URL = (
    "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/"
    "geojson/ne_10m_admin_0_map_units.geojson"
)
NE10_CACHE = DATA_DIR / "natural_earth" / "ne_10m_admin_0_map_units.geojson"
NE10_SOURCE = "Natural Earth 10m admin 0 map units"

# Territories geoBoundaries cannot outline: Hong Kong and Macau are CHN ADM1 units there,
# far too coarse to clip by (Macau is a 4-corner box); its Virgin Islands outline is St
# Croix alone (49k of 106k people), its Pitcairn misses all 50 islanders, and it has no
# Jersey at all. Their rasters are already masked to the territory, though (2026-10-13:
# of HKG's 7.29 M people 1,013 fall in Natural Earth's China, MAC's tile holds 0 in China,
# VIR's 0 in the British Virgin Is.; Jersey's is WorldPop's, Jersey only), so the outline
# need not trace a coast, only keep other places' people out. So: the Natural Earth 10m
# unit grown by this many degrees, which takes in the coastal pixels its coarse coastline
# misses (HKG loses 1.28 M people unbuffered, 0.4 k at 0.05), minus every other unit. That
# cuts the land borders back to Natural Earth's and leaves out the 662 k people of western
# Hong Kong that Macau's 1-degree tile also holds, and Guernsey and France in Jersey's.
# Greenland's geoBoundaries coast runs inland of its towns (Nuuk 0.18 deg outside it) and
# keeps 265 of WorldPop's 55,581 people; WorldPop's raster holds no one in Canada, so its
# unit is grown far enough to take every coastal pixel at that latitude, where a degree
# of longitude is under 40 km (2026-10-18: 48.2 k at 0.05, 55.3 k at 0.2, all at 0.5).
ADM0_FROM_NATURAL_EARTH = {
    "HKG": 0.05,
    "MAC": 0.05,
    "JEY": 0.05,
    "VIR": 0.05,
    "PCN": 0.05,
    "GRL": 0.5,
}


# Countries whose raster holds only their own people, so their clip can safely reach past
# their outline: WorldPop's files are cut at LSIB, the US State Department's line. Their
# outline is grown by this many degrees (about 10 km) to take in the coast and the border
# towns a coarse outline cuts off, and the overlap split (ingest/overlaps.py) cuts it at
# each built neighbour along CGAZ, which follows the same US lines. Measured 2026-10-20,
# Canada's plain outline lost 9,429 of WorldPop's people (0.025%), the grown clip 1. The
# outline drawn on the map, the no-data mask and the country's area stay the plain outline.
CLIP_GROW_DEG = {"CAN": 0.1}


def boundary_path(country: str, adm: int = 0) -> Path:
    return BOUNDARY_DIR / f"{country.upper()}_ADM{adm}.geojson"


def _download(iso3: str, adm: int) -> bytes:
    meta = json.loads(get_bytes(API.format(iso3=iso3, adm=adm), timeout=120))
    data = get_bytes(meta["gjDownloadURL"], timeout=120)
    json.loads(data)  # fail before writing a corrupt file
    return data


def unit_of(doc: dict, name: str) -> bytes:
    """One named unit of a geoBoundaries FeatureCollection, as a FeatureCollection."""
    found = [f for f in doc["features"] if f["properties"].get("shapeName") == name]
    if len(found) != 1:
        raise RuntimeError(f"expected one unit named {name!r}, found {len(found)}")
    return json.dumps({"type": "FeatureCollection", "features": found}).encode()


def fetch_boundary(country: str, adm: int = 0, *, force: bool = False) -> Path:
    """Download the geoBoundaries GeoJSON for a country unless it is already cached."""
    iso3 = country.upper()
    path = boundary_path(iso3, adm)
    if adm == 0 and iso3 in ADM0_FROM_NATURAL_EARTH:
        # A cache from before the switch (geoBoundaries' VIR and PCN) is redone.
        if path.exists() and not force and json.loads(path.read_text()).get("tally"):
            return path
        data = json.dumps(from_natural_earth(iso3, ADM0_FROM_NATURAL_EARTH[iso3])).encode()
    elif path.exists() and not force:
        return path
    elif adm == 0 and iso3 in ADM0_FROM_PARENT:
        parent, parent_adm, name = ADM0_FROM_PARENT[iso3]
        data = unit_of(json.loads(_download(parent, parent_adm)), name)
    else:
        data = _download(iso3, adm)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def natural_earth_units() -> list[dict]:
    """Natural Earth 10m admin-0 map units, downloaded once (13 MB) and cached under data/."""
    if not NE10_CACHE.exists():
        data = get_bytes(NE10_URL, timeout=300)
        json.loads(data)  # fail before caching a truncated file
        NE10_CACHE.parent.mkdir(parents=True, exist_ok=True)
        NE10_CACHE.write_bytes(data)
    return json.loads(NE10_CACHE.read_text())["features"]


def from_natural_earth(iso3: str, grow_deg: float, units: list[dict] | None = None) -> dict:
    """A clipping outline for `iso3`: its Natural Earth unit grown by `grow_deg`, minus
    every other unit it then reaches (see ADM0_FROM_NATURAL_EARTH). The land area of the
    unit as drawn, before growing, rides along for the country's density."""
    from shapely.geometry import mapping, shape
    from shapely.ops import unary_union

    units = natural_earth_units() if units is None else units
    own = [f for f in units if f["properties"].get("ADM0_A3") == iso3]
    if not own:
        raise RuntimeError(f"{iso3}: not in {NE10_SOURCE}")
    land = unary_union([shape(f["geometry"]) for f in own])
    grown = land.buffer(grow_deg)
    others = [
        g
        for f in units
        if f["properties"].get("ADM0_A3") != iso3 and (g := shape(f["geometry"])).intersects(grown)
    ]
    clip = grown.difference(unary_union(others)) if others else grown
    name = own[0]["properties"].get("NAME_EN") or own[0]["properties"].get("NAME")
    return {
        "type": "FeatureCollection",
        "tally": {
            "source": NE10_SOURCE,
            "grown_deg": grow_deg,
            "land_area_km2": round(area_km2(mapping(land)), 1),
        },
        "features": [
            {
                "type": "Feature",
                "properties": {"shapeName": name, "shapeGroup": iso3, "source": NE10_SOURCE},
                "geometry": mapping(clip),
            }
        ],
    }


EARTH_RADIUS_KM = 6371.0088


def area_km2(geometry: dict) -> float:
    """Geodesic area of a GeoJSON Polygon or MultiPolygon in km^2, holes subtracted.

    Spherical excess (Chamberlain & Duquette 2007), the formula web/app.js uses for drawn
    polygons, so a country's density and a drawn polygon's are measured alike.
    """
    import numpy as np

    def ring(coords) -> float:
        a = np.radians(np.asarray(coords, dtype=float)[:, :2])
        lon, lat = a[:, 0], a[:, 1]
        lon2, lat2 = np.roll(lon, -1), np.roll(lat, -1)
        s = np.sum((lon2 - lon) * (2 + np.sin(lat) + np.sin(lat2)))
        return abs(s * EARTH_RADIUS_KM**2 / 2)

    if geometry["type"] == "GeometryCollection":
        return sum(area_km2(g) for g in geometry["geometries"])
    polys = (
        geometry["coordinates"] if geometry["type"] == "MultiPolygon" else [geometry["coordinates"]]
    )
    return sum(ring(p[0]) - sum(ring(h) for h in p[1:]) for p in polys if p and len(p[0]) >= 3)


def land_area_km2(path: Path) -> float:
    """A country's area for its density: its outline's, or for a Natural Earth outline,
    which is grown out to sea, the land it was grown from."""
    doc = json.loads(Path(path).read_text())
    if "land_area_km2" in doc.get("tally", {}):
        return float(doc["tally"]["land_area_km2"])
    return sum(area_km2(g) for g in load_geometries(path))


def outline_shape(path: Path):
    """An outline file as one shapely geometry, made valid (self-intersections are common in
    OSM-derived outlines)."""
    from shapely.geometry import shape
    from shapely.ops import unary_union

    geom = unary_union([shape(g) for g in load_geometries(path)])
    return geom if geom.is_valid else geom.buffer(0)


def clip_base(iso3: str):
    """The outline a country's clip starts from: its own, grown for CLIP_GROW_DEG."""
    geom = outline_shape(fetch_boundary(iso3))
    grow = CLIP_GROW_DEG.get(iso3.upper())
    return geom.buffer(grow, quad_segs=4) if grow else geom


def load_geometries(path: Path) -> list[dict]:
    """GeoJSON-like geometry dicts (what rasterio.features expects) from a GeoJSON file."""
    doc = json.loads(Path(path).read_text())
    if doc.get("type") == "FeatureCollection":
        return [f["geometry"] for f in doc["features"] if f.get("geometry")]
    if doc.get("type") == "Feature":
        return [doc["geometry"]]
    return [doc]
