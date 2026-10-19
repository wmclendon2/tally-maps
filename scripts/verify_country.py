# Copyright (c) 2026 Woody McLendon
# SPDX-License-Identifier: MIT
"""Independent country-level check: population from the border polygon, not the country tag.

Every cell in a country's pack belongs to that country because it was clipped to its
outline at build time. Summing the pack only proves the builder agrees with itself. This
script instead asks the question a user asks -- "how many people are inside this
polygon?" -- using the country's geoBoundaries ADM0 outline, summed over every pack the
way web/static.js does, and reconciles the answer against the country's own total exactly.

    uv run python scripts/verify_country.py --country CMR

The two totals cannot be identical, because the polygon cover is made of hexagons and the
border is not:

  * loss -- this country's people in cells whose center falls outside the outline (just
    over the border, or offshore along the coast).
  * gain -- a *neighbouring* country's people in cells whose center falls inside this
    outline. The polygon total sums a cell across every pack, so they count.

  polygon_total == country_total - loss + gain

That identity is exact and is checked as such. The leftover net error is reported as the
honest measure of how much the hex grid distorts a border at this resolution. It also
checks that every pyramid level sums to the same total, that the total matches the
catalogue, and each part's hashes, header, footprint and totals.

Outlines that cross the antimeridian (FJI, KIR, NZL) are refused: their lon/lat bounding
box spans nearly the whole globe.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated

import h3
import numpy as np
import typer
from rich.console import Console
from rich.table import Table
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ingest.boundaries import boundary_path, fetch_boundary
from ingest.config import MIN_RESOLUTION, SOURCES

console = Console()
app = typer.Typer(add_completion=False)

# Totals of ~1e7 lose the last few bits under summation.
FLOAT_TOL = 1e-6


def load_outline(iso3: str) -> BaseGeometry:
    """The country's ADM0 outline as one shapely geometry."""
    path = boundary_path(iso3)
    if not path.exists():
        path = fetch_boundary(iso3)
    doc = json.loads(path.read_text())
    if doc.get("type") == "FeatureCollection":
        geom = unary_union([shape(f["geometry"]) for f in doc["features"] if f.get("geometry")])
    elif doc.get("type") == "Feature":
        geom = shape(doc["geometry"])
    else:
        geom = shape(doc)
    if not geom.is_valid:  # self-intersections are common in OSM-derived outlines
        geom = geom.buffer(0)
    return geom


@dataclass
class Facts:
    tagged_at_res: float
    tagged_at_base: float
    base_resolution: int
    inside: float  # this country's people in cells the polygon covers
    outside: float  # this country's people in cells it does not  (the loss term)
    by_other: dict[str, float]  # neighbours' people inside our cells  (the gain term)
    catalogue_total: float
    # Packs store float32 populations and a rounded catalogue total.
    tol_abs: float = 1.0
    tol_rel: float = 1e-6
    problems: list[str] = field(default_factory=list)


def query_packs(
    store, iso3: str, source: str, resolution: int, cells: set[str]
) -> tuple[float, Facts]:
    """(polygon total, facts) from the built packs.

    The polygon total sums every pack that holds a covered cell, as the browser does, so
    neighbours' people inside the outline count (the gain term).
    """
    ids = np.array(sorted(h3.str_to_int(c) for c in cells), dtype=np.uint64)
    entry = store.entries[iso3]
    others = [c for c in store.countries_for(ids) if c != iso3]
    inside = float(store.lookup(iso3, resolution, ids).sum())
    by_other = {c: float(p.sum()) for c, p in store.by_country(ids, resolution, others).items()}
    tagged_res = store.total(iso3, resolution)
    facts = Facts(
        tagged_at_res=tagged_res,
        tagged_at_base=store.total(iso3, SOURCES[source].base_resolution),
        base_resolution=SOURCES[source].base_resolution,
        inside=inside,
        outside=tagged_res - inside,
        by_other=by_other,
        catalogue_total=float(entry["total"]),
        problems=store.check(iso3),
    )
    return inside + sum(by_other.values()), facts


def _pct(part: float, whole: float) -> str:
    return f"{part / whole * 100:+.3f}%" if whole else "n/a"


def report(
    iso3: str, source: str, resolution: int, cells: int, total: float, db: Facts,
    expect: float | None,
) -> bool:  # fmt: skip
    ok = True
    t = Table(title=f"{iso3} · {source} · res {resolution}", show_header=False)
    t.add_column("check")
    t.add_column("value", justify="right")
    t.add_column("verdict")

    t.add_row("cells covering the outline", f"{cells:,}", "")
    t.add_section()
    t.add_row("polygon total", f"{total:,.0f}", "")
    t.add_row(f"  tagged {iso3}", f"{db.inside:,.0f}", "")
    gain = sum(db.by_other.values())
    top = ", ".join(
        f"{c} {p:,.0f}" for c, p in sorted(db.by_other.items(), key=lambda kv: -kv[1])[:5]
    )
    t.add_row("  tagged elsewhere (gain)", f"{gain:,.0f}", top)

    t.add_section()
    t.add_row(f"{iso3} tagged total (res {resolution})", f"{db.tagged_at_res:,.0f}", "")
    t.add_row("  inside the outline", f"{db.inside:,.0f}", "")
    t.add_row("  outside it (loss)", f"{db.outside:,.0f}", _pct(-db.outside, db.tagged_at_res))

    # The identity that makes this exact rather than approximate.
    identity = db.tagged_at_res - db.outside + gain
    drift = abs(identity - total)
    good = drift <= max(FLOAT_TOL, abs(total) * FLOAT_TOL)
    ok &= good
    t.add_section()
    t.add_row(
        "tagged - loss + gain == polygon",
        f"{identity:,.2f} vs {total:,.2f}",
        "[green]OK[/green]" if good else f"[red]off by {drift:,.2f}[/red]",
    )
    net = total - db.tagged_at_res
    t.add_row("net border error", f"{net:+,.0f}", _pct(net, db.tagged_at_res))

    # Rolling up never invents or loses people, so every level must carry the same total.
    conserved = abs(db.tagged_at_base - db.tagged_at_res) <= max(
        db.tol_abs, abs(db.tagged_at_base) * db.tol_rel
    )
    ok &= conserved
    t.add_section()
    t.add_row(
        f"pyramid res {db.base_resolution} == res {resolution}",
        f"{db.tagged_at_base:,.1f} vs {db.tagged_at_res:,.1f}",
        "[green]OK[/green]" if conserved else "[red]NOT CONSERVED[/red]",
    )
    same = abs(db.catalogue_total - db.tagged_at_base) <= max(
        db.tol_abs, abs(db.tagged_at_base) * 1e-6
    )
    ok &= same
    t.add_row(
        "catalogue total",
        f"{db.catalogue_total:,.0f}",
        "[green]OK[/green]" if same else "[red]disagrees with the cells[/red]",
    )
    if expect is not None:
        t.add_row(
            "reference (--expect)", f"{expect:,.0f}", _pct(db.tagged_at_base - expect, expect)
        )

    t.add_section()
    t.add_row(
        "pack files, footprints, part totals",
        "",
        "[green]OK[/green]" if not db.problems else f"[red]{len(db.problems)} problem(s)[/red]",
    )
    ok &= not db.problems

    console.print(t)
    for p in db.problems:
        console.print(f"[red]pack: {p}[/red]")
    return ok


@app.command()
def main(
    country: Annotated[str, typer.Option(help="ISO3 code, e.g. CMR")],
    source: Annotated[
        str | None, typer.Option(help="Data source key; default the pack's own")
    ] = None,
    resolution: Annotated[
        int | None, typer.Option(help="H3 resolution; default base-2, a good accuracy/cost trade")
    ] = None,
    packs: Annotated[Path | None, typer.Option(help="Pack directory (default data/packs)")] = None,
    expect: Annotated[
        float | None, typer.Option(help="Published population to compare against")
    ] = None,
) -> None:
    from packstore import PackStore

    iso3 = country.upper()
    store = PackStore(packs) if packs else PackStore()
    if iso3 not in store.entries:
        raise typer.BadParameter(f"no pack for {iso3}; build it with scripts/build_packs.py")
    source = source or store.entries[iso3]["source"]
    if source not in SOURCES:
        raise typer.BadParameter(f"unknown source {source!r}; have {list(SOURCES)}")
    base = SOURCES[source].base_resolution
    # Two levels below base keeps the polyfill quick while the border strip stays a
    # fraction of a percent of the area.
    res = resolution if resolution is not None else max(MIN_RESOLUTION, base - 2)
    if not MIN_RESOLUTION <= res <= base:
        raise typer.BadParameter(f"resolution must be in [{MIN_RESOLUTION}, {base}]")

    geom = load_outline(iso3)
    minx, _, maxx, _ = geom.bounds
    if maxx - minx > 180:
        raise typer.BadParameter(
            f"{iso3} spans {maxx - minx:.0f} deg of longitude; it likely crosses the "
            "antimeridian, which this check cannot handle"
        )
    console.print(f"{iso3}: outline {geom.geom_type}, bounds {[round(b, 3) for b in geom.bounds]}")
    console.print(f"covering at res {res}...")
    cells = set(h3.geo_to_cells(geom, res))
    total, facts = query_packs(store, iso3, source, res, cells)
    if not report(iso3, source, res, len(cells), total, facts, expect):
        raise typer.Exit(code=1)
    console.print("[green]all checks passed[/green]")


if __name__ == "__main__":
    app()
