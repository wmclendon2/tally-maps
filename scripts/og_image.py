# Copyright (c) 2026 Woody McLendon
# SPDX-License-Identifier: MIT
"""Make web/og-image.jpg, the picture Slack, WhatsApp, iMessage and others show for a link.

A 1200 x 630 card: Tally's name and question on the left, a country's real heatmap from the
app on the right with its total. Run it on a build of every country, so the neighbours are
not greyed as "no data":

    uv run python scripts/build_static.py --country ... --out /tmp/tally-all
    uv run --group browser python scripts/og_image.py --dist /tmp/tally-all

The page's link-preview tags (`og:*` in web/index.html) point at the file; keep their
size, type and alt text in step with it.
"""

from __future__ import annotations

import base64
import functools
import http.server
import io
import socketserver
import threading
from pathlib import Path
from typing import Annotated

import typer
from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
W, H, MAP_W = 1200, 630, 720  # the card, and its map side

# Frame the heatmap alone: the app pads the map for its panel, which the card hides.
FIT = """async () => {
  const data = map.getSource("population-cells").serialize().data;
  let w = 180, s = 90, e = -180, n = -90;
  for (const f of data.features) for (const ring of f.geometry.coordinates) for (const [x, y] of ring) {
    if (x < w) w = x; if (x > e) e = x; if (y < s) s = y; if (y > n) n = y;
  }
  map.setPadding({ top: 0, right: 0, bottom: 0, left: 0 });
  // Room at the bottom for the figure, so it covers no data.
  map.fitBounds([[w, s], [e, n]], { padding: { top: 16, right: 16, bottom: 56, left: 16 },
                                    animate: false });
  await new Promise((r) => { map.once("idle", r); setTimeout(r, 15000); });
  return document.getElementById("total").textContent;
}"""
HIDE = "#panel, .maplibregl-control-container, .maplibregl-popup { display: none !important; }"

CARD = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<link rel="stylesheet" href="style.css"><style>
  html, body { margin: 0; width: 1200px; height: 630px; overflow: hidden; }
  body { font-family: "Barlow", system-ui, sans-serif; -webkit-font-smoothing: antialiased; }
  .card { width: 1200px; height: 630px; display: flex; }
  .words { box-sizing: border-box; width: 480px; padding: 54px 48px 46px 56px; display: flex;
           flex-direction: column; background: #fff; color: #1d1a24; }
  .lockup { display: flex; align-items: center; gap: 14px; }
  .lockup svg { width: 58px; height: 58px; }
  .lockup span { font-size: 50px; font-weight: 600; letter-spacing: -0.5px; }
  .headline { margin: 46px 0 0; font-family: "Barlow Semi Condensed", "Barlow", sans-serif;
              font-weight: 600; font-size: 56px; line-height: 1.0; letter-spacing: -0.5px; }
  .sub { margin: 20px 0 0; font-size: 23px; line-height: 1.3; color: #6b6577; }
  .url { margin-top: auto; font-size: 27px; font-weight: 600; color: #980043; }
  .map { position: relative; width: 720px; height: 630px; background: center / cover;
         border-left: 1px solid #e2dee8; box-sizing: border-box; }
  .figure { position: absolute; left: 22px; bottom: 22px; padding: 10px 16px 11px;
            background: rgba(255,255,255,0.94); border-radius: 10px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.18); }
  .n { font-family: "Barlow Semi Condensed", "Barlow", sans-serif; font-weight: 600;
       font-size: 34px; line-height: 1; color: #980043; }
  .n small { font-family: "Barlow", sans-serif; font-size: 16px; font-weight: 500;
             color: #6b6577; margin-left: 4px; }
  .what { font-size: 17px; color: #1d1a24; margin-top: 4px; }
  .credit { position: absolute; right: 10px; bottom: 7px; font-size: 11px; color: #555;
            background: rgba(255,255,255,0.7); padding: 1px 5px; border-radius: 3px; }
</style></head><body><div class="card">
  <div class="words">
    <div class="lockup"><svg viewBox="0 0 40 40" aria-hidden="true">
      <polygon points="20,2 36,11 36,29 20,38 4,29 4,11" fill="#980043"/>
      <polygon points="20,9 30,14.5 30,25.5 20,31 10,25.5 10,14.5" fill="#df65b0"/>
      <polygon points="20,15 25,18 25,22 20,25 15,22 15,18" fill="#f5d6e8"/></svg>
      <span>Tally</span></div>
    <p class="headline">How many people live here?</p>
    <p class="sub">Draw a shape on the map and get an estimate of the people inside it, with a
      density heatmap.</p>
    <div class="url">tallymaps.org</div>
  </div>
  <div class="map" style="background-image: url('MAP_URL')">
    <div class="figure"><div class="n">MILLIONS million<small>(est.)</small></div>
      <div class="what">people in NAME</div></div>
    <div class="credit">© OpenStreetMap contributors · Meta Data for Good</div>
  </div>
</div></body></html>"""


def serve(directory: Path) -> tuple[socketserver.TCPServer, str]:
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args) -> None:
            pass

    httpd = socketserver.ThreadingTCPServer(
        ("127.0.0.1", 0), functools.partial(Quiet, directory=str(directory))
    )
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}/"


def main(
    dist: Annotated[Path, typer.Option(help="A build from build_static.py")] = ROOT / "dist",
    country: Annotated[str, typer.Option(help="ISO3 of the country to show")] = "CMR",
    out: Annotated[Path, typer.Option(help="Where the JPEG goes")] = ROOT / "web" / "og-image.jpg",
    quality: Annotated[int, typer.Option(help="JPEG quality")] = 88,
) -> None:
    iso = country.upper()
    httpd, base = serve(dist)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": MAP_W, "height": H}, device_scale_factor=2)
            page.route("**/static.cloudflareinsights.com/**", lambda route: route.abort())
            page.goto(base)
            page.wait_for_function(
                "() => document.querySelectorAll('#country-pick option').length > 1"
            )
            page.click("#tab-country")
            page.select_option("#country-pick", iso)
            page.wait_for_selector("#result:not([hidden])", timeout=120_000)
            page.add_style_tag(content=HIDE)
            page.evaluate("() => map.resize()")
            total = int(page.evaluate(FIT).replace(",", ""))
            page.wait_for_timeout(1500)  # late tiles and labels
            shot = page.screenshot()
            name = page.eval_on_selector(f"#country-pick option[value={iso}]", "o => o.textContent")

            millions = f"{total / 1e6:.1f}".removesuffix(".0")
            card = (
                CARD.replace("MAP_URL", "data:image/png;base64," + base64.b64encode(shot).decode())
                .replace("MILLIONS", millions)
                .replace("NAME", name)
            )
            page = browser.new_page(viewport={"width": W, "height": H})
            page.route(
                base + "og-card.html",
                lambda route: route.fulfill(body=card, content_type="text/html"),
            )
            page.goto(base + "og-card.html")
            page.evaluate("document.fonts.ready")
            page.wait_for_timeout(300)
            png = page.screenshot()
            browser.close()
    finally:
        httpd.shutdown()

    Image.open(io.BytesIO(png)).convert("RGB").save(out, "JPEG", quality=quality, optimize=True)
    print(
        f"{out.relative_to(ROOT) if out.is_relative_to(ROOT) else out}: {W}x{H}, "
        f"{out.stat().st_size / 1000:.0f} KB; {name}, {millions} million (est.)"
    )


if __name__ == "__main__":
    typer.run(main)
