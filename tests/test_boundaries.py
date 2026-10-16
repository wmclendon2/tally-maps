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


def test_natural_earth_outline_is_grown_but_stops_at_the_neighbours():
    from shapely.geometry import Point, shape

    from ingest.boundaries import area_km2, from_natural_earth

    units = [unit("AAA", square(0, 0, 1, 1)), unit("BBB", square(1, 0, 2, 1))]
    doc = from_natural_earth("AAA", 0.1, units)
    clip = shape(doc["features"][0]["geometry"])
    assert clip.contains(Point(-0.05, 0.5))  # the sea next to the coast is taken in
    assert not clip.contains(Point(1.05, 0.5))  # the neighbour's land is not
    assert doc["tally"]["land_area_km2"] == pytest.approx(area_km2(square(0, 0, 1, 1)), abs=0.1)


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
