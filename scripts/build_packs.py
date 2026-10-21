# Copyright (c) 2026 Woody McLendon
# SPDX-License-Identifier: MIT
"""Build downloadable per-country data packs for Tally's offline mode.

    uv run python scripts/build_packs.py --country CMR --country BRA

Writes `data/packs/` (git-ignored, a cache): gzipped packs plus `catalog.json`, which lists
every pack with its size, hashes and footprint. The browser (web/static.js) downloads the
packs a polygon needs, keeps them in IndexedDB and sums cells with h3-js. build_static.py
copies the demo's countries from this cache into dist/packs/ with a catalogue of their own.

It needs no database: cells are computed from the rasters under
data/raw (downloaded from HDX if missing). Countries are independent, so a run only
rebuilds the countries it is given and keeps the other entries already in the catalogue;
`--fresh` starts an empty one. Pack files no catalogue entry points at are deleted.

Large countries are split into parts. A part is every cell, at every resolution, whose
res-3 ancestor falls in one run of res-3 cells taken in H3 index order (which keeps them
roughly contiguous), cut when the next res-3 cell would push the part past --max-part-mb
of raw data. So a part answers any query cell on its own, every level of it sums to the
same part total, and a polygon over Sao Paulo downloads one part of Brazil, not all of it.
It also keeps every file small enough for any static file server (BRA whole: 27 MB).

Pack layout (all integers little-endian):

    0   4  magic b"TLYP"
    4   2  format major   a reader refuses a major it does not know
    6   2  format minor   additive changes only
    8   4  header length H
    12  H  header, UTF-8 JSON (iso3, part, parts, source, data_version, sections, total, ...)
    ..     zero padding up to a multiple of 8
    B      body: per section `count` uint32 high words, `count` uint32 low words of the H3
           index, then `count` float32 populations, sorted by index. Offsets are from B.

The padding is what keeps every section 4-byte aligned in the file, which the browser's
Uint32Array/Float32Array views require. A pack carries its own header because a stored
pack outlives the catalogue that listed it: the client must be able to read (or refuse)
it without trusting that today's catalogue still describes it.

Each part's `footprint` in the catalogue is its res-3 cells. A query cell can only draw
population from a part whose footprint holds the cell's res-3 parent, so the client can
name exactly the parts a polygon needs with no country outlines at all.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import re
import struct
import sys
import time
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import h3
import numpy as np
import typer
from rich.console import Console

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ingest.config import DATA_DIR, MIN_RESOLUTION, SOURCES
from ingest.continents import continent_name, name_of

MAGIC = b"TLYP"
FORMAT = (1, 0)
FIXED = struct.Struct("<4sHHI")  # magic, major, minor, header length
ALIGN = 8
SOURCE = "meta_hrsl"
# Countries whose Meta raster is unusable or missing, built from another source instead.
# Meta's "Jersey" file (jey_general_2020) has no data over Jersey at all: its 418 k people
# are Guernsey's and Normandy's, and every pixel on the island is nodata (checked
# 2026-10-13). Meta has no file at all for Canada or Greenland, whose borders are
# undisputed, so no neighbour's pack can hold their people. Canada's clip is grown and
# split with its neighbours (ingest/boundaries.CLIP_GROW_DEG).
SOURCE_FOR = {"JEY": "worldpop_100m", "CAN": "worldpop_100m", "GRL": "worldpop_100m"}
PACK_CACHE = DATA_DIR / "packs"
DEFAULT_COUNTRIES = ["CMR", "CAF"]
MAX_PART_MB = 16.0  # raw; ~3 MB once gzipped (whole people compress well)
# Dates or years in the raster file names: Meta's are "..._2020_geotiff.zip" or
# "population_bra_northeast_2018-10-01.zip", WorldPop's "jey_pop_2020_CN_100m_R2025A_v1.tif"
# (whose release, R2025A, is not the data's year: hence no letters either side).
VINTAGE_RE = re.compile(r"(?<![\dA-Za-z])(20\d\d(?:-\d\d-\d\d)?)(?![\dA-Za-z])")

# Credited in the map's attribution line, linked like the basemap's own credits: the
# population sources a build uses, then the outlines (Natural Earth's clip a few
# territories and draw the grey no-data mask).
SOURCE_CREDITS = {
    "meta_hrsl": {
        "what": "Population",
        "name": "Meta Data for Good",
        "url": "https://ai.meta.com/ai-for-good/datasets/high-resolution-population-density-maps/",
        "licence": "CC BY 4.0",
    },
    "worldpop_100m": {
        "what": "Population",
        "name": "WorldPop",
        "url": "https://www.worldpop.org/",
        "licence": "CC BY 4.0",
    },
}
BOUNDARY_CREDITS = [
    {
        "what": "Boundaries",
        "name": "geoBoundaries",
        "url": "https://www.geoboundaries.org/",
        "licence": "CC BY 4.0",
    },
    {
        "what": "Boundaries",
        "name": "Natural Earth",
        "url": "https://www.naturalearthdata.com/",
        "licence": "public domain",
    },
]


def attribution(sources: set[str]) -> list[dict]:
    return [SOURCE_CREDITS[s] for s in sorted(sources)] + BOUNDARY_CREDITS


def source_for(iso3: str) -> str:
    return SOURCE_FOR.get(iso3.upper(), SOURCE)


console = Console()


def display_name(iso3: str) -> str:
    """Shown to visitors; reference/continents.json spells out Natural Earth's abbreviations."""
    return name_of(iso3)


# ---- cell tables ------------------------------------------------------------------


def merge(
    per_country: list[dict[int, tuple[np.ndarray, np.ndarray]]],
) -> dict[int, tuple[np.ndarray, np.ndarray]]:
    """Union the countries level by level. Cells straddling a border are summed."""
    out = {}
    for res in sorted(set().union(*per_country)):
        cells = np.concatenate([c[res][0] for c in per_country if res in c]).astype(np.uint64)
        pops = np.concatenate([c[res][1] for c in per_country if res in c])
        uniq, inverse = np.unique(cells, return_inverse=True)
        out[res] = (uniq, np.bincount(inverse, weights=pops, minlength=uniq.size))
    return out


def encode(levels: dict[int, tuple[np.ndarray, np.ndarray]]) -> tuple[bytes, list[dict]]:
    """The body: per resolution, high words, low words, populations (see the docstring)."""
    chunks, sections, offset = [], [], 0
    for res in sorted(levels):
        cells, pops = levels[res]
        cells = cells.astype(np.uint64)
        hi = (cells >> np.uint64(32)).astype("<u4")
        lo = (cells & np.uint64(0xFFFFFFFF)).astype("<u4")
        for arr in (hi, lo, pops.astype("<f4")):
            chunks.append(arr.tobytes())
        sections.append({"resolution": res, "count": int(cells.size), "offset": offset})
        offset += 12 * cells.size
    return b"".join(chunks), sections


def decode(raw: bytes, sections: list[dict]) -> dict[int, tuple[np.ndarray, np.ndarray]]:
    """Inverse of encode; the builds check their own output with it."""
    out = {}
    for s in sections:
        n, o = s["count"], s["offset"]
        hi = np.frombuffer(raw, "<u4", n, o).astype(np.uint64)
        lo = np.frombuffer(raw, "<u4", n, o + 4 * n).astype(np.uint64)
        pops = np.frombuffer(raw, "<f4", n, o + 8 * n)
        out[s["resolution"]] = ((hi << np.uint64(32)) | lo, pops)
    return out


def tidy(levels: dict) -> dict:
    """Sorted by index (the browser binary-searches) and only cells with people in them:
    an absent cell reads as 0 anyway, so empty ones are dead weight in the download."""
    out = {}
    for res, (cells, pops) in levels.items():
        keep = pops > 0
        cells, pops = np.asarray(cells, np.uint64)[keep], np.asarray(pops)[keep]
        order = np.argsort(cells)
        out[res] = (cells[order], pops[order])
    return out


# H3 index bits: resolution in 52-55, then 15 three-bit digits with digit r at bit
# 3 * (15 - r). The ancestor at res r keeps digits 1-r and sets digits r+1..15 to 7.
_RES_BITS = np.uint64(0xF << 52)


def parent_at(cells: np.ndarray, res: int) -> np.ndarray:
    """h3.cell_to_parent(c, res) for a whole array at once."""
    cells = np.asarray(cells, np.uint64)
    fine = np.uint64((1 << 3 * (15 - res)) - 1)
    return (cells & ~(_RES_BITS | fine)) | np.uint64(res << 52) | fine


def coarse_parent(cells: np.ndarray) -> np.ndarray:
    """h3.cell_to_parent(c, MIN_RESOLUTION) for a whole array at once."""
    return parent_at(cells, MIN_RESOLUTION)


def largest_remainder(
    quota: np.ndarray, group: np.ndarray, cells: np.ndarray, seats: np.ndarray
) -> np.ndarray:
    """Whole numbers near `quota` that sum to `seats[g]` within each group g: every item
    gets its quota rounded down, then the people left over go one each to the largest
    remainders. Ties go to the lower H3 index, so a rebuild is identical."""
    whole = np.floor(quota).astype(np.int64)
    short = seats - np.bincount(group, weights=whole, minlength=seats.size).astype(np.int64)
    order = np.lexsort((cells, -(quota - whole), group))
    g = group[order]
    rank = np.arange(order.size) - np.searchsorted(g, np.arange(seats.size))[g]
    whole[order] += rank < short[g]
    return whole


def whole_people(levels: dict) -> dict:
    """The tidy pyramid in whole people, so no hexagon on the map holds a fraction of one.

    Handed out from the top: the country's rounded total is shared among its res-3 cells,
    then each cell's whole people among its children, each in proportion to the model's
    figures by largest remainder. Every level still sums to the same total, and where
    people live thinly (WorldPop gives a building in the Canadian bush 0.09 of a person)
    a parent's one person lands in its likeliest child instead of a tenth in each. A cell
    moves by under 2 people from the model's figure (CAN, USA; 2026-10-18).
    """
    top, bottom = min(levels), max(levels)
    cells, pops = levels[top]
    pops = pops.astype(np.float64)
    total = round(float(pops.sum()))
    share = pops * (total / pops.sum()) if total else np.zeros_like(pops)
    seats = largest_remainder(share, np.zeros(cells.size, np.int64), cells, np.array([total]))
    out = {top: (cells, seats)}
    for res in range(top + 1, bottom + 1):
        parents, parent_seats = out[res - 1]
        cells, pops = levels[res]
        pops = pops.astype(np.float64)
        g = np.searchsorted(parents, parent_at(cells, res - 1))
        if g.size and (g.max() >= parents.size or np.any(parents[g] != parent_at(cells, res - 1))):
            raise ValueError(f"res {res}: a cell has no parent at res {res - 1}")
        real = np.bincount(g, weights=pops, minlength=parents.size)[g]
        share = pops * parent_seats[g] / np.where(real > 0, real, 1.0)
        out[res] = (cells, largest_remainder(share, g, cells, parent_seats))
    return {res: (cells, seats.astype(np.float64)) for res, (cells, seats) in out.items()}


def split(levels: dict, max_raw_bytes: float) -> list[dict]:
    """Cut a tidy pyramid into parts by res-3 ancestor, as described in the docstring."""
    roots = levels[MIN_RESOLUTION][0]
    group = {}
    cost = np.zeros(roots.size, np.int64)
    for res, (cells, _) in levels.items():
        g = np.searchsorted(roots, coarse_parent(cells))
        if g.size and (g.max() >= roots.size or np.any(roots[g] != coarse_parent(cells))):
            raise ValueError(f"res {res}: a cell has no res-{MIN_RESOLUTION} ancestor in the pack")
        group[res] = g
        cost += np.bincount(g, minlength=roots.size) * 12
    part_of = np.zeros(roots.size, np.int64)
    part, size = 0, 0
    for i, c in enumerate(cost):
        if size and size + c > max_raw_bytes:
            part, size = part + 1, 0
        part_of[i] = part
        size += c
    return [
        {res: (cells[part_of[group[res]] == p], pops[part_of[group[res]] == p])
         for res, (cells, pops) in levels.items()}
        for p in range(part + 1)
    ]  # fmt: skip


# ---- pack files ---------------------------------------------------------------------


def pack_bytes(header: dict, body: bytes) -> bytes:
    """Fixed prefix + JSON header + padding + body, as laid out in the module docstring."""
    head = json.dumps(header, separators=(",", ":")).encode()
    prefix = FIXED.pack(MAGIC, *FORMAT, len(head)) + head
    pad = -len(prefix) % ALIGN
    return prefix + b"\0" * pad + body


def unpack(raw: bytes) -> tuple[dict, int]:
    """Inverse of pack_bytes: (header, offset of the body). Refuses what a browser would."""
    if len(raw) < FIXED.size:
        raise ValueError("pack is shorter than its fixed header")
    magic, major, _minor, n = FIXED.unpack_from(raw)
    if magic != MAGIC:
        raise ValueError(f"not a Tally pack (magic {magic!r})")
    if major != FORMAT[0]:
        raise ValueError(f"pack format {major}.x, this reader knows {FORMAT[0]}.x")
    end = FIXED.size + n
    header = json.loads(raw[FIXED.size : end])
    return header, end + (-end % ALIGN)


def check(raw: bytes, levels: dict) -> None:
    """The pack must read back to the same cells, and every level add up to the same total."""
    header, start = unpack(raw)
    back = decode(raw[start:], header["sections"])
    total = header["total"]
    for res, (cells, _pops) in levels.items():
        assert np.array_equal(back[res][0], cells), f"res {res}: indexes did not round-trip"
        got = float(back[res][1].astype(np.float64).sum())
        assert abs(got - total) < max(1.0, total * 1e-6), f"res {res}: sums to {got}, not {total}"


def build_country(
    iso3: str,
    source: str,
    levels: dict,
    features: list[dict],
    vintage: str,
    max_part_mb: float = MAX_PART_MB,
) -> tuple[list[bytes], dict]:
    """The gzipped parts and the country's catalogue entry, each part checked by decoding it."""
    levels = tidy(whole_people(tidy(levels)))
    base = SOURCES[source].base_resolution
    country_total = float(levels[base][1].sum())
    built = datetime.now(UTC).date().isoformat()
    pieces = split(levels, max_part_mb * 1e6)
    files, parts = [], []
    for i, piece in enumerate(pieces):
        body, sections = encode(piece)
        header = {
            "iso3": iso3,
            "name": display_name(iso3),
            "part": i,
            "parts": len(pieces),
            "source": source,
            "data_version": vintage,
            "tier": "full",
            "min_resolution": min(piece),
            "max_resolution": max(piece),
            "source_base_resolution": base,
            "total": round(float(piece[base][1].sum())),
            "sections": sections,
            "built": built,
        }
        raw = pack_bytes(header, body)
        check(raw, piece)
        packed = gzip.compress(raw, 9, mtime=0)
        digest = hashlib.sha256(packed).hexdigest()
        files.append(packed)
        parts.append(
            {
                "url": f"{iso3}.{digest[:10]}.tlyp.gz",
                "bytes": len(packed),
                "raw_bytes": len(raw),
                "sha256": digest,
                # Some hosts gunzip .gz files on the way out; the client checks this then.
                "raw_sha256": hashlib.sha256(raw).hexdigest(),
                "cells": int(sum(s["count"] for s in sections)),
                "total": header["total"],
                "footprint": sorted(h3.int_to_str(int(c)) for c in piece[MIN_RESOLUTION][0]),
            }
        )
    entry = {
        "iso3": iso3,
        "name": display_name(iso3),
        "source": source,
        "data_version": vintage,
        "total": round(country_total),
        "bbox": [round(v, 4) for v in bbox(features)],
        "max_resolution": max(levels),
        "cells_by_resolution": {str(r): int(levels[r][0].size) for r in sorted(levels)},
        "bytes": sum(p["bytes"] for p in parts),
        "parts": parts,
    }
    return files, entry


def country_area(iso3: str) -> float:
    """km^2 of the clipping outline (land only for a grown Natural Earth one)."""
    from ingest.boundaries import land_area_km2
    from ingest.overlaps import clip_path

    return round(land_area_km2(clip_path(iso3)), 1)


def clip_outline(iso3: str) -> Path:
    """The outline a country's raster is clipped to: its own, minus the overlaps it gives
    to a neighbour (ingest/overlaps.py, F-30)."""
    from ingest.overlaps import SPLIT_PATH, clip_path, load_split

    split = load_split()
    if iso3 not in split["countries"]:
        console.print(
            f"[yellow]{iso3} is not in {SPLIT_PATH.name}: its overlaps with neighbours are not "
            "split, so their packs may both count people there. Run "
            f"scripts/split_overlaps.py --country {iso3} first.[/yellow]"
        )
    return clip_path(iso3, split)


def part_header(path: Path) -> dict:
    """A pack's header, from the first few kB of the gzipped file."""
    with gzip.open(path, "rb") as fh:
        fixed = fh.read(FIXED.size)
        magic, _major, _minor, n = FIXED.unpack(fixed)
        if magic != MAGIC:
            raise ValueError(f"{path}: not a Tally pack")
        return json.loads(fh.read(n))


def enrich(entry: dict, out: Path) -> dict:
    """Fill in what the country picker reads, for entries built before it existed: the
    country's area (for its density) and its populated cells per resolution (to pick the
    heatmap's level without downloading anything)."""
    if "area_km2" not in entry:
        entry["area_km2"] = country_area(entry["iso3"])
    if "cells_by_resolution" not in entry:
        counts: dict[str, int] = {}
        for part in entry["parts"]:
            for sec in part_header(out / part["url"])["sections"]:
                k = str(sec["resolution"])
                counts[k] = counts.get(k, 0) + int(sec["count"])
        entry["cells_by_resolution"] = dict(sorted(counts.items(), key=lambda kv: int(kv[0])))
    return entry


# ---- inputs ---------------------------------------------------------------------------


def outline(geojson: dict, iso3: str) -> list[dict]:
    """The clipping outline as features, rounded to ~10 m to keep the file small."""

    def rnd(x):
        return [rnd(v) for v in x] if isinstance(x, list) else round(x, 4)

    feats = geojson["features"] if geojson.get("type") == "FeatureCollection" else [geojson]
    return [
        {
            "type": "Feature",
            "properties": {"iso3": iso3, "name": display_name(iso3)},
            "geometry": {
                "type": f["geometry"]["type"],
                "coordinates": rnd(f["geometry"]["coordinates"]),
            },
        }
        for f in feats
    ]


def bbox(features: list[dict]) -> list[float]:
    xs, ys = [], []

    def walk(c):
        if isinstance(c[0], (int, float)):
            xs.append(c[0])
            ys.append(c[1])
        else:
            for v in c:
                walk(v)

    for f in features:
        walk(f["geometry"]["coordinates"])
    return [min(xs), min(ys), max(xs), max(ys)]


def country_outline(iso3: str) -> list[dict]:
    from ingest.boundaries import fetch_boundary

    return outline(json.loads(fetch_boundary(iso3).read_text()), iso3)


def raster_paths(iso3: str, source: str) -> list[Path]:
    """The cached rasters for a country, downloading them (HDX, WorldPop) if there are none."""
    src = SOURCES[source]
    paths = src.default_paths(iso3)
    if source == "worldpop_100m":
        from ingest import worldpop_hub

        return paths or worldpop_hub.fetch_country(iso3)
    if source != "meta_hrsl":
        if not paths:
            raise SystemExit(f"{iso3}: no {source} raster under data/raw")
        return paths
    from ingest import hdx

    # Tiled countries (BRA, AUS, USA) ship as region zips without "_general_" in the name.
    paths = paths or sorted(hdx.country_dir(iso3).glob("*.zip"))
    if not paths:
        console.print(f"{iso3}: no raster under data/raw/meta, downloading from HDX")
        paths = hdx.fetch_country(hdx.candidates()[iso3])
    return paths


def data_version(paths: list[Path]) -> str:
    """The data's own vintage, from the raster file names: '2020' or '2018-10-01'.

    Some zips carry no date (population_mus.geotiff.zip) while the GeoTIFF inside does
    (population_mus_2018-10-01.tif), so the names inside each zip count too.
    """
    names = [p.name for p in paths]
    for p in paths:
        if p.suffix.lower() == ".zip" and p.exists():
            with zipfile.ZipFile(p) as z:
                names += z.namelist()
    found = {m for n in names for m in VINTAGE_RE.findall(n)}
    return max(found) if found else "unknown"


def country_levels(iso3: str, source: str) -> tuple[dict, list[dict], str]:
    """{resolution: (cells, pops)} for one country, its outline features and data vintage."""
    from ingest.pipeline import run_country

    src = SOURCES[source]
    paths = raster_paths(iso3, source)
    boundary = clip_outline(iso3)
    r = run_country(iso3, source, paths, src.base_resolution, boundary=boundary, console=console)
    levels = dict(r.levels)
    levels[src.base_resolution] = (r.cells, r.pops)
    return levels, country_outline(iso3), data_version(paths)


# ---- catalogue ------------------------------------------------------------------------


def load_entries(out: Path) -> dict[str, dict]:
    """Catalogue entries whose files are all still there, by ISO3."""
    path = out / "catalog.json"
    if not path.exists():
        return {}
    cat = json.loads(path.read_text())
    if cat.get("format", [0])[0] != FORMAT[0]:
        return {}
    return {
        e["iso3"]: e
        for e in cat.get("countries", [])
        if "parts" in e and all((out / p["url"]).exists() for p in e["parts"])
    }


def write_catalog(out: Path, entries: dict[str, dict]) -> dict:
    sources = sorted({e["source"] for e in entries.values()})
    for e in entries.values():
        enrich(e, out)
    catalog = {
        "format": list(FORMAT),
        "built": datetime.now(UTC).date().isoformat(),
        "min_resolution": MIN_RESOLUTION,
        "footprint_resolution": MIN_RESOLUTION,
        "sources": {s: {"base_resolution": SOURCES[s].base_resolution} for s in sources},
        "attribution": attribution(set(sources)),
        # Name and continent are filled in here, not only when a pack is built, so a change
        # to reference/continents.json reaches the ? panel without rebuilding packs.
        "countries": [
            {**entries[k], "name": display_name(k), "continent": continent_name(k)}
            for k in sorted(entries)
        ],
    }
    (out / "catalog.json").write_text(json.dumps(catalog, separators=(",", ":")))
    return catalog


def prune(out: Path, entries: dict[str, dict]) -> list[str]:
    """Delete pack files that no catalogue entry points at."""
    keep = {p["url"] for e in entries.values() for p in e["parts"]}
    gone = []
    for f in out.glob("*.tlyp.gz"):
        if f.name not in keep:
            f.unlink()
            gone.append(f.name)
    return gone


def build_one(iso3: str, out: Path, max_part_mb: float, quiet: bool) -> tuple[dict, float]:
    """Compute one country, write its part files into `out` and return its catalogue entry.

    Runs in a worker process. Part files are named by content hash, so workers never
    write the same file; the catalogue itself is left to the parent.
    """
    if quiet:
        console.quiet = True
    t = time.perf_counter()
    source = source_for(iso3)
    levels, features, vintage = country_levels(iso3, source)
    files, entry = build_country(iso3, source, levels, features, vintage, max_part_mb)
    entry["area_km2"] = country_area(iso3)
    for part, packed in zip(entry["parts"], files, strict=True):
        (out / part["url"]).write_bytes(packed)
    return entry, time.perf_counter() - t


def job_cost(iso3: str) -> float:
    """Uncompressed raster bytes already on disk (0 if they still need downloading)."""
    from ingest import hdx
    from ingest.parallel import raw_bytes

    source = source_for(iso3)
    paths = SOURCES[source].default_paths(iso3)
    if source == SOURCE:
        paths = paths or sorted(hdx.country_dir(iso3).glob("*.zip"))
    return float(raw_bytes(paths))


def ensure(
    countries: list[str],
    out: Path = PACK_CACHE,
    *,
    rebuild: bool = False,
    fresh: bool = False,
    max_part_mb: float = MAX_PART_MB,
    workers: int = 1,
) -> dict[str, dict]:
    """Build the countries' packs into `out` (reusing cached ones unless `rebuild`) and
    rewrite its catalogue. Returns every entry in the catalogue, by ISO3.

    With several `workers`, countries are built in parallel (ingest.parallel). The
    catalogue is rewritten after each country, so an interrupted run keeps what it
    finished; stale part files are pruned only at the end.
    """
    from ingest import parallel

    out.mkdir(parents=True, exist_ok=True)
    entries = {} if fresh else load_entries(out)
    todo = []
    for iso3 in countries:
        if iso3 in entries and not rebuild:
            console.print(f"{iso3}: packs cached in {out}, reusing (--rebuild to redo)")
        else:
            todo.append(iso3)
    workers = max(1, min(workers, len(todo)))
    if len(todo) > 1:
        console.print(f"building {len(todo)} countries with {workers} worker(s)")
    failures: list[str] = []
    t0 = time.perf_counter()

    def on_done(o: parallel.Outcome, done: int, total: int) -> None:
        if o.error is not None:
            failures.append(o.key)
            console.print(f"[red]{o.key} failed: {type(o.error).__name__}: {o.error}[/red]")
            return
        entry, seconds = o.result
        entries[o.key] = entry
        write_catalog(out, entries)
        sizes = ", ".join(f"{p['bytes'] / 1e6:.2f}" for p in entry["parts"])
        console.print(
            f"[green]{o.key}[/green] {entry['name']}: total {entry['total']:,}, "
            f"{len(entry['parts'])} part(s) [{sizes}] MB, data {entry['data_version']} "
            f"({seconds:.0f} s; {done}/{total}, {time.perf_counter() - t0:.0f} s elapsed)"
        )

    jobs = [
        (iso3, job_cost(iso3), build_one, (iso3, out, max_part_mb, workers > 1)) for iso3 in todo
    ]
    parallel.run(jobs, workers, on_done)
    write_catalog(out, entries)
    for name in prune(out, entries):
        console.print(f"  removed stale {name}")
    if failures:
        raise RuntimeError(f"pack build failed for {', '.join(failures)}")
    return entries


def main(
    country: Annotated[
        list[str] | None, typer.Option(help="ISO3 code; repeat for several (default: CMR and CAF)")
    ] = None,
    out: Annotated[Path, typer.Option(help="Pack cache directory")] = PACK_CACHE,
    rebuild: Annotated[bool, typer.Option(help="Recompute countries already cached")] = True,
    fresh: Annotated[
        bool, typer.Option(help="Drop catalogue entries not built in this run")
    ] = False,
    max_part_mb: Annotated[
        float, typer.Option(help="Split packs above this raw size")
    ] = MAX_PART_MB,
    workers: Annotated[
        int | None,
        typer.Option(help="Countries built at once (default: one per CPU thread, ~4 GB RAM each)"),
    ] = None,
) -> None:
    from ingest.parallel import default_workers

    t0 = time.perf_counter()
    countries = [c.upper() for c in country or DEFAULT_COUNTRIES]
    entries = ensure(
        countries,
        out,
        rebuild=rebuild,
        fresh=fresh,
        max_part_mb=max_part_mb,
        workers=workers or default_workers(),
    )
    size = (out / "catalog.json").stat().st_size
    console.print(
        f"\n[green]Wrote {out}[/green]: {len(entries)} countries, "
        f"catalog.json {size / 1e3:.0f} KB, in {time.perf_counter() - t0:.0f} s"
    )


if __name__ == "__main__":
    typer.run(main)
