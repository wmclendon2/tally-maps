# Verifying the data

Five checks, from "is the total right?" to "are people in the right places?". They read
the built packs in `data/packs/` (or a built `dist/`), and the overlap audit the rasters;
`make verify`, `make parity`, `make countries-check` and `make overlaps` run them.

| Check | Script | Catches |
| --- | --- | --- |
| Country totals | `verify_country.py` | Lost or double-counted people, broken packs |
| City positions | `verify_cities.py` | Shifted, flipped or misregistered rasters |
| Browser parity | `browser_parity.py` | `static.js` drifting from `ingest/resolve.py` |
| Country picker and memory | `browser_countries.py` | Wrong country totals in the browser, memory growth |
| Outline overlaps | `audit_overlaps.py` | People counted in two countries where their outlines overlap |

## Country totals

```bash
uv run python scripts/verify_country.py --country CMR
```

Rather than trusting that a pack's cells are its country's, this queries the country's own
geoBoundaries outline as a shape, the way a user would, and reconciles the answer exactly:

```
polygon_total == tagged_total - border_loss + cross_border_gain
```

`border_loss` is the country's cells whose centers fall outside its outline, and
`cross_border_gain` is the neighbors' cells whose centers fall inside it. It then checks
that every pyramid level sums to the same total, and that the total matches the catalog.

- It sums every pack that holds a covered cell, as `static.js` does, and checks each
  part's hashes, header, footprint and per-level totals.
- The default resolution is base − 2 (7 for Meta). The outline is filled whole: Cameroon
  takes 3 s, Tanzania 3 minutes, and Brazil and South Africa are the slowest because their
  outlines are detailed.
- Cameroon: polygon 26,619,950, gain 338,993, loss 213,945. An independent database
  built from the same rasters gave identical figures (2026-10-13). With whole people
  (2026-10-18): polygon 26,619,953, loss 213,944.
- Small islands show a large "net border error" at resolution 7 (Seychelles −26%, Mayotte
  −24%, Comoros −14%, Cape Verde −10%) because coarse cell centers fall in the sea. The
  identity still balances.
- Outlines that cross the 180° meridian (Fiji, Kiribati, New Zealand) are refused
  ("spans 360 deg"): a limit of this check, not of the data.

The first 53 countries passed on 2026-10-13, and the five territories clipped to Natural
Earth outlines passed when they were added.

Canada and Greenland (WorldPop, 2026-10-18) passed. Both scripts take the source from the
pack, so no `--source` is needed.

- Canada: polygon 37,434,213, gain 17,520 (USA), loss 592,279. The −1.5% net border
  error is the jagged Arctic coast at resolution 7, as with the small islands. Clipping
  the raster dropped only 9,429 of WorldPop's 38,018,401. With whole people: polygon
  37,434,221, gain 17,500, loss 592,251. Since 2026-10-20 Canada's clip is its outline
  grown 0.1° and cut at the USA and Greenland along CGAZ (`boundaries.CLIP_GROW_DEG`): the
  pack holds 38,018,400, all but 1 of WorldPop's total, and the net border error is
  −1.53% (this check fills the plain outline, and the coastal people the grown clip keeps
  lie outside it; the identity still balances).
- Greenland: 55,581, with no gain or loss, the raster's whole total. geoBoundaries'
  outline kept only 265 of them (its coast runs inland of the towns), so Greenland is
  clipped to Natural Earth's (`ingest/boundaries.py`).

Iran, Myanmar, Afghanistan, Yemen and North Korea (WorldPop, F-10 batch 2, 2026-10-20)
passed, and Canada again. Their clips are their outlines grown 0.1° and cut at built
neighbors along CGAZ (`boundaries.CLIP_GROW_DEG`), so each pack keeps nearly all of
WorldPop's own total:

| Country | Pack total | WorldPop's total | Short | Net border error (res 7) |
| --- | ---: | ---: | ---: | ---: |
| Iran | 87,480,245 | 87,481,767 | 1,522 (0.002%) | −0.70% |
| Myanmar | 52,821,918 | 52,825,300 | 3,382 (0.006%) | −1.66% |
| Afghanistan | 38,429,950 | 38,429,950 | 0 | −0.09% |
| Yemen | 35,638,195 | 35,640,680 | 2,485 (0.007%) | −0.78% |
| North Korea | 26,089,013 | 26,089,153 | 140 (0.001%) | −1.26% |
| Canada | 38,018,400 | 38,018,401 | 1 | −1.53% |

The net border errors are larger than for Meta countries because this check fills the
plain outline, and the coastal people the grown clip keeps (Bandar Abbas, Nampo, the
Irrawaddy delta) lie outside it; the identity still balances. Before the grown clip,
Canada's pack held 9,429 fewer.

## City positions

```bash
uv run python scripts/verify_cities.py [--country CMR] [--min-pop 0]
```

This is the only check that tests *where* people are. A national total stays the same if
the latitude and longitude are swapped, the raster is shifted half a pixel, or it is upside
down. Ground truth is Natural Earth's populated places (public domain, in
`reference/cities.json`), which are independent of Meta, WorldPop, HDX and geoBoundaries.
Only Natural Earth's `pop_max` is used, loosely, since `pop_min` is unreliable (Lagos 1,536).

**What it measures.** For each city, the offset of the population-weighted center of a
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
each city belongs to and take the largest normalized offset per shuffle. That handles the
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
| 2026-10-18, `--min-pop 0`, CAN and GRL only | 50 | 1.8 km | 0.476 | CAN 0.5 km N | 0.82 |
| 2026-10-18, default min-pop, 198 countries | 545 | 3.4 km | 0.487 | LBY 6.7 km SE | 0.32 |

The 2026-10-13 run printed identical output from the packs and from the earlier database.
The first run
is the baseline: mean offset 4.5 km, mean vector 0.6 km east, systematic fraction 0.13,
median contrast 15×. A later run well outside that wants a look.

**Known false alarms.**

- **Coastal cities.** A box around a coastal city is partly sea, so the population center
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
   catalog total, and the catalog's `cells_by_resolution` must match the packs.

After every step it forces garbage collection and reads the heap, including the typed
arrays, and fails over `--max-mb`. It also warns when a country's `area_km2` is outside
0.6–1.6× Natural Earth's area (countries over 1,000 km² only). `country_resolution` in the
script ports `countryResolution` in `static.js`; keep them in step.

First full run, 196 countries: all identical, peak 150 MB (the page alone is 40 MB),
about 30 s. Before the memory cap, the same stress boxes held 763 MB. With Canada and
Greenland (198 countries, 2026-10-18): all identical, peak still 150 MB. With whole
people: all identical, peak 149 MB.

## Outline overlaps

```bash
uv run python scripts/audit_overlaps.py [--country XXX] [--candidate XXX] [--workers N]
```

Each pack keeps the raster pixels whose center lies inside the outline it was clipped to.
Where two outlines overlap, a pixel there is inside both, and Meta's files cover each country's
bounding box, so a neighbor's file usually holds the same people: both packs count them.
`verify_country.py` cannot see this, since each country reconciles exactly with itself.

Since F-30 packs are clipped to their outline minus the overlaps given to a neighbor
(`ingest/overlaps.py`, ARCHITECTURE.md), and the audit reads those clips. The audit finds
every pair of built outlines that overlap (each outline split into its
polygons, so only pieces whose boxes meet are intersected), then reads each country's own
raster inside its overlaps with the build's rule: pixel center inside, the same valid-pixel
test. Each raster is read once for all of its country's overlaps, since the rasters are
strips inside zips and a sliver along a long border spans most of the country's rows.
`min(a, b)` of the two sides is the double count: exact when both rasters are the same map
(Meta's are cut from one global map; in 50 of the 115 pairs over 100 people the two sides
agree within 1%), an estimate where they differ in scaling or vintage. `--candidate`
checks a country not built yet against the built ones, before its pack is added. It exits
1 when a pair counts more than `--fail-above` (default 1,000) people twice; the results go
to `data/audit/overlaps.json`.

**2026-10-20, all 198 countries** (65 min with 7 workers): 217 overlapping pairs, 161 with
people in both rasters, **1,673,012 people counted twice** (0.03% of all packs' people),
in 119 countries; 85 pairs over 1,000, 25 over 10,000. The overlaps are where two
outlines (each drawn from its own national source) disagree about where the border runs:
dozens of strips and patches along it, up to about 1,000 km² each. They matter where
people live on the border: towns, river banks.

| Pair | Overlap | Counted twice | Note |
| --- | ---: | ---: | --- |
| BGD–IND | 2,426 km² | 293,647 | 54 pieces; the largest (842 km²) along Sylhet's border with Meghalaya. BGD's raster holds 1.61 M there |
| CMR–NGA | 2,926 km² | 220,530 | 96 pieces; the largest (1,091 km²) near 8.7°N 12.8°E |
| ARG–PRY | 1,997 km² | 108,023 | 40 pieces, along the Paraguay river from Formosa to Clorinda and Asunción |
| ARG–BRA | 1,963 km² | 92,272 | |
| TKM–UZB | 3,030 km² | 82,195 | |
| COD–ZMB | 915 km² | 76,697 | |
| BEN–NGA | 514 km² | 62,269 | |
| NER–NGA | 501 km² | 50,205 | |
| CMR–TCD | 544 km² | 48,600 | |
| ARE–OMN | 336 km² | 47,638 | |

As a share of a country's people, the most affected are Bhutan (1.6%), Paraguay (1.5%),
Turkmenistan (1.4%) and Cameroon (1.0%). A drawn shape on one of these borders counts
those people twice.

**The split (F-30, 2026-10-20).** `scripts/split_overlaps.py` splits every overlap along
geoBoundaries CGAZ (the rule: ARCHITECTURE.md, `ingest/overlaps.py`) and checks each pair's
clips: none may overlap, and every piece of the overlap must stay in one of them (or in a
third country's, near a tripoint). Measured by planar area at the sliver's latitude (the
spherical formula in `area_km2` puts a 1.5 m² sliver at 12,000 m²), the worst pair's clips
share 16 m² and drop 78 m² (NER–TCD), a fraction of one 30 m pixel, from floating-point
edges. 132 countries give something away; their packs were rebuilt (27 min, 6 workers).

| After the split, 2026-10-20 | Result |
| --- | --- |
| Overlap audit (57 min, 7 workers) | 137 pairs touch, only in slivers; **0 people counted twice** (was 1,673,012) |
| World total | 5,480,360,908, down 1,713,213: the double counts, plus 40 k where the side keeping a strip has a model holding fewer people there |
| Largest changes | BGD −230,336, ARG −210,144, NGA −206,734, CMR −165,848, ZMB −100,846; as a share, TKM −1.46%, BTN −1.41%. No country rose; 86 are unchanged, Puerto Rico and the other territories CGAZ lacks among them |
| `verify_country.py` CMR, BGD, ARG, TKM, PRI | All pass; the identity is exact, and the strips a neighbor now counts are its gain term |
| `verify_cities.py`, 198 countries | Unchanged: 545 cities, median 3.4 km, p = 0.487, worst LBY 6.7 km SE (family-wise p 0.32) |
| `browser_parity.py` | All 18 cases identical |
| `browser_countries.py` | All 198 countries identical in the browser; peak 143 MB |
| Packs | 269.83 MB (140 KB smaller); the coverage map is unchanged |

**With F-10 batch 2 (2026-10-20, 203 countries):** `split_overlaps.py --country IRN ...`
added 16 pairs and changed Canada's two (its grown clip); 16 neighbors' packs were
rebuilt. Their totals fell by at most 4,326 (Bangladesh, 0.003%), the strips now counted
by the new countries. The audit (36 min, 6 workers): 140 pairs touch, only in slivers;
**0 people counted twice**. The city check: 566 cities, median 3.4 km, p = 0.668, Iran's
nine 2.5 km W (p 0.098), family-wise p 0.33. Browser parity 18 of 18, all 203 countries
identical in the browser (5,720,821,521 people, peak 143 MB), `browser_update.py` passes.
The no-data mask clears the five (Bandar Abbas and Nampo included) and keeps Pakistan,
China and Azerbaijan gray.

**With every WorldPop country (F-10 batches 3 and 4, 2026-10-23, 233 countries, local
branch):** 7,768,510,368 people. Each WorldPop country's pack against WorldPop's own total
for it:

| Short of WorldPop's total | Countries |
| --- | --- |
| 0 to 0.01% | 22, among them China −41,388 (0.003%), Pakistan, Ukraine, Sudan, Israel, Western Sahara, Sint Maarten, Saint-Martin |
| 0.01% to 0.11% | Luxembourg −656 (0.105%), South Sudan −5,150, Somalia −5,409, Palestinian Territories −1,519, Russia −37,078 (0.025%), Åland −7, Montenegro −135, Morocco −4,077, Kosovo −188 |

The Palestinian Territories' file follows LSIB, which puts East Jerusalem on its side;
geoBoundaries and Natural Earth draw it in Israel. Its 261,554 people inside Israel's outline
are in Israel's pack (`build_packs.ADDED_TO`, the owner's choice, 2026-10-23), and are
counted with the Palestinian Territories above.

**Known limits,** left as they are (maintainer's call): WorldPop pixels within about a
kilometer of a border with a Meta country that fall on the neighbor's side of CGAZ
(Luxembourg's and South Sudan's, traced); Dongxing, on China's border with Vietnam; the
southern Kurils (13,528 in Russia's file, CGAZ gives them to Japan, whose file is empty
there) and the Halayib triangle (6,168 in Egypt's Meta file, CGAZ gives it to Sudan); the
0.26 km² sliver where China, India and Nepal meet, which the split leaves to none of them.
The 49 Meta neighbors whose totals fell lost at most 85,574 (India, 0.006%): mostly the
double counts the split removes, in part people of whom the receiving file holds fewer.

| Check | Result |
| --- | --- |
| Overlap audit (56 min) | 213 pairs touch, counting the places added to Israel's and Australia's packs; **0 people counted twice** in every cut pair, and in 25 of the 27 uncut pairs (two WorldPop rasters compared pixel by pixel, others by res-9 cell, which counts a border cell's two sides: Israel–Palestinian Territories once showed 19,634). China–Macau 4,342 and China–Hong Kong 1,018, by res-9 cell (Meta's 30 m grid against WorldPop's), an upper bound; both kept uncut on purpose, so the audit exits 1 on them |
| `verify_cities.py` | 739 cities in 233 countries, no country stands out (worst LBY 6.7 km SE, family-wise p = 0.26) |
| `browser_parity.py` | All 18 cases identical |
| `browser_countries.py` | All 233 countries identical in the browser; peak 143 MB |
| `browser_update.py` | Passes |
| Packs | 373 MB |

**Where disputed areas' people are counted** (Natural Earth's disputed areas, people in
each pack inside them; WorldPop's 2020 files follow LSIB): Crimea, Donetsk and Luhansk in
Ukraine (2.51 M, 2.37 M, 1.50 M); the Golan Heights in Israel (51,903); East Jerusalem in
Israel (256,625, from the Palestinian Territories' file; 6,893 at its edges in theirs);
Northern Cyprus in Cyprus (416,174); Karabakh in Azerbaijan (112,284, before the 2023
exodus); Western Sahara, both sides of the wall, in Western Sahara (488,175); Taiwan in
Taiwan (22.7 M), none in China; Kosovo in Kosovo; Essequibo in Guyana; Azad Kashmir and
Gilgit-Baltistan in Pakistan (4.23 M, 1.01 M); Indian-administered Jammu and Kashmir and
Arunachal Pradesh in India; Aksai Chin in China; Abyei split between Sudan (103,375) and
South Sudan (41,433); Somaliland in Somalia; Abkhazia, South Ossetia and Transnistria in
Georgia and Moldova (Meta's files).

## Whole people

Since 2026-10-18 every cell holds a whole number of people (`whole_people` in
`build_packs.py`; see ARCHITECTURE.md). After the rebuild of all 198 countries, none of
their 115,021,057 cells held a fraction or less than one person, and the world total was
unchanged to the person (5,482,074,121). The 24 coarsest cells over 16.7 million people
(float32's limit for exact integers) can be a person or two off a whole number. The city
check was unchanged (545 cities, median 3.4 km, p = 0.487).

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
which countries have a general-population GeoTIFF in Meta's HDX catalog, which have a
built pack (`--loaded` takes an explicit list instead), and lists the countries with no
Meta raster at all. Outlines come from Natural Earth 1:50m, cached under `data/`. Pillow's
built-in font is ASCII-only, so the script uses a system TrueType font when it finds one and
transliterates otherwise (or Curaçao and Åland would render as boxes). It pages the HDX
catalog 200 rows at a time: at 25 a page, the count changed between runs because CKAN's
ordering shifts between requests.
