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

import hashlib
import inspect
import json
from pathlib import Path

from ingest.config import BOUNDARY_DIR, DATA_DIR, WORLDPOP_COUNTRIES
from ingest.fetch import get_bytes

API = "https://www.geoboundaries.org/api/current/gbOpen/{iso3}/ADM{adm}/"

# Meta ships rasters for these, but geoBoundaries has no ADM0 for them (404): they are
# ADM1 units of another country there.
ADM0_FROM_PARENT = {
    "PRI": ("USA", 1, "Puerto Rico"),
    # No geoBoundaries ADM0; Natural Earth's is too coarse for its skerries: of WorldPop's
    # 30,011 people it left out 449 (1.5%), grown 0.05 deg; this unit grown 0.1, 7
    # (2026-10-22).
    "ALA": ("FIN", 1, "Åland Islands"),
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
    # No geoBoundaries ADM0 at all (2026-10-22); WorldPop's rasters hold each one alone.
    "SXM": 0.05,
    "MAF": 0.05,
    "NFK": 0.05,  # added to Australia's pack (build_packs.ADDED_TO)
    "SPM": 0.05,
    "ESH": 0.05,
}

# Natural Earth files some places under its own code: a unit is ours when its ADM0_A3,
# GU_A3 (a geounit filed inside a larger unit) or ISO_A3 (Aland is "ALD",
# Western Sahara "SAH") is the code asked for.
NE_CODE_FIELDS = ("ADM0_A3", "GU_A3", "ISO_A3")

# Natural Earth draws where control runs: its Western Sahara is the strip east of the
# Moroccan wall (90,696 km2, under 10% of WorldPop's 544,694 people), and Laayoune, Dakhla
# and Smara are in its Morocco, which reaches 21.4 deg N. Tally's Western Sahara is the
# whole territory, as WorldPop's file is: its strip plus Natural Earth's Morocco south of
# 27 deg 40' N, the territory's northern border (269,152 km2; 2026-10-22). Place: (the unit
# a part is taken from, the latitude the part stops at). That country's outline is cut at
# the same latitude (outline_path): geoBoundaries' Morocco holds all of Western Sahara,
# whose people are in Western Sahara's pack, not Morocco's.
NE_PART_OF = {"ESH": ("MAR", 27 + 40 / 60)}

# geoBoundaries' code where it differs from Tally's (WorldPop's and Natural Earth's).
GEOBOUNDARIES_CODE = {"KOS": "XKX"}


# Countries whose raster holds only their own people, so their clip can safely reach past
# their outline: WorldPop's files are cut at LSIB, the US State Department's line. Their
# outline is grown by this many degrees (about 10 km) to take in the coast and the border
# towns a coarse outline cuts off (Iran's, Natural Earth's, missed Bandar Abbas: 0.57% of
# its people; North Korea's Nampo, 0.50%), and the overlap split (ingest/overlaps.py) cuts
# it at each built neighbor along CGAZ, which follows the same US lines. Measured
# 2026-10-20, what WorldPop counts and the clip loses: IRN 0.57% -> 0.001%, MMR 0.21% ->
# 0.009%, PRK 0.50% -> 0.002%, YEM 0.04% -> 0.02%, AFG 0.003% -> 0. The outline drawn on
# the map, the no-data mask and the country's area stay the plain outline. Every WorldPop
# country with a geoBoundaries outline is grown. A WorldPop place outlined from Natural
# Earth has its clip grown from its own unit as far as its outline is, but not cut back
# at the other units (the outline drawn still is): Natural Earth's line across St Martin
# put 12,285 of Sint Maarten's people in Saint-Martin's unit and 761 the other way, so
# each clip lost them (2026-10-22). Degrees, not a distance: 0.1 deg of longitude is
# 5.5 km at 60 deg N. Taken from WORLDPOP_COUNTRIES, so a new one is grown too.
CLIP_GROW_DEG = {
    iso3: ADM0_FROM_NATURAL_EARTH.get(iso3, 0.1) for iso3 in sorted(WORLDPOP_COUNTRIES)
}

# WorldPop countries whose geoBoundaries outline is too coarse to clip by even grown: the
# clip is grown from it joined with the Natural Earth 10m unit. China's has 3 parts and
# 2,182 points, no coastal islands (Zhoushan's million) and a coast that cuts across bays
# and reclaimed land: grown 0.1 deg it left out 2.45 M of WorldPop's people, 0.1-0.2 deg
# beyond it; with Natural Earth's 70 parts, 41 k (2026-10-22). The map outline, no-data
# mask and area stay geoBoundaries'.
CLIP_WITH_NATURAL_EARTH = {"CHN"}

# A WorldPop country whose raster holds people inside another's outline, given to that
# country: its clip leaves the other's outline out, and build_packs.ADDED_TO adds those
# people to the other's pack, so one line splits them. WorldPop's Palestine file (cut at
# LSIB) holds East Jerusalem, which geoBoundaries' and Natural Earth's outlines put in
# Israel: ~255,000 people, in Israel's pack (the owner's choice, 2026-10-23).
CLIP_LEAVES_OUT = {"PSE": "ISR"}


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
        # Redone when how it is made changes (a cache from before the switch,
        # geoBoundaries' VIR and PCN, has no key at all).
        key = natural_earth_key(iso3)
        made = json.loads(path.read_text()).get("tally", {}).get("made") if path.exists() else None
        if made == key and not force:
            return path
        doc = from_natural_earth(iso3, ADM0_FROM_NATURAL_EARTH[iso3])
        doc["tally"]["made"] = key
        data = json.dumps(doc).encode()
    elif path.exists() and not force:
        return path
    elif adm == 0 and iso3 in ADM0_FROM_PARENT:
        parent, parent_adm, name = ADM0_FROM_PARENT[iso3]
        data = unit_of(json.loads(_download(parent, parent_adm)), name)
    else:
        data = _download(GEOBOUNDARIES_CODE.get(iso3, iso3), adm)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Replaced, not written through: a worktree's outlines may be links to another's.
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)
    return path


def natural_earth_key(iso3: str) -> str:
    """What a Natural Earth outline is made from, besides Natural Earth's file: its grow,
    any part it takes from another unit, and the code that makes it."""
    doc = {
        "grow": ADM0_FROM_NATURAL_EARTH[iso3],
        "part_of": NE_PART_OF.get(iso3),
        "fields": NE_CODE_FIELDS,
        "code": inspect.getsource(from_natural_earth) + inspect.getsource(natural_earth_land),
    }
    return hashlib.sha256(json.dumps(doc, sort_keys=True).encode()).hexdigest()[:16]


def outline_path(iso3: str) -> Path:
    """The outline a country is drawn, masked and measured by: its boundary file, cut at
    the latitude where a place takes a part of it (NE_PART_OF). Its clip still starts from
    the whole file (clip_base): its raster holds no one in that part."""
    from shapely.geometry import box, mapping

    iso3 = iso3.upper()
    src = fetch_boundary(iso3)
    cuts = [north for code, north in NE_PART_OF.values() if code == iso3]
    if not cuts:
        return src
    raw = src.read_bytes()
    key = hashlib.sha256(raw + repr(cuts).encode()).hexdigest()[:16]
    path = BOUNDARY_DIR / "outline" / src.name
    if path.exists() and json.loads(path.read_text()).get("tally", {}).get("cut") == key:
        return path
    doc = json.loads(raw)
    geom = outline_shape(src).intersection(box(-180, max(cuts), 180, 90))
    feature = next(iter(doc.get("features") or [{}]), {})
    out = {
        "type": "FeatureCollection",
        "tally": {**doc.get("tally", {}), "cut": key},
        "features": [
            {
                "type": "Feature",
                "properties": feature.get("properties", {}),
                "geometry": mapping(geom),
            }
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(out))
    tmp.replace(path)
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

    def ours(f: dict) -> bool:
        return any(f["properties"].get(k) == iso3 for k in NE_CODE_FIELDS)

    own = [f for f in units if ours(f)]
    land = natural_earth_land(iso3, units)
    grown = land.buffer(grow_deg)
    others = [g for f in units if not ours(f) and (g := shape(f["geometry"])).intersects(grown)]
    clip = grown.difference(unary_union(others).difference(land)) if others else grown
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


def natural_earth_land(iso3: str, units: list[dict] | None = None):
    """A place's Natural Earth 10m unit(s) as one shapely geometry, made valid, with any
    part it takes from another unit (NE_PART_OF)."""
    from shapely.geometry import box, shape
    from shapely.ops import unary_union

    units = natural_earth_units() if units is None else units

    def land_of(code: str):
        own = [f for f in units if any(f["properties"].get(k) == code for k in NE_CODE_FIELDS)]
        if not own:
            raise RuntimeError(f"{code}: not in {NE10_SOURCE}")
        geom = unary_union([shape(f["geometry"]) for f in own])
        return geom if geom.is_valid else geom.buffer(0)

    geom = land_of(iso3)
    if iso3 in NE_PART_OF:
        code, north = NE_PART_OF[iso3]
        geom = geom.union(land_of(code).intersection(box(-180, -90, 180, north)))
    return geom


def clip_settings(iso3: str) -> dict:
    """What clip_base makes a country's clip from, besides the outline files: only the
    settings that apply, so a country with none has the same key as before they existed."""
    iso3 = iso3.upper()
    doc = {"grow": CLIP_GROW_DEG.get(iso3)}
    if iso3 in CLIP_WITH_NATURAL_EARTH:
        doc["natural_earth"] = True
    if iso3 in CLIP_LEAVES_OUT:
        doc["leaves_out"] = CLIP_LEAVES_OUT[iso3]
    if iso3 in NE_PART_OF and iso3 in CLIP_GROW_DEG:
        doc["part_of"] = NE_PART_OF[iso3]
    if cuts := sorted(north for code, north in NE_PART_OF.values() if code == iso3):
        doc["cut"] = cuts  # its area is the cut outline's (outline_path)
    return doc


# Grown outlines, cached by what was grown: Canada's arctic coast takes 45 s to grow, and
# every worker of split_overlaps.py that builds Canada's, the USA's or Greenland's clip,
# and each run of build_packs.py and audit_overlaps.py, would grow it again.
GROWN_CACHE = BOUNDARY_DIR / "grown"


def grown(geom, deg: float):
    """geom grown by deg degrees, from GROWN_CACHE when it was grown before."""
    import shapely

    key = hashlib.sha256(shapely.to_wkb(geom) + repr(deg).encode()).hexdigest()[:20]
    path = GROWN_CACHE / f"{key}.wkb"
    if path.exists():
        return shapely.from_wkb(path.read_bytes())
    out = geom.buffer(deg, quad_segs=4)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{id(out)}.tmp")
    tmp.write_bytes(shapely.to_wkb(out))
    tmp.replace(path)
    return out


def clip_base(iso3: str):
    """The outline a country's clip starts from: its own (joined with Natural Earth's for
    CLIP_WITH_NATURAL_EARTH; for a WorldPop place outlined from Natural Earth, its unit not
    cut back at the others), grown for CLIP_GROW_DEG, less the outline in CLIP_LEAVES_OUT."""
    iso3 = iso3.upper()
    if iso3 in ADM0_FROM_NATURAL_EARTH and iso3 in CLIP_GROW_DEG:
        geom = natural_earth_land(iso3)
    else:
        geom = outline_shape(fetch_boundary(iso3))
    if iso3 in CLIP_WITH_NATURAL_EARTH:
        geom = geom.union(natural_earth_land(iso3))
    grow = CLIP_GROW_DEG.get(iso3)
    if grow:
        geom = grown(geom, grow)
    if iso3 in CLIP_LEAVES_OUT:
        geom = geom.difference(outline_shape(fetch_boundary(CLIP_LEAVES_OUT[iso3])))
    return geom


def load_geometries(path: Path) -> list[dict]:
    """GeoJSON-like geometry dicts (what rasterio.features expects) from a GeoJSON file."""
    doc = json.loads(Path(path).read_text())
    if doc.get("type") == "FeatureCollection":
        return [f["geometry"] for f in doc["features"] if f.get("geometry")]
    if doc.get("type") == "Feature":
        return [doc["geometry"]]
    return [doc]
