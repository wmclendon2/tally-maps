# Copyright (c) 2026 Woody McLendon
# SPDX-License-Identifier: MIT
"""Meta's High Resolution Population Density Maps on HDX: the catalogue and downloads.

Finds each country's general-population GeoTIFF through HDX's CKAN API (no key needed) and
downloads it to data/raw/meta/<ISO3>/. scripts/build_packs.py calls this for any country
whose raster is not here yet. Part-downloaded zips resume.

    # every country in Meta's catalogue, with its data year and download size
    uv run python -m ingest.hdx
"""

from __future__ import annotations

import http.client
import json
import re
import time
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from ingest.config import RAW_DIR
from ingest.fetch import CURL, USER_AGENT, curl_to_file, get_bytes

SOURCE = "meta_hrsl"
CKAN = "https://data.humdata.org/api/3/action/package_search"
QUERY = '"High Resolution Population Density"'
ORG = "meta"
# A "general" (total population) raster is any GeoTIFF zip whose name carries no
# demographic keyword. Names vary by vintage: cmr_general_2020_geotiff.zip,
# population_deu.geotiff.zip, population_npl_2018-10-01_geotiff.zip,
# jpn_population_2020_geotiff.zip, population_turkey_2020_tif.zip, and tiled sets
# such as AUS_northeast_2019-06-01_geotiff.zip or USA_lat_24_lon_-81_geotiff.zip.
TIF_ZIP_RE = re.compile(r"(geotiff|tiff?)\.zip$|\.tiff?$", re.IGNORECASE)
DEMOGRAPHIC_RE = re.compile(
    r"children|elderly|\bmen\b|_men_|women|youth|female|male|proportion|age_\d", re.IGNORECASE
)
YEAR_RE = re.compile(r"(?<!\d)(20\d{2})(?!\d)")

console = Console()
app = typer.Typer(add_completion=False)


@dataclass(frozen=True)
class Resource:
    name: str
    url: str
    size: int | None


@dataclass(frozen=True)
class Candidate:
    iso3: str
    dataset: str
    title: str
    year: int  # 0 when the file name carries no year
    resources: tuple[Resource, ...]  # one file, or several tiles of the same vintage

    @property
    def size(self) -> int:
        return sum(r.size or 0 for r in self.resources)

    @property
    def urls(self) -> str:
        return ";".join(r.url for r in self.resources)


def fetch_catalog(rows: int = 200) -> list[dict]:
    """All Meta HRSL datasets on HDX, as raw CKAN package dicts.

    Paged large deliberately: with rows=25 the catalogue came back with 195, 196 or 197
    datasets on consecutive runs, because CKAN's result ordering can shift between page
    requests. One or two pages is both steadier and quicker.
    """
    packages: list[dict] = []
    start = 0
    while True:
        q = urllib.parse.urlencode(
            {"q": QUERY, "fq": f"organization:{ORG}", "rows": rows, "start": start}
        )
        result = json.loads(get_bytes(f"{CKAN}?{q}"))["result"]
        packages.extend(result["results"])
        start += rows
        if start >= result["count"] or not result["results"]:
            return packages


def is_general_raster(name: str) -> bool:
    return bool(TIF_ZIP_RE.search(name)) and not DEMOGRAPHIC_RE.search(name)


def pick_general(package: dict) -> Candidate | None:
    """The newest general-population GeoTIFF resource(s) of a package, or None.

    Packages whose HDX group is not a single ISO3 country (continent bundles,
    multi-country sets) are skipped. Several files of the same vintage are tiles.
    """
    groups = [g["name"] for g in package.get("groups", [])]
    if len(groups) != 1 or len(groups[0]) != 3:
        return None
    iso3 = groups[0].upper()
    by_year: dict[int, list[Resource]] = {}
    for r in package.get("resources", []):
        name = r.get("name", "")
        if not is_general_raster(name):
            continue
        m = YEAR_RE.search(name)
        year = int(m.group(1)) if m else 0
        by_year.setdefault(year, []).append(Resource(name, r["url"], r.get("size")))
    if not by_year:
        return None
    year = max(by_year)
    res = tuple(sorted(by_year[year], key=lambda r: r.name))
    return Candidate(iso3, package["name"], package.get("title", ""), year, res)


def candidates() -> dict[str, Candidate]:
    out: dict[str, Candidate] = {}
    for pkg in fetch_catalog():
        c = pick_general(pkg)
        if c and (c.iso3 not in out or c.year > out[c.iso3].year):
            out[c.iso3] = c
    return dict(sorted(out.items()))


def country_dir(iso3: str) -> Path:
    return RAW_DIR / "meta" / iso3.upper()


def download(url: str, dest: Path, *, progress=None, attempts: int = 5) -> Path:
    """Fetch to dest.part (resuming if present), verify it, then rename to dest."""
    if dest.exists():
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    if CURL:
        curl_to_file(url, part, resume=part.exists(), timeout=3600)
    else:
        _download_urllib(url, part, progress=progress, attempts=attempts)
    if dest.suffix.lower() == ".zip" and not zipfile.is_zipfile(part):
        part.unlink()
        raise RuntimeError(f"{dest.name}: downloaded file is not a zip")
    part.replace(dest)
    return dest


def _download_urllib(url: str, part: Path, *, progress, attempts: int) -> None:
    """Stream with Range-based resume and a size check against Content-Length."""
    total = 0
    for attempt in range(1, attempts + 1):
        done = part.stat().st_size if part.exists() else 0
        headers = {"User-Agent": USER_AGENT}
        if done:
            headers["Range"] = f"bytes={done}-"
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                if done and resp.status != 206:  # server ignored Range: start over
                    done = 0
                if resp.status == 206:
                    total = int(resp.headers["Content-Range"].rsplit("/", 1)[1])
                else:
                    total = int(resp.headers.get("Content-Length") or 0)
                with part.open("ab" if done else "wb") as fh:
                    while chunk := resp.read(1 << 20):
                        fh.write(chunk)
                        done += len(chunk)
                        if progress:
                            progress(done, total)
        except (OSError, http.client.HTTPException) as exc:
            if attempt == attempts:
                raise
            console.print(
                f"[yellow]{part.name}: retry {attempt} after {type(exc).__name__}[/yellow]"
            )
            time.sleep(2 * attempt)
            continue
        if not total or done == total:
            return
        console.print(f"[yellow]{part.name}: got {done:,} of {total:,} bytes, resuming[/yellow]")
    raise RuntimeError(f"{part.name}: incomplete after {attempts} attempts")


def fetch_country(cand: Candidate, *, progress=None) -> list[Path]:
    """Download one country's general raster archive; cached under data/raw/meta/<ISO3>."""
    d = country_dir(cand.iso3)
    return [download(r.url, d / r.name, progress=progress) for r in cand.resources]


@app.command()
def main() -> None:
    """Print Meta's catalogue on HDX: every country, its data year and download size."""
    cands = candidates()
    table = Table(title=f"HDX Meta HRSL catalogue: {len(cands)} countries")
    table.add_column("ISO3")
    table.add_column("year")
    table.add_column("size MB", justify="right")
    table.add_column("downloaded")
    table.add_column("files", justify="right")
    table.add_column("resource")
    for iso3, c in cands.items():
        have = any(country_dir(iso3).glob("*.zip"))
        table.add_row(
            iso3,
            str(c.year) if c.year else "?",
            f"{c.size / 1e6:,.0f}",
            "yes" if have else "-",
            str(len(c.resources)),
            c.resources[0].name,
        )
    console.print(table)


if __name__ == "__main__":
    app()
