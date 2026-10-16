# Verifying the data

Four checks, from "is the total right?" to "are people in the right places?". All of them
read the built packs in `data/packs/` (or a built `dist/`); `make verify`, `make parity`
and `make countries-check` run them.

| Check | Script | Catches |
| --- | --- | --- |
| Country totals | `verify_country.py` | Lost or double-counted people, broken packs |
| City positions | `verify_cities.py` | Shifted, flipped or misregistered rasters |
| Browser parity | `browser_parity.py` | `static.js` drifting from `ingest/resolve.py` |
| Country picker and memory | `browser_countries.py` | Wrong country totals in the browser, memory growth |

## Country totals

```bash
uv run python scripts/verify_country.py --country CMR
```

Rather than trusting that a pack's cells are its country's, this queries the country's own
geoBoundaries outline as a shape, the way a user would, and reconciles the answer exactly:

```
polygon_total == tagged_total - border_loss + cross_border_gain
```

`border_loss` is the country's cells whose centres fall outside its outline, and
`cross_border_gain` is the neighbours' cells whose centres fall inside it. It then checks
that every pyramid level sums to the same total, and that the total matches the catalogue.

- It sums every pack that holds a covered cell, as `static.js` does, and checks each
  part's hashes, header, footprint and per-level totals.
- The default resolution is base − 2 (7 for Meta). The outline is filled whole: Cameroon
  takes 3 s, Tanzania 3 minutes, and Brazil and South Africa are the slowest because their
  outlines are detailed.
- Cameroon: polygon 26,619,950, gain 338,993, loss 213,945. An independent database
  built from the same rasters gave identical figures (2026-10-13).
- Small islands show a large "net border error" at resolution 7 (Seychelles −26%, Mayotte
  −24%, Comoros −14%, Cape Verde −10%) because coarse cell centres fall in the sea. The
  identity still balances.
- Outlines that cross the 180° meridian (Fiji, Kiribati, New Zealand) are refused
  ("spans 360 deg"): a limit of this check, not of the data.

The first 53 countries passed on 2026-10-13, and the five territories clipped to Natural
Earth outlines passed when they were added.

## City positions

```bash
uv run python scripts/verify_cities.py [--country CMR] [--min-pop 0]
```

This is the only check that tests *where* people are. A national total stays the same if
the latitude and longitude are swapped, the raster is shifted half a pixel, or it is upside
down. Ground truth is Natural Earth's populated places (public domain, in
`reference/cities.json`), which are independent of Meta, WorldPop, HDX and geoBoundaries.
Only Natural Earth's `pop_max` is used, loosely, since `pop_min` is unreliable (Lagos 1,536).

**What it measures.** For each city, the offset of the population-weighted centre of a
box around it from the published point, and the density contrast between the core and the
outskirts.

**Global verdict.** Georeferencing faults are systematic, so the verdict is about the
whole set. The "systematic fraction" is |mean offset vector| / mean |offset|: near 0 for
healthy scatter, near 1 when every city is off in the same direction. The pass mark is
derived rather than fixed. Averaging n random offsets leaves a residue of order 1/√n, so
the script computes the null distribution (Rayleigh, scale σ/√n, from the observed scatter)
and gates on `--min-p` (default 0.001). A fixed distance would let a real shift through at
n = 100 and raise false alarms at n = 7. At n = 100 it detects a shift of about 3 km.

**Per country.** Each country is its own raster, so one misregistered file moves only its
own cities, and the global mean hides it (13 shifted cities in 100 move it by under
1.5 km). So each country is also tested against a permutation null: reshuffle which country
each city belongs to and take the largest normalised offset per shuffle. That handles the
heavy tails and the many simultaneous comparisons at once. Power depends on the number of
cities: 13 cities always catch an 8 km shift, 8 cities need 12 km, 3 cities need 20 km.
Few countries have three or more cities over the default `--min-pop 300000`, so run the
per-country pass with `--min-pop 0`.

**Results.**

| Run | Cities | Median offset | Global p | Worst country | Family-wise p |
| --- | ---: | ---: | ---: | --- | ---: |
| 2026-09-19, res 8, default min-pop | 100 | 3.8 km | 0.28 | | |
| 2026-09-19, `--min-pop 0` | 202 | | 0.028 | GIN 7.3 km NE (3 cities) | 0.27 |
| 2026-10-13, `--min-pop 0`, 53 countries | 248 | 3.0 km | 0.118 | GIN 7.3 km NE | 0.20 |

The 2026-10-13 run printed identical output from the packs and from the earlier database.
The first run
is the baseline: mean offset 4.5 km, mean vector 0.6 km east, systematic fraction 0.13,
median contrast 15×. A later run well outside that wants a look.

**Known false alarms.**

- **Coastal cities.** A box around a coastal city is partly sea, so the population centre
  sits inland of the published point. The land-area centroid of the outline alone
  reproduces these offsets (Annaba observed −3.7/−10.1 km vs land −4.0/−11.3; Toliara
  +9.7/+2.0 vs +11.0/+3.3; Benghazi +6.9/−4.4 vs +10.5/−5.1). Libya and Algeria both read
  "S" because inland from the Mediterranean is south. Globally this pull is 0.26 km east,
  about a third of the observed mean vector, and 64 of 202 boxes are over 10% water.
  Because this bias is fixed rather than random, the global p-value keeps falling as cities
  are added (0.28 to 0.028 when n doubled) and will eventually fail spuriously. Subtract
  the land-centroid expectation, or keep to boxes that are mostly land, before trusting it.
  The per-country test is unaffected: the bias is shared across shuffles and cancels.
- **Density ratio outliers** come from the reference data, not from errors. Bombo (75k
  listed, 33×) is 35 km from Kampala. Banjul (34×) is a tiny city proper inside a metro of
  1.4 million. Brazzaville (5.5×) picks up Kinshasa across the river, since the box sums
  every country.

Natural Earth 50m limits how many cities can be tested; its 10m populated places would
raise that.

## Browser parity

```bash
uv run --group browser python scripts/browser_parity.py [--dist DIR]
```

`scripts/static_parity.py` computes the Python answers (`ingest/resolve.py` over the packs)
for 18 shapes: one-part and multi-part countries (NGA, ETH, all of Brazil), islands (MUS,
SYC), borders (Garoua-Boulaï between CMR and CAF, COD/COG, KEN/UGA), open ocean, a shape
smaller than one cell, and Normal and Fine. `browser_parity.py` serves a built `dist/`, runs
each shape through `TallyStatic.query` in headless Chrome from empty storage, and compares
the resolution, cell counts and totals. A small build runs only the cases its countries
cover.

## Country picker and memory

```bash
uv run --group browser python scripts/browser_countries.py \
    [--dist DIR] [--country XXX] [--max-mb 200] [--no-stress]
```

Serves a built `dist/` (or `--url`) and, in headless Chrome from empty storage:

1. draws boxes over the USA, India, Brazil and large cities, at Normal and Fine;
2. runs `TallyStatic.country` on every country. Each answer must match the same level
   summed in Python from the pack files (resolution, populated cells, sum) and show the
   catalogue total, and the catalogue's `cells_by_resolution` must match the packs.

After every step it forces garbage collection and reads the heap, including the typed
arrays, and fails over `--max-mb`. It also warns when a country's `area_km2` is outside
0.6–1.6× Natural Earth's area (countries over 1,000 km² only). `country_resolution` in the
script ports `countryResolution` in `static.js`; keep them in step.

First full run, 196 countries: all identical, peak 150 MB (the page alone is 40 MB),
about 30 s. Before the memory cap, the same stress boxes held 763 MB.

## Plausibility against Natural Earth

Checked once against Natural Earth's `POP_EST`: 132 of 140 countries are within
0.75–1.33×. The low ones are low in Meta's own data, not because of clipping (the outlines
match): Greece 7.7 million (about 28% low everywhere, Athens 2.6 million), Guyana 0.56
million, Solomon Islands 0.39 million. Moldova's 4.0 million is above Natural Earth's figure
because Natural Earth excludes Transnistria.

## The coverage map

```bash
uv run python scripts/coverage_map.py
```

Writes `docs/img/coverage-map.png` (the README's map) and `docs/img/coverage.json`. It shows
which countries have a general-population GeoTIFF in Meta's HDX catalogue, which have a
built pack (`--loaded` takes an explicit list instead), and lists the countries with no
Meta raster at all. Outlines come from Natural Earth 1:50m, cached under `data/`. Pillow's
built-in font is ASCII-only, so the script uses a system TrueType font when it finds one and
transliterates otherwise (or Curaçao and Åland would render as boxes). It pages the HDX
catalogue 200 rows at a time: at 25 a page, the count changed between runs because CKAN's
ordering shifts between requests.
