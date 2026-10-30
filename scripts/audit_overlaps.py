# Copyright (c) 2026 Woody McLendon
# SPDX-License-Identifier: MIT
"""Outline-overlap audit: people that two countries' packs both count.

    uv run python scripts/audit_overlaps.py                    # every built country
    uv run python scripts/audit_overlaps.py --country IND      # only pairs with India
    uv run python scripts/audit_overlaps.py --candidate PAK    # a country not built yet

A pack is its country's raster clipped to an outline, keeping each pixel whose center is
inside (ingest/raster.py). Where two outlines overlap, a pixel there is inside both: a
claimed territory (India's outline takes in Pakistan-administered Kashmir) or a sliver
where two outlines disagree by a few meters. If both rasters hold people at that spot,
both packs count them. Meta's files cover each country's bounding box, so a neighbor's
file usually does.

So for every pair of outlines that overlap, this reads each country's own raster inside
the overlap with the build's rule (pixel center inside, the same valid-pixel test) and
reports the people each holds there. min(a, b) is the double count when both rasters are
the same map (Meta's countries are cut from one global map); across two sources it is an
estimate. A pair both clips keep uncut (two WorldPop countries, ingest/overlaps.KEPT_BY_BOTH)
holds each side's own people there, so for it the double count is where both rasters put
people in the same pixel, the smaller of the two summed, when they share a grid (WorldPop's
files all lie on one 3-arcsecond grid). Across grids (Hong Kong's Meta 30 m against China's
WorldPop 100 m) it is the same in res-9 cells: an upper bound, as a cell on the border holds
both sides (Israel-Palestinian Territories 19,634 in cells, 0 in pixels; 2026-10-23). Pairs over
--fail-above people double counted make it exit 1.

Built countries are read with the outlines their packs were clipped to: their own outline
minus the overlaps given to a neighbor (ingest/overlaps.py, F-30). Before that split this
found 1.67 M people counted twice; after it, what is left are slivers from floating-point
edges, and the odd pixel whose center lies exactly on one. A place whose raster is added to
another country's pack (build_packs.ADDED_TO) is read as its own outline, PLACE@COUNTRY
(PSE@ISR: Palestine's people in Israel's pack), clipped as the build clips it.

A --candidate, a country not built yet, is checked the same way against the built ones,
with its own outline, its side read from its raster if one is cached (WorldPop by
default): what adding it would count twice before scripts/split_overlaps.py takes it in.
Results also go to data/audit/overlaps.json.
"""

from __future__ import annotations

import json
import math
import sys
import time
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Annotated

import numpy as np
import rasterio
import shapely
import typer
from rasterio.features import geometry_mask
from rasterio.windows import Window, from_bounds
from rich.console import Console
from rich.table import Table
from shapely.geometry import mapping
from shapely.geometry.base import BaseGeometry

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_packs import ADDED_TO, PACK_CACHE, raster_paths, source_for

from ingest.boundaries import area_km2, clip_base, outline_shape
from ingest.config import DATA_DIR, SOURCES
from ingest.overlaps import added_clip_path, clip_path, kept_by_both, polygonal
from ingest.pipeline import expand_rasters
from ingest.raster import _reduce, _valid_pixels, pixels_to_cells

AUDIT_DIR = DATA_DIR / "audit"
# Rows read at a time: a claim the size of Kashmir is ~10,000 rows of 30 m pixels.
ROWS_PER_READ = 1024
# Where an uncut pair's two rasters on different grids are compared cell by cell: the
# packs' base resolution.
COLOCATE_RES = 9
console = Console()
app = typer.Typer(add_completion=False)


@dataclass
class Overlap:
    a: str
    b: str
    km2: float
    people_a: float | None  # None: no raster to read (a candidate with none cached)
    people_b: float | None
    colocated: float | None = None  # an uncut pair's people where both rasters put some

    @property
    def both(self) -> float:
        """People both packs count there: exact when both are cut from one map."""
        if self.colocated is not None:
            return self.colocated
        if self.people_a is None or self.people_b is None:
            return 0.0
        return min(self.people_a, self.people_b)


@dataclass
class Filled:
    """Where a raster puts people inside an area: (keys, people) by res-9 cell (None when
    not asked for) and by pixel of its grid (None when its files lie on different grids)."""

    cells: tuple[np.ndarray, np.ndarray] | None
    pixels: tuple[np.ndarray, np.ndarray]
    grid: tuple | None


def grid_of(t) -> tuple:
    """A raster's pixel size and where its pixel edges fall: equal for two rasters whose
    pixels coincide."""
    return (round(t.a, 15), round(t.e, 15), round(t.c / t.a % 1, 3), round(t.f / t.e % 1, 3))


def pixel_keys(lats: np.ndarray, lngs: np.ndarray, t) -> np.ndarray:
    """A pixel center's row and column on its grid counted from 90 N, 180 W, as one key."""
    rows = np.floor((lats - 90.0) / t.e).astype(np.uint64)
    cols = np.floor((lngs + 180.0) / t.a).astype(np.uint64)
    return (rows << np.uint64(32)) | cols


def colocated(a: Filled, b: Filled) -> float:
    """People where both rasters put some, the smaller side's per pixel when they share a
    grid, else per res-9 cell."""
    if not (a.pixels[0].size and b.pixels[0].size):
        return 0.0  # a side with no one there (and so no grid)
    same = a.grid is not None and a.grid == b.grid
    if not same and (a.cells is None or b.cells is None):
        raise RuntimeError("two rasters on different grids were read without their cells")
    x, y = (a.pixels, b.pixels) if same else (a.cells, b.cells)
    _, i, j = np.intersect1d(x[0], y[0], assume_unique=True, return_indices=True)
    return float(np.minimum(x[1][i], y[1][j]).sum())


def overlaps(outlines: dict[str, BaseGeometry], pairs_with: set[str] | None = None) -> dict:
    """{(a, b): overlap geometry} for every pair of outlines whose areas overlap, a < b.

    Outlines are split into their polygons, so a coast of 50,000 islands costs a bounding-box
    lookup, not a test against every neighbor; only pieces whose boxes meet are intersected.
    `pairs_with` keeps only pairs that include one of those countries.
    """
    pieces, owner = [], []
    for iso3, g in outlines.items():
        for p in getattr(g, "geoms", [g]):
            pieces.append(p)
            owner.append(iso3)
    owner_arr = np.array(owner)
    tree = shapely.STRtree(pieces)
    left, right = tree.query(pieces)
    keep = owner_arr[left] < owner_arr[right]
    if pairs_with is not None:
        keep &= np.isin(owner_arr[left], list(pairs_with)) | np.isin(
            owner_arr[right], list(pairs_with)
        )
    left, right = left[keep], right[keep]
    geoms = np.array(pieces, dtype=object)
    inter = shapely.intersection(geoms[left], geoms[right])
    found: dict[tuple[str, str], list] = defaultdict(list)
    for i, j, g in zip(left, right, inter, strict=True):
        if not g.is_empty:
            area = polygonal(g)
            if not area.is_empty:
                found[(owner[i], owner[j])].append(area)
    return {pair: shapely.union_all(gs) for pair, gs in sorted(found.items())}


def raster_people(
    paths: list[str], areas: dict, by_cell: frozenset = frozenset(), cells: frozenset = frozenset()
) -> tuple[dict, dict]:
    """{key: people in the rasters at pixels whose center lies in areas[key]}, as the build
    counts them, and for the keys in `by_cell` {key: Filled}, by res-9 cell too for the keys
    in `cells` (one h3 call a pixel, so only where the other side lies on another grid).

    The rasters are strips inside zips, so any window unpacks whole strips: a sliver along a
    1,000 km border costs most of the country however it is sliced. So each raster is read
    once, top to bottom, for all of a country's overlaps together, and only the rows some
    overlap reaches are read at all.
    """
    totals = {k: 0.0 for k in areas}
    found: dict = {k: ([], [], [], set()) for k in by_cell if k in areas}
    shapes = {k: [mapping(g)] for k, g in areas.items()}
    for path in paths:
        with rasterio.open(path) as ds:
            spans = {k: sp for k, g in areas.items() if (sp := _span(ds, g.bounds))}
            if not spans:
                continue
            top = min(sp[2] for sp in spans.values())
            bottom = max(sp[3] for sp in spans.values())
            for r in range(top, bottom, ROWS_PER_READ):
                h = min(ROWS_PER_READ, bottom - r)
                active = [k for k, sp in spans.items() if sp[2] < r + h and r < sp[3]]
                if not active:
                    continue
                c_min = min(spans[k][0] for k in active)
                c_max = max(spans[k][1] for k in active)
                block = ds.read(1, window=Window(c_min, r, c_max - c_min, h))
                rows, cols = _valid_pixels(block, ds.nodata)
                if not rows.size:
                    continue
                for k in active:
                    c0, c1 = spans[k][0] - c_min, spans[k][1] - c_min
                    sel = (cols >= c0) & (cols < c1)
                    if not sel.any():
                        continue
                    rr, cc = rows[sel], cols[sel]
                    inside = geometry_mask(
                        shapes[k],
                        out_shape=(h, c1 - c0),
                        transform=ds.window_transform(Window(c_min + c0, r, c1 - c0, h)),
                        invert=True,
                    )[rr, cc - c0]
                    pops = block[rr[inside], cc[inside]].astype(np.float64)
                    totals[k] += float(pops.sum())
                    if k in found and pops.size:
                        t = ds.window_transform(Window(c_min, r, c_max - c_min, h))
                        lngs = t.c + (cc[inside] + 0.5) * t.a
                        lats = t.f + (rr[inside] + 0.5) * t.e
                        if k in cells:
                            found[k][0].append(pixels_to_cells(lats, lngs, COLOCATE_RES))
                        found[k][1].append(pops)
                        found[k][2].append(pixel_keys(lats, lngs, t))
                        found[k][3].add(grid_of(t))

    def table(keys: list, pops: list) -> tuple[np.ndarray, np.ndarray]:
        return _reduce(
            np.concatenate(keys or [np.empty(0, np.uint64)]),
            np.concatenate(pops or [np.empty(0)]),
        )

    filled = {
        k: Filled(
            table(c, p) if k in cells else None,
            table(px, p),
            next(iter(g)) if len(g) == 1 else None,
        )
        for k, (c, p, px, g) in found.items()
    }
    return totals, filled


def read_people(
    areas: dict[tuple[str, str], BaseGeometry],
    rasters: dict[str, list[str]],
    workers: int,
    t0: float,
    by_cell: frozenset = frozenset(),
    with_cells: frozenset = frozenset(),
) -> tuple[dict[tuple[str, str, str], float | None], dict]:
    """{(a, b, iso3): people in iso3's raster inside areas[(a, b)]}, for iso3 in a and b;
    None where iso3 has no raster to read. With it, for the pairs in `by_cell`,
    {(a, b, iso3): Filled}, with res-9 cells for the pairs in `with_cells`.

    One job per country: its raster is read once for all of its areas. Biggest rasters
    first, so the long ones start early.
    """
    by_country: dict[str, dict] = defaultdict(dict)
    for pair, area in areas.items():
        for iso3 in pair:
            by_country[iso3][pair] = area

    def size(iso3: str) -> int:
        return sum(
            Path(p.split("!")[0].removeprefix("zip://")).stat().st_size for p in rasters[iso3]
        )

    people: dict[tuple[str, str, str], float | None] = {
        (a, b, iso3): None for (a, b) in areas for iso3 in (a, b) if not rasters[iso3]
    }
    cells: dict = {}
    with ProcessPoolExecutor(max_workers=workers) as pool:
        jobs = {
            pool.submit(raster_people, rasters[iso3], country_areas, by_cell, with_cells): iso3
            for iso3, country_areas in sorted(by_country.items(), key=lambda kv: -size(kv[0]))
            if rasters[iso3]
        }
        with console.status("Reading rasters") as status:
            for n, job in enumerate(as_completed(jobs), 1):
                iso3 = jobs[job]
                totals, found = job.result()
                for (a, b), value in totals.items():
                    people[(a, b, iso3)] = value
                for (a, b), table in found.items():
                    cells[(a, b, iso3)] = table
                status.update(f"Reading rasters: {n}/{len(jobs)} countries")
                if not console.is_terminal:
                    print(f"{n}/{len(jobs)} {iso3} done at {time.time() - t0:.0f} s", flush=True)
    return people, cells


def _span(ds, bounds: tuple[float, float, float, float]) -> tuple[int, int, int, int] | None:
    """(col0, col1, row0, row1) of the pixels whose centers can lie within `bounds`."""
    w, s, e, n = bounds
    left, bottom, right, top = ds.bounds
    w, s, e, n = max(w, left), max(s, bottom), min(e, right), min(n, top)
    if w >= e or s >= n:
        return None
    win = from_bounds(w, s, e, n, transform=ds.transform)
    col0 = max(math.floor(win.col_off), 0)
    row0 = max(math.floor(win.row_off), 0)
    col1 = min(math.ceil(win.col_off + win.width), ds.width)
    row1 = min(math.ceil(win.row_off + win.height), ds.height)
    return (col0, col1, row0, row1) if col0 < col1 and row0 < row1 else None


def built_countries(packs: Path = PACK_CACHE) -> dict[str, list[float]]:
    """{iso3: bbox [w, s, e, n]} of every built country, from the catalog."""
    catalog = json.loads((packs / "catalog.json").read_text())
    return {e["iso3"]: e["bbox"] for e in sorted(catalog["countries"], key=lambda e: e["iso3"])}


def near(bbox: list[float], others: dict[str, list[float]]) -> list[str]:
    """Countries whose bbox meets `bbox`. Loading every outline takes ~1.5 min, so a check of
    one country loads only those that can touch it."""
    w, s, e, n = bbox
    return [
        k for k, (w2, s2, e2, n2) in others.items() if w2 <= e and w <= e2 and s2 <= n and s <= n2
    ]


def clipped_outline(iso3: str) -> BaseGeometry:
    """The outline a built country's pack was clipped to (ingest/overlaps.py)."""
    return outline_shape(clip_path(iso3))


def added_places(codes: list[str]) -> dict[str, tuple[str, str]]:
    """{PLACE@COUNTRY: (country, place)} for the places added to these countries' packs."""
    return {f"{c}@{h}": (h, c) for h in codes for c in ADDED_TO.get(h, {})}


def rasters_for(iso3: str, source: str | None = None, *, cached_only: bool = False) -> list[str]:
    if "@" in iso3:
        place, host = iso3.split("@")
        return rasters_for(place, source or ADDED_TO[host][place], cached_only=cached_only)
    source = source or source_for(iso3)
    paths = SOURCES[source].default_paths(iso3) if cached_only else raster_paths(iso3, source)
    return expand_rasters(paths) if paths else []


@app.command()
def main(
    country: Annotated[
        list[str] | None, typer.Option("--country", help="Only pairs with these")
    ] = None,
    candidate: Annotated[
        list[str] | None, typer.Option("--candidate", help="Countries not built yet")
    ] = None,
    candidate_source: Annotated[
        str, typer.Option(help="Source of the candidates' rasters, if cached")
    ] = "worldpop_100m",
    fail_above: Annotated[
        float, typer.Option(help="Exit 1 when a pair double counts more people than this")
    ] = 1000.0,
    out: Annotated[Path, typer.Option(help="JSON results")] = AUDIT_DIR / "overlaps.json",
    workers: Annotated[int, typer.Option(help="Rasters read in parallel")] = 6,
) -> None:
    t0 = time.time()
    boxes = built_countries()
    built = list(boxes)
    candidates = sorted({c.upper() for c in candidate or []} - set(built))
    focus = {c.upper() for c in country or []} | set(candidates) or None
    with console.status("Loading outlines"):
        outlines = {iso3: clip_base(iso3) for iso3 in candidates}
        if focus is None:
            codes = built
        else:
            # A bbox from the catalog (built) or the outline (candidate): the neighbors
            # are whatever can touch it.
            focus_boxes = [boxes[c] if c in boxes else list(outlines[c].bounds) for c in focus]
            codes = sorted({k for b in focus_boxes for k in near(b, boxes)} | (focus & set(built)))
        outlines |= {iso3: clipped_outline(iso3) for iso3 in codes}
        added = added_places(codes)
        outlines |= {k: outline_shape(added_clip_path(*hc)) for k, hc in added.items()}
        if focus is not None:
            focus |= {k for k, (host, _) in added.items() if host in focus}
    console.print(f"{len(outlines)} outlines loaded in {time.time() - t0:.0f} s")
    with console.status("Finding overlaps"):
        found = overlaps(outlines, focus)
    console.print(f"{len(found)} overlapping pairs in {time.time() - t0:.0f} s")

    rasters = {
        iso3: rasters_for(iso3, candidate_source, cached_only=True)
        if iso3 in candidates
        else rasters_for(iso3)
        for pair in found
        for iso3 in pair
    }

    def place(k: str) -> str:
        return k.split("@")[0]

    def source_of(k: str) -> str:
        if k in added:
            host, code = added[k]
            return ADDED_TO[host][code]
        return candidate_source if k in candidates else source_for(k)

    uncut = frozenset(p for p in found if kept_by_both(place(p[0]), place(p[1])))
    # Two WorldPop rasters lie on one grid, compared pixel by pixel; any other pair needs
    # res-9 cells.
    by_cell = frozenset(p for p in uncut if len({source_of(k) for k in p} | {"worldpop_100m"}) > 1)
    people, cells = read_people(found, rasters, workers, t0, uncut, by_cell)

    def shared(a: str, b: str) -> float | None:
        if (a, b) not in uncut or (a, b, a) not in cells or (a, b, b) not in cells:
            return None
        return colocated(cells[(a, b, a)], cells[(a, b, b)])

    results = [
        Overlap(a, b, area_km2(mapping(area)), people[(a, b, a)], people[(a, b, b)], shared(a, b))
        for (a, b), area in found.items()
    ]
    results.sort(key=lambda r: (-r.both, -r.km2))

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {
                "built": built,
                "candidates": candidates,
                "pairs": [asdict(r) | {"both": r.both} for r in results],
            },
            indent=1,
        )
    )

    def fmt(x: float | None) -> str:
        return "—" if x is None else f"{x:,.0f}"

    shown = [r for r in results if r.both >= 1 or (r.a in candidates or r.b in candidates)]
    table = Table(title="People inside two outlines (pairs with any)")
    for col in ("Pair", "Overlap km²", "In A's raster", "In B's raster", "Counted twice"):
        table.add_column(col, justify="left" if col == "Pair" else "right")
    for r in shown:
        table.add_row(
            f"{r.a}–{r.b}", f"{r.km2:,.1f}", fmt(r.people_a), fmt(r.people_b), fmt(r.both)
        )
    console.print(table)
    total = sum(r.both for r in results)
    over = [r for r in results if r.both > fail_above]
    console.print(
        f"{len(results)} overlapping pairs, {sum(r.both >= 1 for r in results)} with people in "
        f"both; {total:,.0f} people counted twice in all; {len(over)} pairs over "
        f"{fail_above:,.0f}. Written to {out} in {time.time() - t0:.0f} s."
    )
    raise typer.Exit(1 if over else 0)


if __name__ == "__main__":
    app()
