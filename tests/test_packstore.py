# Copyright (c) 2026 Woody McLendon
# SPDX-License-Identifier: MIT
"""Reading built packs for the verify scripts: lookups across countries, integrity checks."""

import json
import sys
from pathlib import Path

import h3
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from build_packs import build_country, merge, write_catalog
from packstore import PackStore

from ingest.pyramid import rollup

FEATURES = [
    {
        "type": "Feature",
        "properties": {},
        "geometry": {"type": "Polygon", "coordinates": [[[8, 2], [16, 2], [16, 13], [8, 2]]]},
    }
]
BORDER = (4.0, 12.0)  # one point both countries put people in


def levels_for(points):
    cells = np.array([h3.str_to_int(h3.latlng_to_cell(la, ln, 9)) for la, ln, _ in points])
    pops = np.array([p for *_, p in points], dtype=np.float64)
    order = np.argsort(cells)
    cells, pops = cells[order].astype(np.uint64), pops[order]
    levels = rollup(cells, pops, 9, 3)
    levels[9] = (cells, pops)
    return merge([levels])


@pytest.fixture
def store(tmp_path):
    entries = {}
    for iso3, points in {
        "AAA": [(3.87, 11.52, 1000.0), (*BORDER, 40.0)],
        "BBB": [(*BORDER, 60.0), (5.5, 13.0, 7.0)],
    }.items():
        files, entry = build_country(iso3, "meta_hrsl", levels_for(points), FEATURES, "2020")
        for part, packed in zip(entry["parts"], files, strict=True):
            (tmp_path / part["url"]).write_bytes(packed)
        entries[iso3] = entry
    write_catalog(tmp_path, entries)
    return PackStore(tmp_path)


def cell(lat, lng, res=9):
    return np.array([h3.str_to_int(h3.latlng_to_cell(lat, lng, res))], dtype=np.uint64)


def test_lookup_reads_one_country_and_by_country_sums_the_border_cell(store):
    assert store.lookup("AAA", 9, cell(*BORDER)).tolist() == [40.0]
    assert store.lookup("BBB", 9, cell(3.87, 11.52)).tolist() == [0.0]
    both = store.by_country(cell(*BORDER), 9, store.countries())
    assert {k: v.tolist() for k, v in both.items()} == {"AAA": [40.0], "BBB": [60.0]}


def test_every_level_carries_the_country_total(store):
    for res in range(3, 10):
        assert store.total("AAA", res) == pytest.approx(1040.0)


def test_check_passes_clean_packs(store):
    assert store.check("AAA") == [] and store.check("BBB") == []


def test_check_catches_a_changed_unreadable_or_missing_file(store):
    import gzip

    path = store.root / store.entries["AAA"]["parts"][0]["url"]
    original = path.read_bytes()
    path.write_bytes(gzip.compress(gzip.decompress(original), 1, mtime=1))  # same cells
    assert [p for p in store.check("AAA") if "sha256 differs" in p]
    path.write_bytes(original[: len(original) // 2])
    assert [p for p in store.check("AAA") if "unreadable" in p]
    path.unlink()
    assert [p for p in store.check("AAA") if "missing" in p]


def test_check_catches_a_catalogue_total_that_disagrees(store, tmp_path):
    cat = json.loads((tmp_path / "catalog.json").read_text())
    cat["countries"][0]["total"] += 500
    (tmp_path / "catalog.json").write_text(json.dumps(cat))
    assert any("catalogue says" in p for p in PackStore(tmp_path).check("AAA"))
