# Copyright (c) 2026 Woody McLendon
# SPDX-License-Identifier: MIT
"""Offline checks for the city position test, including proof that it detects a shift.

A test that claims to catch a georeferencing fault is worth nothing until it is shown
failing on one, so the synthetic cases below move a city and assert the detector notices.
"""

import json
import math

import h3
import numpy as np
import pytest

from scripts.verify_cities import (
    Aggregate,
    City,
    CityResult,
    aggregate,
    city_box,
    load_cities,
    offset_km,
    score_city,
)

RES = 8
CITY = City("Testville", "XXX", 4.0, 9.7, 1_000_000)


def synth_cells(blob_lat, blob_lng, *, box_lat=CITY.lat, box_lng=CITY.lng, radius_km=25.0):
    """A dense blob on a thin rural background, inside a box centred on the city point."""
    cells = h3.geo_to_cells(city_box(box_lat, box_lng, radius_km), RES)
    out = []
    for c in cells:
        lat, lng = h3.cell_to_latlng(c)
        d = math.hypot(*offset_km(blob_lat, blob_lng, lat, lng))
        out.append({"h3": c, "population": 2000.0 if d <= 4.0 else 1.0})
    return out


def test_offset_km_matches_known_distances():
    east, north = offset_km(0.0, 0.0, 1.0, 0.0)
    assert north == pytest.approx(111.19, abs=0.1) and east == pytest.approx(0.0, abs=1e-9)
    east, north = offset_km(0.0, 0.0, 0.0, 1.0)
    assert east == pytest.approx(111.19, abs=0.1) and north == pytest.approx(0.0, abs=1e-9)
    # A degree of longitude shrinks with the cosine of latitude.
    east, _ = offset_km(60.0, 0.0, 60.0, 1.0)
    assert east == pytest.approx(111.19 * 0.5, abs=0.5)


def test_city_box_is_square_in_km():
    b = city_box(4.0, 9.7, 25.0)
    minx, miny, maxx, maxy = b.bounds
    width, _ = offset_km(4.0, minx, 4.0, maxx)
    _, height = offset_km(miny, 9.7, maxy, 9.7)
    assert width == pytest.approx(50.0, abs=0.5)
    assert height == pytest.approx(50.0, abs=0.5)


def test_correctly_placed_city_scores_near_zero_offset():
    r = score_city(CITY, synth_cells(CITY.lat, CITY.lng), core_km=5.0, ring_km=20.0)
    assert r.offset_km < 1.0
    assert r.contrast > 100, "a dense core on empty countryside should stand out"


def test_shifted_city_is_detected_with_the_right_bearing():
    """Move the people 10 km east; the measured offset must follow them."""
    east_deg = 10.0 / (111.19 * math.cos(math.radians(CITY.lat)))
    r = score_city(CITY, synth_cells(CITY.lat, CITY.lng + east_deg), core_km=5.0, ring_km=20.0)
    assert r.east_km == pytest.approx(10.0, rel=0.2)
    assert abs(r.north_km) < 1.0
    assert r.contrast < 5.0, "the core is now empty, so contrast should collapse"


def test_shift_beyond_the_box_empties_it():
    """A fault too big to fit in the box shows up as no people, not as a large offset."""
    r = score_city(CITY, synth_cells(CITY.lat + 5.0, CITY.lng), core_km=5.0, ring_km=20.0)
    assert r.contrast == pytest.approx(1.0, rel=0.01), "only flat background is left"
    assert r.total < 10_000


def _result(east, north):
    return CityResult(CITY, 1e6, east, north, 50.0, 500.0, 10.0, 100)


def test_random_offsets_read_as_scatter():
    """Real cities miss their reference point in every direction; that must not fail."""
    ring = [
        _result(8 * math.cos(a), 8 * math.sin(a)) for a in [i * math.tau / 12 for i in range(12)]
    ]
    agg = aggregate(ring)
    assert agg.mean_magnitude == pytest.approx(8.0, rel=0.01)
    assert agg.mean_vector_km < 0.5, "opposing errors cancel"
    assert agg.systematic < 0.1


def test_uniform_shift_reads_as_systematic():
    """The signature of a georeferencing bug: every city off the same way."""
    agg = aggregate([_result(7.0, 0.0) for _ in range(12)])
    assert agg.mean_vector_km == pytest.approx(7.0, rel=0.01)
    assert agg.systematic == pytest.approx(1.0, rel=0.01)
    assert agg.bearing == "E"


def test_bearing_names_the_direction():
    assert aggregate([_result(0.0, 5.0)]).bearing == "N"
    assert aggregate([_result(-5.0, 0.0)]).bearing == "W"
    assert aggregate([_result(0.0, -5.0)]).bearing == "S"


def test_empty_city_is_flagged_not_averaged():
    empty = CityResult(CITY, 0.0, math.nan, math.nan, math.nan, 0.0, 0.0, 0)
    agg = aggregate([empty, _result(3.0, 0.0)])
    assert agg.n == 1, "a city with no people must not pollute the mean"
    assert aggregate([empty]) is None


def test_load_cities_filters_by_country_and_population(tmp_path):
    path = tmp_path / "cities.json"
    path.write_text(
        json.dumps(
            {
                "cities": [
                    {"name": "Big", "iso3": "CMR", "lat": 4.0, "lng": 9.7, "pop_max": 900_000},
                    {"name": "Small", "iso3": "CMR", "lat": 5.0, "lng": 10.0, "pop_max": 1_000},
                    {"name": "Away", "iso3": "AGO", "lat": -8.8, "lng": 13.2, "pop_max": 900_000},
                ]
            }
        )
    )
    got = load_cities(path, countries={"CMR"}, min_pop=100_000)
    assert [c.name for c in got] == ["Big"]
    assert len(load_cities(path, min_pop=100_000)) == 2


def test_checked_in_reference_is_usable():
    cities = load_cities(min_pop=1_000_000)
    assert len(cities) > 100
    douala = next(c for c in cities if c.name == "Douala")
    assert douala.iso3 == "CMR"
    assert (douala.lat, douala.lng) == pytest.approx((4.06, 9.71), abs=0.05)


def test_natural_earth_codes_are_mapped_to_iso3(tmp_path):
    """Somaliland and Western Sahara are filed under the recognised state's ISO3."""
    path = tmp_path / "cities.json"
    path.write_text(
        json.dumps(
            {
                "cities": [
                    {
                        "name": "Hargeisa",
                        "iso3": "SOL",
                        "lat": 9.6,
                        "lng": 44.1,
                        "pop_max": 477_876,
                    },
                    {
                        "name": "McMurdo",
                        "iso3": "ATA",
                        "lat": -77.8,
                        "lng": 166.7,
                        "pop_max": 500_000,
                    },
                ]
            }
        )
    )
    got = load_cities(path, min_pop=100_000)
    assert [(c.name, c.iso3) for c in got] == [("Hargeisa", "SOM")]
    assert load_cities(path, countries={"SOM"}, min_pop=100_000)[0].name == "Hargeisa"


def _ring(n, radius=4.5, shift_east=0.0):
    """n offsets spread evenly around a circle, optionally with a real shift added."""
    return [
        CityResult(
            CITY,
            1e6,
            radius * math.cos(i * math.tau / n) + shift_east,
            radius * math.sin(i * math.tau / n),
            50.0,
            500.0,
            10.0,
            100,
        )
        for i in range(n)
    ]


def test_p_value_matches_a_monte_carlo_null():
    """The closed form is checked against simulation, not just asserted.

    Reproduces the first real run -- 100 cities, median offset 3.8 km, mean 4.5 --
    and confirms that its 0.6 km mean vector is the unremarkable thing the formula
    says it is. Note the null depends on the mean *squared* offset, so a spread of
    magnitudes matters: a same-mean set of equal-length offsets gives a smaller null.
    """
    rng = np.random.default_rng(7)
    n, trials = 100, 20_000
    sigma = np.sqrt(2 * np.log(4.5 / 3.8))  # lognormal fit to median 3.8, mean 4.5
    mag = rng.lognormal(np.log(3.8), sigma, size=(trials, n))
    ang = rng.uniform(0, 2 * np.pi, size=(trials, n))
    vec = np.hypot((mag * np.cos(ang)).mean(1), (mag * np.sin(ang)).mean(1))
    empirical = float((vec >= 0.6).mean())

    agg = Aggregate(
        n=n,
        mean_east=0.6,
        mean_north=0.0,
        mean_magnitude=float(mag.mean()),
        mean_square=float((mag**2).mean()),
        median_offset=3.8,
        median_contrast=15.0,
    )
    assert agg.p_value == pytest.approx(empirical, abs=0.02)
    assert 0.2 < empirical < 0.4, "the observed run is squarely inside the null"


def test_same_offset_is_noise_at_n100_but_not_at_n7():
    """The whole point of deriving the null: the bar moves with the sample size."""
    big, small = aggregate(_ring(100)), aggregate(_ring(7))
    for agg in (big, small):
        agg.mean_east, agg.mean_north = 2.0, 0.0
    assert big.p_value < 0.001, "2 km over 100 cities cannot be noise"
    assert small.p_value > 0.05, "2 km over 7 cities easily is"


def test_real_shift_is_significant():
    agg = aggregate(_ring(100, shift_east=3.0))
    assert agg.mean_vector_km == pytest.approx(3.0, rel=0.01)
    assert agg.p_value < 1e-9
    assert agg.bearing == "E"


def test_clean_scatter_is_not_significant():
    agg = aggregate(_ring(100))
    assert agg.mean_vector_km < 0.01
    assert agg.p_value > 0.9


def test_p_value_is_defined_for_degenerate_input():
    assert aggregate([_result(0.0, 0.0)]).p_value == 1.0


# --- per-country breakdown -------------------------------------------------

from scripts.verify_cities import country_stats, permutation_test

# Roughly the real run: 100 cities over 52 countries, a few big ones and a long tail.
COUNTRY_SIZES = [13, 13, 8, 6, 5, 4, 4, 3, 3, 3] + [2] * 8 + [1] * 26


def synth_results(sizes, *, shift_iso=None, shift_east=0.0, seed=0):
    """Cities with realistic scatter, optionally with one country's raster displaced."""
    rng = np.random.default_rng(seed)
    sigma = np.sqrt(2 * np.log(4.5 / 3.8))  # matches the observed 3.8 km median
    out = []
    for i, k in enumerate(sizes):
        iso = f"C{i:02d}"
        mag = rng.lognormal(np.log(3.8), sigma, size=k)
        ang = rng.uniform(0, 2 * np.pi, size=k)
        for m, a in zip(mag, ang, strict=True):
            east = m * np.cos(a) + (shift_east if iso == shift_iso else 0.0)
            city = City(f"{iso}-{len(out)}", iso, 4.0, 9.7, 500_000)
            out.append(
                CityResult(city, 1e6, float(east), float(m * np.sin(a)), 20.0, 400.0, 20.0, 99)
            )
    return out


def test_country_stats_group_and_rank():
    stats = country_stats(synth_results([3, 3, 1]), min_cities=3)
    assert {c.iso3 for c in stats} == {"C00", "C01", "C02"}
    assert [c.tested for c in stats if c.iso3 == "C02"] == [False], "1 city is untestable"
    assert stats == sorted(stats, key=lambda c: -c.t), "ranked worst first"


def test_country_stats_ignore_cities_with_no_people():
    empty = CityResult(City("x", "C99", 0, 0, 1), 0.0, math.nan, math.nan, math.nan, 0, 0, 0)
    assert all(c.iso3 != "C99" for c in country_stats([*synth_results([3]), empty], min_cities=1))


def test_clean_data_does_not_flag_any_country():
    results = synth_results(COUNTRY_SIZES, seed=1)
    stats = country_stats(results, min_cities=3)
    p = permutation_test(results, stats, permutations=4000, seed=1)
    assert p > 0.05, f"no country is shifted, yet family-wise p = {p}"


def test_one_shifted_country_is_caught_among_ninety_healthy_cities():
    """The case the global statistic misses: a single misregistered raster."""
    results = synth_results(COUNTRY_SIZES, shift_iso="C00", shift_east=8.0, seed=2)

    # The global mean vector barely moves: 13 of 100 cities, diluted eightfold.
    assert aggregate(results).mean_vector_km < 1.5

    stats = country_stats(results, min_cities=3)
    p = permutation_test(results, stats, permutations=4000, seed=2)
    assert p < 0.01, f"shifted country not detected, family-wise p = {p}"
    assert stats[0].iso3 == "C00", "the shifted country should rank worst"
    assert stats[0].bearing == "E"


def test_family_wise_error_rate_is_controlled():
    """Testing ~20 countries at once must not manufacture false alarms."""
    alarms = sum(
        permutation_test(
            r := synth_results(COUNTRY_SIZES, seed=100 + i),
            country_stats(r, min_cities=3),
            permutations=2000,
            seed=100 + i,
        )
        < 0.05
        for i in range(20)
    )
    assert alarms <= 2, f"{alarms}/20 clean runs raised an alarm; expected about 1"


def test_countries_below_the_threshold_are_not_tested():
    results = synth_results([2, 2, 2], shift_iso="C00", shift_east=50.0, seed=3)
    stats = country_stats(results, min_cities=3)
    assert not any(c.tested for c in stats)
    assert permutation_test(results, stats, permutations=500, seed=3) == 1.0
