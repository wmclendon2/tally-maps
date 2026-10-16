# Copyright (c) 2026 Woody McLendon
# SPDX-License-Identifier: MIT
"""Configuration: data sources, base resolutions, paths."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
BOUNDARY_DIR = DATA_DIR / "boundaries"

# Coarsest pyramid level. Res 3 cells are ~12,000 km^2: a few dozen per country.
MIN_RESOLUTION = 3


@dataclass(frozen=True)
class Source:
    """A population raster product and the H3 resolution it is binned at."""

    name: str  # the source key recorded in each pack
    base_resolution: int
    filename_pattern: str  # glob under data/raw, formatted with iso3 (upper) / iso3l (lower)

    def default_paths(self, country: str) -> list[Path]:
        iso3 = country.upper()
        return sorted(RAW_DIR.glob(self.filename_pattern.format(iso3=iso3, iso3l=iso3.lower())))


# Base resolution is per source: res 9 cells (~0.1 km2) hold several source pixels.
SOURCES: dict[str, Source] = {
    "meta_hrsl": Source("meta_hrsl", 9, "meta/{iso3}/*_general_*.zip"),
    # Packs only (ingest/worldpop_hub.py), for places Meta has no data for. Res 9 cells
    # (~0.1 km2) hold ~10 of its 100 m pixels, and match Meta's packs, so a polygon over
    # both is summed at one resolution.
    "worldpop_100m": Source("worldpop_100m", 9, "worldpop_100m/{iso3}/*_100m_*.tif"),
}
