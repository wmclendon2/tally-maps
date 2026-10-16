# Copyright (c) 2026 Woody McLendon
# SPDX-License-Identifier: MIT
"""Check the country picker in a real browser, every country, and how much memory it takes.

    uv run --group browser python scripts/browser_countries.py \\
        [--dist dist] [--country XXX ...] [--url https://...] [--max-mb 200]

Countries: for each one in the built site's catalogue, headless Chrome (fresh profile, so
everything is downloaded) runs `TallyStatic.country`, the call behind the country picker.
That downloads every part of the country, checks each against its SHA-256 and sums the
level the picker shows. The answer must match the same level summed in Python from the
same pack files: resolution, populated cells and total. Python also checks what the
picker reads from the catalogue without downloading anything: every level of the packs
sums to the catalogue total, `cells_by_resolution` matches the packs, and `area_km2` is
within a loose bound of Natural Earth's area for the country (a wrong outline shows up as
a country a fraction of its size; small islands differ most, so it only warns).

Memory: before the countries it draws boxes over the largest countries and their biggest
cities, Normal and Fine, the queries that held hundreds of MB before the cap in static.js.
After every step it forces a garbage collection and reads Chrome's heap (JS objects plus
ArrayBuffer storage, which is where the pack tables live), and fails if that ever passes
--max-mb. It also reports static.js's own count of what its cache holds.

With --url it runs against a copy of the same build served somewhere else, still taking
the expected answers from the local --dist build. That downloads every pack (~400 MB).
"""

from __future__ import annotations

import gzip
import json
import sys
import time
from pathlib import Path
from typing import Annotated

import numpy as np
import typer
from rich.console import Console
from rich.table import Table

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from browser_parity import chrome, serve
from build_packs import decode, unpack

from ingest.boundaries import area_km2
from ingest.config import ROOT
from ingest.resolve import AUTO_TARGET_CELLS, MAX_CELLS_LIMIT

console = Console()
REL_TOL = 1e-9  # JS sums float32 cells in another order than numpy
# The catalogue's area is the clipping outline's; Natural Earth's 50m outline is coarser
# and leaves out or adds small islands, lakes and disputed land, so only a big gap means
# something is wrong. Below MIN_AREA_KM2 the coastline is most of the country: no check.
AREA_RATIO = (0.6, 1.6)
MIN_AREA_KM2 = 1000
# Boxes for the memory pass: the whole of the largest countries, then their densest
# cities, which reach the finest levels. A box runs only if its countries are in the build.
STRESS = {
    "usa": ((-125.0, 24.5, -66.9, 49.4), {"USA"}),
    "india": ((68.1, 6.7, 97.4, 35.5), {"IND"}),
    "brazil": ((-74.0, -34.0, -34.0, 5.3), {"BRA"}),
    "delhi": ((76.9, 28.4, 77.4, 28.9), {"IND"}),
    "mumbai": ((72.75, 18.88, 73.05, 19.3), {"IND"}),
    "kolkata": ((88.2, 22.4, 88.55, 22.75), {"IND"}),
    "new-york": ((-74.1, 40.55, -73.75, 40.9), {"USA"}),
    "los-angeles": ((-118.5, 33.85, -118.0, 34.2), {"USA"}),
    "sao-paulo": ((-46.83, -23.75, -46.37, -23.4), {"BRA"}),
    "lagos": ((3.2, 6.35, 3.6, 6.7), {"NGA"}),
    "dhaka": ((90.3, 23.65, 90.5, 23.9), {"BGD"}),
    "jakarta": ((106.7, -6.35, 106.95, -6.1), {"IDN"}),
}


def country_resolution(entry: dict, detail: int = 0) -> int:
    """web/static.js countryResolution: the level the picker shows a country at."""
    counts = {int(k): int(v) for k, v in entry["cells_by_resolution"].items()}
    levels = sorted(counts)
    res = levels[0]
    for r in levels:
        if counts[r] <= AUTO_TARGET_CELLS:
            res = r
    res = min(res + detail, int(entry["max_resolution"]))
    while res > levels[0] and counts[res] > MAX_CELLS_LIMIT:
        res -= 1
    return res


def expected(packs: Path, entry: dict) -> tuple[dict, list[str]]:
    """The picker's answer for one country, from its pack files, and any catalogue problems."""
    problems = []
    counts: dict[int, int] = {}
    sums: dict[int, float] = {}
    for part in entry["parts"]:
        raw = gzip.decompress((packs / part["url"]).read_bytes())
        header, start = unpack(raw)
        for res, (cells, pops) in decode(raw[start:], header["sections"]).items():
            counts[res] = counts.get(res, 0) + int(cells.size)
            sums[res] = sums.get(res, 0.0) + float(pops.astype(np.float64).sum())
    listed = {int(k): int(v) for k, v in entry["cells_by_resolution"].items()}
    if listed != counts:
        problems.append(f"cells_by_resolution {listed} but the packs hold {counts}")
    for res, total in sums.items():
        if abs(total - entry["total"]) > max(1.0, entry["total"] * 1e-6):
            problems.append(f"res {res} sums to {total:,.1f}, catalogue says {entry['total']:,}")
    res = country_resolution(entry)
    want = {"resolution": res, "populated_cells": counts.get(res, 0), "total": sums.get(res, 0.0)}
    return want, problems


def natural_earth_areas() -> dict[str, float]:
    from coverage_map import NE_CACHE, fetch_world

    fetch_world()
    out: dict[str, float] = {}
    for f in json.loads(NE_CACHE.read_text())["features"]:
        p = f["properties"]
        iso3 = (p.get("ADM0_A3") or "").upper()
        if f.get("geometry"):
            out[iso3] = out.get(iso3, 0.0) + area_km2(f["geometry"])
    return out


def box(w, s, e, n):
    return {"type": "Polygon", "coordinates": [[[w, s], [e, s], [e, n], [w, n], [w, s]]]}


def run_browser(url: str, isos: list[str], stress: dict) -> tuple[dict, list[dict]]:
    """{iso3: picker answer} and one memory sample per step."""
    from playwright.sync_api import sync_playwright

    answers: dict[str, dict] = {}
    samples: list[dict] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=chrome(), headless=True)
        page = browser.new_context(service_workers="block").new_page()
        page.set_default_timeout(600_000)
        # A hosted copy may carry an analytics beacon; never count a check as a visit.
        page.route("**/static.cloudflareinsights.com/**", lambda route: route.abort())
        page.route("**/cdn-cgi/rum*", lambda route: route.abort())
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(url)
        page.wait_for_function("typeof TallyStatic !== 'undefined'")
        cdp = page.context.new_cdp_session(page)

        def sample(step: str, seconds: float) -> None:
            cdp.send("HeapProfiler.collectGarbage")
            heap = cdp.send("Runtime.getHeapUsage")
            cache = page.evaluate("TallyStatic.memory()")
            samples.append(
                {
                    "step": step,
                    "mb": (heap["usedSize"] + heap.get("backingStorageSize", 0)) / 1e6,
                    "cache_mb": cache["bytes"] / 1e6,
                    "budget_mb": cache["budget"] / 1e6,
                    "seconds": seconds,
                }
            )

        sample("page loaded", 0.0)
        for name, (geometry, detail) in stress.items():
            t = time.perf_counter()
            r = page.evaluate(
                """async ([g, d]) => {
                    const r = await TallyStatic.query(g, d);
                    return {total: r.total, missing: r.missing, resolution: r.resolution};
                }""",
                [geometry, detail],
            )
            if r["missing"]:
                errors.append(f"{name}: could not download {r['missing']}")
            sample(f"draw {name}", time.perf_counter() - t)
        for i, iso3 in enumerate(isos, 1):
            t = time.perf_counter()
            answers[iso3] = page.evaluate(
                """async (iso3) => {
                    const r = await TallyStatic.country(iso3, 0);
                    return {resolution: r.resolution, populated_cells: r.populated_cells,
                            total: r.total, cells_total: r.cells_total, missing: r.missing};
                }""",
                iso3,
            )
            sample(f"country {iso3}", time.perf_counter() - t)
            if i % 25 == 0 or i == len(isos):
                console.print(f"  {i}/{len(isos)} countries, {samples[-1]['mb']:.0f} MB")
        browser.close()
    if errors:
        raise RuntimeError(f"page errors: {errors}")
    return answers, samples


def main(
    dist: Annotated[Path, typer.Option(help="Built static site to test")] = ROOT / "dist",
    url: Annotated[
        str | None,
        typer.Option(
            help="Test a copy of this build served at this URL, not a local copy of --dist"
        ),
    ] = None,
    country: Annotated[
        list[str] | None, typer.Option(help="ISO3 to check; repeat (default: all in --dist)")
    ] = None,
    max_mb: Annotated[
        float, typer.Option(help="Fail if the page's heap passes this after any step")
    ] = 200.0,
    no_stress: Annotated[
        bool, typer.Option("--no-stress", help="Skip the memory pass over the largest countries")
    ] = False,
) -> None:
    packs = dist / "packs"
    if not (packs / "catalog.json").exists():
        raise typer.BadParameter(f"{dist} has no packs/catalog.json; run build_static.py")
    catalog = json.loads((packs / "catalog.json").read_text())
    entries = {e["iso3"]: e for e in catalog["countries"]}
    isos = [c.upper() for c in country] if country else sorted(entries)
    unknown = set(isos) - set(entries)
    if unknown:
        raise typer.BadParameter(f"not in {dist}: {sorted(unknown)}")

    t0 = time.perf_counter()
    want, problems = {}, {}
    ne = natural_earth_areas()
    warnings = []
    for iso3 in isos:
        e = entries[iso3]
        if "cells_by_resolution" not in e or "area_km2" not in e:
            problems[iso3] = ["catalogue lacks cells_by_resolution/area_km2; rebuild"]
            continue
        want[iso3], probs = expected(packs, e)
        if probs:
            problems[iso3] = probs
        ref = ne.get(iso3)
        if (
            ref
            and ref >= MIN_AREA_KM2
            and not AREA_RATIO[0] <= e["area_km2"] / ref <= AREA_RATIO[1]
        ):
            warnings.append(f"{iso3} area {e['area_km2']:,.0f} km2, Natural Earth {ref:,.0f}")
    console.print(f"expected answers from the packs in {time.perf_counter() - t0:.0f} s")

    stress = {}
    if not no_stress:
        for name, (bounds, needs) in STRESS.items():
            if needs <= set(entries):
                stress[f"{name}"] = (box(*bounds), 0)
                stress[f"{name}-fine"] = (box(*bounds), 1)
    t0 = time.perf_counter()
    checked = [i for i in isos if i in want]
    if url:
        got, samples = run_browser(url, checked, stress)
    else:
        server, local = serve(dist)
        try:
            got, samples = run_browser(local, checked, stress)
        finally:
            server.shutdown()
    console.print(f"browser pass in {time.perf_counter() - t0:.0f} s")

    bad = []
    for iso3 in checked:
        w, g = want[iso3], got[iso3]
        same = (
            g["resolution"] == w["resolution"]
            and g["populated_cells"] == w["populated_cells"]
            and abs(g["cells_total"] - w["total"]) <= max(1e-6, abs(w["total"]) * REL_TOL)
            and g["total"] == entries[iso3]["total"]
            and not g["missing"]
        )
        if not same:
            bad.append(f"{iso3}: browser {g}, expected {w}")

    t = Table(title="Memory: Chrome heap after each step (JS + ArrayBuffers, after GC)")
    for col in ("step", "heap MB", "cache MB", "seconds"):
        t.add_column(col, justify="left" if col == "step" else "right")
    worst = max(samples, key=lambda s: s["mb"])
    shown = [s for s in samples if not s["step"].startswith("country")]
    shown += sorted(
        (s for s in samples if s["step"].startswith("country")), key=lambda s: -s["mb"]
    )[:5]
    for s in shown:
        t.add_row(s["step"], f"{s['mb']:.0f}", f"{s['cache_mb']:.0f}", f"{s['seconds']:.1f}")
    console.print(t)
    console.print(
        f"peak {worst['mb']:.0f} MB after {worst['step']} (limit {max_mb:.0f} MB; "
        f"static.js cache budget {worst['budget_mb']:.0f} MB)"
    )

    for w in warnings:
        console.print(f"[yellow]warning: {w}[/yellow]")
    for iso3, probs in problems.items():
        for p in probs:
            console.print(f"[red]{iso3}: {p}[/red]")
    for b in bad:
        console.print(f"[red]{b}[/red]")
    failed = bool(problems or bad) or worst["mb"] > max_mb
    if worst["mb"] > max_mb:
        console.print(f"[red]heap reached {worst['mb']:.0f} MB, over {max_mb:.0f} MB[/red]")
    if failed:
        raise typer.Exit(code=1)
    total = sum(entries[i]["total"] for i in checked)
    console.print(
        f"[green]all {len(checked)} countries identical in the browser "
        f"({total:,} people); memory within {max_mb:.0f} MB[/green]"
    )


if __name__ == "__main__":
    typer.run(main)
