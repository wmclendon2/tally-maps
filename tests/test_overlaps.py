# Copyright (c) 2026 Woody McLendon
# SPDX-License-Identifier: MIT
"""Offline checks for the overlap split (F-30). No data/ files needed."""

import json
import sqlite3
import struct

import pytest
import shapely
from shapely.geometry import box

from ingest import overlaps as ov
from scripts import split_overlaps as so

# A's and B's outlines overlap over x 1..2; CGAZ draws their border at x 1.5.
A, B = box(0, 0, 2, 1), box(1, 0, 3, 1)
UNITS = {"AAA": box(-1, -1, 1.5, 2), "BBB": box(1.5, -1, 4, 2)}


def outline_of(outlines):
    return outlines.__getitem__


def pair(a, b, rest_to):
    return {"a": a, "b": b, "rest_to": rest_to}


def clips(outlines, pairs, units):
    return {k: ov.clip_shape(k, pairs, outline_of(outlines), units) for k in outlines}


def assert_partition(outlines, clipped):
    """The clips never overlap, and together cover exactly what the outlines did."""
    keys = sorted(clipped)
    for i, a in enumerate(keys):
        for b in keys[i + 1 :]:
            assert ov.polygonal(clipped[a].intersection(clipped[b])).area < 1e-12, (a, b)
    before = shapely.union_all(list(outlines.values()))
    after = shapely.union_all(list(clipped.values()))
    assert before.symmetric_difference(after).area < 1e-12


def test_an_overlap_is_split_along_cgaz():
    outlines = {"AAA": A, "BBB": B}
    clipped = clips(outlines, [pair("AAA", "BBB", "AAA")], UNITS)
    assert clipped["AAA"].equals(box(0, 0, 1.5, 1))
    assert clipped["BBB"].equals(box(1.5, 0, 3, 1))
    assert_partition(outlines, clipped)


@pytest.mark.parametrize("rest_to", ["AAA", "BBB"])
def test_the_part_in_neither_unit_goes_whole_to_the_decided_side(rest_to):
    # CGAZ leaves x 1.4..1.6 to neither (a third unit, or sea).
    units = {"AAA": box(-1, -1, 1.4, 2), "BBB": box(1.6, -1, 4, 2)}
    outlines = {"AAA": A, "BBB": B}
    clipped = clips(outlines, [pair("AAA", "BBB", rest_to)], units)
    rest = box(1.4, 0, 1.6, 1)
    assert clipped[rest_to].contains(rest)
    other = "BBB" if rest_to == "AAA" else "AAA"
    assert clipped[other].intersection(rest).area == 0
    assert_partition(outlines, clipped)


def test_a_side_with_no_cgaz_unit_puts_the_whole_overlap_to_people():
    # CGAZ folds the territory into its parent: split along CGAZ, the parent would get it.
    parent, territory = box(0, 0, 10, 10), box(2, 2, 3, 3)
    outlines = {"PAR": parent, "TER": territory}
    units = {"PAR": box(-1, -1, 11, 11)}
    clipped = clips(outlines, [pair("PAR", "TER", "TER")], units)
    assert clipped["TER"].equals(territory)
    assert clipped["PAR"].equals(parent.difference(territory))
    assert ov.rest_of(territory, units["PAR"], None).equals(territory)
    assert_partition(outlines, clipped)


def test_three_overlapping_outlines_still_partition():
    outlines = {"AAA": box(0, 0, 2, 2), "BBB": box(1, 0, 3, 2), "CCC": box(0.5, 1, 2.5, 3)}
    units = {"AAA": box(-1, -1, 1.5, 1.5), "BBB": box(1.5, -1, 4, 1.5), "CCC": box(-1, 1.5, 4, 4)}
    pairs = [pair("AAA", "BBB", "AAA"), pair("AAA", "CCC", "AAA"), pair("BBB", "CCC", "BBB")]
    clipped = clips(outlines, pairs, units)
    assert_partition(outlines, clipped)
    assert clipped["CCC"].equals(box(0.5, 1.5, 2.5, 3))


def test_b_side_is_worked_out_near_the_overlap():
    overlap = ov.overlap_of(A, B)
    side = ov.b_side(overlap, UNITS["AAA"], UNITS["BBB"], True)
    # Only CGAZ's unit for BBB within NEAR_DEG of the overlap's box, not all of it.
    assert side.equals(box(1.5, -ov.NEAR_DEG, 2 + ov.NEAR_DEG, 1 + ov.NEAR_DEG))
    assert ov.b_side(overlap, None, UNITS["BBB"], True).is_empty  # no unit: all to AAA
    assert ov.b_side(overlap, None, UNITS["BBB"], False).contains(overlap)


def test_a_clip_refuses_a_split_made_with_another_cgaz(boundaries):
    with pytest.raises(RuntimeError, match="another CGAZ"):
        ov.clip_path("AAA", split_doc())


def test_overlap_of_matches_a_plain_intersection():
    islands = shapely.MultiPolygon([box(0, 0, 1, 1), box(5, 5, 6, 6)])
    assert ov.overlap_of(islands, box(0.5, 0.5, 5.5, 5.5)).equals(
        shapely.MultiPolygon([box(0.5, 0.5, 1, 1), box(5, 5, 5.5, 5.5)])
    )
    assert ov.overlap_of(A, box(2, 0, 3, 1)).is_empty  # a shared edge is no overlap
    assert ov.overlap_of(ov.EMPTY, A).is_empty


def test_pairs_of_other_countries_change_nothing():
    outlines = {"AAA": A, "BBB": B, "CCC": box(10, 10, 11, 11)}
    clipped = clips(outlines, [pair("AAA", "BBB", "AAA")], UNITS)
    assert clipped["CCC"].equals(outlines["CCC"])


def gpkg_blob(geom, envelope_code=1):
    """A GeoPackage geometry blob, its envelope filled with zeros (the reader skips it)."""
    size = (0, 32, 48, 48, 64)[envelope_code]
    head = b"GP" + bytes([0, envelope_code << 1 | 1]) + struct.pack("<i", 4326)
    return head + bytes(size) + shapely.to_wkb(geom)


@pytest.mark.parametrize("code", [0, 1, 2, 4])
def test_gpkg_geometry_skips_any_envelope(code):
    assert ov.gpkg_geometry(gpkg_blob(box(0, 0, 1, 1), code)).equals(box(0, 0, 1, 1))


def test_gpkg_geometry_refuses_other_blobs():
    with pytest.raises(ValueError):
        ov.gpkg_geometry(shapely.to_wkb(box(0, 0, 1, 1)))


def test_cgaz_units_reads_and_merges_rows(tmp_path):
    path = tmp_path / "cgaz.gpkg"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE gpkg_contents (table_name TEXT, data_type TEXT)")
        db.execute("INSERT INTO gpkg_contents VALUES ('globalADM0', 'features')")
        db.execute("CREATE TABLE globalADM0 (fid INTEGER, geom BLOB, shapeGroup TEXT)")
        rows = [
            (1, gpkg_blob(box(0, 0, 1, 1)), "AAA"),
            (2, gpkg_blob(box(1, 0, 2, 1)), "AAA"),
            (3, gpkg_blob(box(5, 5, 6, 6)), "BBB"),
        ]
        db.executemany("INSERT INTO globalADM0 VALUES (?, ?, ?)", rows)
    units = ov.cgaz_units(["AAA", "CCC"], path)
    assert list(units) == ["AAA"]
    assert units["AAA"].equals(box(0, 0, 2, 1))
    assert ov.cgaz_units([], path) == {}


def write_outline(path, geom, **tally):
    doc = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"shapeName": path.stem},
                "geometry": geom.__geo_interface__,
            }
        ],
    }
    if tally:
        doc["tally"] = tally
    path.write_text(json.dumps(doc))
    return path


@pytest.fixture
def boundaries(tmp_path, monkeypatch):
    files = {
        "AAA": write_outline(tmp_path / "AAA.geojson", A, land_area_km2=5.0),
        "BBB": write_outline(tmp_path / "BBB.geojson", B),
        "CCC": write_outline(tmp_path / "CCC.geojson", box(10, 10, 11, 11)),
    }
    from ingest import boundaries as gb

    monkeypatch.setattr(ov, "fetch_boundary", lambda iso3: files[iso3])
    monkeypatch.setattr(gb, "fetch_boundary", lambda iso3: files[iso3])
    monkeypatch.setattr(ov, "CLIP_DIR", tmp_path / "clip")
    return files


def split_doc(rest_to="AAA"):
    return {
        "cgaz": {"sha256": "x"},
        "countries": ["AAA", "BBB", "CCC"],
        "pairs": [pair("AAA", "BBB", rest_to)],
    }


def test_clip_path_writes_the_clip_and_keeps_the_outlines_metadata(boundaries):
    from ingest.boundaries import land_area_km2, outline_shape

    path = ov.clip_path("AAA", split_doc(), units=UNITS)
    assert path.parent.name == "clip"
    assert outline_shape(path).equals(box(0, 0, 1.5, 1))
    assert land_area_km2(path) == 5.0  # a Natural Earth outline's land area rides along
    assert ov.clip_path("CCC", split_doc(), units=UNITS) == boundaries["CCC"]


def test_clip_path_reuses_its_cache_until_the_pairs_change(boundaries, monkeypatch):
    from ingest.boundaries import outline_shape

    path = ov.clip_path("BBB", split_doc(), units=UNITS)
    calls = []
    real = ov.clip_shape
    monkeypatch.setattr(ov, "clip_shape", lambda *a: calls.append(a) or real(*a))
    assert ov.clip_path("BBB", split_doc(), units=UNITS) == path
    assert calls == []
    # A gap in CGAZ whose rest now goes to BBB: a new key, so the clip is redone.
    units = {"AAA": box(-1, -1, 1.4, 2), "BBB": box(1.6, -1, 4, 2)}
    ov.clip_path("BBB", split_doc("BBB"), units=units)
    assert len(calls) == 1
    assert outline_shape(path).equals(box(1.4, 0, 3, 1))


def test_the_rest_goes_to_more_people_ties_to_the_lower_code():
    assert so.decide(("AAA", "BBB"), {("AAA", "BBB", "AAA"): 5.0, ("AAA", "BBB", "BBB"): 9.0}) == (
        "BBB"
    )
    assert so.decide(("AAA", "BBB"), {("AAA", "BBB", "AAA"): 5.0, ("AAA", "BBB", "BBB"): 5.0}) == (
        "AAA"
    )
    # No raster to read counts as no one.
    assert so.decide(("AAA", "BBB"), {("AAA", "BBB", "AAA"): None, ("AAA", "BBB", "BBB"): 1.0}) == (
        "BBB"
    )


def test_check_pair_counts_a_third_country_that_keeps_the_piece():
    # Near a tripoint all three overlap, and CGAZ gives the shared corner to CCC.
    outlines = {"AAA": box(0, 0, 2, 2), "BBB": box(1, 0, 3, 2), "CCC": box(0.5, 1, 2.5, 3)}
    units = {"AAA": box(-1, -1, 1.5, 1.5), "BBB": box(1.5, -1, 4, 1.5), "CCC": box(-1, 1.5, 4, 4)}
    pairs = [pair("AAA", "BBB", "AAA"), pair("AAA", "CCC", "AAA"), pair("BBB", "CCC", "BBB")]
    clipped = clips(outlines, pairs, units)
    get, cut = outlines.__getitem__, clipped.__getitem__
    assert so.check_pair(pairs[0], get, cut)[1] > 0  # AAA and BBB alone drop the corner
    assert so.check_pair(pairs[0], get, cut, ["CCC"]) == (0.0, 0.0)


def test_check_pair_finds_clips_that_overlap_or_drop_a_piece():
    outlines = {"AAA": A, "BBB": B}
    good = clips(outlines, [pair("AAA", "BBB", "AAA")], UNITS)
    p = pair("AAA", "BBB", "AAA")
    assert so.check_pair(p, outlines.__getitem__, good.__getitem__) == (0.0, 0.0)
    both, lost = so.check_pair(p, outlines.__getitem__, outlines.__getitem__)
    assert both > 1000 and lost == 0.0
    dropped = {"AAA": box(0, 0, 1, 1), "BBB": box(2, 0, 3, 1)}
    both, lost = so.check_pair(p, outlines.__getitem__, dropped.__getitem__)
    assert both == 0.0 and lost > 1000


def test_the_split_file_is_json_with_one_pair_a_line(tmp_path):
    split = {"about": "x", "countries": ["AAA", "BBB"], "pairs": [pair("AAA", "BBB", "AAA")] * 2}
    path = tmp_path / "split.json"
    so.write_split(split, path)
    assert json.loads(path.read_text()) == split
    assert sum('"a":"AAA"' in line for line in path.read_text().splitlines()) == 2


def test_a_grown_clip_reaches_past_its_outline_but_keeps_its_area(boundaries, monkeypatch):
    from ingest import boundaries as gb
    from ingest.boundaries import land_area_km2, outline_shape

    monkeypatch.setitem(gb.CLIP_GROW_DEG, "CCC", 0.1)
    monkeypatch.setitem(ov.CLIP_GROW_DEG, "CCC", 0.1)
    path = ov.clip_path("CCC", split_doc())  # in no pair, but grown
    assert path.parent.name == "clip"
    clip = outline_shape(path)
    assert clip.contains(box(9.95, 9.95, 11.05, 11.05))
    # Density is people over the outline's own area, not the sea it was grown into.
    assert land_area_km2(path) == pytest.approx(
        gb.area_km2(box(10, 10, 11, 11).__geo_interface__), abs=0.1
    )


def test_a_grown_clip_is_cut_at_its_neighbour_along_cgaz(boundaries, monkeypatch):
    from ingest import boundaries as gb
    from ingest.boundaries import outline_shape

    monkeypatch.setitem(gb.CLIP_GROW_DEG, "BBB", 0.2)
    monkeypatch.setitem(ov.CLIP_GROW_DEG, "BBB", 0.2)
    clip = outline_shape(ov.clip_path("BBB", split_doc(), units=UNITS))
    # Grown 0.2 it reaches into AAA's outline as far as x 0.8, but CGAZ gives AAA all of
    # that west of 1.5; the strips beside AAA's outline (sea, here) stay BBB's.
    assert ov.polygonal(clip.intersection(A)).equals(box(1.5, 0, 2, 1))
    assert clip.contains(box(0.9, 1.05, 3.1, 1.15))
    assert clip.bounds[2] == pytest.approx(3.2)
