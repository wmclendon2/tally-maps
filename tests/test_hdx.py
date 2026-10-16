# Copyright (c) 2026 Woody McLendon
# SPDX-License-Identifier: MIT
import zipfile

from ingest.hdx import is_general_raster, pick_general
from ingest.pipeline import expand_rasters


def _pkg(groups, resources, name="pkg"):
    return {
        "name": name,
        "title": name,
        "groups": [{"name": g} for g in groups],
        "resources": [{"name": n, "url": f"https://x/{n}", "size": 1} for n in resources],
    }


def test_general_raster_detection():
    yes = (
        "cmr_general_2020_geotiff.zip",
        "ind_general_2025_tif.zip",
        "ken_general_2020.tif",
        "population_deu.geotiff.zip",
        "population_npl_2018-10-01_geotiff.zip",
        "jpn_population_2020_geotiff.zip",
        "population_turkey_2020_tif.zip",
        "AUS_northeast_2019-06-01_geotiff.zip",
        "USA_lat_24_lon_-81_geotiff.zip",
    )
    no = (
        "cmr_women_2020_geotiff.zip",
        "cmr_general_2020_csv.zip",
        "cmr_dg_metadata.json.zip",
        "DEU_men_geotiff.zip",
        "ind_female_2025_tiff.zip",
        "ind_male_2025_tiff.zip",
        "ind_children_under_five_2025_proportion_allocated_20205.zip",
        "AUS_northeast_youth_15_24_2019-06-01_geotiff.zip",
        "USA_lat_24_lon_-81_women_of_reproductive_age_15_49_geotiff.zip",
    )
    for n in yes:
        assert is_general_raster(n), n
    for n in no:
        assert not is_general_raster(n), n


def test_pick_newest_general_keeps_tiles_and_skips_bundles():
    pkg = _pkg(
        ["cmr"],
        [
            "population_cmr_2018-10-01_geotiff.zip",
            "cmr_general_2020_geotiff.zip",
            "cmr_men_2020_geotiff.zip",
        ],
    )
    c = pick_general(pkg)
    assert c.iso3 == "CMR" and c.year == 2020
    assert [r.name for r in c.resources] == ["cmr_general_2020_geotiff.zip"]
    tiles = pick_general(
        _pkg(
            ["aus"],
            [
                "AUS_northwest_2019-06-01_geotiff.zip",
                "AUS_northeast_2019-06-01_geotiff.zip",
                "AUS_northeast_men_2019-06-01_geotiff.zip",
            ],
        )
    )
    assert [r.name for r in tiles.resources] == [
        "AUS_northeast_2019-06-01_geotiff.zip",
        "AUS_northwest_2019-06-01_geotiff.zip",
    ]
    undated = pick_general(_pkg(["deu"], ["population_deu.geotiff.zip"]))
    assert undated.year == 0 and undated.size == 1
    assert pick_general(_pkg(["world"], ["population_af_2018-10-01.zip"])) is None
    assert pick_general(_pkg(["ind", "pak"], ["population_ind_pak_general.zip"])) is None
    assert pick_general(_pkg(["imn"], ["IMN_men_2019-08-03_csv.zip"])) is None


def test_expand_rasters_lists_zip_members_as_virtual_paths(tmp_path):
    z = tmp_path / "a.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("sub/b.tif", b"TIF")
        zf.writestr("a.tif", b"TIF")
        zf.writestr("readme.txt", b"no")
    got = expand_rasters([z, tmp_path / "plain.tif", "zip://x.zip!y.tif"])
    assert got == [
        f"zip://{z}!a.tif",
        f"zip://{z}!sub/b.tif",
        str(tmp_path / "plain.tif"),
        "zip://x.zip!y.tif",
    ]
