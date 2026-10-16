# Copyright (c) 2026 Woody McLendon
# SPDX-License-Identifier: MIT
"""The continent lookup backing `ingest.hdx --continent`."""

import pytest

from ingest import continents


def test_slug_normalises_what_a_user_might_type():
    assert continents.slug("North America") == "north-america"
    assert continents.slug(" AFRICA ") == "africa"
    assert continents.slug("South_America") == "south-america"


def test_every_continent_has_countries():
    table = continents.by_continent()
    assert set(table) == {
        "africa",
        "asia",
        "europe",
        "north-america",
        "oceania",
        "south-america",
    }
    assert all(len(v) > 5 for v in table.values())


def test_countries_in_is_case_and_space_insensitive():
    assert continents.countries_in("Africa") == continents.countries_in("africa")
    assert "USA" in continents.countries_in("North America")


def test_unknown_continent_lists_the_valid_options():
    with pytest.raises(KeyError, match="africa"):
        continents.countries_in("atlantis")


def test_french_overseas_departments_are_filed_under_africa():
    """Natural Earth folds them into France, but HDX ships them as their own ISO3."""
    africa = continents.countries_in("africa")
    assert "MYT" in africa and "REU" in africa


def test_continent_of_round_trips():
    for iso3 in ("CMR", "BRA", "JPN", "DEU", "AUS"):
        assert iso3 in continents.countries_in(continents.continent_of(iso3))
    assert continents.continent_of("ZZZ") is None


def test_no_country_is_on_two_continents():
    seen = [iso3 for members in continents.by_continent().values() for iso3 in members]
    assert len(seen) == len(set(seen))


def test_antarctica_and_open_ocean_are_excluded():
    """Neither has a population raster; listing them would only offer dead options."""
    assert "antarctica" not in continents.by_continent()
    assert continents.continent_of("ATA") is None


def test_open_ocean_countries_with_rasters_have_a_continent():
    """Natural Earth files these under "Seven seas (open ocean)"; HDX has a raster for each."""
    assert {"MUS", "SYC"} <= set(continents.countries_in("africa"))
    assert "MDV" in continents.countries_in("asia")
    assert continents.name_of("MUS") == "Mauritius"
