# Copyright (c) 2026 Woody McLendon
# SPDX-License-Identifier: MIT
"""Check web/static.js against the API's resolver in a real browser, from empty storage.

    uv run --group browser python scripts/browser_parity.py [--dist dist]

Serves a built static site (scripts/build_static.py) on a free local port, opens it in
headless Chrome with a fresh profile (so every pack is downloaded, nothing is cached),
runs `TallyStatic.query` on each case in scripts/static_parity.py, and compares the
resolution, cell counts and total with the Python answer from the same packs. Exits 1 on
any mismatch. This replaces running the parity polygons by hand in the browser pane.

With --url it tests a copy of the same build served somewhere else instead, still taking
the expected answers from the local --dist build.

Uses the system Chrome or Chromium when there is one; otherwise Playwright's own build,
which `uv run --group browser playwright install chromium` downloads once.
"""

from __future__ import annotations

import functools
import shutil
import sys
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from packstore import PackStore
from static_parity import expected

from ingest.config import ROOT

console = Console()
# JS sums float32 cells in a different order from numpy, so totals agree to rounding only.
REL_TOL = 1e-9


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args) -> None:
        pass


def serve(root: Path) -> tuple[ThreadingHTTPServer, str]:
    handler = functools.partial(QuietHandler, directory=str(root))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}/"


def chrome() -> str | None:
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        if path := shutil.which(name):
            return path
    return None


def run_cases(url: str, cases: dict) -> dict:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=chrome(), headless=True)
        page = browser.new_context(service_workers="block").new_page()
        # A hosted copy may carry an analytics beacon; a check must never count as a visit.
        page.route("**/static.cloudflareinsights.com/**", lambda route: route.abort())
        page.route("**/cdn-cgi/rum*", lambda route: route.abort())
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(url)
        page.wait_for_function("typeof TallyStatic !== 'undefined'")
        got = page.evaluate(
            """async (cases) => {
                const out = {};
                for (const [name, c] of Object.entries(cases)) {
                    const r = await TallyStatic.query(c.geometry, c.detail);
                    out[name] = {resolution: r.resolution, cell_count: r.cell_count,
                                 populated_cells: r.populated_cells, total: r.total,
                                 missing: r.missing};
                }
                return out;
            }""",
            cases,
        )
        browser.close()
    if errors:
        raise RuntimeError(f"page errors: {errors}")
    return got


def main(
    dist: Annotated[Path, typer.Option(help="Built static site to test")] = ROOT / "dist",
    url: Annotated[
        str | None,
        typer.Option(
            help="Test a copy of this build served at this URL, not a local copy of --dist"
        ),
    ] = None,
) -> None:
    if not (dist / "packs" / "catalog.json").exists():
        raise typer.BadParameter(f"{dist} has no packs/catalog.json; run build_static.py")
    want = expected(PackStore(dist / "packs"))
    if url:
        got = run_cases(url, want)
    else:
        server, local = serve(dist)
        try:
            got = run_cases(local, want)
        finally:
            server.shutdown()

    where = url or "a local copy"
    t = Table(title=f"static.js vs api/resolve.py, {len(want)} cases, from empty storage, {where}")
    for col in ("case", "res", "cells", "populated", "total", ""):
        t.add_column(col, justify="left" if col == "case" else "right")
    bad = 0
    for name, w in want.items():
        g = got[name]
        same = (
            g["resolution"] == w["resolution"]
            and g["cell_count"] == w["cell_count"]
            and g["populated_cells"] == w["populated_cells"]
            and abs(g["total"] - w["total"]) <= max(1e-6, abs(w["total"]) * REL_TOL)
            and not g["missing"]
        )
        bad += not same
        t.add_row(
            name,
            str(g["resolution"]),
            f"{g['cell_count']:,}",
            f"{g['populated_cells']:,}",
            f"{g['total']:,.1f}",
            "[green]OK[/green]" if same else f"[red]expected {w}[/red]",
        )
    console.print(t)
    if bad:
        console.print(f"[red]{bad} of {len(want)} cases differ[/red]")
        raise typer.Exit(code=1)
    console.print(f"[green]all {len(want)} cases identical[/green]")


if __name__ == "__main__":
    typer.run(main)
