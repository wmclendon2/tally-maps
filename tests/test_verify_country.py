# Copyright (c) 2026 Woody McLendon
# SPDX-License-Identifier: MIT
"""Offline checks for the country verifier. No data/ files needed."""

import json

from shapely.geometry import box

import scripts.verify_country as vc

BIG_BOX = box(10.0, 2.0, 14.0, 6.0)


def test_load_outline_unwraps_a_feature_collection(tmp_path, monkeypatch):
    path = tmp_path / "XXX_ADM0.geojson"
    feature = {"type": "Feature", "geometry": BIG_BOX.__geo_interface__, "properties": {}}
    path.write_text(json.dumps({"type": "FeatureCollection", "features": [feature]}))
    monkeypatch.setattr(vc, "boundary_path", lambda iso3: path)
    assert vc.load_outline("XXX").equals(BIG_BOX)


def facts(**over) -> vc.Facts:
    base = {
        "tagged_at_res": 1000.0,
        "tagged_at_base": 1000.0,
        "base_resolution": 9,
        "inside": 950.0,
        "outside": 50.0,
        "by_other": {"NBR": 30.0},
        "catalogue_total": 1000.0,
    }
    return vc.Facts(**{**base, **over})


def test_the_border_identity_passes_when_it_balances():
    # polygon total = country total - loss + gain = 1000 - 50 + 30
    assert vc.report("XXX", "meta_hrsl", 7, 10, 980.0, facts(), None)


def test_the_border_identity_fails_when_people_go_missing():
    assert not vc.report("XXX", "meta_hrsl", 7, 10, 900.0, facts(), None)


def test_a_pyramid_level_that_loses_people_fails():
    assert not vc.report("XXX", "meta_hrsl", 7, 10, 980.0, facts(tagged_at_base=1100.0), None)
