# Copyright (c) 2026 Woody McLendon
# SPDX-License-Identifier: MIT
"""Per-country offline packs: layout, split, catalogue entry, footprint. Synthetic cells only."""

import gzip
import hashlib
import json
import random
import struct
import sys
from pathlib import Path

import h3
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from build_packs import (
    ALIGN,
    FORMAT,
    MAGIC,
    build_country,
    coarse_parent,
    data_version,
    decode,
    merge,
    pack_bytes,
    prune,
    split,
    tidy,
    unpack,
    write_catalog,
)

from ingest.pyramid import rollup


def pyramid(points, base=9):
    cells = np.array([h3.str_to_int(h3.latlng_to_cell(la, ln, base)) for la, ln, _ in points])
    pops = np.array([p for *_, p in points], dtype=np.float64)
    order = np.argsort(cells)
    cells, pops = cells[order].astype(np.uint64), pops[order]
    levels = rollup(cells, pops, base, 3)
    levels[base] = (cells, pops)
    return merge([levels])


FEATURES = [
    {
        "type": "Feature",
        "properties": {},
        "geometry": {"type": "Polygon", "coordinates": [[[8, 2], [16, 2], [16, 13], [8, 2]]]},
    }
]
CMR = [(3.87, 11.52, 1000.5), (4.05, 9.7, 800.0), (10.6, 14.3, 25.0)]
# Spread over Brazil so the cells fall under many different res-3 ancestors.
rng = random.Random(7)
BRA = [(rng.uniform(-30, 0), rng.uniform(-60, -38), rng.uniform(1, 500)) for _ in range(400)]


def build(points=CMR, max_part_mb=16.0):
    return build_country("CMR", "meta_hrsl", pyramid(points), FEATURES, "2020", max_part_mb)


def read(packed):
    raw = gzip.decompress(packed)
    header, start = unpack(raw)
    return header, decode(raw[start:], header["sections"])


@pytest.mark.parametrize("header_len", range(17))
def test_body_is_aligned_whatever_the_header_length(header_len):
    """The browser views the body as Uint32Array/Float32Array, which throw on odd offsets."""
    header = {"x": "a" * header_len}
    raw = pack_bytes(header, b"\x01\x02\x03\x04")
    back, start = unpack(raw)
    assert back == header
    assert start % ALIGN == 0
    assert raw[start:] == b"\x01\x02\x03\x04"


def test_unpack_refuses_foreign_files_and_unknown_majors():
    with pytest.raises(ValueError, match="not a Tally pack"):
        unpack(b"\x1f\x8b" + b"\0" * 20)
    raw = struct.pack("<4sHHI", MAGIC, FORMAT[0] + 1, 0, 2) + b"{}"
    with pytest.raises(ValueError, match="format"):
        unpack(raw)


def test_coarse_parent_matches_h3():
    cells = [h3.latlng_to_cell(la, ln, r) for la, ln, _ in BRA[:50] for r in (3, 5, 8, 9)]
    got = coarse_parent(np.array([h3.str_to_int(c) for c in cells], np.uint64))
    assert [h3.int_to_str(int(g)) for g in got] == [h3.cell_to_parent(c, 3) for c in cells]


def test_small_country_is_one_part_that_round_trips():
    files, entry = build()
    assert len(files) == len(entry["parts"]) == 1
    header, back = read(files[0])
    assert header["iso3"] == "CMR" and header["data_version"] == "2020"
    assert (header["part"], header["parts"]) == (0, 1)
    assert header["total"] == entry["total"] == round(1825.5)
    assert sorted(back) == list(range(3, 10))
    for cells, pops in back.values():
        assert np.all(cells[:-1] < cells[1:]), "sections must stay sorted for binary search"
        assert pops.astype(np.float64).sum() == pytest.approx(1825.5)
    p = entry["parts"][0]
    assert p["sha256"] == hashlib.sha256(files[0]).hexdigest()
    assert p["raw_sha256"] == hashlib.sha256(gzip.decompress(files[0])).hexdigest()
    assert p["url"] == f"CMR.{p['sha256'][:10]}.tlyp.gz"
    assert p["bytes"] == len(files[0]) == entry["bytes"]


def test_split_parts_are_disjoint_and_add_up():
    levels = tidy(pyramid(BRA))
    parts = split(levels, 3000)  # bytes: forces many parts
    assert len(parts) > 3
    for res, (cells, pops) in levels.items():
        joined = np.sort(np.concatenate([p[res][0] for p in parts]))
        assert np.array_equal(joined, cells), f"res {res}: cells lost or duplicated"
        assert sum(p[res][1].sum() for p in parts) == pytest.approx(pops.sum())
    for p in parts:
        totals = {round(float(p[res][1].sum()), 3) for res in p}
        assert len(totals) == 1, "every level of a part must sum to the same part total"


def test_split_respects_the_size_cap_unless_one_root_is_bigger():
    levels = tidy(pyramid(BRA))
    for cap in (2000, 6000, 20000):
        for p in split(levels, cap):
            raw = 12 * sum(c.size for c, _ in p.values())
            roots = p[3][0].size
            assert raw <= cap or roots == 1


def test_each_part_answers_for_its_own_footprint():
    """The client picks a part by a cell's res-3 parent; that part must hold the cell."""
    files, entry = build(BRA, max_part_mb=0.003)
    assert len(entry["parts"]) > 3
    owner = {h: i for i, p in enumerate(entry["parts"]) for h in p["footprint"]}
    assert len(owner) == sum(len(p["footprint"]) for p in entry["parts"]), "footprints overlap"
    for i, packed in enumerate(files):
        header, back = read(packed)
        assert (header["part"], header["parts"]) == (i, len(files))
        for cells, _ in back.values():
            for c in cells[:20]:
                assert owner[h3.cell_to_parent(h3.int_to_str(int(c)), 3)] == i
    assert sum(p["total"] for p in entry["parts"]) == pytest.approx(entry["total"], abs=len(files))


def test_same_cells_give_the_same_file():
    """gzip mtime=0 and a content-hashed name: rebuilding unchanged data changes nothing."""
    assert build()[0] == build()[0]


def test_empty_cells_are_left_out():
    _, entry = build(CMR + [(6.0, 12.0, 0.0)])
    assert entry["parts"][0]["cells"] == build()[1]["parts"][0]["cells"]


def test_catalog_lists_countries_and_prune_keeps_only_listed_packs(tmp_path):
    files, entry = build()
    (tmp_path / entry["parts"][0]["url"]).write_bytes(files[0])
    (tmp_path / "CMR.0000000000.tlyp.gz").write_bytes(b"old")
    cat = write_catalog(tmp_path, {"CMR": entry})
    assert cat["format"] == list(FORMAT)
    listed = json.loads((tmp_path / "catalog.json").read_text())["countries"][0]
    assert listed["iso3"] == "CMR"
    assert listed["continent"] == "Africa"  # the ? panel groups countries by it
    assert prune(tmp_path, {"CMR": entry}) == ["CMR.0000000000.tlyp.gz"]
    assert (tmp_path / entry["parts"][0]["url"]).exists()


def test_data_version_reads_meta_file_names():
    assert data_version([Path("cmr_general_2020_geotiff.zip")]) == "2020"
    tiles = [Path(f"population_bra_{r}_2018-10-01.geotiff.zip") for r in ("northeast", "south")]
    assert data_version(tiles) == "2018-10-01"
    assert data_version([Path("x.zip")]) == "unknown"


def test_data_version_looks_inside_undated_zips(tmp_path):
    import zipfile

    z = tmp_path / "population_mus.geotiff.zip"
    with zipfile.ZipFile(z, "w") as f:
        f.writestr("population_mus_2018-10-01.tif", b"")
    assert data_version([z]) == "2018-10-01"


def test_ensure_writes_the_catalogue_even_when_a_country_fails(tmp_path, monkeypatch):
    import build_packs

    def fake_levels(iso3, source):
        if iso3 == "CAF":
            raise RuntimeError("no raster")
        return pyramid(CMR), FEATURES, "2020"

    monkeypatch.setattr(build_packs, "country_levels", fake_levels)
    monkeypatch.setattr(build_packs, "job_cost", lambda iso3: 0.0)
    with pytest.raises(RuntimeError, match="CAF"):
        build_packs.ensure(["CMR", "CAF"], tmp_path, workers=1)
    cat = json.loads((tmp_path / "catalog.json").read_text())
    assert [c["iso3"] for c in cat["countries"]] == ["CMR"]
    assert (tmp_path / cat["countries"][0]["parts"][0]["url"]).exists()


def test_data_version_skips_worldpop_release_names():
    assert data_version([Path("jey_pop_2020_CN_100m_R2025A_v1.tif")]) == "2020"


def test_jersey_comes_from_worldpop_and_is_credited():
    from build_packs import attribution, source_for

    assert source_for("jey") == "worldpop_100m"
    assert source_for("CMR") == "meta_hrsl"
    names = [a["name"] for a in attribution({"meta_hrsl", "worldpop_100m"})]
    assert names[:2] == ["Meta Data for Good", "WorldPop"]


def test_catalogue_backfills_what_the_country_picker_reads(tmp_path):
    files, entry = build()
    (tmp_path / entry["parts"][0]["url"]).write_bytes(files[0])
    counts = entry["cells_by_resolution"]
    old = {k: v for k, v in entry.items() if k not in ("cells_by_resolution", "area_km2")}
    listed = write_catalog(tmp_path, {"CMR": old})["countries"][0]
    assert listed["cells_by_resolution"] == counts  # read back from the pack headers
    assert listed["area_km2"] == 1000.0  # tests/conftest.py
    assert sum(counts.values()) == entry["parts"][0]["cells"]
