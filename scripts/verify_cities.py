# Copyright (c) 2026 Woody McLendon
# SPDX-License-Identifier: MIT
"""Positional check: are the people in the right places, not just the right number?

A national total is blind to where anyone lives. A flipped lat/lng, a half-pixel
transform offset, a raster read bottom-up or a wrong CRS origin all still sum to
26.5 million for Cameroon. Only a test that looks at *location* catches them.

    uv run python scripts/verify_cities.py
    uv run python scripts/verify_cities.py --country CMR --min-pop 0
    uv run python scripts/verify_cities.py --offline

Ground truth is Natural Earth's populated places (public domain), checked in at
reference/cities.json -- a source with no connection to Meta, WorldPop, HDX or
geoBoundaries, so agreement means something.

For each city the script sums the built packs over a box around the published point, the
way the browser would, and measures:

  * offset -- how far the population-weighted centroid of the box sits from that
    point, as an east/north vector in km.
  * contrast -- density in the core (within core_km) over density in the outer
    annulus. A real city is many times denser than its own outskirts; population
    smeared evenly across a country would score ~1.
  * magnitude -- box total against Natural Earth's pop_max, checked only loosely.
    Its estimates are dated and its pop_min is unreliable (Lagos is listed as
    1,536), so this catches gross errors and nothing finer.

The verdict rests on the *aggregate*, because a georeferencing fault is systematic:
every city moves the same way. Individual cities are noisy -- a coastal city loses
half its box to the sea, a sprawling one has its mass off-centre, a Natural Earth
point may sit a few km off downtown -- and those errors point in random directions,
so they cancel. A real shift does not cancel.

  mean offset vector  -- near zero when the noise is random, large under a shift.
  systematic fraction -- |mean vector| / mean|vector|. Near 0 means the offsets
    disagree with each other (healthy scatter); near 1 means they all point the
    same way, which is the signature of a georeferencing bug.

What counts as "near zero" depends on how many cities were scored: averaging n
random offsets leaves a residue of order 1/sqrt(n), so the same 0.6 km mean vector
is unremarkable over 100 cities and alarming over 7. Rather than hard-code a
distance, the script derives the null from the observed scatter -- the mean of n
independent isotropic offsets is Rayleigh distributed with scale sigma/sqrt(n),
where 2*sigma^2 is the mean squared offset -- and reports the probability of a mean
vector at least this long arising from noise alone. That is what --min-p gates on,
so the test keeps the same sensitivity whether it is run over one country or fifty.

A shift too large to fit in the box cannot move the centroid any further, so it
shows up instead as the total collapsing and contrast falling to ~1.

The global figure has one blind spot, and it is the likeliest real fault: every
country is built from its own HRSL raster, so a single misregistered file moves
only that country's cities. Thirteen shifted cities among a hundred barely nudge the
global mean. The script therefore also tests each country on its own, against a null
built by reshuffling which country each city belongs to and taking the largest
normalised offset in every shuffle -- which handles both the heavy tails and the
~50 simultaneous comparisons without a separate correction.

Power depends on how many cities a country has: with 13 it detects an 8 km shift
every time, with 3 it needs ~20 km, and below --min-cities it is not tested at all.
At the default --min-pop only about a dozen countries qualify; --min-pop 0 roughly
doubles that, at 202 cities instead of 100.

--offline lists the reference places and their boxes from the checked-in reference alone,
with no packs.
"""

from __future__ import annotations

import json
import math
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any

import h3
import typer
from rich.console import Console
from rich.table import Table
from shapely.geometry import box

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ingest.config import SOURCES

console = Console()
app = typer.Typer(add_completion=False)

REFERENCE = Path(__file__).resolve().parent.parent / "reference" / "cities.json"
EARTH_R_KM = 6371.0088

# Natural Earth codes disputed and partly-recognised territories separately; our data
# follows geoBoundaries/HDX, which file them under the ISO3 of the recognised state.
ALIASES = {
    "SOL": "SOM",  # Somaliland
    "SAH": "ESH",  # Western Sahara
    "PSX": "PSE",  # Palestinian Territories
    "KOS": "XKX",  # Kosovo (no ISO3; XKX is the usual stand-in)
}
# Research stations on a continent with no population raster.
SKIP_ISO3 = {"ATA"}


@dataclass(frozen=True)
class City:
    name: str
    iso3: str
    lat: float
    lng: float
    pop_max: int


def load_cities(
    path: Path = REFERENCE, *, countries: set[str] | None = None, min_pop: int = 0
) -> list[City]:
    doc = json.loads(path.read_text())
    out = []
    for c in doc["cities"]:
        iso3 = ALIASES.get(c["iso3"], c["iso3"])
        if iso3 in SKIP_ISO3 or c["pop_max"] < min_pop:
            continue
        if countries is not None and iso3 not in countries:
            continue
        out.append(City(c["name"], iso3, float(c["lat"]), float(c["lng"]), int(c["pop_max"])))
    return sorted(out, key=lambda c: (-c.pop_max, c.iso3, c.name))


def offset_km(lat0: float, lng0: float, lat: float, lng: float) -> tuple[float, float]:
    """East/north offset in km from one point to another. Flat-earth, fine over ~50 km."""
    north = math.radians(lat - lat0) * EARTH_R_KM
    east = math.radians(lng - lng0) * EARTH_R_KM * math.cos(math.radians((lat + lat0) / 2))
    return east, north


def city_box(lat: float, lng: float, radius_km: float):
    """Square box of +/- radius_km around a point, widened in longitude by latitude."""
    dlat = math.degrees(radius_km / EARTH_R_KM)
    coslat = max(math.cos(math.radians(lat)), 0.01)  # guard the poles
    dlng = math.degrees(radius_km / (EARTH_R_KM * coslat))
    return box(lng - dlng, lat - dlat, lng + dlng, lat + dlat)


@dataclass
class CityResult:
    city: City
    total: float
    east_km: float
    north_km: float
    contrast: float
    core_density: float
    ring_density: float
    cells: int

    @property
    def offset_km(self) -> float:
        return math.hypot(self.east_km, self.north_km)

    @property
    def pop_ratio(self) -> float:
        return self.total / self.city.pop_max if self.city.pop_max else math.nan


def score_city(city: City, cells: list[dict[str, Any]], *, core_km: float, ring_km: float):
    """Turn one /population response into an offset vector and a core/outskirts contrast."""
    total = 0.0
    wlat = wlng = 0.0
    core_pop = core_area = ring_pop = ring_area = 0.0
    for c in cells:
        pop = float(c["population"])
        lat, lng = h3.cell_to_latlng(c["h3"])
        area = h3.cell_area(c["h3"], "km^2")
        total += pop
        wlat += pop * lat
        wlng += pop * lng
        d = math.hypot(*offset_km(city.lat, city.lng, lat, lng))
        if d <= core_km:
            core_pop += pop
            core_area += area
        elif d >= ring_km:
            ring_pop += pop
            ring_area += area
    if total <= 0:
        return CityResult(city, 0.0, math.nan, math.nan, math.nan, 0.0, 0.0, len(cells))
    east, north = offset_km(city.lat, city.lng, wlat / total, wlng / total)
    core_density = core_pop / core_area if core_area else 0.0
    ring_density = ring_pop / ring_area if ring_area else 0.0
    # An empty annulus means the countryside is truly empty, which is maximal contrast.
    contrast = core_density / ring_density if ring_density > 0 else math.inf
    return CityResult(city, total, east, north, contrast, core_density, ring_density, len(cells))


def pack_cells(store, geom, resolution: int) -> list[dict[str, Any]]:
    """The populated cells in `geom` from the built packs, each summed across every
    country's pack that holds it (a box near a border spans two)."""
    import numpy as np

    ids = np.array([h3.str_to_int(c) for c in h3.geo_to_cells(geom, resolution)], dtype=np.uint64)
    if not ids.size:
        return []
    total = np.zeros(ids.size)
    for pops in store.by_country(ids, resolution, store.countries_for(ids)).values():
        total += pops
    return [
        {"h3": h3.int_to_str(int(c)), "population": float(p)}
        for c, p in zip(ids.tolist(), total.tolist(), strict=True)
        if p > 0
    ]


@dataclass
class Aggregate:
    n: int
    mean_east: float
    mean_north: float
    mean_magnitude: float
    mean_square: float  # mean of |offset|^2, the scatter the null is built from
    median_offset: float
    median_contrast: float

    @property
    def mean_vector_km(self) -> float:
        return math.hypot(self.mean_east, self.mean_north)

    @property
    def systematic(self) -> float:
        """0 = offsets point every which way (noise). 1 = they all agree (a shift)."""
        return self.mean_vector_km / self.mean_magnitude if self.mean_magnitude else 0.0

    @property
    def p_value(self) -> float:
        """Chance of a mean vector this long if the offsets were pure isotropic noise.

        Sum n independent 2-D offsets with no real shift and the mean is Rayleigh
        distributed with scale sigma/sqrt(n), where 2*sigma^2 is the mean squared
        offset. So the bar scales as 1/sqrt(n): a fixed kilometre threshold would
        wave through a shift in a 100-city run and cry wolf on a 7-city one.
        """
        if self.n < 2 or self.mean_square <= 0:
            return 1.0
        scale_sq = (self.mean_square / 2) / self.n
        return math.exp(-(self.mean_vector_km**2) / (2 * scale_sq))

    @property
    def bearing(self) -> str:
        if self.mean_vector_km < 1e-9:
            return "-"
        deg = (math.degrees(math.atan2(self.mean_east, self.mean_north)) + 360) % 360
        points = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
        return points[round(deg / 45) % 8]


def aggregate(results: list[CityResult]) -> Aggregate | None:
    usable = [r for r in results if r.total > 0 and not math.isnan(r.east_km)]
    if not usable:
        return None
    # A city with nobody in its outer ring (Macau: the ring is sea and mainland China,
    # which its raster leaves empty) has an infinite contrast; with only such cities there
    # is no finite median.
    finite = [r.contrast for r in usable if not math.isinf(r.contrast)]
    return Aggregate(
        n=len(usable),
        mean_east=statistics.fmean(r.east_km for r in usable),
        mean_north=statistics.fmean(r.north_km for r in usable),
        mean_magnitude=statistics.fmean(r.offset_km for r in usable),
        mean_square=statistics.fmean(r.offset_km**2 for r in usable),
        median_offset=statistics.median(r.offset_km for r in usable),
        median_contrast=statistics.median(finite) if finite else math.inf,
    )


@dataclass
class CountryStat:
    iso3: str
    n: int
    mean_east: float
    mean_north: float
    median_contrast: float
    p_value: float = 1.0  # filled in by the permutation test, uncorrected
    tested: bool = False

    @property
    def mean_vector_km(self) -> float:
        return math.hypot(self.mean_east, self.mean_north)

    @property
    def t(self) -> float:
        """|mean vector| * sqrt(n): comparable across countries of different sizes."""
        return self.mean_vector_km * math.sqrt(self.n)

    @property
    def bearing(self) -> str:
        if self.mean_vector_km < 1e-9:
            return "-"
        deg = (math.degrees(math.atan2(self.mean_east, self.mean_north)) + 360) % 360
        return ["N", "NE", "E", "SE", "S", "SW", "W", "NW"][round(deg / 45) % 8]


def country_stats(results: list[CityResult], *, min_cities: int) -> list[CountryStat]:
    """One offset vector per country. Each country is a separate HRSL raster, so a
    misregistered file shows up here even when 90 healthy cities bury it globally."""
    groups: dict[str, list[CityResult]] = {}
    for r in results:
        if r.total > 0 and not math.isnan(r.east_km):
            groups.setdefault(r.city.iso3, []).append(r)
    out = []
    for iso3, rs in sorted(groups.items()):
        finite = [r.contrast for r in rs if not math.isinf(r.contrast)]
        out.append(
            CountryStat(
                iso3=iso3,
                n=len(rs),
                mean_east=statistics.fmean(r.east_km for r in rs),
                mean_north=statistics.fmean(r.north_km for r in rs),
                median_contrast=statistics.median(finite) if finite else math.inf,
                tested=len(rs) >= min_cities,
            )
        )
    return sorted(out, key=lambda c: -c.t)


def permutation_test(
    results: list[CityResult],
    stats: list[CountryStat],
    *,
    permutations: int,
    seed: int,
) -> float:
    """Family-wise p for "some country is shifted", by shuffling cities between countries.

    Two things make a parametric test awkward here: the offsets are heavy-tailed, so a
    Rayleigh null misstates the odds, and testing ~50 countries at once would throw two
    or three false alarms a run. Reshuffling which country each city belongs to fixes
    both. It preserves the observed spread exactly, and taking the largest t across all
    countries in every shuffle builds the null for the *worst* country, so no multiple
    comparison correction is needed on top.

    Any global bias survives the shuffle too, so this asks whether a country stands out
    from the others rather than whether it differs from zero.
    """
    import numpy as np

    usable = [r for r in results if r.total > 0 and not math.isnan(r.east_km)]
    eligible = [c for c in stats if c.tested]
    if not eligible or len(usable) < 3:
        return 1.0

    by_iso: dict[str, list[CityResult]] = {}
    for r in usable:
        by_iso.setdefault(r.city.iso3, []).append(r)
    order = [c.iso3 for c in stats]
    east = np.array([r.east_km for iso in order for r in by_iso[iso]])
    north = np.array([r.north_km for iso in order for r in by_iso[iso]])
    counts = np.array([c.n for c in stats])
    starts = np.concatenate(([0], np.cumsum(counts)[:-1]))
    mask = np.array([c.tested for c in stats])

    rng = np.random.default_rng(seed)
    idx = rng.permuted(np.tile(np.arange(east.size), (permutations, 1)), axis=1)
    me = np.add.reduceat(east[idx], starts, axis=1) / counts
    mn = np.add.reduceat(north[idx], starts, axis=1) / counts
    t = np.hypot(me, mn) * np.sqrt(counts)

    observed = np.array([c.t for c in stats])
    for i, c in enumerate(stats):  # uncorrected, for diagnosis only
        c.p_value = float((t[:, i] >= observed[i]).mean()) if c.tested else 1.0
    # +1 on both sides: a permutation test can never honestly report p = 0.
    null_max = t[:, mask].max(axis=1)
    return float((1 + (null_max >= observed[mask].max()).sum()) / (permutations + 1))


def report_countries(
    stats: list[CountryStat],
    family_p: float,
    *,
    min_p: float,
    min_cities: int,
    show: int,
) -> bool:
    tested = [c for c in stats if c.tested]
    if not tested:
        console.print(f"[yellow]no country has {min_cities}+ cities; skipping breakdown[/yellow]")
        return True
    t = Table(title=f"per-country offsets ({len(tested)} countries with {min_cities}+ cities)")
    for col in ("ISO3", "cities", "mean vector", "bearing", "p", "contrast"):
        t.add_column(col, justify="right" if col != "ISO3" else "left")
    for c in tested[:show]:
        flag = "[red]" if c.p_value < min_p else ""
        end = "[/red]" if flag else ""
        t.add_row(
            c.iso3,
            str(c.n),
            f"{flag}{c.mean_vector_km:.1f} km{end}",
            c.bearing,
            f"{c.p_value:.3f}",
            "inf" if math.isinf(c.median_contrast) else f"{c.median_contrast:,.1f}x",
        )
    console.print(t)
    skipped = [c for c in stats if not c.tested]
    if skipped:
        console.print(
            f"[dim]not tested ({len(skipped)} countries under {min_cities} cities): "
            + " ".join(c.iso3 for c in skipped)
            + "[/dim]"
        )
    worst = tested[0]
    ok = family_p >= min_p
    console.print(
        f"worst country {worst.iso3} ({worst.n} cities, {worst.mean_vector_km:.1f} km "
        f"{worst.bearing}); family-wise p = {family_p:.4f} "
        + (
            "[green]-- no country stands out[/green]"
            if ok
            else f"[red]-- {worst.iso3} looks misregistered[/red]"
        )
    )
    return ok


def report(
    results: list[CityResult],
    agg: Aggregate | None,
    *,
    source: str,
    resolution: int,
    min_p: float,
    min_contrast: float,
    show: int,
) -> bool:
    t = Table(title=f"city positions · {source} · res {resolution}")
    for col, just in (
        ("city", "left"),
        ("ISO3", "left"),
        ("NE pop_max", "right"),
        ("box total", "right"),
        ("ratio", "right"),
        ("offset km", "right"),
        ("E/N km", "right"),
        ("contrast", "right"),
    ):
        t.add_column(col, justify=just)
    for r in sorted(results, key=lambda r: -r.offset_km)[:show]:
        contrast = "inf" if math.isinf(r.contrast) else f"{r.contrast:,.1f}"
        t.add_row(
            r.city.name,
            r.city.iso3,
            f"{r.city.pop_max:,}",
            f"{r.total:,.0f}",
            f"{r.pop_ratio:.2f}",
            f"{r.offset_km:.1f}",
            f"{r.east_km:+.1f}/{r.north_km:+.1f}",
            contrast,
        )
    console.print(t)
    console.print(f"(worst {min(show, len(results))} of {len(results)} by offset)")

    if agg is None:
        console.print("[red]no city returned any population at all[/red]")
        return False

    ok = True
    s = Table(title="aggregate", show_header=False)
    s.add_column("check")
    s.add_column("value", justify="right")
    s.add_column("verdict")
    s.add_row("cities scored", f"{agg.n:,}", "")
    s.add_row("median offset", f"{agg.median_offset:.1f} km", "")
    s.add_row("mean |offset|", f"{agg.mean_magnitude:.1f} km", "")

    s.add_row("mean offset vector", f"{agg.mean_vector_km:.1f} km {agg.bearing}", "")
    s.add_row("systematic fraction", f"{agg.systematic:.2f}", "")
    # The null bar moves with the sample size, so compare against it rather than a
    # fixed distance: 0.6 km over 100 cities is noise, over 7 cities it is not.
    expected = math.sqrt((agg.mean_square / 2) / agg.n) * math.sqrt(math.pi / 2)
    s.add_row("  expected if pure noise", f"{expected:.1f} km", f"n = {agg.n}")
    shift_ok = agg.p_value >= min_p
    ok &= shift_ok
    s.add_row(
        "  probability under noise",
        f"p = {agg.p_value:.3g}",
        "[green]consistent with scatter[/green]"
        if shift_ok
        else f"[red]p < {min_p:g}: shifted {agg.bearing}[/red]",
    )
    con_ok = agg.median_contrast >= min_contrast
    ok &= con_ok
    s.add_row(
        "median core/outskirts contrast",
        f"{agg.median_contrast:,.1f}x",
        "[green]OK[/green]" if con_ok else f"[red]< {min_contrast}: not concentrated[/red]",
    )
    empty = [r for r in results if r.total <= 0]
    if empty:
        ok = False
        s.add_row(
            "cities with no people",
            f"{len(empty)}",
            "[red]" + ", ".join(f"{r.city.name} ({r.city.iso3})" for r in empty[:6]) + "[/red]",
        )
    console.print(s)
    return ok


@app.command()
def main(
    country: Annotated[
        list[str] | None, typer.Option(help="Limit to these ISO3 codes; default every built one")
    ] = None,
    source: Annotated[str, typer.Option(help="Data source key")] = "meta_hrsl",
    packs: Annotated[Path | None, typer.Option(help="Pack directory (default data/packs)")] = None,
    resolution: Annotated[int, typer.Option(help="H3 resolution for the city boxes")] = 8,
    radius_km: Annotated[float, typer.Option(help="Half-width of the box around each city")] = 25.0,
    core_km: Annotated[float, typer.Option(help="Radius counted as the city core")] = 5.0,
    ring_km: Annotated[float, typer.Option(help="Inner radius of the outskirts annulus")] = 20.0,
    min_pop: Annotated[
        int, typer.Option(help="Skip places below this Natural Earth pop_max")
    ] = 300_000,
    limit: Annotated[int | None, typer.Option(help="Stop after this many cities")] = None,
    min_p: Annotated[
        float, typer.Option(help="Fail below this probability that the shift is mere noise")
    ] = 0.001,
    min_contrast: Annotated[float, typer.Option(help="Fail below this median contrast")] = 5.0,
    min_cities: Annotated[
        int, typer.Option(help="Cities a country needs before it is tested on its own")
    ] = 3,
    permutations: Annotated[int, typer.Option(help="Shuffles for the per-country null")] = 20_000,
    seed: Annotated[int, typer.Option(help="Seed for the permutation test")] = 0,
    show: Annotated[int, typer.Option(help="Rows of per-city detail to print")] = 20,
    offline: Annotated[bool, typer.Option(help="Reference and geometry only; no packs")] = False,
) -> None:
    if source not in SOURCES:
        raise typer.BadParameter(f"unknown source {source!r}; have {list(SOURCES)}")
    if not REFERENCE.exists():
        raise typer.BadParameter(f"missing reference {REFERENCE}")
    if not (core_km < ring_km <= radius_km):
        raise typer.BadParameter("need core_km < ring_km <= radius_km")

    wanted = {c.upper() for c in country} if country else None
    if offline:
        cities = load_cities(countries=wanted, min_pop=min_pop)
        console.print(f"reference holds {len(cities):,} places matching the filters")
        for c in cities[:show]:
            b = city_box(c.lat, c.lng, radius_km)
            console.print(
                f"  {c.name} ({c.iso3}) {c.lat:.3f},{c.lng:.3f} "
                f"pop_max {c.pop_max:,} box {b.bounds[0]:.3f}..{b.bounds[2]:.3f}",
                highlight=False,
            )
        return

    from packstore import PackStore

    store = PackStore(packs) if packs else PackStore()
    sources = {e["source"] for e in store.entries.values()}
    if source not in sources:
        raise typer.BadParameter(f"no {source} packs; have {sorted(sources)}")
    built = {c.upper() for c, e in store.entries.items() if e["source"] == source}
    if wanted:
        missing = wanted - built
        if missing:
            raise typer.BadParameter(f"no pack built for: {sorted(missing)}")
        built = wanted
    base = store.base_resolution(source)
    if resolution > base:
        raise typer.BadParameter(f"res {resolution} is finer than {source}'s base res {base}")

    cities = load_cities(countries=built, min_pop=min_pop)
    if limit:
        cities = cities[:limit]
    if not cities:
        raise typer.BadParameter("no reference cities match; lower --min-pop")
    console.print(f"checking {len(cities):,} cities across {len(built)} countries")

    results: list[CityResult] = []
    for i, c in enumerate(cities, 1):
        box_ = city_box(c.lat, c.lng, radius_km)
        cells = pack_cells(store, box_, resolution)
        r = score_city(c, cells, core_km=core_km, ring_km=ring_km)
        results.append(r)
        if i % 25 == 0 or i == len(cities):
            console.print(f"  {i}/{len(cities)}", highlight=False)

    ok = report(
        results,
        aggregate(results),
        source=source,
        resolution=resolution,
        min_p=min_p,
        min_contrast=min_contrast,
        show=show,
    )
    # The global mean hides a single bad raster: one country is a handful of cities
    # among a hundred, so its offsets average away. Test each country on its own.
    stats = country_stats(results, min_cities=min_cities)
    family_p = permutation_test(results, stats, permutations=permutations, seed=seed)
    ok &= report_countries(stats, family_p, min_p=min_p, min_cities=min_cities, show=show)
    if not ok:
        raise typer.Exit(code=1)
    console.print("[green]people are where they should be[/green]")


if __name__ == "__main__":
    app()
