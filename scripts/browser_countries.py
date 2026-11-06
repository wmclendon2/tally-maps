# Copyright (c) 2026 Woody McLendon
# SPDX-License-Identifier: MIT
"""Check the country picker in a real browser, every country, and how much memory it takes.

    uv run --group browser python scripts/browser_countries.py \\
        [--dist dist] [--country XXX ...] [--url https://...] [--max-mb 200]

Countries: for each one in the built site's catalog, headless Chrome (fresh profile, so
everything is downloaded) runs `TallyStatic.country`, the call behind the country picker.
That downloads the country's overview if it has one (F-25), else every part, checks each
against its SHA-256 and sums the level the picker shows. The answer must match the same
level summed in Python from the parts: resolution, populated cells and total, and Fine as
well for a country with an overview. Python also checks what the picker reads from the catalog
without downloading anything: every level of the packs sums to the catalog total,
`cells_by_resolution` matches the packs, an overview's levels are its parts' cell for cell, and
`area_km2` is within a loose bound of Natural Earth's area for the country (a wrong outline
shows up as a country a fraction of its size; small islands differ most, so it only warns).

Overviews: for a few countries the browser also shows that one is read in place of the parts
and gives a drawn shape the same count as the parts do, that a saved country reads its own
parts with no connection, and that with no overview and some parts it says "at least".

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
from build_packs import country_resolution, decode, merge, part_levels, unpack

from ingest.boundaries import area_km2
from ingest.config import ROOT

console = Console()
REL_TOL = 1e-9  # JS sums float32 cells in another order than numpy
# The catalog's area is the clipping outline's; Natural Earth's 50m outline is coarser
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


def expected(packs: Path, entry: dict) -> tuple[dict, list[str]]:
    """The picker's answer for one country, from its pack files, and any catalog problems."""
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
            problems.append(f"res {res} sums to {total:,.1f}, catalog says {entry['total']:,}")
    res = country_resolution(entry)
    want = {"resolution": res, "populated_cells": counts.get(res, 0), "total": sums.get(res, 0.0)}
    if overview := entry.get("overview"):
        problems += overview_problems(packs, entry, overview)
        fine = country_resolution(entry, 1)
        want["fine"] = {
            "resolution": fine,
            "populated_cells": counts.get(fine, 0),
            "total": sums.get(fine, 0.0),
        }
    return want, problems


def overview_problems(packs: Path, entry: dict, overview: dict) -> list[str]:
    """An overview must hold just what the parts hold at its levels, cell for cell."""
    raw = gzip.decompress((packs / overview["url"]).read_bytes())
    header, start = unpack(raw)
    held = decode(raw[start:], header["sections"])
    wanted = tuple(
        r for r in map(int, entry["cells_by_resolution"]) if r <= overview["max_resolution"]
    )
    parts = merge([part_levels(packs / p["url"], wanted) for p in entry["parts"]])
    if sorted(held) != sorted(parts):
        return [f"overview has levels {sorted(held)}, not {sorted(parts)}"]
    problems = []
    for res, (cells, pops) in parts.items():
        same = np.array_equal(held[res][0], cells) and np.array_equal(
            held[res][1], pops.astype("<f4")
        )
        if not same:
            problems.append(f"overview differs from the parts at res {res}")
    return problems


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


# Take files out of this browser's store of packs, as if never downloaded.
DROP_STORED = """(urls) => new Promise((resolve, reject) => {
    const open = indexedDB.open("tally-packs", 1);
    open.onerror = () => reject(open.error);
    open.onsuccess = () => {
        const tx = open.result.transaction("parts", "readwrite");
        for (const u of urls) tx.objectStore("parts").delete(u);
        tx.oncomplete = () => { open.result.close(); resolve(); };
        tx.onerror = () => reject(tx.error);
    };
})"""


SAVED_ROW = """async (c) => {
    const row = (await TallyStatic.countries()).find((x) => x.iso3 === c);
    return {overviewBytes: row.overviewBytes, savedParts: row.savedParts};
}"""


# Draw the ? panel, press Remove on the first row saying `words`; what that row said, or null.
CLICK_REMOVE = """async (words) => {
    await renderSaved();
    const row = [...document.querySelectorAll('#saved-list .saved-list li')]
        .find((li) => li.textContent.includes(words));
    const button = [...(row?.querySelectorAll('button') ?? [])].find((b) => b.textContent === 'Remove');
    if (!button) return null;
    const said = row.textContent;
    button.click();
    return said;
}"""


def box(w, s, e, n):
    return {"type": "Polygon", "coordinates": [[[w, s], [e, s], [e, n], [w, n], [w, s]]]}


def overview_checks(page, entry: dict, errors: list[str]) -> None:
    """What an overview changes for one country. Every step reloads, so nothing is in memory."""
    iso3, overview = entry["iso3"], entry["overview"]
    fetched: list[str] = []

    def note(request) -> None:
        if request.url.endswith(".tlyp.gz"):
            fetched.append(request.url.rsplit("/", 1)[1])

    def fresh(stored_away: list[str] = (), offline: bool = False) -> None:
        page.context.set_offline(False)
        page.evaluate(DROP_STORED, list(stored_away))
        page.reload()
        page.wait_for_function("typeof TallyStatic !== 'undefined'")
        page.evaluate("TallyStatic.load(document.querySelector('meta[name=tally-data]').content)")
        fetched.clear()
        page.context.set_offline(offline)

    def whole() -> dict:
        return page.evaluate(
            """async (c) => {
                const r = await TallyStatic.country(c, 0);
                return {total: r.total, cells_total: r.cells_total, missing: r.missing};
            }""",
            iso3,
        )

    def drawn(shape: dict) -> dict:
        return page.evaluate(
            """async (g) => {
                const r = await TallyStatic.query(g, 0);
                return {total: r.total, cells: r.populated_cells, resolution: r.resolution,
                        missing: r.missing};
            }""",
            shape,
        )

    page.on("request", note)
    try:
        page.evaluate("async (c) => TallyStatic.removeCountry(c)", iso3)
        fresh()
        got = whole()
        if fetched != [overview["url"]] or got["total"] != entry["total"]:
            errors.append(f"{iso3} from nothing read {fetched}, total {got['total']}")

        # Kept by that read, so the ? panel says so and Remove takes it away.
        saved = page.evaluate(SAVED_ROW, iso3)
        if not (saved["overviewBytes"] == overview["bytes"] and not saved["savedParts"]):
            errors.append(f"{iso3}'s kept overview is not listed: {saved}")
        said = page.evaluate(CLICK_REMOVE, "Overview saved")
        if not said:
            errors.append(f"the ? panel has no Remove for {iso3}'s overview")
        page.wait_for_function(
            """async (c) => (await TallyStatic.countries()).find((x) => x.iso3 === c).overviewBytes === 0""",
            arg=iso3,
        )

        # Storage that opens and then fails is only a missing shortcut: the count still comes.
        fresh()
        page.evaluate(
            """() => {
                window.realTransaction = IDBDatabase.prototype.transaction;
                IDBDatabase.prototype.transaction = () => { throw new Error("connection lost"); };
            }"""
        )
        got = whole()
        page.evaluate("() => { IDBDatabase.prototype.transaction = window.realTransaction; }")
        if got["missing"] or got["total"] != entry["total"]:
            errors.append(f"{iso3} with storage failing: {got}")

        # The catalog lists an overview the host does not serve: the parts answer, and no
        # "reload" that could never help.
        page.evaluate("async (c) => TallyStatic.removeCountry(c)", iso3)
        fresh()
        missing = f"**/{overview['url']}"
        page.route(missing, lambda route: route.fulfill(status=404))
        try:
            got = whole()
            parts = {p["url"] for p in entry["parts"]}
            if got["total"] != entry["total"] or got["missing"] or not parts <= set(fetched):
                errors.append(f"{iso3} with its overview gone: {got}, read {fetched}")
        finally:
            page.unroute(missing)
        page.evaluate("async (c) => TallyStatic.removeCountry(c)", iso3)
        fresh()

        # A box over the whole country, read from the overview, then from the parts alone.
        shape = box(*entry["bbox"])
        by_overview = drawn(shape)
        if by_overview["resolution"] > overview["max_resolution"]:
            errors.append(f"{iso3}'s box is at res {by_overview['resolution']}: pick another")
        page.evaluate("async (c) => TallyStatic.saveCountry(c)", iso3)
        fresh([overview["url"]], offline=True)
        by_parts = drawn(shape)
        if by_overview != by_parts or fetched:
            errors.append(f"{iso3} drawn: overview {by_overview}, parts {by_parts}, read {fetched}")

        got = whole()
        if got["missing"] or got["total"] != entry["total"] or fetched:
            errors.append(f"{iso3} saved, offline: {got}, read {fetched}")

        # Some parts saved and no overview, offline: it falls back on them and says "at least".
        if len(entry["parts"]) > 1:
            fresh([entry["parts"][0]["url"]], offline=True)
            got = whole()
            if got["missing"] != [entry["name"]] or not 0 < got["cells_total"] < entry["total"]:
                errors.append(f"{iso3} with a part missing, offline: {got}")
    finally:
        page.remove_listener("request", note)
        page.context.set_offline(False)
    page.evaluate("async (c) => TallyStatic.removeCountry(c)", iso3)


def run_browser(
    url: str, isos: list[str], stress: dict, fine: set[str], overview_entry: dict | None
) -> tuple[dict, list[dict]]:
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
            if iso3 in fine:
                answers[iso3]["fine"] = page.evaluate(
                    """async (iso3) => {
                        const r = await TallyStatic.country(iso3, 1);
                        return {resolution: r.resolution, populated_cells: r.populated_cells,
                                cells_total: r.cells_total, missing: r.missing};
                    }""",
                    iso3,
                )
            sample(f"country {iso3}", time.perf_counter() - t)
            if i % 25 == 0 or i == len(isos):
                console.print(f"  {i}/{len(isos)} countries, {samples[-1]['mb']:.0f} MB")
        # A continent's overview must survive the tidy-up a page load does, and answer with
        # no connection; and having read them must change no drawn shape's count.
        whole = [i for i in isos if i.startswith("continent:")]
        again = next(iter(stress.items()), None)
        if whole:
            before = None
            if again:
                before = page.evaluate(
                    "async ([g, d]) => (await TallyStatic.query(g, d)).total", list(again[1])
                )
            page.reload()
            page.wait_for_function("typeof TallyStatic !== 'undefined'")
            page.wait_for_timeout(1000)
            page.context.set_offline(True)
            for code in whole:
                r = page.evaluate(
                    "async (c) => { await TallyStatic.load("
                    "document.querySelector('meta[name=tally-data]').content);"
                    " const r = await TallyStatic.country(c, 0);"
                    " return {total: r.total, missing: r.missing}; }",
                    code,
                )
                if r["missing"] or r["total"] != answers[code]["total"]:
                    errors.append(f"{code} offline after a reload: {r}")
            page.context.set_offline(False)
            if again and before != page.evaluate(
                "async ([g, d]) => (await TallyStatic.query(g, d)).total", list(again[1])
            ):
                errors.append(f"{again[0]} changed after reading the continents")
            # The ? panel lists a continent's kept overview, and Remove takes it away.
            key = whole[0].split(":", 1)[1]
            if not page.evaluate(CLICK_REMOVE, f"All of {key}"):
                errors.append(f"the ? panel has no Remove for {whole[0]}")
            page.wait_for_function(
                "async (k) => !(await TallyStatic.savedContinents()).some((c) => c.key === k)",
                arg=key,
            )
        if overview_entry:
            overview_checks(page, overview_entry, errors)
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
    # Whole continents take the same path, from their overview packs (F-33).
    continents = {e["iso3"]: e for e in catalog.get("continents", [])}
    entries.update(continents)
    if not country:
        isos += sorted(continents)
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
            problems[iso3] = ["catalog lacks cells_by_resolution/area_km2; rebuild"]
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
    fine = {i for i in checked if "fine" in want[i]}
    # The smallest with parts to fall back on, if there is one.
    with_overview = sorted(
        (entries[i] for i in fine), key=lambda e: (len(e["parts"]) < 2, e["bytes"])
    )
    trial = with_overview[0] if with_overview else None
    if url:
        got, samples = run_browser(url, checked, stress, fine, trial)
    else:
        server, local = serve(dist)
        try:
            got, samples = run_browser(local, checked, stress, fine, trial)
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
        if "fine" in w:
            f, wf = g["fine"], w["fine"]
            same = (
                same
                and f["resolution"] == wf["resolution"]
                and f["populated_cells"] == wf["populated_cells"]
                and abs(f["cells_total"] - wf["total"]) <= max(1e-6, abs(wf["total"]) * REL_TOL)
                and not f["missing"]
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
    # A continent's people are its countries' people: added once, as countries.
    places = [i for i in checked if i not in continents]
    total = sum(entries[i]["total"] for i in places)
    also = f" and {len(checked) - len(places)} continents" if len(places) < len(checked) else ""
    console.print(
        f"[green]all {len(places)} countries{also} identical in the browser "
        f"({total:,} people); memory within {max_mb:.0f} MB[/green]"
    )


if __name__ == "__main__":
    typer.run(main)
