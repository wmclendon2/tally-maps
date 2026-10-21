# Copyright (c) 2026 Woody McLendon
# SPDX-License-Identifier: MIT
"""Check that a tab left open survives a release (F-31), in headless Chrome.

    uv run --group browser python scripts/browser_update.py [--dist dist]

Serves a copy of a built site (scripts/build_static.py) on a free local port, opens it with
its service worker, then changes the served copy under the open page, as a deploy does:

  1. First visit: no "new version" notice.
  2. A release with no new data (the shell version bumped): coming back to the tab shows
     the notice.
  3. A rebuilt country (its pack files renamed in a new catalogue, the old ones gone): the
     open page still gets the country's exact total, with no error, and the notice.
  4. A pack gone with nothing new in the catalogue: the error says so in words, with no
     HTTP status in it.
  5. Reload from the notice: the same circle and count come back, and no notice.

Exits 1 on any failure. The copy's packs are links to --dist's, so it costs no disk.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from browser_parity import chrome, serve

from ingest.config import ROOT

console = Console()
TIMEOUT_MS = 120_000
# The map is up, with the layers a count draws on.
MAP_READY = (
    "typeof map !== 'undefined' && map.getSource('population-cells') && map.getSource('radius')"
)
# A count is on the panel and nothing is still running.
COUNTED = (
    "!document.getElementById('result').hidden && "
    "!/^(Querying|Downloading)/.test(document.getElementById('status').textContent)"
)


def copy_site(dist: Path, to: Path) -> None:
    """dist/ copied, its packs linked rather than copied."""
    shutil.copytree(dist, to, ignore=shutil.ignore_patterns("*.tlyp.gz"), dirs_exist_ok=True)
    for f in (dist / "packs").glob("*.tlyp.gz"):
        (to / "packs" / f.name).symlink_to(f.resolve())


def bump_shell(site: Path) -> int:
    """A release with no new data: the shell version up by one, as a deploy would ship it."""
    sw = (site / "sw.js").read_text()
    old = int(re.search(r'const SHELL_VERSION = "(\d+)";', sw).group(1))
    (site / "sw.js").write_text(
        sw.replace(f'SHELL_VERSION = "{old}"', f'SHELL_VERSION = "{old + 1}"')
    )
    page = (site / "index.html").read_text()
    (site / "index.html").write_text(page.replace(f"?v={old}", f"?v={old + 1}"))
    return old + 1


def rename_packs(site: Path, iso3: str) -> None:
    """A rebuilt country: new file names in a new catalogue, the old files gone."""
    path = site / "packs" / "catalog.json"
    catalog = json.loads(path.read_text())
    entry = next(c for c in catalog["countries"] if c["iso3"] == iso3)
    for i, part in enumerate(entry["parts"]):
        new = f"{iso3}.f31renamed{i}.tlyp.gz"
        (site / "packs" / part["url"]).rename(site / "packs" / new)
        part["url"] = new
    path.write_text(json.dumps(catalog, separators=(",", ":")))


def pick(dist: Path) -> list[dict]:
    """Three small countries with people: one to count around, one to rebuild, one to lose."""
    catalog = json.loads((dist / "packs" / "catalog.json").read_text())
    small = sorted(
        (c for c in catalog["countries"] if c["total"] > 100_000 and len(c["parts"]) == 1),
        key=lambda c: c["bytes"],
    )
    if len(small) < 3:
        raise typer.BadParameter(f"{dist} needs three one-part countries; build more")
    return small[:3]


def run(site: Path, url: str, countries: list[dict]) -> list[str]:
    from playwright.sync_api import sync_playwright

    around, rebuilt, lost = countries
    failures: list[str] = []

    def check(ok: bool, what: str) -> None:
        console.print(("[green]ok[/green] " if ok else "[red]FAILED[/red] ") + what)
        if not ok:
            failures.append(what)

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=chrome(), headless=True)
        page = browser.new_context().new_page()  # with the service worker
        page.set_default_timeout(TIMEOUT_MS)
        # A hosted copy may carry an analytics beacon; never count a check as a visit.
        page.route("**/static.cloudflareinsights.com/**", lambda route: route.abort())
        page.route("**/cdn-cgi/rum*", lambda route: route.abort())
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        notice = page.locator("#update")

        page.goto(url)
        page.wait_for_function(MAP_READY)
        page.evaluate(
            """async () => {
                await navigator.serviceWorker.ready;
                while (!navigator.serviceWorker.controller) await new Promise((r) => setTimeout(r, 50));
            }"""
        )
        # A count to keep across the reload: around the busiest hexagon of a small country.
        page.evaluate(
            """async (iso3) => {
                const r = await TallyStatic.country(iso3, 0);
                const top = r.cells.reduce((a, b) => (b.population > a.population ? b : a));
                const [lat, lng] = h3.cellToLatLng(top.h3);
                radiusCenter = [lng, lat];
                queryRadius();
            }""",
            around["iso3"],
        )
        page.wait_for_function(COUNTED)
        count = page.locator("#total").text_content()
        centre = page.evaluate("radiusCenter")
        page.wait_for_timeout(1000)
        check(notice.is_hidden(), "1. first visit: no notice")

        version = bump_shell(site)
        page.evaluate("lastUpdateCheck = 0; document.dispatchEvent(new Event('visibilitychange'))")
        notice.wait_for(state="visible")
        check(
            page.locator("#update-text").text_content() == "A new version of Tally is available.",
            f"2. a release with no new data (v{version}): the notice on coming back to the tab",
        )

        page.evaluate("document.getElementById('update').hidden = true")
        rename_packs(site, rebuilt["iso3"])
        got = page.evaluate(
            """async (iso3) => {
                try {
                    const r = await TallyStatic.country(iso3, 0);
                    return {total: r.total, missing: r.missing};
                } catch (e) { return {error: e.message}; }
            }""",
            rebuilt["iso3"],
        )
        check(
            got.get("total") == rebuilt["total"] and not got.get("missing"),
            f"3. {rebuilt['name']} rebuilt under the page: {got}, expected {rebuilt['total']:,}",
        )
        check(notice.is_visible(), "3. ... and the notice")

        (site / "packs" / lost["parts"][0]["url"]).unlink()
        got = page.evaluate(
            """async (iso3) => {
                try { await TallyStatic.country(iso3, 0); return {error: null}; }
                catch (e) { return {error: e.message, name: e.name}; }
            }""",
            lost["iso3"],
        )
        message = got.get("error") or ""
        check(
            got.get("name") == "DataUpdatedError" and message and "HTTP" not in message,
            f"4. {lost['name']}'s pack gone: {message!r}",
        )

        with page.expect_navigation():
            page.locator("#update-reload").click()
        page.wait_for_function(MAP_READY)
        page.wait_for_function(COUNTED)
        check(
            page.locator("#total").text_content() == count
            and page.evaluate("radiusCenter") == centre,
            f"5. reload keeps the circle and its count ({count})",
        )
        page.wait_for_timeout(1000)
        check(notice.is_hidden(), "5. ... and shows no notice on the new version")
        browser.close()
    if errors:
        failures.append(f"page errors: {errors}")
    return failures


def main(
    dist: Annotated[Path, typer.Option(help="Built static site to test")] = ROOT / "dist",
) -> None:
    if not (dist / "packs" / "catalog.json").exists():
        raise typer.BadParameter(f"{dist} has no packs/catalog.json; run build_static.py")
    countries = pick(dist)
    site = Path(tempfile.mkdtemp(prefix="tally-update-"))
    try:
        copy_site(dist, site)
        server, url = serve(site)
        try:
            failures = run(site, url, countries)
        finally:
            server.shutdown()
    finally:
        shutil.rmtree(site)
    if failures:
        console.print(f"[red]{len(failures)} check(s) failed[/red]")
        raise typer.Exit(code=1)
    console.print("[green]a tab left open survives a release[/green]")


if __name__ == "__main__":
    typer.run(main)
