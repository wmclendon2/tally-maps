# Copyright (c) 2026 Woody McLendon
# SPDX-License-Identifier: MIT
"""Draw a world map of which countries the project can load population data for.

    uv run python scripts/coverage_map.py

Writes docs/img/coverage-map.png, which is committed and shown in the README, so nobody
needs to run this to see the answer -- only to refresh it after building more countries.
docs/img/coverage.json records the same state as data.

Two layers, because "available" and "loaded" are different questions:

  available  `scripts/build_packs.py --country XXX` would work: the country has a
             general-population GeoTIFF in Meta's HDX catalogue (read live from HDX),
             or build_packs.SOURCE_FOR builds it from WorldPop.
  loaded     the country has a built pack in data/packs/catalog.json; --loaded takes
             an explicit list instead.

Country outlines come from Natural Earth 1:50m (public domain), cached under data/.
Drawn with Pillow on an equirectangular projection -- no mapping stack required, and
the output is a flat PNG rather than something that needs a renderer to read.
"""

from __future__ import annotations

import json
import sys
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ingest import continents
from ingest.config import DATA_DIR
from ingest.fetch import get_bytes

console = Console()
app = typer.Typer(add_completion=False)

NE_URL = (
    "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/"
    "geojson/ne_50m_admin_0_countries.geojson"
)
NE_CACHE = DATA_DIR / "natural_earth" / "ne_50m_admin_0_countries.geojson"

# Equirectangular bounds. Antarctica is dropped: it has no population raster and
# would waste a third of the canvas.
LAT_MAX, LAT_MIN = 84.0, -60.0

BACKGROUND = "#ffffff"
OCEAN = "#eef1f4"
NO_DATA = "#d8d8d4"
AVAILABLE = "#dcc0e0"  # the app's palest ramp step
LOADED = "#980043"  # and its darkest
BORDER = "#ffffff"
INK = "#1b1b1f"
MUTED = "#6a6a72"
LIST_INK = "#2d2d34"  # the absent-country roll: readable, not quite black


@dataclass(frozen=True)
class Country:
    iso3: str
    name: str
    polygons: tuple[tuple[list, list], ...]  # (exterior ring, [hole rings]) per part


def fetch_world(path: Path = NE_CACHE) -> list[Country]:
    """Natural Earth country outlines, downloaded once and cached under data/."""
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        console.print(f"downloading Natural Earth outlines -> {path}")
        # Via ingest.fetch, not urllib: filtering proxies truncate HTTP/1.1 bodies a few
        # kB short without erroring, which this 3 MB file hits reliably (CLAUDE.md).
        data = get_bytes(NE_URL, timeout=120)
        json.loads(data)  # fail before caching a truncated file
        path.write_bytes(data)
    doc = json.loads(path.read_text())
    out = []
    for f in doc["features"]:
        props, geom = f["properties"], f.get("geometry")
        iso3 = (props.get("ADM0_A3") or "").upper()
        if not geom or len(iso3) != 3:
            continue
        parts = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
        rings = tuple((p[0], list(p[1:])) for p in parts if p)
        out.append(Country(iso3, props.get("NAME") or iso3, rings))
    return out


def project(lon: float, lat: float, width: int, height: int) -> tuple[float, float]:
    x = (lon + 180.0) / 360.0 * width
    y = (LAT_MAX - lat) / (LAT_MAX - LAT_MIN) * height
    return x, y


def catalog_countries() -> set[str]:
    from ingest.hdx import candidates

    return set(candidates())


def worldpop_countries() -> set[str]:
    """Countries build_packs takes from WorldPop: Meta lacks them, or its file is unusable."""
    from build_packs import SOURCE_FOR

    return {iso3 for iso3, source in SOURCE_FOR.items() if source == "worldpop_100m"}


def packed_countries(root: Path | None = None) -> tuple[set[str], str]:
    """Countries with a built pack, from the pack catalogue. Whatever the pack's source:
    the app serves them all (Jersey's is WorldPop's, since Meta's file lacks the island)."""
    from build_packs import PACK_CACHE

    path = (root or PACK_CACHE) / "catalog.json"
    if not path.exists():
        raise typer.BadParameter(f"no {path}; build packs first, or pass --loaded")
    entries = json.loads(path.read_text())["countries"]
    return {e["iso3"] for e in entries}, str(path)


def wrap(draw, text: str, font, max_width: float) -> list[str]:
    """Greedy word wrap against real glyph widths, since PIL will not do it for us."""
    words, lines, line = text.split(), [], ""
    for w in words:
        trial = f"{line} {w}".strip()
        if line and draw.textlength(trial, font=font) > max_width:
            lines.append(line)
            line = w
        else:
            line = trial
    if line:
        lines.append(line)
    return lines


# Pillow ships no TrueType font, and its built-in bitmap font is ASCII only -- which
# turns Curacao, Aland and St-Barthelemy into empty boxes. Try the usual system faces
# first and fall back to transliteration rather than printing tofu.
FONT_CANDIDATES = (
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/System/Library/Fonts/Geneva.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "C:/Windows/Fonts/arial.ttf",
)


@lru_cache(maxsize=1)
def _font_file() -> str | None:
    return next((p for p in FONT_CANDIDATES if Path(p).exists()), None)


def ascii_only(text: str) -> str:
    """Strip accents so names stay readable on the bitmap fallback font."""
    stripped = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return stripped or text


def legible(text: str) -> str:
    return text if _font_file() else ascii_only(text)


@lru_cache(maxsize=16)
def _font(size: int):
    from PIL import ImageFont

    path = _font_file()
    if path:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass
    try:  # Pillow >= 10.1 can scale its built-in font
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def render(
    world: list[Country],
    available: set[str],
    loaded: set[str],
    *,
    width: int,
    show_loaded: bool,
    subtitle: str,
    absent_countries: list[str],
    absent_territories: list[str],
):
    from PIL import Image, ImageDraw

    scale = 2  # draw big, shrink down: Pillow's polygons have no antialiasing
    height = int(width * (LAT_MAX - LAT_MIN) / 360.0)
    margin_top = int(width * 0.075)
    # Room for the legend, the roll of countries with no data, and the attribution.
    margin_bottom = int(width * (0.060 + 0.022 * max(1, (len(absent_countries) // 14) + 1)))
    W, H = width * scale, height * scale
    img = Image.new("RGB", (W, H + (margin_top + margin_bottom) * scale), BACKGROUND)
    top = margin_top * scale
    # The world is drawn on its own canvas and pasted in, so anything reaching past the
    # projection bounds (Antarctica, mostly) is clipped instead of smearing over the legend.
    sheet = Image.new("RGB", (W, H), OCEAN)
    draw = ImageDraw.Draw(sheet)

    for c in world:
        if show_loaded and c.iso3 in loaded:
            fill = LOADED
        elif c.iso3 in available:
            fill = AVAILABLE
        else:
            fill = NO_DATA
        for exterior, holes in c.polygons:
            pts = [project(x, y, W, H) for x, y, *_ in exterior]
            if len(pts) >= 3:
                draw.polygon(pts, fill=fill, outline=BORDER)
            for hole in holes:  # Lesotho inside South Africa, etc.
                hp = [project(x, y, W, H) for x, y, *_ in hole]
                if len(hp) >= 3:
                    draw.polygon(hp, fill=OCEAN, outline=BORDER)
    img.paste(sheet, (0, top))
    draw = ImageDraw.Draw(img)

    title_f, body_f, small_f = _font(34 * scale), _font(19 * scale), _font(15 * scale)
    # The roll of countries with no data is the part people actually read, so it gets
    # its own larger size and a darker ink than the attribution underneath it.
    list_f, head_f = _font(23 * scale), _font(22 * scale)
    pad = 26 * scale
    draw.text((pad, 20 * scale), "Population data coverage", font=title_f, fill=INK)
    draw.text((pad, 62 * scale), subtitle, font=body_f, fill=MUTED)

    y = top + H + 20 * scale
    swatch = 17 * scale
    legend = (
        [(LOADED, f"Currently available in this app instance ({len(loaded)})")]
        if show_loaded
        else []
    )
    legend += [
        (AVAILABLE, f"Available to load ({len(available - (loaded if show_loaded else set()))})"),
        (NO_DATA, "Not available yet"),
    ]
    x = pad
    for colour, label in legend:
        draw.rectangle([x, y, x + swatch, y + swatch], fill=colour, outline="#00000022")
        draw.text((x + swatch + 9 * scale, y + 1 * scale), label, font=body_f, fill=INK)
        x += swatch + 14 * scale + int(draw.textlength(label, font=body_f)) + 34 * scale
    y += 34 * scale
    if absent_countries:
        draw.text(
            (pad, y),
            f"No Meta data, not yet built from WorldPop ({len(absent_countries)} countries)",
            font=head_f,
            fill=INK,
        )
        y += 32 * scale
        joined = legible(", ".join(absent_countries))
        for line in wrap(draw, joined, list_f, W - 2 * pad):
            draw.text((pad, y), line, font=list_f, fill=LIST_INK)
            y += 30 * scale
        y += 4 * scale
    if absent_territories:
        for line in wrap(
            draw,
            legible(
                # Not named: the map shows them in grey, and the roll was mostly specks.
                f"Also absent: {len(absent_territories)} dependencies and disputed "
                "territories, shown in grey."
            ),
            small_f,
            W - 2 * pad,
        ):
            draw.text((pad, y), line, font=small_f, fill=MUTED)
            y += 21 * scale
    draw.text(
        (pad, y + 9 * scale),
        "Population: Meta Data for Good High Resolution Population Density (CC BY 4.0) via HDX; "
        "WorldPop (CC BY 4.0).  Outlines: Natural Earth (public domain).  Antarctica omitted.",
        font=small_f,
        fill=MUTED,
    )
    return img.resize((width, img.height // scale), Image.LANCZOS)


@app.command()
def main(
    out: Annotated[Path, typer.Option(help="PNG to write")] = Path("docs/img/coverage-map.png"),
    width: Annotated[int, typer.Option(help="Output width in pixels")] = 2000,
    loaded: Annotated[
        str | None, typer.Option(help="Comma-separated ISO3 list, instead of the catalogue")
    ] = None,
    show_loaded: Annotated[
        bool, typer.Option(help="Shade countries that have a built pack")
    ] = True,
) -> None:
    """Render docs/img/coverage-map.png: what the project can load, and what it has."""
    meta = catalog_countries()
    worldpop = worldpop_countries() - meta
    available = meta | worldpop
    console.print(f"{len(meta)} countries in the Meta HDX catalogue, {len(worldpop)} from WorldPop")

    if loaded is not None:
        have, origin = {c.strip().upper() for c in loaded.split(",") if c.strip()}, "--loaded"
    elif show_loaded:
        have, origin = packed_countries()
    else:
        have, origin = set(), "not shown"
    if show_loaded:
        console.print(f"{len(have)} loaded (from {origin})")
    write(out, available, have, width=width, show_loaded=show_loaded, worldpop=worldpop)


def write(
    out: Path,
    available: set[str],
    have: set[str],
    *,
    width: int = 2000,
    show_loaded: bool = True,
    worldpop: set[str] = frozenset(),
) -> None:
    """Draw the PNG and its coverage.json. scripts/build_static.py calls this directly to
    give the demo its own copy, with only the demo's countries shaded as loaded."""
    world = fetch_world()
    console.print(f"{len(world)} country outlines")

    drawn = {c.iso3 for c in world}
    for label, group in (("available", available), ("loaded", have)):
        missing = sorted(group - drawn)
        if missing:  # e.g. Natural Earth files Mayotte and Reunion under France
            console.print(f"[yellow]{label}: no outline for {' '.join(missing)}[/yellow]")

    # Everything Natural Earth knows about, minus what can be built: the roll printed
    # under the map. Territories are listed apart so the count means "countries".
    known = set(continents.by_continent_members())
    absent_countries, absent_territories = continents.split_by_kind(known - available)
    console.print(
        f"{len(absent_countries)} countries and {len(absent_territories)} territories "
        "have no Meta raster and are not built from WorldPop"
    )

    subtitle = f"{len(available) - len(worldpop)} countries available from Meta HRSL via HDX"
    if worldpop:
        subtitle += f", {len(worldpop)} from WorldPop"
    if show_loaded:
        subtitle += f"  ·  {len(have)} currently in this app instance"
    img = render(
        world,
        available,
        have,
        width=width,
        show_loaded=show_loaded,
        subtitle=subtitle,
        absent_countries=absent_countries,
        absent_territories=absent_territories,
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, optimize=True)
    console.print(
        f"[green]wrote {out} ({out.stat().st_size / 1e3:.0f} kB, {img.width}x{img.height})[/green]"
    )

    # The same state as data, for anyone who wants the lists rather than the picture.
    meta = out.with_name("coverage.json")
    meta.write_text(
        json.dumps(
            {
                "_note": "Generated by scripts/coverage_map.py alongside the PNG.",
                "image": out.name,
                "available": sorted(available),
                "from_worldpop": sorted(worldpop),
                "loaded": sorted(have),
                "absent_countries": absent_countries,
                "absent_territories": absent_territories,
            },
            indent=1,
            ensure_ascii=False,
        )
        + "\n"
    )
    console.print(f"[green]wrote {meta}[/green]")


if __name__ == "__main__":
    app()
