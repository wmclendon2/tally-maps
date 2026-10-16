# Copyright (c) 2026 Woody McLendon
# SPDX-License-Identifier: MIT
"""WorldPop 100 m rasters from the WorldPop hub (CC BY 4.0), for places Meta has no data for.

    from ingest import worldpop_hub; worldpop_hub.fetch_country("JEY")

Downloads one country's GeoTIFF to data/raw/worldpop_100m/<ISO3>/ (git-ignored) through
the hub's REST API, which needs no key. The product is Global2 R2025A, constrained (people
only where there are buildings, like Meta's maps), at 3 arc-seconds (~100 m), for YEAR.
"""

from __future__ import annotations

import json
from pathlib import Path

from ingest.config import RAW_DIR
from ingest.fetch import CURL, curl_to_file, get_bytes

API = "https://hub.worldpop.org/rest/data/pop/{product}?iso3={iso3}"
PRODUCT = "G2_CN_POP_R25A_100m"
# Meta's rasters are 2018-2020, so the WorldPop countries match them rather than show a
# later year beside them. The product runs 2015-2030; change this and rebuild to move.
YEAR = "2020"


def country_dir(iso3: str) -> Path:
    return RAW_DIR / "worldpop_100m" / iso3.upper()


def file_url(iso3: str, year: str = YEAR, product: str = PRODUCT) -> str:
    """The GeoTIFF's URL for one country and year, from the hub's listing."""
    listing = json.loads(get_bytes(API.format(product=product, iso3=iso3.upper()), timeout=120))
    rows = [r for r in listing.get("data", []) if r.get("popyear") == year]
    if len(rows) != 1 or len(rows[0].get("files", [])) != 1:
        raise RuntimeError(f"{iso3}: expected one {product} file for {year}, found {rows}")
    return rows[0]["files"][0]


def fetch_country(iso3: str, year: str = YEAR) -> list[Path]:
    """The country's raster, downloaded unless already cached."""
    url = file_url(iso3, year)
    dest = country_dir(iso3) / url.rsplit("/", 1)[-1]
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(".part")
        if CURL:
            curl_to_file(url, tmp, timeout=1800)
        else:
            tmp.write_bytes(get_bytes(url, timeout=1800))
        tmp.rename(dest)
    return [dest]
