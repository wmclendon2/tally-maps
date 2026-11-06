# Tally

Draw a shape on a map, get the population inside it and a density heatmap. Public site:
https://tallymaps.org. This repo is for building and running Tally locally;
hosting the public site is not part of it. How it works and why: `docs/ARCHITECTURE.md`.
Read it before changing the pipeline, the pack format or the resolver.

## Shape of the project

- **A static app, no server or database.** `scripts/build_packs.py` turns rasters into
  per-country packs in `data/packs/`; `scripts/build_static.py` writes `dist/` = `web/` +
  packs + the no-data mask; `web/static.js` answers in the browser; `web/files.js` reads a
  file of shapes or points into rows for the File tab (F-02). All 196 Meta countries
  have packs (Jersey's from WorldPop), plus 37 from WorldPop (233). `ingest/resolve.py` is the Python reference
  for static.js's resolution rule. (API mode, PostGIS + FastAPI, was retired on 2026-10-15.)
- Data: Meta HRSL 30 m via HDX (`ingest/hdx.py`), WorldPop 100 m where Meta has no data
  (38 places, `ingest.config.WORLDPOP_COUNTRIES`; `ingest/worldpop_hub.py`; Norfolk Island's
  people go in Australia's pack and the Palestinian Territories' in East Jerusalem in
  Israel's, `build_packs.ADDED_TO`), clipped to geoBoundaries ADM0
  (Natural Earth for HKG/MAC/JEY/VIR/PCN/GRL/SXM/MAF/SPM/ESH, Finland's ADM1 unit for ALA,
  `ingest/boundaries.py`). Base H3 res 9, pyramid down to res 3.
- Docs: `README.md` (for people without an AI tool: every step a plain command; keep it and
  the `Makefile` in step with CLI flags), `docs/ARCHITECTURE.md`, `docs/VERIFICATION.md`
  (methods and baselines of the checks), `docs/DATA_SOURCES.md` (Meta vs WorldPop and the
  alternatives, as of 2026-10-19), `docs/ROADMAP.md`.

## Invariants (easy to break, hard to notice)

- `web/static.js` ports `ingest/resolve.py`: same automatic resolution (AUTO_TARGET_CELLS
  4000), Fine = exactly one level finer capped at the base res, MAX_CELLS_LIMIT 40,000 with
  a refusal (never a silent coarser answer). **Change both together**
  and run `scripts/browser_parity.py`. `build_packs.py`'s `country_resolution` ports
  `countryResolution` in static.js.
- Shell versioning: `index.html` loads `app.js?v=N`, `static.js?v=N`, `files.js?v=N`, `style.css?v=N`,
  `i18n.js?v=N` (which fetches `i18n/<lang>.json?v=N`), and `sw.js` sets
  `SHELL_VERSION = "N"`. **Bump both on any change to a versioned asset or a language file**
  (`tests/test_web_shell.py` fails if they drift). Only navigations use `ignoreSearch`
  offline, so a stale asset never stands in for a versioned one.
- Never remove an answer field a cached app reads: for an installed PWA that is a breaking
  change. app.js raises `StaleAppError` ("Tally has been updated. Reload to get the
  latest.", with the Reload notice) when an answer lacks `base_resolution`/`total` or a cell
  lacks `h3`/`population`.
- A tab open across a deploy (F-31): a 404 on a pack makes static.js reread the catalog
  and retry once (`withFreshCatalog`); errors a person sees never carry an HTTP status. The
  "new version" notice compares the worker's `SHELL_VERSION` (sw.js answers "version")
  with the page's `?v=`; Reload keeps the count in `sessionStorage`. Checked by
  `scripts/browser_update.py`.
- Languages (F-01): every word on screen is a key in `web/i18n/en.json`, looked up with
  `t()` (app.js) or marked `data-i18n*` (index.html, whose English must match en.json); a
  new key goes in every `web/i18n/*.json`. Errors static.js shows carry `key`/`vars`
  (`said()`); app.js's `errText` says them. Numbers through `fmtInt`/`fmtFixed` (the
  visitor's locale), never `toLocaleString("en-US")`. Exported keys stay English.
  `tests/test_i18n.py` checks all of it. A new language: its file, `LANGUAGES` in
  `i18n.js`, and the list in `sw.js`.
- A file's rows (F-02) go through `TallyStatic.query` one by one, each at its own level, so a
  row equals the same shape drawn. A row is never dropped: what cannot be counted stays in
  the list and says why. A list keeps numbers only, never a row's hexagons. `scripts/browser_files.py`
  checks it; a new format goes in there too. Everything picked makes one list; a file that cannot
  be read stops it and is named (`files.read`).
- Tally reads its own saved files back (F-41, `savedRow` in files.js, on `counted_with`): a circle
  by `latitude`/`longitude`/`radius_km`, while its outline still fits them (else the outline wins);
  `drawn` means Tally named the row, so it is named afresh; `country_code` picks a whole country
  or continent again. Its columns are not carried as the file's own. A new saved column goes in
  `OWN_COLUMNS`. Files saved before these columns still open (`OLD_SHAPE_TITLES`).
- Keep the `population-outline` layer and the drawn shape in any heatmap change, and
  `tolerance: 0` on the `population-cells` source (otherwise sub-pixel hexes vanish when
  zoomed out).
- Packs hold whole people (`build_packs.whole_people`, since 2026-10-18): no cell may show
  a fraction or "0 people". Keep it in any change to how levels are built; the tests check
  every level, and every cell's children, sum exactly.
- A continent's overview pack (F-33) stays out of `parts` and `byRoot` in static.js and has no
  footprint: a drawn shape reading one would count the same people twice. It answers through
  `TallyStatic.country("continent:Africa")`, so `countryResolution` and `browser_countries.py` cover it.
- A country's overview pack (F-25, the entry's `overview`) holds exactly its parts' cells at
  its levels (`browser_countries.py` compares them on real data), so it may answer drawn shapes:
  `standIns` in static.js swaps it for all of a country's parts or none, never both, and only
  when it costs fewer bytes than the parts still missing, so a saved country reads its parts.
  `build_static.py` writes them, so they change no country's pack.
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
  106 M people, 79.6 M of them in neighbors). Clipping is mandatory; `--no-clip` is for
  debugging. FJI's, KIR's and NZL's grids run past 180° (to 181.8, 202.8, 183.8) while
  outlines stop at it, so `raster.clip_for_grid` adds the outline moved by 360° (F-37: the
  clip had dropped Fiji's 14,298 east of 180 and the Chatham Islands' 627). Anything else
  that reads a grid against an outline needs the same; `audit_overlaps.py` does not (no
  overlap is near 180 today). Meta's KIR file has no one east of 180 (no Line Islands), and its FJI file no one from
  179.824 E to 179.912 W, a gap in the raster itself (Rabi, Waiyevo and Somosomo read empty).
  A shape past 180 is a separate matter: `resolve.py` and static.js wrap its longitudes (F-39).
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
  coverage map). Meta's JEY file has no data over Jersey. The 38 in
  `WORLDPOP_COUNTRIES` are WorldPop 100 m (`build_packs.SOURCE_FOR`). WorldPop's files hold
  only their own country (cut at LSIB), so a WorldPop country's clip is its outline grown
  0.1° (`boundaries.CLIP_GROW_DEG`; coarse outlines cut off Bandar Abbas and Nampo), or its
  Natural Earth unit grown and not cut back at the other units (Natural Earth's line across
  St Martin lost a third of Sint Maarten), and cut at built Meta neighbors by the overlap
  split; its map outline, no-data mask and area stay plain. China's clip adds Natural
  Earth's coast (`CLIP_WITH_NATURAL_EARTH`; geoBoundaries' lost 2.45 M). Two WorldPop
  countries' overlap is not cut: both clips keep it (`overlaps.kept_by_both`, also CHN with
  HKG and MAC), as no pixel is in both files (`audit_overlaps.py` compares one grid pixel by
  pixel). Western Sahara is the whole territory (`boundaries.NE_PART_OF`; Natural Earth's is
  the strip east of the Moroccan wall), and Morocco's drawn outline and area stop at its
  27°40′ N line (`boundaries.outline_path`). A place added to another's pack is clipped by
  `overlaps.added_clip_path` (the host's outline less what it gives away) and audited as
  `PLACE@HOST`. Clip keys (`overlaps.split_key`) cover the grow of the neighbors a clip
  is cut at; grown outlines are cached in `data/boundaries/grown/`.
  geoBoundaries publishes outlines as CC BY 4.0, but each outline's source keeps its own
  license (85 of the 233 are ODbL, mostly OpenStreetMap; NOTICE.md lists them). Before building a new country, take it into the overlap
  split (below): Meta's bounding-box files can hold people inside a neighbor's claim
  (IND's outline takes in Pakistan-administered Kashmir), so both packs would count them,
  and `audit_overlaps.py --candidate XXX` shows how many. Kosovo is `KOS` at WorldPop,
  `XKX` at geoBoundaries. geoBoundaries has no ADM0 for PRI (`ADM0_FROM_PARENT` takes USA
  ADM1).
- An outline is not proof of data (IND's claims Pakistan-administered Kashmir, FIN's holds
  Aland): the no-data mask keeps an overlap gray unless the pack has >= 5 people/km2 there
  (`CLAIM_SHARE`, `MIN_DENSITY` in `build_static.py`).
- Neighboring outlines overlap where their sources disagree (1.67 M people were in two
  packs). Packs are clipped to `ingest/overlaps.clip_path` (the outline minus the overlaps
  given away along CGAZ, cached in `data/boundaries/clip/`), not the raw outline. Who
  gets each overlap's rest is checked in (`reference/overlap_split.json`); a build reads
  CGAZ itself (`data/cgaz/`, 162 MB, pinned by commit and sha256; the line through the
  overlaps alone was 22 MB, too big to check in). CGAZ has no unit for PRI, GUM, VIR, ASM, MNP, VGB, GUF (it
  folds them into their parent, whose raster is empty there), so those overlaps go by
  raster people; a plain CGAZ split would hand Puerto Rico's 3.6 M to the USA's pack. A
  new country: `split_overlaps.py --country XXX` (needs every covered country built), then
  rebuild the countries it names and run `make overlaps`.
- `reference/continents.json` is Natural Earth 50m's CONTINENT field, checked in; MUS/SYC/
  MDV (filed under "Seven seas") are filed by REGION_UN.

## Commands

Always run uv as `UV_CACHE_DIR=.uv-cache uv ...` (the cache lives in `./.uv-cache`); the
`Makefile` targets honor it (`UV_CACHE_DIR=.uv-cache make test`).

- Env: `UV_CACHE_DIR=.uv-cache uv sync` (add `--group browser` for the browser checks)
- Tests: `UV_CACHE_DIR=.uv-cache uv run pytest -q`; lint: `uv run ruff check .` **and**
  `uv run ruff format --check .` (CI runs both; `ruff format .` fixes the second). Before
  committing, `UV_CACHE_DIR=.uv-cache make check` runs all three.
- Packs: `UV_CACHE_DIR=.uv-cache uv run python scripts/build_packs.py --country CMR
  [--country XXX ...] [--rebuild] [--fresh] [--max-part-mb 16] [--workers N]`
- Build: `UV_CACHE_DIR=.uv-cache uv run python scripts/build_static.py [--country CMR ...]
  [--out DIR]`, then serve `dist/` on 8081 (`preview_start` name `demo`). The URL must carry
  `:8081`; with the server down the service worker replays a frozen shell.
- Parity: `UV_CACHE_DIR=.uv-cache uv run --group browser python scripts/browser_parity.py [--dist DIR]`
- Country check + memory: `UV_CACHE_DIR=.uv-cache uv run --group browser python
  scripts/browser_countries.py [--dist DIR] [--country XXX] [--max-mb 200] [--no-stress]`
  and `scripts/browser_update.py [--dist DIR]` (a tab left open across a release, F-31;
  needs three one-part countries in the build). All three run in the sandbox: they serve
  on a local port inside the process.
- Files: `UV_CACHE_DIR=.uv-cache uv run --group browser python scripts/browser_files.py [--dist DIR]`
  (`make files-check`; needs CMR and FJI in the build).
- Verify: `UV_CACHE_DIR=.uv-cache uv run python scripts/verify_country.py --country CMR`
  and `scripts/verify_cities.py [--country XXX] [--min-pop 0]`.
- Catalog: `uv run python -m ingest.hdx` (Meta's countries and download sizes).
- Overlaps: `UV_CACHE_DIR=.uv-cache uv run python scripts/split_overlaps.py [--country XXX]`
  (writes `reference/overlap_split.json` and the clips; all packs) and
  `scripts/audit_overlaps.py [--country XXX] [--candidate XXX]` (`make overlaps`, ~1 h).
- Coverage map: `UV_CACHE_DIR=.uv-cache uv run python scripts/coverage_map.py`
  (writes the committed `docs/img/coverage-map.png` + `coverage.json`).
- Link-preview image: `UV_CACHE_DIR=.uv-cache uv run --group browser python scripts/og_image.py
  --dist DIR` (a build of every country, so neighbors are not grayed) writes `web/og-image.jpg`
  (1200x630, < 300 KB); the `og:*` tags in `index.html` describe it.
- Vendored libs: `scripts/vendor_libs.py` (bump `PACKAGES`, rerun, fix the paths in
  `index.html`/`sw.js`, bump the shell version; it never deletes old versions).

## Working on this repo

- Never commit anything under `data/`, `dist/` or `private/`.
- `docs/ROADMAP.md` is the feature pipeline: the maintainer decides what moves. File new
  ideas in Inbox with a first sizing, shape items when asked, and record shipped items with
  the date.
- Not built yet: parity polygons outside Africa/Brazil (F-28), the browser checks in CI
  (F-27 level 2).
- CI: `.github/workflows/check.yml` runs `make check`'s steps on every push and pull
  request (no data needed). Keep the actions pinned by commit hash; a push that changes a
  workflow needs the `workflow` scope on the GitHub login.
