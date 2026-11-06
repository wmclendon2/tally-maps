# Copyright (c) 2026 Woody McLendon
# SPDX-License-Identifier: MIT
"""Real files through the File tab (F-02), in headless Chrome.

    uv run --group browser python scripts/browser_files.py [--dist DIR]

Writes each format the tab opens (GeoJSON, KML, KMZ, zipped and loose Shapefile, CSV as
Excel writes it in English and in French), opens each through the page's own file input,
and checks every row against ingest/resolve.py over the packs: the same number as drawing
that shape. Then the things a person would notice: rows that cannot be counted say why and
are never dropped, a picked row's number equals its line in the list, Normal and Fine
recount the list, Stop stops, files that cannot be read say so, a reload keeps the list, and
the saved CSV and GeoJSON hold what the list showed, and the parsers load offline. A list of 500 shapes is timed and its
memory measured. Needs CMR and FJI in the build.
"""

from __future__ import annotations

import csv
import io
import json
import math
import re
import shutil
import struct
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Annotated

import h3
import numpy as np
import typer
from browser_parity import chrome, serve
from packstore import PackStore
from rich.console import Console

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ingest.config import ROOT
from ingest.resolve import resolve

console = Console()
TALLY_500_SECONDS = 120  # a list of 500, packs on disk, must end within this
TALLY_500_MB = 400


def box(w, s, e, n):
    return [[[w, s], [e, s], [e, n], [w, n], [w, s]]]


YAOUNDE = box(11.42, 3.78, 11.62, 3.98)
DOUALA = box(9.65, 3.95, 9.8, 4.1)
GAROUA = box(13.3, 9.25, 13.5, 9.4)
TAVEUNI_JUMP = [[[179.7, -17.1], [-179.7, -17.1], [-179.7, -16.5], [179.7, -16.5], [179.7, -17.1]]]
# Yaoundé with a hole in the middle, and two pieces of one shape.
HOLED = [
    box(11.42, 3.78, 11.62, 3.98)[0],
    [[11.5, 3.86], [11.5, 3.9], [11.54, 3.9], [11.54, 3.86], [11.5, 3.86]],
]
TWO_PIECES = [DOUALA, GAROUA]
OCEAN = box(-30.0, -10.0, -29.0, -9.0)


def geojson_file() -> bytes:
    def feature(name, geometry, **props):
        return {"type": "Feature", "properties": {"nom_zone": name, **props}, "geometry": geometry}

    return json.dumps(
        {
            "type": "FeatureCollection",
            "features": [
                feature("Yaoundé", {"type": "Polygon", "coordinates": YAOUNDE}, code="=A1"),
                feature("Avec un trou", {"type": "Polygon", "coordinates": HOLED}),
                feature("Deux morceaux", {"type": "MultiPolygon", "coordinates": TWO_PIECES}),
                feature("Taveuni", {"type": "Polygon", "coordinates": TAVEUNI_JUMP}),
                feature("Océan", {"type": "Polygon", "coordinates": OCEAN}),
                feature("Une route", {"type": "LineString", "coordinates": [[11, 4], [12, 4]]}),
                feature("Rien", None),
                feature("Clinique", {"type": "Point", "coordinates": [11.5, 3.9]}, radius_km=2),
                feature("Village", {"type": "Point", "coordinates": [9.7, 4.0]}),
            ],
        }
    ).encode()


def kml_file() -> bytes:
    def coords(ring):
        return " ".join(f"{x},{y},0" for x, y in ring)

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2"><Document>
<Placemark><name>Yaoundé</name><Polygon><outerBoundaryIs><LinearRing><coordinates>
{coords(YAOUNDE[0])}</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>
<Placemark><name>With a pin</name><MultiGeometry>
<Point><coordinates>9.7,4.0,0</coordinates></Point>
<Polygon><outerBoundaryIs><LinearRing><coordinates>{coords(DOUALA[0])}</coordinates>
</LinearRing></outerBoundaryIs></Polygon></MultiGeometry></Placemark>
</Document></kml>""".encode()


def kmz_file() -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("doc.kml", kml_file())
    return out.getvalue()


def mercator(lng, lat):
    r = 6378137.0
    return r * math.radians(lng), r * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))


WEB_MERCATOR_PRJ = (
    'PROJCS["WGS_1984_Web_Mercator_Auxiliary_Sphere",GEOGCS["GCS_WGS_1984",'
    'DATUM["D_WGS_1984",SPHEROID["WGS_1984",6378137.0,298.257223563]],PRIMEM["Greenwich",0.0],'
    'UNIT["Degree",0.0174532925199433]],PROJECTION["Mercator_Auxiliary_Sphere"],'
    'PARAMETER["False_Easting",0.0],PARAMETER["False_Northing",0.0],'
    'PARAMETER["Central_Meridian",0.0],PARAMETER["Standard_Parallel_1",0.0],'
    'PARAMETER["Auxiliary_Sphere_Type",0.0],UNIT["Meter",1.0]]'
)


def shapefile(records: list[tuple[str, list]], project=lambda x, y: (x, y)) -> dict[str, bytes]:
    """A Shapefile by the spec's bytes: polygons, each one ring, and a NAME column."""
    bodies = []
    for _, ring in records:
        points = [project(x, y) for x, y in ring]
        xs, ys = zip(*points)
        bodies.append(
            struct.pack("<i4d2i", 5, min(xs), min(ys), max(xs), max(ys), 1, len(points))
            + struct.pack("<i", 0)
            + b"".join(struct.pack("<2d", *p) for p in points)
        )
    all_points = [project(x, y) for _, ring in records for x, y in ring]
    xs, ys = zip(*all_points)

    def header(words):
        return (
            struct.pack(">i", 9994)
            + b"\0" * 20
            + struct.pack(">i", words)
            + struct.pack("<2i4d4d", 1000, 5, min(xs), min(ys), max(xs), max(ys), 0, 0, 0, 0)
        )

    shp_body = b""
    shx_body = b""
    offset = 50
    for number, body in enumerate(bodies, 1):
        words = len(body) // 2
        shp_body += struct.pack(">2i", number, words) + body
        shx_body += struct.pack(">2i", offset, words)
        offset += 4 + words
    width = 30
    dbf = (
        struct.pack("<B3BIHH20x", 3, 126, 11, 5, len(records), 33 + 32 + 1, width + 1)
        + b"NAME".ljust(11, b"\0")
        + b"C"
        + b"\0" * 4
        + bytes([width, 0])
        + b"\0" * 14
        + b"\r"
        + b"".join(b" " + name.encode("latin-1").ljust(width) for name, _ in records)
        + b"\x1a"
    )
    return {
        "zones.shp": header(offset) + shp_body,
        "zones.shx": header(50 + len(bodies) * 4) + shx_body,
        "zones.dbf": dbf,
    }


def zipped(parts: dict[str, bytes]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("__MACOSX/._zones.shp", b"not a shapefile")
        for name, data in parts.items():
            z.writestr(name, data)
    return out.getvalue()


def write_files(where: Path) -> dict[str, Path]:
    records = [("Yaounde", YAOUNDE[0]), ("Douala", DOUALA[0])]
    plain = shapefile(records)
    projected = {
        **shapefile(records, mercator),
        "zones.prj": WEB_MERCATOR_PRJ.encode(),
    }
    files = {
        "zones.geojson": geojson_file(),
        "zones.kml": kml_file(),
        "zones.kmz": kmz_file(),
        "zones_shp.zip": zipped(plain),
        "projected_shp.zip": zipped(projected),
        "sites.csv": (
            b"name,latitude,longitude,radius_km,clinic_id\r\n"
            b'Clinique "A",3.9,11.5,2,+237\r\n'
            b",4.0,9.7,,B\r\n"
            b"Hors carte,95,11.5,2,C\r\n"
            b"Rayon nul,3.9,11.5,0,D\r\n"
        ),
        # As Excel saves it in French: semicolons, decimal commas, the Windows code page.
        "sites_fr.csv": ("Nom;Lat;Long;Rayon\r\nGaroua é;9,3;13,4;1,5\r\n").encode("cp1252"),
        "no_columns.csv": b"city,people\r\nYaounde,3\r\n",
        "meters.geojson": json.dumps(
            {
                "type": "FeatureCollection",
                "crs": {"type": "name", "properties": {"name": "EPSG:3857"}},
                "features": [
                    {
                        "type": "Feature",
                        "properties": {},
                        "geometry": {"type": "Polygon", "coordinates": box(1, 1, 2, 2)},
                    }
                ],
            }
        ).encode(),
        "big_numbers.geojson": json.dumps(
            {
                "type": "Feature",
                "properties": {},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": box(1270000, 430000, 1280000, 440000),
                },
            }
        ).encode(),
        "broken.geojson": b'{"type": "FeatureCollection", "features": [',
        "zones.gpkg": b"SQLite format 3\0",
        "photo.jpg": b"\xff\xd8\xff",
        "empty.geojson": b'{"type": "FeatureCollection", "features": []}',
        "too_many.csv": ("name,lat,lon\r\n" + "x,1,1\r\n" * 501).encode(),
    }
    for name, data in {**plain, **{f"loose/{k}": v for k, v in plain.items()}}.items():
        files.setdefault(name, data)
    paths = {}
    for name, data in files.items():
        path = where / name
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(data)
        paths[name] = path
    grid = []
    for row in range(25):
        for col in range(20):
            w, s = 9.5 + col * 0.25, 3.0 + row * 0.25
            grid.append(
                {
                    "type": "Feature",
                    "properties": {"name": f"Zone {row}-{col}"},
                    "geometry": {"type": "Polygon", "coordinates": box(w, s, w + 0.25, s + 0.25)},
                }
            )
    paths["grid_500.geojson"] = where / "grid_500.geojson"
    paths["grid_500.geojson"].write_text(
        json.dumps({"type": "FeatureCollection", "features": grid})
    )
    return paths


def circle(lng, lat, km):
    """app.js's circle: the same 96 corners."""
    rad = math.pi / 180
    d = km / 6371.0088
    p1 = lat * rad
    ring = []
    for i in range(96):
        b = -2 * math.pi * i / 96
        p2 = math.asin(math.sin(p1) * math.cos(d) + math.cos(p1) * math.sin(d) * math.cos(b))
        dl = math.atan2(
            math.sin(b) * math.sin(d) * math.cos(p1), math.cos(d) - math.sin(p1) * math.sin(p2)
        )
        ring.append([lng + dl / rad, p2 / rad])
    ring.append(ring[0])
    return [ring]


def reference(store: PackStore, geometry: dict, detail: int = 0) -> int:
    base = max(e["max_resolution"] for e in store.entries.values())
    r = resolve(geometry, base, detail=detail)
    ids = np.array([int(c, 16) for c in r.cells], dtype=np.uint64)
    pops = np.zeros(ids.size)
    for p in store.by_country(ids, r.resolution, store.countries_for(ids)).values():
        pops += p
    return round(float(pops.sum()))


class Check:
    def __init__(self):
        self.failed = 0

    def __call__(self, name: str, ok: bool, detail="") -> None:
        self.failed += not ok
        mark = "[green]OK[/green]" if ok else f"[red]FAILED[/red] {detail}"
        console.print(f"  {name}: {mark}")


ROWS_JS = """() => [...document.querySelectorAll('#file-rows li')].map((li) => ({
  name: li.querySelector('.file-name').textContent,
  value: li.querySelector('.file-value').textContent,
  note: li.querySelector('.file-note').textContent,
}))"""
IDLE_JS = """() => fileSet && !fileSet.running
    && document.querySelector('#file-list').hidden === false"""


def open_file(page, *paths: Path) -> list[dict]:
    page.evaluate(
        "() => { document.querySelector('#status').textContent = ''; if (fileSet) closeFile(); }"
    )
    page.set_input_files("#file-input", [str(p) for p in paths])
    page.wait_for_function(IDLE_JS, timeout=180000)
    return page.evaluate(ROWS_JS)


def refused(page, path: Path) -> str:
    page.evaluate(
        "() => { document.querySelector('#status').textContent = ''; if (fileSet) closeFile(); }"
    )
    page.set_input_files("#file-input", str(path))
    page.wait_for_function(
        "() => document.querySelector('#status.error')?.textContent", timeout=30000
    )
    return page.text_content("#status")


def people(text: str) -> int:
    return int(text.replace(",", "")) if text.replace(",", "").isdigit() else -1


SHOWN_JS = """() => ({
    total: lastResult?.total,
    cells: lastResult?.cells?.length,
    selected: fileSet?.selected,
    caption: document.querySelector('#counted').textContent,
    saveAll: !document.querySelector('#file-save').hidden,
    saveRow: !document.querySelector('#save-row').hidden,
    hint: !document.querySelector('#file-hint').hidden,
})"""
FACTS_JS = """() => fileSet.rows.map(r => ({
    name: rowName(r), kind: r.kind, center: r.center ?? null, radiusKm: r.radiusKm ?? null,
    drawn: !!r.drawn, props: r.props, total: r.result?.total ?? null,
}))"""


def count_now(page, script: str, *arg) -> int:
    page.evaluate(script, *arg)
    page.wait_for_function("() => !inflight && lastResult", timeout=120000)
    return page.evaluate("() => lastResult.total")


def save(page, button: str, path: Path) -> Path:
    with page.expect_download() as d:
        page.click(button)
    shutil.copy(d.value.path(), path)
    SAVED_AS[path] = d.value.suggested_filename
    return path


SAVED_AS: dict[Path, str] = {}


def named(path: Path, label: str) -> bool:
    """Was this file offered as tally-<label>-<date>.<ext>?"""
    return (
        re.fullmatch(rf"tally-{label}-\d{{12}}{re.escape(path.suffix)}", SAVED_AS[path]) is not None
    )


def shown_row(page, *paths: Path) -> dict:
    open_file(page, *paths)
    page.wait_for_function("() => !inflight && selectedRow() && lastResult", timeout=120000)
    return page.evaluate(SHOWN_JS)


def round_trip(page, where: Path, check: Check) -> None:
    """Save a count, open it again: what comes back is what was saved (F-41)."""
    console.print("[bold]Round trip[/bold]")
    center, km = [11.5167, 3.8667], 4.828032
    shape = {
        "type": "Polygon",
        "coordinates": [[[11.45, 3.8], [11.6, 3.8], [11.6, 3.95], [11.47, 3.97], [11.45, 3.8]]],
    }

    circle_total = count_now(
        page,
        """([c, k]) => { closeFile(); setMode('radius'); lastResult = null;
             radiusCenter = c; radiusKm = k; showRadius(); queryRadius(); }""",
        [center, km],
    )
    first_caption = page.text_content("#counted")
    circle_file = save(page, "#export-geojson", where / "circle.geojson")
    check("a circle's file says radius", named(circle_file, "radius"), SAVED_AS[circle_file])
    shown = shown_row(page, circle_file)
    row = page.evaluate(FACTS_JS)[0]
    check("a saved circle comes back a circle", row["kind"] == "circle", row)
    check("at its center and radius", row["center"] == center and row["radiusKm"] == km, row)
    check("with the same count", row["total"] == circle_total == shown["total"], row)
    check("named by its radius, not a title", row["name"].startswith("Within "), row)
    check("said as when first counted", shown["caption"] == first_caption, shown)
    check("one row: its hexagons come up unasked", shown["cells"] > 0, shown)
    check("one row: no Save all", not shown["saveAll"], shown)
    check("one row: Save stays Save", not shown["saveRow"], shown)
    check("none of the saved columns count as the file's own", row["props"] == {}, row)

    again = save(page, "#export-geojson", where / "circle_again.geojson")
    shown_row(page, again)
    back = page.evaluate(FACTS_JS)[0]
    check(
        "saved and opened twice, the same circle",
        (back["center"], back["radiusKm"], back["name"], back["total"], back["props"])
        == (center, km, row["name"], circle_total, {}),
        back,
    )

    shape_total = count_now(
        page,
        "g => { closeFile(); setMode('shape'); lastResult = null; queryPopulation(g); }",
        shape,
    )
    first_caption = page.text_content("#counted")
    shape_file = save(page, "#export-geojson", where / "drawn.geojson")
    check("a drawn shape's file says shape", named(shape_file, "shape"), SAVED_AS[shape_file])
    shown = shown_row(page, shape_file)
    row = page.evaluate(FACTS_JS)[0]
    check("a drawn shape comes back a shape", row["kind"] == "shape" and row["drawn"], row)
    check("with the same count", row["total"] == shape_total == shown["total"], row)
    check("said as when first counted", shown["caption"] == first_caption, shown)
    check("called a drawn shape", row["name"] == "Drawn shape", row)

    def last(path: Path) -> dict:
        return json.loads(path.read_text())["features"][-1]

    two = where / "two.geojson"
    features = [last(circle_file), last(shape_file)]
    two.write_text(json.dumps({"type": "FeatureCollection", "features": features}))
    open_file(page, two)
    shown = page.evaluate(SHOWN_JS)
    check("two rows: neither picked unasked", shown["selected"] is None, shown)
    check("two rows: Save all and the hint", shown["saveAll"] and shown["hint"], shown)
    page.locator("#file-rows li").nth(0).locator("button").click()
    page.wait_for_function("() => !inflight && selectedRow()", timeout=120000)
    shown = page.evaluate(SHOWN_JS)
    check("a picked row: Save this row, no hint", shown["saveRow"] and not shown["hint"], shown)
    both = page.evaluate(FACTS_JS)
    saved_all = save(page, "#file-geojson", where / "two_saved.geojson")
    check("a list's file says list", named(saved_all, "list"), SAVED_AS[saved_all])
    open_file(page, saved_all)
    check("Save all, GeoJSON: the same rows back", page.evaluate(FACTS_JS) == both)
    slug = page.evaluate('() => fileSlug("Côte d\'Ivoire / ../x")')
    check("a name is made safe for a file", slug == "cote-d-ivoire-x", slug)
    only_circle = where / "one_in_list.csv"
    open_file(page, two)
    with_csv = save(page, "#file-csv", only_circle)
    rows = {r["name"]: r for r in csv.DictReader(io.StringIO(with_csv.read_text("utf-8-sig")))}
    check("Save all, CSV: says which Tally named", rows["Drawn shape 2"]["drawn"] == "true", rows)

    whole = count_now(
        page,
        """() => { closeFile(); setMode('country'); lastResult = null;
             const pick = document.querySelector('#country-pick');
             pick.value = 'CMR'; pick.dispatchEvent(new Event('change')); }""",
    )
    country_file = save(page, "#export-geojson", where / "country.geojson")
    check(
        "a country's file names it", named(country_file, "country-cameroon"), SAVED_AS[country_file]
    )
    page.evaluate("() => { clearAll(); lastResult = null; setMode('file'); }")
    page.set_input_files("#file-input", str(country_file))
    page.wait_for_function("() => !inflight && lastResult", timeout=120000)
    back = page.evaluate(
        """() => ({ file: !!fileSet, total: lastResult.total, picked: lastCountry,
             tab: !document.querySelector('#mode-country').hidden })"""
    )
    check(
        "a saved country opens as that country",
        back == {"file": False, "total": whole, "picked": "CMR", "tab": True},
        back,
    )
    old = json.loads(country_file.read_text())
    del old["features"][-1]["properties"]["country_code"]
    old_country = where / "old_country.geojson"
    old_country.write_text(json.dumps(old))
    page.evaluate("() => { clearAll(); lastResult = null; setMode('file'); }")
    page.set_input_files("#file-input", str(old_country))
    page.wait_for_function("() => !inflight && lastResult", timeout=120000)
    check("an older file too", page.evaluate("lastCountry") == "CMR")
    rows = open_file(page, country_file, circle_file)
    check("in a list, it says where to find it", "Country tab" in rows[0]["note"], rows[0])
    check("and the list still counts the rest", people(rows[1]["value"]) == circle_total, rows[1])

    moved = last(circle_file)
    ring = moved["geometry"]["coordinates"][0]
    moved["geometry"]["coordinates"][0] = [[x + 0.05, y] for x, y in ring]
    moved_file = where / "moved.geojson"
    moved_file.write_text(json.dumps(moved))
    shown_row(page, moved_file)
    row = page.evaluate(FACTS_JS)[0]
    check("an outline moved in a map tool stays a shape", row["center"] is None, row)
    check("and counts where it now is", row["total"] != circle_total, row)

    old = last(circle_file)
    for key in ("latitude", "longitude", "radius_km", "drawn"):
        del old["properties"][key]
    old["properties"]["name"] = "Population within 5 km of 3.867°N, 11.517°E"
    old_circle = where / "old_circle.geojson"
    old_circle.write_text(json.dumps(old))
    shown = shown_row(page, old_circle)
    row = page.evaluate(FACTS_JS)[0]
    check("an older file: its circle stays a shape", row["center"] is None, row)
    check("an older file: its count too", row["total"] == circle_total, row)
    check(
        "an older file: no doubled words",
        shown["caption"].endswith("within 5 km of 3.867°N, 11.517°E"),
        shown,
    )

    old = last(shape_file)
    del old["properties"]["drawn"]
    old["properties"]["name"] = "Population inside a drawn shape"
    old_shape = where / "old_shape.geojson"
    old_shape.write_text(json.dumps(old))
    shown = shown_row(page, old_shape)
    row = page.evaluate(FACTS_JS)[0]
    check("an older file: called a drawn shape", row["name"] == "Drawn shape", row)
    check("an older file: no doubled words", "Population" not in shown["caption"], shown)


def several_files(page, where: Path, paths: dict[str, Path], check: Check) -> None:
    """Everything picked makes one list, or the file that stopped it is named."""
    console.print("[bold]Several files at once[/bold]")
    circle_file, drawn_file = where / "circle.geojson", where / "drawn.geojson"
    alone = []
    for path in (circle_file, drawn_file):
        shown_row(page, path)
        alone.append(page.evaluate(FACTS_JS)[0])
    open_file(page, circle_file, drawn_file)
    together = page.evaluate(FACTS_JS)
    got = [{k: v for k, v in r.items() if k != "name"} for r in together]
    wanted = [{k: v for k, v in r.items() if k != "name"} for r in alone]
    check("two saved files: one list, in order", got == wanted, together)
    check(
        "a drawn shape in a list has its row number",
        together[1]["name"] == "Drawn shape 2",
        together,
    )
    title = page.text_content("#file-name")
    check("named for both", title == "circle.geojson, drawn.geojson", title)
    shown = page.evaluate(SHOWN_JS)
    check("two rows: neither picked unasked", shown["selected"] is None, shown)
    check("two rows: Save all and the hint", shown["saveAll"] and shown["hint"], shown)

    second = where / "drawn_second.geojson"
    shutil.copy(drawn_file, second)
    open_file(page, drawn_file, second)
    names = [r["name"] for r in page.evaluate(FACTS_JS)]
    check("two drawn shapes can be told apart", names == ["Drawn shape 1", "Drawn shape 2"], names)
    saved_again = save(page, "#file-geojson", where / "both_drawn.geojson")
    open_file(page, saved_again)
    names = [r["name"] for r in page.evaluate(FACTS_JS)]
    check("and keep their numbers once saved", names == ["Drawn shape 1", "Drawn shape 2"], names)
    shown_row(page, drawn_file)
    check("alone, no number", page.evaluate(FACTS_JS)[0]["name"] == "Drawn shape")

    point = where / "point.csv"
    point.write_text("latitude,longitude,radius_km\n3.8667,11.5167,5\n")
    unnamed = where / "unnamed.geojson"
    box_shape = {
        "type": "Polygon",
        "coordinates": [[[11.45, 3.8], [11.6, 3.8], [11.6, 3.95], [11.45, 3.95], [11.45, 3.8]]],
    }
    unnamed.write_text(json.dumps(box_shape))
    mixed = open_file(page, unnamed, point, paths["loose/zones.dbf"], paths["loose/zones.shp"])
    names = [r["name"] for r in mixed]
    check(
        "any formats together, a Shapefile's parts as one, numbered through",
        names == ["Shape 1", "Point 2", "Yaounde", "Douala"],
        names,
    )

    def stopped(*files: Path) -> str:
        page.evaluate(
            "() => { document.querySelector('#status').textContent = ''; if (fileSet) closeFile(); }"
        )
        page.set_input_files("#file-input", [str(f) for f in files])
        page.wait_for_function(
            "() => document.querySelector('#status.error')?.textContent", timeout=30000
        )
        return page.text_content("#status")

    bad = where / "bad.geojson"
    bad.write_text("not json")
    said = stopped(unnamed, bad)
    check("a file that cannot be read is named", "bad.geojson" in said, said)
    check("and no list is left to look complete", page.evaluate("() => !fileSet"), said)
    said = stopped(unnamed, paths["loose/zones.dbf"])
    check("a Shapefile part with no .shp is named", "zones.dbf" in said, said)
    said = stopped(paths["grid_500.geojson"], unnamed)
    check("the row limit covers them together", "These files have 501 rows" in said, said)


def run(url: str, paths: dict[str, Path], store: PackStore, check: Check) -> None:
    from playwright.sync_api import sync_playwright

    def poly(c):
        return {"type": "Polygon", "coordinates": c}

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=chrome(), headless=True)
        context = browser.new_context(
            service_workers="block",
            accept_downloads=True,
            locale="en-US",
            viewport={"width": 1280, "height": 800},
        )
        page = context.new_page()
        page.route("**/static.cloudflareinsights.com/**", lambda route: route.abort())
        page.route("**/cdn-cgi/rum*", lambda route: route.abort())
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(url)
        page.wait_for_function("typeof TallyFiles !== 'undefined' && map.getLayer('file-fill')")
        page.wait_for_function("typeof map !== 'undefined' && map.loaded()", timeout=30000)
        page.click("#tab-file")

        console.print("[bold]GeoJSON[/bold]")
        rows = open_file(page, paths["zones.geojson"])
        names = [r["name"] for r in rows]
        check(
            "every feature is a row, named from its own column",
            names
            == [
                "Yaoundé",
                "Avec un trou",
                "Deux morceaux",
                "Taveuni",
                "Océan",
                "Une route",
                "Rien",
                "Clinique",
                "Village",
            ],
            names,
        )
        want = {
            "Yaoundé": poly(YAOUNDE),
            "Avec un trou": poly(HOLED),
            "Deux morceaux": {"type": "MultiPolygon", "coordinates": TWO_PIECES},
            "Taveuni": poly(TAVEUNI_JUMP),
            "Clinique": poly(circle(11.5, 3.9, 2)),
            "Village": poly(circle(9.7, 4.0, 5)),
        }
        for r in rows:
            if r["name"] in want:
                w = reference(store, want[r["name"]])
                check(f"{r['name']} = the reference, {w:,}", people(r["value"]) == w, r)
        by = {r["name"]: r for r in rows}
        check("open ocean says no data", by["Océan"]["value"] == "No data here", by["Océan"])
        check("a line says why", "no area" in by["Une route"]["note"], by["Une route"])
        check("no geometry says why", "no shape" in by["Rien"]["note"], by["Rien"])
        check(
            "a hole takes people out",
            people(by["Avec un trou"]["value"]) < people(by["Yaoundé"]["value"]),
        )
        counted = [people(r["value"]) for r in rows if people(r["value"]) > 0]
        check("the sum is the rows'", people(page.text_content("#file-sum")) == sum(counted))
        check("the overlap caveat shows", page.is_visible("#file-overlap"))
        check("the skipped rows are counted up", "2 rows" in page.text_content("#file-skipped"))
        check("a point with no radius gets the box", page.is_visible("#file-radius-row"))

        console.print("[bold]A picked row[/bold]")
        button = page.locator("#file-rows li").nth(0).locator("button")
        button.click()
        page.wait_for_function("() => !inflight && lastResult")
        check("the count equals its line", page.text_content("#total") == by["Yaoundé"]["value"])
        check("it says where", "Yaoundé" in page.text_content("#counted"))
        check("its line is marked", button.get_attribute("aria-current") == "true")
        ok = page.evaluate(
            "() => JSON.stringify(map.getFilter('file-selected')) === JSON.stringify(['==', ['get', 'i'], 0])"
        )
        check("its outline is marked on the map", ok)
        page.locator("#file-rows li").nth(3).locator("button").click()
        page.wait_for_function("() => !inflight && selectedRow()?.name === 'Taveuni'")
        page.wait_for_function("() => lastResult.cells.some((c) => h3.cellToLatLng(c.h3)[1] < 0)")
        check("a shape across 180° counts both sides", True)
        area = page.evaluate("() => lastResult.areaKm2")
        check("and measures as one piece", 3000 < area < 6000, area)
        page.locator("#file-rows li").nth(0).locator("button").click()
        page.wait_for_function("() => !inflight && selectedRow()?.name === 'Yaoundé'")

        console.print("[bold]A click on the map[/bold]")
        page.evaluate("() => map.jumpTo({ center: [9.72, 4.02], zoom: 9 })")
        page.wait_for_function("() => map.loaded()", timeout=30000)
        at = page.evaluate("() => { const p = map.project([9.72, 4.04]); return [p.x, p.y]; }")
        page.mouse.click(*at)
        page.wait_for_function(
            "() => selectedRow()?.name === 'Deux morceaux' && !inflight", timeout=15000
        )
        check("picks the shape's row", True)
        page.locator("#file-rows li").nth(0).locator("button").click()
        page.wait_for_function("() => !inflight && selectedRow()?.name === 'Yaoundé'")

        console.print("[bold]Normal and Fine[/bold]")
        page.click("#detail-fine")
        page.wait_for_function("() => !fileSet.running && !inflight")
        rows = page.evaluate(ROWS_JS)
        w = reference(store, poly(YAOUNDE), detail=1)
        check(f"Fine recounts the list, {w:,}", people(rows[0]["value"]) == w, rows[0])
        check("and the count", page.text_content("#total") == rows[0]["value"])
        page.click("#detail-normal")
        page.wait_for_function("() => !fileSet.running && !inflight")

        console.print("[bold]The radius box[/bold]")
        page.fill("#file-radius", "1")
        page.dispatch_event("#file-radius", "change")
        page.wait_for_function("() => !fileSet.running")
        rows = page.evaluate(ROWS_JS)
        w = reference(store, poly(circle(9.7, 4.0, 1)))
        check(f"a new radius recounts the point, {w:,}", people(rows[8]["value"]) == w, rows[8])
        w = reference(store, poly(circle(11.5, 3.9, 2)))
        check("and leaves a point's own radius alone", people(rows[7]["value"]) == w, rows[7])

        console.print("[bold]Saved files[/bold]")
        with page.expect_download() as d:
            page.click("#file-csv")
        text = Path(d.value.path()).read_text(encoding="utf-8-sig")
        table = list(csv.DictReader(io.StringIO(text)))
        check("a CSV row for every row", len(table) == len(rows), len(table))
        check("population", int(table[0]["population_estimate"]) == people(rows[0]["value"]))
        check("the area", abs(float(table[0]["area_km2"]) - 487) < 15, table[0]["area_km2"])
        check("the file's columns", table[0]["nom_zone"] == "Yaoundé")
        check("a formula is defused", table[0]["code"] == "'=A1", table[0]["code"])
        check("a skipped row says why", "no area" in table[5]["note"], table[5])
        check("no data is not a zero", table[4]["population_estimate"] == "", table[4])
        check("a point has its radius", float(table[8]["radius_km"]) == 1, table[8])
        with page.expect_download() as d:
            page.click("#file-geojson")
        saved = json.loads(Path(d.value.path()).read_text())
        one = saved["features"][0]
        check("GeoJSON: every row", len(saved["features"]) == len(rows))
        check("GeoJSON: population", one["properties"]["population"] == people(rows[0]["value"]))
        check("GeoJSON: own columns", one["properties"]["nom_zone"] == "Yaoundé")
        again = paths["zones.geojson"].parent / "saved_again.geojson"
        again.write_text(json.dumps(saved))
        rows_again = open_file(page, again)
        same = [r["value"] for r in rows_again] == [r["value"] for r in rows]
        check("Tally reads its own saved file back to the same list", same, rows_again)
        tally_file = again.parent / "one_count.geojson"
        page.locator("#file-rows li").nth(0).locator("button").click()
        page.wait_for_function("() => !inflight && selectedRow()")
        with page.expect_download() as d:
            page.click("#export-geojson")
        shutil.copy(d.value.path(), tally_file)
        offered = d.value.suggested_filename
        check("a named row's file gives its name", "shape-yaounde-" in offered, offered)
        single = open_file(page, tally_file)
        check("a single count's file is one row, not its hexagons", len(single) == 1, len(single))
        round_trip(page, again.parent, check)
        several_files(page, again.parent, paths, check)

        console.print("[bold]Other formats[/bold]")
        rows = open_file(page, paths["zones.kml"])
        check("KML names", [r["name"] for r in rows] == ["Yaoundé", "With a pin"], rows)
        w = [reference(store, poly(YAOUNDE)), reference(store, poly(DOUALA))]
        check("KML counts, the polygon over its pin", [people(r["value"]) for r in rows] == w, rows)
        rows = open_file(page, paths["zones.kmz"])
        check("KMZ", [people(r["value"]) for r in rows] == w, rows)
        rows = open_file(page, paths["zones_shp.zip"])
        check("Shapefile names", [r["name"] for r in rows] == ["Yaounde", "Douala"], rows)
        check("Shapefile counts", [people(r["value"]) for r in rows] == w, rows)
        rows = open_file(
            page, paths["loose/zones.shp"], paths["loose/zones.dbf"], paths["loose/zones.shx"]
        )
        check("Shapefile as loose files", [people(r["value"]) for r in rows] == w, rows)
        rows = open_file(page, paths["projected_shp.zip"])
        off = [abs(people(r["value"]) - x) / x for r, x in zip(rows, w)]
        check("Shapefile in meters, by its .prj", max(off) < 0.001, rows)
        rows = open_file(page, paths["sites.csv"])
        check("CSV: quotes", rows[0]["name"] == 'Clinique "A"', rows[0])
        w = reference(store, poly(circle(11.5, 3.9, 2)))
        check("CSV: count", people(rows[0]["value"]) == w, rows[0])
        check("CSV: no name", rows[1]["name"] == "Point 2", rows[1])
        check("CSV: bad latitude says why", "latitude" in rows[2]["note"], rows[2])
        check("CSV: bad radius says why", "radius" in rows[3]["note"], rows[3])
        rows = open_file(page, paths["sites_fr.csv"])
        w = reference(store, poly(circle(13.4, 9.3, 1.5)))
        check(
            "CSV as French Excel writes it",
            rows[0]["name"] == "Garoua é" and people(rows[0]["value"]) == w,
            rows,
        )

        console.print("[bold]A dropped file[/bold]")
        page.evaluate("() => { if (fileSet) closeFile(); setMode('shape'); }")
        page.evaluate(
            """(text) => {
              const data = new DataTransfer();
              data.items.add(new File([text], 'dropped.kml'));
              window.dispatchEvent(new DragEvent('dragenter', { dataTransfer: data, cancelable: true }));
              window.dispatchEvent(new DragEvent('drop', { dataTransfer: data, cancelable: true }));
            }""",
            kml_file().decode(),
        )
        page.wait_for_function(IDLE_JS, timeout=30000)
        check(
            "opens in the File tab",
            page.is_visible("#mode-file") and not page.is_visible("#file-drop"),
        )
        check("and is counted", page.text_content("#file-name") == "dropped.kml")

        console.print("[bold]Files that cannot be read[/bold]")
        for name, says in {
            "no_columns.csv": "latitude and longitude. It has: city, people",
            "meters.geojson": "WGS 84",
            "big_numbers.geojson": "WGS 84",
            "broken.geojson": "Could not read",
            "zones.gpkg": "GeoPackage",
            "photo.jpg": "cannot open",
            "empty.geojson": "no shapes",
            "too_many.csv": "501 rows",
            "loose/zones.dbf": "one part of a Shapefile",
        }.items():
            text = refused(page, paths[name])
            check(name, says in text, text)

        console.print("[bold]Stop, and a reload[/bold]")
        page.evaluate("() => { if (fileSet) closeFile(); }")
        page.set_input_files("#file-input", str(paths["grid_500.geojson"]))
        page.wait_for_function("() => fileSet?.running && fileSet.rows.some((r) => r.result)")
        page.click("#file-stop")
        page.wait_for_function("() => !fileSet.running")
        done = page.evaluate("() => fileSet.rows.filter((r) => r.result).length")
        page.wait_for_timeout(300)
        same = done == page.evaluate("() => fileSet.rows.filter((r) => r.result).length")
        check("Stop stops", same and done < 500, done)
        check("and says how far", "of 500" in page.text_content("#file-progress"))
        rows = open_file(page, paths["zones.geojson"])
        page.locator("#file-rows li").nth(0).locator("button").click()
        page.wait_for_function("() => !inflight && lastResult")
        page.evaluate("() => keepCount()")
        page.reload()
        page.wait_for_function(IDLE_JS, timeout=60000)
        page.wait_for_function("() => !inflight && lastResult")
        after = page.evaluate(ROWS_JS)
        check("a reload keeps the list", [r["value"] for r in after] == [r["value"] for r in rows])
        check("and the picked row", page.text_content("#total") == rows[0]["value"])

        console.print("[bold]In French[/bold]")
        page.evaluate("() => { keepCount(); localStorage.setItem('lang', 'fr'); }")
        fr = context.new_page()
        fr.route("**/static.cloudflareinsights.com/**", lambda route: route.abort())
        fr.goto(url + "?lang=fr")
        fr.wait_for_function("() => TallyI18n && document.querySelector('#tab-file')")
        fr.wait_for_function(
            "() => document.querySelector('#tab-file').textContent === 'Fichiers'", timeout=10000
        )
        fr.click("#tab-file")
        check(
            "the tab is in French", fr.text_content("#file-open").strip() == "Ouvrir des fichiers"
        )
        fr.close()

        console.print("[bold]About[/bold]")
        page.click("#coverage-open")
        check("no beacon: nothing says visits are counted", not page.is_visible("#about-visits"))
        page.keyboard.press("Escape")
        check("Esc closes it", page.evaluate("() => document.querySelector('#coverage').hidden"))
        hosted = context.new_page()

        def with_beacon(route):
            # Where a hosted copy puts it: after app.js, before </body>. Never loads anything.
            answer = route.fetch()
            tag = "<script type='text/plain' data-cf-beacon='{}'></script></body>"
            route.fulfill(response=answer, body=answer.text().replace("</body>", tag))

        hosted.route(url, with_beacon)
        hosted.goto(url)
        hosted.wait_for_function("() => typeof toggleCoverage === 'function'")
        hosted.wait_for_timeout(1000)
        hosted.click("#coverage-open")
        check("a beacon after app.js: About says so", hosted.is_visible("#about-visits"))
        hosted.close()

        console.print("[bold]Offline[/bold]")
        # A new visitor's worker has cached the shell; the parsers load from it, and with no
        # pack on the device a row says what is missing rather than show a zero.
        away = browser.new_context(locale="en-US", viewport={"width": 1280, "height": 800})
        off = away.new_page()
        off.route("**/static.cloudflareinsights.com/**", lambda route: route.abort())
        off.goto(url)
        off.wait_for_function(
            "() => navigator.serviceWorker.getRegistration().then((r) => r?.active?.state === 'activated')",
            timeout=60000,
        )
        off.reload()
        off.wait_for_function(
            "() => navigator.serviceWorker.controller && typeof map !== 'undefined'"
        )
        away.set_offline(True)
        off.click("#tab-file")
        off.set_input_files("#file-input", str(paths["zones_shp.zip"]))
        off.wait_for_function(IDLE_JS, timeout=60000)
        rows = off.evaluate(ROWS_JS)
        check("a Shapefile opens offline", [r["name"] for r in rows] == ["Yaounde", "Douala"], rows)
        check("no pack: not counted, not zero", rows[0]["value"] == "Not counted", rows[0])
        check("and says which data", "Cameroon" in rows[0]["note"], rows[0])
        off.set_input_files("#file-input", str(paths["zones.kml"]))
        off.wait_for_function(
            "() => fileSet.name === 'zones.kml' && !fileSet.running", timeout=60000
        )
        check("a KML opens offline", off.evaluate(ROWS_JS)[0]["name"] == "Yaoundé")
        away.close()

        console.print("[bold]500 shapes[/bold]")
        page.evaluate("() => { if (fileSet) closeFile(); }")
        t = page.evaluate("() => performance.now()")
        rows = open_file(page, paths["grid_500.geojson"])
        seconds = (page.evaluate("() => performance.now()") - t) / 1000
        total = sum(max(0, people(r["value"])) for r in rows)
        check(
            f"500 rows in {seconds:.1f} s",
            len(rows) == 500 and seconds < TALLY_500_SECONDS,
            seconds,
        )
        # At one level, shapes that adjoin count every hexagon exactly once. (Against the box
        # counted whole on its own, res 5, the sum is 2.3% lower, and nearer the truth: res 8
        # and res 9 agree to 0.011%.)
        cells = h3.geo_to_cells(poly(box(9.5, 3.0, 14.5, 9.25)), 8)
        ids = np.array([int(c, 16) for c in cells], dtype=np.uint64)
        whole = round(
            sum(x.sum() for x in store.by_country(ids, 8, store.countries_for(ids)).values())
        )
        check(f"they sum to {total:,}, exactly the whole box at their level", total == whole, whole)
        session = context.new_cdp_session(page)
        session.send("HeapProfiler.collectGarbage")
        session.send("Performance.enable")
        metrics = {m["name"]: m["value"] for m in session.send("Performance.getMetrics")["metrics"]}
        mb = metrics["JSHeapUsedSize"] / 1e6
        check(f"page memory after: {mb:.0f} MB", mb < TALLY_500_MB, mb)
        browser.close()
    check("no page errors", not errors, errors)


def main(
    dist: Annotated[Path, typer.Option(help="Built static site to test")] = ROOT / "dist",
) -> None:
    store = PackStore(dist / "packs")
    if not {"CMR", "FJI"} <= set(store.entries):
        raise typer.BadParameter("the build needs CMR and FJI")
    check = Check()
    server, url = serve(dist)
    where = Path(tempfile.mkdtemp(prefix="tally-files-"))
    try:
        run(url, write_files(where), store, check)
    finally:
        server.shutdown()
        shutil.rmtree(where, ignore_errors=True)
    if check.failed:
        console.print(f"[red]{check.failed} failed[/red]")
        raise typer.Exit(1)
    console.print("[green]every check passed[/green]")


if __name__ == "__main__":
    typer.run(main)
