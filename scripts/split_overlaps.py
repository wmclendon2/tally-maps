# Copyright (c) 2026 Woody McLendon
# SPDX-License-Identifier: MIT
"""Split every outline overlap between its two countries, and build their clips (F-30).

    uv run python scripts/split_overlaps.py                  # every built country
    uv run python scripts/split_overlaps.py --country PAK    # and countries to build next

Where two countries' outlines overlap, both packs would count the people there. The rule
is in ingest/overlaps.py: the overlap is split along geoBoundaries CGAZ's border, and the
rest (in neither side's CGAZ unit, or all of it when CGAZ has no unit for a side) goes to
the side whose raster holds more people there, ties to the lower ISO3 code. This finds the
overlapping pairs, reads both rasters inside each rest (with the build's rule, as
scripts/audit_overlaps.py does) and writes the decisions to reference/overlap_split.json,
which is checked in: a build needs only the outlines, CGAZ and that file.

It needs every country the file already covers to be built (or passed with --country),
so a build of a few countries cannot drop the others' pairs. It then builds every
country's clip into data/boundaries/clip/ and checks each pair: the
two clips must not overlap, and every piece of the overlap must stay in one of them, so no
one is lost. It names the countries whose clips changed: rebuild their packs
(build_packs.py --country ...), then run scripts/audit_overlaps.py.
"""

from __future__ import annotations

import json
import math
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Annotated

import shapely
import typer
from rich.console import Console
from rich.table import Table
from shapely.geometry import mapping

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from audit_overlaps import built_countries, overlaps, rasters_for, read_people
from verify_country import load_outline

from ingest.boundaries import area_km2, outline_shape
from ingest.overlaps import (
    CGAZ_SHA256,
    CGAZ_URL,
    SPLIT_PATH,
    cgaz_units,
    clip_path,
    load_split,
    overlap_of,
    pairs_of,
    pieces,
    polygonal,
    rest_of,
    split_key,
)

# A pair's clips may overlap, or drop, this much from floating-point edges: 100 m2, a
# ninth of one 30 m pixel (on 2026-10-20 the worst were 16 m2 shared, 78 m2 dropped).
SLIVER_KM2 = 1e-4
KM_PER_DEG = 111.195  # on a sphere of the mean radius, as area_km2
console = Console()
app = typer.Typer(add_completion=False)


def km2(g) -> float:
    return 0.0 if g.is_empty else area_km2(mapping(g))


def sliver_km2(g) -> float:
    """km2 of slivers, from their planar area at their latitude. area_km2's sum loses the
    last digits it needs here: it puts a 1.5 m2 sliver at 12,000 m2."""
    return sum(p.area * KM_PER_DEG**2 * math.cos(math.radians(p.centroid.y)) for p in pieces(g))


def decide(pair: tuple[str, str], people: dict) -> str:
    """Who gets a pair's rest: the side whose raster holds more people there, ties (and
    no raster) to the lower ISO3 code, a."""
    a, b = pair
    pa, pb = (people.get((a, b, k)) or 0.0 for k in pair)
    return b if pb > pa else a


def check_pair(pair: dict, outline, clip, thirds: list[str] = ()) -> tuple[float, float]:
    """(km2 both clips still hold, km2 of the overlap no clip holds) for one pair. Where a
    third outline overlaps both (`thirds`, near a tripoint), it may keep the piece."""
    a, b = pair["a"], pair["b"]
    overlap = overlap_of(outline(a), outline(b))
    if overlap.is_empty:
        return 0.0, 0.0
    both = overlap_of(clip(a), clip(b))
    kept = shapely.union_all([overlap_of(overlap, clip(k)) for k in (a, b, *thirds)])
    return sliver_km2(both), sliver_km2(polygonal(overlap.difference(kept)))


def write_split(split: dict, path: Path = SPLIT_PATH) -> None:
    """One pair a line, so a rerun's diff shows which pairs changed."""
    lines = [f" {json.dumps(k)}: {json.dumps(v)}" for k, v in split.items() if k != "pairs"]
    rows = ",\n".join("  " + json.dumps(p, separators=(",", ":")) for p in split["pairs"])
    path.write_text("{\n" + ",\n".join(lines) + ',\n "pairs": [\n' + rows + "\n ]\n}\n")


@app.command()
def main(
    country: Annotated[
        list[str] | None, typer.Option("--country", help="Countries not built yet to take in")
    ] = None,
    workers: Annotated[int, typer.Option(help="Rasters read, and clips built, at once")] = 6,
) -> None:
    t0 = time.time()
    codes = sorted(set(built_countries()) | {c.upper() for c in country or []})
    old = load_split() if SPLIT_PATH.exists() else None
    if old and (missing := sorted(set(old["countries"]) - set(codes))):
        # A build of a few countries would otherwise rewrite the checked-in split without
        # the others' pairs.
        console.print(
            f"[red]{SPLIT_PATH.name} covers {len(missing)} countries not built here "
            f"({' '.join(missing[:10])}{' ...' if len(missing) > 10 else ''}): build them "
            "first, or pass them with --country.[/red]"
        )
        raise typer.Exit(2)
    with console.status("Loading outlines"):
        outlines = {iso3: load_outline(iso3) for iso3 in codes}
    found = sorted(overlaps(outlines))
    units = cgaz_units(codes)
    console.print(
        f"{len(outlines)} outlines, {len(found)} overlapping pairs, {len(units)} with a CGAZ "
        f"unit, in {time.time() - t0:.0f} s"
    )

    with console.status("Splitting overlaps along CGAZ"):
        overlap = {p: overlap_of(outlines[p[0]], outlines[p[1]]) for p in found}
        found = [p for p in found if not overlap[p].is_empty]
        rest = {p: rest_of(overlap[p], units.get(p[0]), units.get(p[1])) for p in found}
    # A rest the file already holds, unchanged, keeps its decision: its rasters are not
    # read again (taking in one new country reads only its neighbours').
    known = {}
    if old and old["cgaz"]["sha256"] == CGAZ_SHA256:
        known = {(q["a"], q["b"]): q for q in old["pairs"]}
    kept = {p for p in found if p in known and known[p]["rest_km2"] == round(km2(rest[p]), 3)}
    to_read = {p: g for p, g in rest.items() if not g.is_empty and p not in kept}
    rasters = {iso3: rasters_for(iso3) for p in to_read for iso3 in p}
    people = read_people(to_read, rasters, workers, t0)
    console.print(f"{len(to_read)} rests read, {len(kept)} kept from {SPLIT_PATH.name}")

    pairs = []
    for p in found:
        a, b = p
        q = known[p] if p in kept else None
        pairs.append(
            {
                "a": a,
                "b": b,
                "overlap_km2": round(km2(overlap[p]), 3),
                "cgaz_units": [a in units, b in units],
                "rest_km2": round(km2(rest[p]), 3),
                "rest_people": q["rest_people"]
                if q
                else [round(people.get((a, b, k)) or 0.0, 1) for k in p],
                "rest_to": q["rest_to"] if q else decide(p, people),
            }
        )
    split = {
        "about": "Who keeps the rest of each outline overlap (F-30): see ingest/overlaps.py. "
        "Written by scripts/split_overlaps.py.",
        "cgaz": {"url": CGAZ_URL, "sha256": CGAZ_SHA256},
        "countries": codes,
        "pairs": pairs,
    }
    write_split(split)
    console.print(f"Wrote {SPLIT_PATH} in {time.time() - t0:.0f} s")

    involved = sorted({c for p in pairs for c in (p["a"], p["b"])})
    with ProcessPoolExecutor(max_workers=workers) as pool:
        paths = dict(zip(involved, pool.map(clip_path, involved, [split] * len(involved))))
    clips = {iso3: outline_shape(paths[iso3]) for iso3 in involved}
    console.print(f"{len(clips)} clips built in {time.time() - t0:.0f} s")

    table = Table(title="Overlaps whose rest held people, and pairs that failed the check")
    for col in ("Pair", "Overlap km²", "Rest km²", "Rest people A", "Rest people B", "To"):
        table.add_column(col, justify="left" if col in ("Pair", "To") else "right")
    bad = []
    partners: dict[str, set[str]] = {}
    for p in pairs:
        partners.setdefault(p["a"], set()).add(p["b"])
        partners.setdefault(p["b"], set()).add(p["a"])
    for p in pairs:
        thirds = sorted(partners[p["a"]] & partners[p["b"]])
        both, lost = check_pair(p, outlines.__getitem__, clips.__getitem__, thirds)
        failed = both > SLIVER_KM2 or lost > SLIVER_KM2
        if failed:
            bad.append(f"{p['a']}–{p['b']}: clips share {both:.6f} km², drop {lost:.6f} km²")
        if failed or max(p["rest_people"]) >= 1:
            table.add_row(
                f"{p['a']}–{p['b']}" + (" [red]FAILED[/red]" if failed else ""),
                f"{p['overlap_km2']:,.1f}",
                f"{p['rest_km2']:,.2f}",
                f"{p['rest_people'][0]:,.0f}",
                f"{p['rest_people'][1]:,.0f}",
                p["rest_to"],
            )
    console.print(table)
    given = {iso3: km2(polygonal(outlines[iso3].difference(clips[iso3]))) for iso3 in involved}
    console.print(
        f"{len(pairs)} pairs; {sum(given.values()):,.0f} km² given away in all, most by "
        + ", ".join(f"{k} {v:,.0f}" for k, v in sorted(given.items(), key=lambda kv: -kv[1])[:5])
    )
    # A clip changes when its pairs do; one that gives nothing away now and had no pairs
    # before is its outline, so its pack is unchanged.
    changed = [
        c
        for c in codes
        if (given.get(c, 0.0) > 0 or (old and pairs_of(c, old)))
        and (old is None or split_key(c, old) != split_key(c, split))
    ]
    built = set(built_countries())
    rebuild = [c for c in changed if c in built]
    if rebuild:
        console.print(
            f"{len(rebuild)} clips changed; rebuild their packs:\n  "
            "uv run python scripts/build_packs.py " + " ".join(f"--country {c}" for c in rebuild)
        )
    for line in bad:
        console.print(f"[red]{line}[/red]")
    console.print(f"Done in {time.time() - t0:.0f} s")
    raise typer.Exit(1 if bad else 0)


if __name__ == "__main__":
    app()
