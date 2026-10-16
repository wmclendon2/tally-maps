# Copyright (c) 2026 Woody McLendon
# SPDX-License-Identifier: MIT
"""Expected answers for the static demo, computed the API's way, to check web/static.js against.

    uv run python scripts/static_parity.py [--packs dist/packs] > expected.json

Resolves a fixed set of polygons with api/resolve.py (the API's own resolver, no database)
and sums the cells from the packs decoded in Python. web/static.js must give the same
resolution, cell counts and totals for each: run `TallyStatic.query` on the same polygons
in the demo page and compare (scripts/browser_parity.py does that in headless Chrome).
Covers one-part and multi-part countries, cross-border boxes, islands, open ocean, a
sub-cell polygon and the Fine step. A case whose countries are not in the pack directory
is skipped, so the same list works for a three-country demo and for the full set.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Annotated

import numpy as np
import typer

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from packstore import PackStore

from ingest.config import ROOT
from ingest.resolve import resolve

# name: (west, south, east, north), detail
CASES = {
    "yaounde": ((11.42, 3.78, 11.62, 3.98), 0),
    "yaounde-fine": ((11.42, 3.78, 11.62, 3.98), 1),
    "cameroon": ((8.5, 2.0, 16.0, 13.0), 0),
    "garoua-boulai-border": ((14.3, 5.6, 14.8, 6.1), 0),
    "bangui": ((18.45, 4.3, 18.65, 4.45), 0),
    "sao-paulo": ((-46.83, -23.75, -46.37, -23.4), 0),
    "rio-fine": ((-43.8, -23.1, -43.1, -22.75), 1),
    "brazil-southeast": ((-50.0, -25.0, -40.0, -18.0), 0),
    "brazil": ((-74.0, -34.0, -34.0, 5.3), 0),
    "ocean": ((-30.0, -10.0, -29.0, -9.0), 0),
    "brasilia-tiny": ((-47.88, -15.795, -47.879, -15.794), 0),
    # Added 2026-10-13, for the countries packed beyond CMR/CAF/BRA.
    "lagos": ((3.2, 6.35, 3.6, 6.7), 0),  # NGA is two parts
    "nigeria": ((2.6, 4.2, 14.7, 13.9), 0),  # needs both NGA parts and its neighbours
    "addis-ababa-fine": ((38.65, 8.9, 38.9, 9.1), 1),  # ETH is two parts
    "mauritius": ((57.3, -20.55, 57.82, -19.95), 0),  # an island: most cells are sea
    "mahe-fine": ((55.35, -4.8, 55.55, -4.55), 1),  # SYC, the smallest pack
    "kinshasa-brazzaville": ((15.1, -4.5, 15.5, -4.15), 0),  # COD/COG across the river
    "busia-border": ((34.0, 0.35, 34.25, 0.55), 0),  # KEN/UGA
}
# The countries each case needs; a case runs only when all of them are packed.
NEEDS = {
    "yaounde": {"CMR"},
    "yaounde-fine": {"CMR"},
    "cameroon": {"CMR"},
    "garoua-boulai-border": {"CMR", "CAF"},
    "bangui": {"CAF"},
    "sao-paulo": {"BRA"},
    "rio-fine": {"BRA"},
    "brazil-southeast": {"BRA"},
    "brazil": {"BRA"},
    "ocean": set(),
    "brasilia-tiny": {"BRA"},
    "lagos": {"NGA"},
    "nigeria": {"NGA"},
    "addis-ababa-fine": {"ETH"},
    "mauritius": {"MUS"},
    "mahe-fine": {"SYC"},
    "kinshasa-brazzaville": {"COD", "COG"},
    "busia-border": {"KEN", "UGA"},
}


def box(w, s, e, n):
    return {"type": "Polygon", "coordinates": [[[w, s], [e, s], [e, n], [w, n], [w, s]]]}


def expected(store: PackStore) -> dict:
    """{case: the API's answer from these packs} for every case whose countries are packed."""
    base = max(e["max_resolution"] for e in store.entries.values())
    out = {}
    for name, (bounds, detail) in CASES.items():
        if not NEEDS[name] <= set(store.entries):
            continue
        geo = box(*bounds)
        r = resolve(geo, base, detail=detail)
        ids = np.array([int(c, 16) for c in r.cells], dtype=np.uint64)
        pops = np.zeros(ids.size)
        for p in store.by_country(ids, r.resolution, store.countries_for(ids)).values():
            pops += p
        out[name] = {
            "geometry": geo,
            "detail": detail,
            "resolution": r.resolution,
            "cell_count": len(r.cells),
            "populated_cells": int((pops > 0).sum()),
            "total": float(pops.sum()),
        }
    return out


def main(
    packs: Annotated[Path, typer.Option(help="Pack directory with catalog.json")] = ROOT
    / "dist"
    / "packs",
) -> None:
    json.dump(expected(PackStore(packs)), sys.stdout, indent=1)


if __name__ == "__main__":
    typer.run(main)
