# Copyright (c) 2026 Woody McLendon
# SPDX-License-Identifier: MIT
"""Build Tally for the chosen countries into dist/, ready to serve.

    uv run python scripts/build_static.py --country CMR --country CAF

Writes `dist/`: a copy of `web/` plus the chosen countries' data packs. The page downloads
the packs a polygon needs into the browser (IndexedDB) and sums cells there with h3-js, so
it needs no server code and no database (any static file server will do, such as
`python3 -m http.server`), and works offline for data already downloaded.

The packs come from build_packs.py's cache in data/packs/: countries already there are
reused (Brazil takes ~2.5 min to compute), missing ones are built from the rasters, and
`--rebuild-packs` recomputes them. Only the chosen countries are copied into dist/packs/,
with a catalogue of their own, so the app never offers data it does not claim to have.
Pack format: see build_packs.py.

`dist/data/manifest.json` describes the build: its countries, their combined bbox, the
grey "no data" mask (`nodata.geojson`), credits, and where the pack catalogue is.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import shutil
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_packs import (
    PACK_CACHE,
    attribution,
    country_outline,
    ensure,
    write_catalog,
)

from ingest.config import ROOT

DEFAULT_COUNTRIES = ["CMR", "CAF", "BRA"]
WEB = ROOT / "web"
console = Console()


# The mask is a light wash at world zoom, so ~1 km is plenty. It replaced dashed borders
# around the covered countries, which at 191 countries were 8.8 MB fetched by every visitor
# (and drew a red line round most of the world); the mask is ~160 KB gzipped.
MASK_TOLERANCE_DEG = 0.01
# Where a grey country meets a covered one, Natural Earth's and geoBoundaries' borders
# disagree by a few hundred metres; opening by this much drops the slivers that leaves.
MASK_SLIVER_DEG = 0.02
# A covered outline can also take in a real piece of a grey country: India's claims most of
# Pakistan-administered Kashmir, Finland's holds Aland, the USA's the Virgin Islands. The
# outline alone would clear those, but the rasters behind them hold almost nobody there
# (Kashmir 76k people over 79,000 km2, Aland 0). So an overlap bigger than this share of the
# grey country stays grey unless the pack has at least MIN_DENSITY people per km2 in it.
CLAIM_SHARE = 0.05
MIN_DENSITY = 5.0


def no_data(countries: list[str], packs: Path = PACK_CACHE) -> dict:
    """Land with no data in this build, as one GeoJSON feature: every Natural Earth country
    not built, minus where the built ones' clipping outlines and data reach into it, so the
    grey stops where the data does."""
    import h3
    import numpy as np
    from coverage_map import NE_CACHE, fetch_world
    from packstore import PackStore
    from shapely import STRtree
    from shapely.geometry import mapping, shape
    from shapely.ops import unary_union

    fetch_world()  # downloads and caches the file if needed
    built = set(countries)

    def codes(p: dict) -> set[str]:
        return {(p.get(k) or "").upper() for k in ("ADM0_A3", "ISO_A3", "ISO_A3_EH")}

    features = json.loads(NE_CACHE.read_text())["features"]
    greys = [shape(f["geometry"]).buffer(0) for f in features if not codes(f["properties"]) & built]
    # Only covered countries that touch the grey matter; unioning all of them takes minutes.
    pieces = [(iso3, shape(f["geometry"])) for iso3 in countries for f in country_outline(iso3)]
    tree = STRtree([g for _, g in pieces])
    near: dict[str, list] = {}
    for grey in greys:
        for i in tree.query(grey.buffer(0.1)):
            iso3, g = pieces[int(i)]
            near.setdefault(iso3, []).append(g)
    outlines = {
        iso3: unary_union([g.simplify(0.005).buffer(0) for g in set(gs)])
        for iso3, gs in near.items()
    }
    store = PackStore(packs)

    def has_people(iso3: str, area) -> bool:
        res = int(store.entries[iso3]["max_resolution"])
        cells = np.array(
            [h3.str_to_int(c) for c in h3.geo_to_cells(mapping(area), res)], dtype=np.uint64
        )
        people = store.lookup(iso3, res, np.sort(cells)).sum() if cells.size else 0.0
        km2 = cells.size * h3.average_hexagon_area(res, "km^2")
        return bool(km2) and people / km2 >= MIN_DENSITY

    kept = []
    for grey in greys:
        for iso3, outline in outlines.items():
            if not grey.intersects(outline):
                continue
            overlap = grey.intersection(outline)
            if overlap.area <= CLAIM_SHARE * grey.area or has_people(iso3, overlap):
                grey = grey.difference(outline)
        kept.append(grey)
    mask = unary_union(kept).buffer(-MASK_SLIVER_DEG).buffer(MASK_SLIVER_DEG)
    g = mapping(mask.simplify(MASK_TOLERANCE_DEG, preserve_topology=True))

    def rnd(x):
        return [rnd(v) for v in x] if isinstance(x, (list, tuple)) else round(x, 3)

    geometry = {"type": g["type"], "coordinates": rnd(g["coordinates"])}
    return {
        "type": "FeatureCollection",
        "features": [{"type": "Feature", "properties": {}, "geometry": geometry}],
    }


# The mask takes ~70 s for 196 countries, nearly the whole build, and only changes when
# its inputs do. Cached under data/ by a key over everything it reads: the code of
# no_data() and its settings, the countries and their pack files (content-hashed names, so
# a rebuilt pack is a new key) and the Natural Earth file.
NODATA_CACHE = ROOT / "data" / "nodata"


def nodata_key(entries: dict) -> str:
    from coverage_map import NE_CACHE, fetch_world

    fetch_world()
    h = hashlib.sha256(inspect.getsource(no_data).encode())
    h.update(repr((MASK_TOLERANCE_DEG, MASK_SLIVER_DEG, CLAIM_SHARE, MIN_DENSITY)).encode())
    for iso3 in sorted(entries):
        h.update(f"{iso3}:{','.join(sorted(p['url'] for p in entries[iso3]['parts']))};".encode())
    h.update(hashlib.sha256(NE_CACHE.read_bytes()).digest())
    return h.hexdigest()[:20]


def cached_no_data(entries: dict) -> tuple[str, bool]:
    """The mask as JSON text, and whether it came from the cache."""
    path = NODATA_CACHE / f"{nodata_key(entries)}.geojson"
    if path.exists():
        return path.read_text(), True
    text = json.dumps(no_data(list(entries)), separators=(",", ":"))
    NODATA_CACHE.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text)
    tmp.replace(path)
    return text, False


def main(
    country: Annotated[
        list[str] | None,
        typer.Option(help="ISO3 code; repeat for several (default: CMR, CAF and BRA)"),
    ] = None,
    out: Annotated[Path, typer.Option(help="Output directory (replaced)")] = ROOT / "dist",
    rebuild_packs: Annotated[
        bool, typer.Option(help="Recompute packs even if data/packs/ already has them")
    ] = False,
) -> None:
    t0 = time.perf_counter()
    countries = [c.upper() for c in country or DEFAULT_COUNTRIES]
    cached = ensure(countries, PACK_CACHE, rebuild=rebuild_packs)
    entries = {iso3: cached[iso3] for iso3 in countries}
    xs = [v for e in entries.values() for v in e["bbox"][0::2]]
    ys = [v for e in entries.values() for v in e["bbox"][1::2]]
    manifest = {
        "built": datetime.now(UTC).date().isoformat(),
        "countries": [
            {k: e[k] for k in ("iso3", "name", "total", "bytes", "data_version")}
            for e in entries.values()
        ],
        "total": sum(e["total"] for e in entries.values()),
        "bbox": [min(xs), min(ys), max(xs), max(ys)],
        "catalog": "../packs/catalog.json",
        "nodata": "nodata.geojson",
        "attribution": attribution({e["source"] for e in entries.values()}),
    }

    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(WEB, out, ignore=shutil.ignore_patterns(".DS_Store"))
    data = out / "data"
    data.mkdir()
    mask, mask_cached = cached_no_data(entries)
    (data / "nodata.geojson").write_text(mask)
    (data / "manifest.json").write_text(json.dumps(manifest, indent=1))
    packs = out / "packs"
    packs.mkdir()
    for e in entries.values():
        for p in e["parts"]:
            shutil.copy2(PACK_CACHE / p["url"], packs / p["url"])
    write_catalog(packs, entries)

    console.print(f"\n[green]Built {out}[/green] in {time.perf_counter() - t0:.0f} s")
    for e in entries.values():
        parts = e["parts"]
        biggest = max(p["bytes"] for p in parts)
        console.print(
            f"  {e['iso3']} {e['name']}: {e['total']:,} people, {len(parts)} part(s), "
            f"{e['bytes'] / 1e6:.2f} MB (largest file {biggest / 1e6:.2f} MB)"
        )
    console.print(f"  catalog.json: {(packs / 'catalog.json').stat().st_size / 1e3:.0f} KB")
    console.print(
        f"  nodata.geojson: {(data / 'nodata.geojson').stat().st_size / 1e3:.0f} KB"
        + (" (cached)" if mask_cached else "")
    )
    console.print(f"  total population {manifest['total']:,}")
    console.print(
        "\nTry it: python3 -m http.server 8081 --directory dist --bind 127.0.0.1"
        "  ->  http://127.0.0.1:8081"
    )


if __name__ == "__main__":
    typer.run(main)
