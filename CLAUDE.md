# Tally

Draw a shape on a map, get the population inside it and a density heatmap. Public site:
https://tallymaps.org. This repo is for building and running Tally locally;
hosting the public site is not part of it. How it works and why: `docs/ARCHITECTURE.md`.
Read it before changing the pipeline, the pack format or the resolver.

## Shape of the project

- **A static app, no server or database.** `scripts/build_packs.py` turns rasters into
  per-country packs in `data/packs/`; `scripts/build_static.py` writes `dist/` = `web/` +
  packs + the no-data mask; `web/static.js` answers in the browser. All 196 Meta countries
  have packs, plus CAN and GRL from WorldPop. `ingest/resolve.py` is the Python reference
  for static.js's resolution rule. (API mode, PostGIS + FastAPI, was retired on 2026-10-15;
  batch upload, F-02, is planned in the browser.)
- Data: Meta HRSL 30 m via HDX (`ingest/hdx.py`), WorldPop 100 m where Meta has no data
  (CAN, GRL, JEY, `ingest/worldpop_hub.py`), clipped to geoBoundaries ADM0 (Natural Earth for
  HKG/MAC/JEY/VIR/PCN/GRL, `ingest/boundaries.py`). Base H3 res 9, pyramid down to res 3.
- Docs: `README.md` (for people without an AI tool: every step a plain command; keep it and
  the `Makefile` in step with CLI flags), `docs/ARCHITECTURE.md`, `docs/VERIFICATION.md`
  (methods and baselines of the checks), `docs/ROADMAP.md`.

## Invariants (easy to break, hard to notice)

- `web/static.js` ports `ingest/resolve.py`: same automatic resolution (AUTO_TARGET_CELLS
  4000), Fine = exactly one level finer capped at the base res, MAX_CELLS_LIMIT 40,000 with
  a refusal (never a silent coarser answer). **Change both together**
  and run `scripts/browser_parity.py`. `browser_countries.py`'s `country_resolution` ports
  `countryResolution` in static.js.
- Shell versioning: `index.html` loads `app.js?v=N`, `static.js?v=N`, `style.css?v=N`, and
  `sw.js` sets `SHELL_VERSION = "N"`. **Bump both on any change to a versioned asset**
  (`tests/test_web_shell.py` fails if they drift). Only navigations use `ignoreSearch`
  offline, so a stale asset never stands in for a versioned one.
- Never remove an answer field a cached app reads: for an installed PWA that is a breaking
  change. app.js raises `StaleAppError` ("App out of date. Reload to update.") when an
  answer lacks `base_resolution`/`total` or a cell lacks `h3`/`population`.
- Keep the `population-outline` layer and the drawn shape in any heatmap change, and
  `tolerance: 0` on the `population-cells` source (otherwise sub-pixel hexes vanish when
  zoomed out).
- Packs hold whole people (`build_packs.whole_people`, since 2026-10-18): no cell may show
  a fraction or "0 people". Keep it in any change to how levels are built; the tests check
  every level, and every cell's children, sum exactly.
- Pack format: a format **major** bump makes old readers refuse a pack; additive changes
  bump the minor. Packs are named by content hash; `write_catalog` adds `continent`,
  `cells_by_resolution` and `area_km2` at write time, so those need no pack rebuild.
- `web/index.html`'s `<meta name="tally-data" content="data/manifest.json">` tells app.js
  and static.js where the data is; `build_static.py` puts the data there in `dist/`. `web/`
  alone has no data, so always serve a build.
- Map controls at the bottom stack newest-on-top: add the attribution before the scale.
  On phones the bottom sheet is lifted by `--credits-h` so it never hides the credits.
- The service worker never caches packs (`*.tlyp.gz`, kept in IndexedDB `tally-packs`) or
  tiles; its activate step deletes every cache but the shell's (`tally-shell-vN`).
- No analytics, hosting config or deploy scripts in this repo: `web/` and `build_static.py`
  never add a beacon. The browser checks still block common analytics beacons, so a check
  against a hosted copy never counts as a visit.
- Every code file starts with the two-line copyright and `SPDX-License-Identifier: MIT`
  header (`web/vendor/` keeps its authors' headers); add it to new files.

## Data gotchas

- Meta country files cover the country's **bounding box**, not its outline (CMR's holds
  106 M people, 79.6 M of them in neighbours). Clipping is mandatory; `--no-clip` is for
  debugging.
- Some Meta GeoTIFFs store strips out of row order; `raster_to_h3` reads those in file
  order (`read_order="auto"`), or reading a zip member backwards re-inflates (DZA 26 min ->
  99 s). Compute is ~2.7 s per GB of uncompressed raster.
- Rasters are read straight from the zips via GDAL `zip://` paths. USA zips carry
  `__MACOSX/._*.tif` stubs (`pipeline.is_geotiff_member` skips them); BRA's zips lack
  "_general_", so `raster_paths` globs the country dir. `data_version` also reads names
  inside the zip (COM/MUS/MYT/STP zips carry no date).
- HDX downloads redirect to `s3.us-east-1.amazonaws.com`. All HTTP goes through
  `ingest/fetch.py` (curl first, urllib fallback, retries on 408/425/429/5xx). Some proxies
  truncate HTTP/1.1 bodies from urllib with no error; keep curl first.
- Meta has no file for RUS, CHN, PAK, CAN, GRL, MAR, SDN, SSD, SOM, ESH (and others; see the
  coverage map). Meta's JEY file has no data over Jersey. CAN, GRL and JEY are WorldPop
  100 m (`build_packs.SOURCE_FOR`). Add a WorldPop country with a disputed border only after
  checking the outline overlaps: Meta's bounding-box files can hold people inside a
  neighbour's claim (IND's outline takes in Pakistan-administered Kashmir), so both packs
  would count them. Kosovo is `KOS` at WorldPop, `XKX` at geoBoundaries. geoBoundaries has
  no ADM0 for PRI (`ADM0_FROM_PARENT` takes USA ADM1).
- An outline is not proof of data (IND's claims Pakistan-administered Kashmir, FIN's holds
  Aland): the no-data mask keeps an overlap grey unless the pack has >= 5 people/km2 there
  (`CLAIM_SHARE`, `MIN_DENSITY` in `build_static.py`).
- `reference/continents.json` is Natural Earth 50m's CONTINENT field, checked in; MUS/SYC/
  MDV (filed under "Seven seas") are filed by REGION_UN.

## Commands

Always run uv as `UV_CACHE_DIR=.uv-cache uv ...` (the cache lives in `./.uv-cache`); the
`Makefile` targets honour it (`UV_CACHE_DIR=.uv-cache make test`).

- Env: `UV_CACHE_DIR=.uv-cache uv sync` (add `--group browser` for the browser checks)
- Tests: `UV_CACHE_DIR=.uv-cache uv run pytest -q`; lint: `uv run ruff check .` (or `make check`)
- Packs: `UV_CACHE_DIR=.uv-cache uv run python scripts/build_packs.py --country CMR
  [--country XXX ...] [--rebuild] [--fresh] [--max-part-mb 16] [--workers N]`
- Build: `UV_CACHE_DIR=.uv-cache uv run python scripts/build_static.py [--country CMR ...]
  [--out DIR]`, then serve `dist/` on 8081 (`preview_start` name `demo`). The URL must carry
  `:8081`; with the server down the service worker replays a frozen shell.
- Parity: `UV_CACHE_DIR=.uv-cache uv run --group browser python scripts/browser_parity.py [--dist DIR]`
- Country check + memory: `UV_CACHE_DIR=.uv-cache uv run --group browser python
  scripts/browser_countries.py [--dist DIR] [--country XXX] [--max-mb 200] [--no-stress]`
  (both run in the sandbox: they serve on a local port inside the process).
- Verify: `UV_CACHE_DIR=.uv-cache uv run python scripts/verify_country.py --country CMR`
  and `scripts/verify_cities.py [--country XXX] [--min-pop 0]`.
- Catalogue: `uv run python -m ingest.hdx` (Meta's countries and download sizes).
- Coverage map: `UV_CACHE_DIR=.uv-cache uv run python scripts/coverage_map.py`
  (writes the committed `docs/img/coverage-map.png` + `coverage.json`).
- Link-preview image: `UV_CACHE_DIR=.uv-cache uv run --group browser python scripts/og_image.py
  --dist DIR` (a build of every country, so neighbours are not greyed) writes `web/og-image.jpg`
  (1200x630, < 300 KB); the `og:*` tags in `index.html` describe it.
- Vendored libs: `scripts/vendor_libs.py` (bump `PACKAGES`, rerun, fix the paths in
  `index.html`/`sw.js`, bump the shell version; it never deletes old versions).

## Working on this repo

- Never commit anything under `data/`, `dist/` or `private/`.
- `docs/ROADMAP.md` is the feature pipeline: the maintainer decides what moves. File new
  ideas in Inbox with a first sizing, shape items when asked, and record shipped items with
  the date.
- Not built yet: WorldPop 100 m for the rest of the countries Meta lacks (F-10, batches in
  the roadmap; disputed borders wait on the overlap audit, F-30), parity polygons outside
  Africa/Brazil (F-28), CI (F-27).
