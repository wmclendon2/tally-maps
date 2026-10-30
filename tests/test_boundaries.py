# Copyright (c) 2026 Woody McLendon
# SPDX-License-Identifier: MIT
import json

import pytest

from ingest.boundaries import unit_of


def fc(*names):
    return {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {"shapeName": n}, "geometry": None} for n in names
        ],
    }


def test_unit_of_picks_one_named_unit():
    doc = fc(
        "Guangdong",
        "Hong Kong Special Administrative Region",
        "Macau Special Administrative Region",
    )
    out = json.loads(unit_of(doc, "Hong Kong Special Administrative Region"))
    assert [f["properties"]["shapeName"] for f in out["features"]] == [
        "Hong Kong Special Administrative Region"
    ]


@pytest.mark.parametrize("names", [(), ("Puerto Rico", "Puerto Rico")])
def test_unit_of_refuses_missing_or_ambiguous(names):
    with pytest.raises(RuntimeError):
        unit_of(fc(*names), "Puerto Rico")


def square(w, s, e, n):
    return {"type": "Polygon", "coordinates": [[[w, s], [e, s], [e, n], [w, n], [w, s]]]}


def unit(iso3, geometry):
    return {"type": "Feature", "properties": {"ADM0_A3": iso3, "NAME": iso3}, "geometry": geometry}


def test_area_of_a_degree_square_at_the_equator_and_holes():
    from ingest.boundaries import area_km2

    # 111.195 km a side on a sphere of the mean radius.
    assert area_km2(square(0, 0, 1, 1)) == pytest.approx(12_364, rel=1e-3)
    holed = {
        "type": "Polygon",
        "coordinates": [
            *square(0, 0, 1, 1)["coordinates"],
            *square(0.25, 0.25, 0.75, 0.75)["coordinates"],
        ],
    }
    assert area_km2(holed) == pytest.approx(12_364 * 0.75, rel=1e-3)


def test_natural_earth_outline_is_grown_but_stops_at_the_neighbors():
    from shapely.geometry import Point, shape

    from ingest.boundaries import area_km2, from_natural_earth

    units = [unit("AAA", square(0, 0, 1, 1)), unit("BBB", square(1, 0, 2, 1))]
    doc = from_natural_earth("AAA", 0.1, units)
    clip = shape(doc["features"][0]["geometry"])
    assert clip.contains(Point(-0.05, 0.5))  # the sea next to the coast is taken in
    assert not clip.contains(Point(1.05, 0.5))  # the neighbor's land is not
    assert doc["tally"]["land_area_km2"] == pytest.approx(area_km2(square(0, 0, 1, 1)), abs=0.1)


def test_natural_earth_outline_found_by_its_gu_or_iso_code():
    from shapely.geometry import Point, shape

    from ingest.boundaries import from_natural_earth

    # Christmas Island and Cocos are both ADM0 "IOA"; Aland is ADM0 "ALD", ISO "ALA".
    christmas = unit("IOA", square(0, 0, 1, 1))
    christmas["properties"]["GU_A3"] = "CXR"
    cocos = unit("IOA", square(5, 0, 6, 1))
    cocos["properties"]["GU_A3"] = "CCK"
    aland = unit("ALD", square(10, 0, 11, 1))
    aland["properties"]["ISO_A3"] = "ALA"
    units = [christmas, cocos, aland]
    clip = shape(from_natural_earth("CXR", 0.1, units)["features"][0]["geometry"])
    assert clip.contains(Point(0.5, 0.5)) and not clip.contains(Point(5.5, 0.5))
    clip = shape(from_natural_earth("ALA", 0.1, units)["features"][0]["geometry"])
    assert clip.contains(Point(10.5, 0.5))


def test_natural_earth_outline_replaces_an_old_geoboundaries_cache(tmp_path, monkeypatch):
    from ingest import boundaries

    monkeypatch.setattr(boundaries, "boundary_path", lambda iso3, adm=0: tmp_path / f"{iso3}.json")
    monkeypatch.setattr(
        boundaries,
        "from_natural_earth",
        lambda iso3, d: {
            "type": "FeatureCollection",
            "tally": {"land_area_km2": 5.0},
            "features": [],
        },
    )
    (tmp_path / "VIR.json").write_text(json.dumps(fc("St. Croix")))
    path = boundaries.fetch_boundary("VIR")
    assert boundaries.land_area_km2(path) == 5.0


def test_natural_earth_outline_can_take_a_part_of_another_unit(monkeypatch):
    from shapely.geometry import Point, shape

    from ingest import boundaries

    # Like Western Sahara: AAA's own unit is the eastern strip, and BBB's unit reaches
    # over the west of it; AAA takes BBB's part south of y 1.
    monkeypatch.setattr(boundaries, "NE_PART_OF", {"AAA": ("BBB", 1)})
    units = [unit("AAA", square(1, 0, 2, 1)), unit("BBB", square(0, 0, 1, 2))]
    doc = boundaries.from_natural_earth("AAA", 0.1, units)
    clip = shape(doc["features"][0]["geometry"])
    assert clip.contains(Point(0.5, 0.5)) and clip.contains(Point(1.5, 0.5))
    assert not clip.contains(Point(0.5, 1.5))  # BBB keeps the rest of its unit
    expected = boundaries.area_km2(square(0, 0, 2, 1))
    assert doc["tally"]["land_area_km2"] == pytest.approx(expected, abs=0.5)


def test_natural_earth_outline_is_redone_when_how_it_is_made_changes(tmp_path, monkeypatch):
    from ingest import boundaries

    monkeypatch.setattr(boundaries, "boundary_path", lambda iso3, adm=0: tmp_path / f"{iso3}.json")
    calls = []

    def made(iso3, d):
        calls.append(d)
        return {"type": "FeatureCollection", "tally": {"grown_deg": d}, "features": []}

    monkeypatch.setattr(boundaries, "from_natural_earth", made)
    boundaries.fetch_boundary("VIR")
    boundaries.fetch_boundary("VIR")
    assert calls == [0.05]  # cached
    # A cache made another way (here, another grow) has another key: redone.
    monkeypatch.setitem(boundaries.ADM0_FROM_NATURAL_EARTH, "VIR", 0.1)
    path = boundaries.fetch_boundary("VIR")
    assert calls == [0.05, 0.1] and json.loads(path.read_text())["tally"]["grown_deg"] == 0.1


def test_an_outline_is_cut_where_a_place_takes_a_part_of_it(tmp_path, monkeypatch):
    from shapely.geometry import box, mapping

    from ingest import boundaries

    # Like Morocco's outline, which holds all of Western Sahara south of 27 deg 40'.
    src = tmp_path / "BBB_ADM0.geojson"
    src.write_text(json.dumps({**fc("B"), "features": [
        {"type": "Feature", "properties": {"shapeName": "B"}, "geometry": mapping(box(0, 0, 1, 2))}
    ]}))  # fmt: skip
    monkeypatch.setattr(boundaries, "fetch_boundary", lambda iso3: src)
    monkeypatch.setattr(boundaries, "BOUNDARY_DIR", tmp_path)
    monkeypatch.setattr(boundaries, "NE_PART_OF", {"AAA": ("BBB", 1)})
    path = boundaries.outline_path("BBB")
    assert path != src and boundaries.outline_shape(path).equals(box(0, 1, 1, 2))
    assert boundaries.land_area_km2(path) == pytest.approx(boundaries.area_km2(square(0, 1, 1, 2)))
    assert boundaries.outline_path("BBB") == path  # cached
    assert boundaries.outline_path("AAA") == src  # nothing taken from it


def test_every_worldpop_country_has_its_clip_grown():
    from ingest.boundaries import ADM0_FROM_NATURAL_EARTH, CLIP_GROW_DEG
    from ingest.config import WORLDPOP_COUNTRIES

    assert set(CLIP_GROW_DEG) == WORLDPOP_COUNTRIES
    assert CLIP_GROW_DEG["CAN"] == 0.1 and CLIP_GROW_DEG["GRL"] == ADM0_FROM_NATURAL_EARTH["GRL"]
