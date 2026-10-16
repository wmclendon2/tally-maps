# Copyright (c) 2026 Woody McLendon
# SPDX-License-Identifier: MIT
"""Read the built country packs (data/packs/) the way the browser does, for the checks.

The verify scripts used to ask the API, which reads PostGIS. Since 2026-10-13 new
countries go into packs only (build_packs.py never touches the database), so the checks
read what is actually shipped: the gzipped parts listed in catalog.json.

A cell can hold people from several countries (a border cell is in both packs), so every
lookup sums across all the packs that hold it, exactly as web/static.js does.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import sys
from pathlib import Path

import h3
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_packs import PACK_CACHE, coarse_parent, decode, unpack

from ingest.config import MIN_RESOLUTION

Levels = dict[int, tuple[np.ndarray, np.ndarray]]


class PackStore:
    def __init__(self, root: Path = PACK_CACHE):
        self.root = Path(root)
        path = self.root / "catalog.json"
        if not path.exists():
            raise FileNotFoundError(f"no {path}; build packs with scripts/build_packs.py")
        self.catalog = json.loads(path.read_text())
        self.entries = {e["iso3"]: e for e in self.catalog["countries"]}
        self._levels: dict[str, Levels] = {}
        # res-3 cell -> countries with a part there: how static.js picks what to download.
        self.roots: dict[int, set[str]] = {}
        for iso3, e in self.entries.items():
            for part in e["parts"]:
                for c in part["footprint"]:
                    self.roots.setdefault(h3.str_to_int(c), set()).add(iso3)

    def countries(self) -> list[str]:
        return sorted(self.entries)

    def base_resolution(self, source: str) -> int:
        return int(self.catalog["sources"][source]["base_resolution"])

    def _read(self, url: str) -> bytes:
        return gzip.decompress((self.root / url).read_bytes())

    def levels(self, iso3: str) -> Levels:
        """Every level of one country, its parts merged; cells sorted within each level."""
        if iso3 not in self._levels:
            merged: dict[int, list[tuple[np.ndarray, np.ndarray]]] = {}
            for part in self.entries[iso3]["parts"]:
                raw = self._read(part["url"])
                header, start = unpack(raw)
                for res, (cells, pops) in decode(raw[start:], header["sections"]).items():
                    merged.setdefault(res, []).append((cells, pops.astype(np.float64)))
            out = {}
            for res, chunks in merged.items():
                cells = np.concatenate([c for c, _ in chunks])
                pops = np.concatenate([p for _, p in chunks])
                order = np.argsort(cells)
                out[res] = (cells[order], pops[order])
            self._levels[iso3] = out
        return self._levels[iso3]

    def total(self, iso3: str, res: int) -> float:
        return float(self.levels(iso3)[res][1].sum())

    def lookup(self, iso3: str, res: int, cells: np.ndarray) -> np.ndarray:
        """Population of `cells` (uint64) in one country's pack; 0 where it has none."""
        have, pops = self.levels(iso3)[res]
        idx = np.searchsorted(have, cells)
        idx[idx >= have.size] = 0
        hit = have[idx] == cells if have.size else np.zeros(cells.size, bool)
        return np.where(hit, pops[idx] if have.size else 0.0, 0.0)

    def countries_for(self, cells: np.ndarray) -> list[str]:
        """Countries whose packs can hold any of `cells`, by footprint, as static.js decides."""
        if not cells.size:
            return []
        roots = np.unique(coarse_parent(cells)).tolist()
        return sorted(set().union(*(self.roots.get(r, set()) for r in roots)))

    def by_country(
        self, cells: np.ndarray, res: int, countries: list[str]
    ) -> dict[str, np.ndarray]:
        """{iso3: population per cell} for the given cells, for each country that has any."""
        out = {}
        for iso3 in countries:
            pops = self.lookup(iso3, res, cells)
            if pops.any():
                out[iso3] = pops
        return out

    def check(self, iso3: str) -> list[str]:
        """What the browser relies on: hashes, headers, footprints and totals agree."""
        e = self.entries[iso3]
        problems: list[str] = []
        footprints: list[set[int]] = []
        part_totals = 0.0
        for i, part in enumerate(e["parts"]):
            path = self.root / part["url"]
            if not path.exists():
                problems.append(f"{part['url']}: missing")
                continue
            packed = path.read_bytes()
            if hashlib.sha256(packed).hexdigest() != part["sha256"]:
                problems.append(f"{part['url']}: sha256 differs from the catalogue")
            try:
                raw = gzip.decompress(packed)
            except (OSError, EOFError) as exc:
                problems.append(f"{part['url']}: unreadable ({exc})")
                continue
            if hashlib.sha256(raw).hexdigest() != part["raw_sha256"]:
                problems.append(f"{part['url']}: raw_sha256 differs from the catalogue")
            header, start = unpack(raw)
            if (header["iso3"], header["part"], header["parts"]) != (iso3, i, len(e["parts"])):
                problems.append(
                    f"{part['url']}: header says {header['iso3']} part {header['part']}"
                )
            levels = decode(raw[start:], header["sections"])
            fp = {h3.str_to_int(c) for c in part["footprint"]}
            footprints.append(fp)
            base = header["source_base_resolution"]
            base_total = float(levels[base][1].astype(np.float64).sum())
            part_totals += base_total
            if abs(base_total - part["total"]) > 1.0:
                problems.append(
                    f"{part['url']}: cells sum to {base_total:,.1f}, not {part['total']:,}"
                )
            for res, (cells, pops) in levels.items():
                level_total = float(pops.astype(np.float64).sum())
                if abs(level_total - base_total) > max(1.0, base_total * 1e-6):
                    problems.append(f"{part['url']}: res {res} sums to {level_total:,.1f}")
                if cells.size and not set(np.unique(coarse_parent(cells)).tolist()) <= fp:
                    problems.append(f"{part['url']}: res {res} has cells outside its footprint")
        for a in range(len(footprints)):
            for b in range(a + 1, len(footprints)):
                if footprints[a] & footprints[b]:
                    problems.append(f"parts {a} and {b} share footprint cells")
        if abs(part_totals - e["total"]) > max(1.0, e["total"] * 1e-6):
            problems.append(f"parts sum to {part_totals:,.1f}, catalogue says {e['total']:,}")
        if MIN_RESOLUTION not in self.levels(iso3):
            problems.append(f"no res {MIN_RESOLUTION} level")
        return problems
