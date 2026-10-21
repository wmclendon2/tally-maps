# Roadmap

What is planned for Tally, what is being considered, and what has shipped. Ideas and
feedback are welcome as GitHub issues.

**Stages:** **Inbox** (captured, not yet thought through) → **Shaped** (scope, effort and
open questions written down) → **Next** (chosen, in order) → **Doing** → **Shipped**.
**Parked** means deliberately not now, with the reason.

**Sizing:** effort **S** (a session or less), **M** (a few sessions), **L** (a week or
more). Value **★** nice to have, **★★** a clear win, **★★★** opens new users or uses.
Privacy **✅** stays in the browser, **⚠️** needs a server or sends data somewhere.

"Local, private, works offline" is the product's promise, so a ⚠️ item is not ruled out,
but it is a deliberate choice.

## Board

| ID | Feature | Stage | Effort | Value | Privacy | Depends on |
| --- | --- | --- | --- | --- | --- | --- |
| F-01 | Multi-lingual interface | Shaped | M | ★★★ | ✅ | — |
| F-02 | Upload polygons (SHP/GeoJSON/KML/GPKG), batch results | Shaped | M–L | ★★★ | ✅ | — |
| F-04 | Export results (CSV / GeoJSON) | Inbox | S | ★★ | ✅ | — |
| F-05 | Shareable link to a shape (in the URL fragment) | Inbox | S | ★★ | ✅ | — |
| F-06 | Pick a region or district instead of drawing | Parked (country level shipped) | M | ★★★ | ✅ | ADM1/ADM2 outlines |
| F-07 | Age and sex breakdown (Meta's women, under-5s, youth, 60+ layers) | Parked | L | ★★★ | ✅ | Pack format minor bump |
| F-09 | Compare two shapes, or one area across data years | Inbox | M | ★★ | ✅ | F-13 for years |
| F-10 | WorldPop 100 m for countries Meta lacks | Shaped (Jersey, Canada, Greenland done) | M | ★★★ | ✅ | — (F-30 shipped) |
| F-13 | Data versioning and refresh of saved packs | Inbox | M | ★★ | ✅ | F-29 |
| F-16 | Embeddable widget for articles | Inbox | M | ★★ | ✅ | F-05 |
| F-17 | Printable one-page report (PDF) for an area | Inbox | M | ★★ | ✅ | F-04 |
| F-18 | Public batch API for developers | Parked | L | ★★ | ⚠️ | — |
| F-25 | Make the app faster, first visit and large shapes | Shaped | M | ★★ | ✅ | — |
| F-26 | First-visit welcome: a one-time hint, or open on an example count | Inbox | S | ★ | ✅ | Feedback from first-time users |
| F-27 | Continuous integration: tests and lint on every push | Shaped (level 1 shipped) | S–M | ★★ | ✅ | — |
| F-28 | Parity polygons outside Africa and Brazil | Inbox | S | ★★ | ✅ | — |
| F-29 | Provenance per pack: the source file's address and hash | Inbox | S–M | ★ | ✅ | — |
| F-31 | A tab left open survives a deploy: "new version" notice, retry on a renamed pack | Shaped | S | ★★ | ✅ | — |
| F-32 | Compare Meta and WorldPop where both have data | Inbox | S–M | ★★ | ✅ | — |
| F-33 | Continent view: population total and density map | Inbox | M (S after the overview tier) | ★★ | ✅ | F-25's overview tier; F-10 for Asia and Europe |
| F-34 | Tests with real language and people-group areas (open data) | Inbox | S–M | ★★ | ✅ | — |
| F-35 | Detail limits: measure the cost and accuracy of more cells | Inbox | S (M if it leads to a change) | ★★ | ✅ | After F-34 |

Numbers missing from this board (F-24) are items about the hosted site, which this repo
does not cover.

## Shaped items

### F-01 Multi-lingual interface
- **Why:** much of the coverage is French- or Portuguese-speaking (most of Central and West
  Africa, Brazil). A French UI alone opens up the main Central and West African audience.
- **Approach:**
  - Put all strings in `web/i18n/<lang>.json` and look them up with `t("key")`.
  - Format numbers and areas with `Intl.NumberFormat`, since separators differ by language.
  - Detect the language from `navigator.language`, let the user override it, remember it.
  - Country names from `Intl.DisplayNames`, so no translation table is needed.
- **First languages:** French, Portuguese, Spanish, then Arabic, which is right-to-left and
  needs a layout pass of its own.
- **Effort:** M, mostly the one-off string extraction; each further language is S.
- **Open questions:** who checks the translations; whether the trust pages are translated
  at launch.

### F-02 Upload polygons, batch processing
- **Why:** turns Tally from "explore" into a work tool. NGOs, planners and researchers keep
  catchment areas, health zones and service areas as files.
- **Approach (all in the browser, nothing uploaded; no server needed):**
  - GeoJSON natively, KML (`@tmcw/togeojson`), zipped Shapefile (`shpjs`, which handles
    `.prj` reprojection), and GeoPackage (`sql.js`, larger, so perhaps later).
  - Drag and drop or a file picker; the features as a list, each run through
    `TallyStatic.query`, downloading only the packs they touch.
  - Results as a table (name, total, area, density), highlighted on the map when clicked.
  - Export to CSV, and GeoJSON with a `population` property (pairs with F-04).
  - Points with a radius, as well as polygons: much of what users hold is points
    (people-group centres, clinics, villages, project sites). A CSV of name, latitude,
    longitude and radius (or one radius for all), each counted as a circle, in the same
    table. Added 2026-10-20.
- **Limits to decide:** features per batch (500 to start), vertices, and features over
  areas with no data, which are reported and never dropped silently.
- **Risks:** multipolygons and holes need parity tests; large batches should run in a Web
  Worker so the map stays responsive.
- **Effort:** M for GeoJSON + KML + SHP with a table and CSV; L with GeoPackage and a worker.

### F-10 WorldPop for the countries Meta lacks
- **Why:** the largest gap in coverage. About 30 countries and 12 territories have no Meta
  data, among them China, Pakistan, Russia, Iran and Myanmar: over 2 billion people.
- **Approach:** one `SOURCE_FOR` entry per country in `scripts/build_packs.py` (WorldPop
  Global2 R2025A, constrained, 2020), clipped to geoBoundaries, or to Natural Earth where
  geoBoundaries has no usable outline. Verified from the packs like every other country,
  and each clipped total compared with WorldPop's own total for the country: Canada lost
  0.02% to clipping, while Greenland's geoBoundaries coast lost 99.5% and needed Natural
  Earth.
- **Batches:**
  1. **Done 2026-10-18:** Canada and Greenland (Jersey 2026-10-13).
  2. **Small places with undisputed borders:** Andorra, Luxembourg, Isle of Man,
     Montenegro, Cuba, Curaçao, Sint Maarten, Bermuda, the Falklands, Niue, Norfolk
     Island, Saint Barthélemy, Saint-Martin, Saint Pierre and Miquelon, Christmas Island
     and Cocos. Several need Natural Earth outlines; Natural Earth draws Christmas Island
     and Cocos as one shape.
  3. **Disputed borders, after F-30:** Pakistan and China (Kashmir, Aksai Chin,
     Arunachal), Azerbaijan (Karabakh), Morocco and Western Sahara, Russia and Ukraine
     (Crimea), Israel, Palestine and Syria (the Golan), Sudan and South Sudan (Abyei),
     Cyprus, Somalia, Åland (inside Finland's outline), Venezuela (Guyana's Essequibo),
     Kosovo (`KOS` at WorldPop, `XKX` at geoBoundaries).
- **Known limits:** Russia crosses the 180° meridian, which `verify_country.py` refuses,
  so it needs another total check. China's pack will be the largest. The Vatican has no
  WorldPop file; Somaliland and Northern Cyprus have no code of their own and come with
  Somalia's and Cyprus's outlines; Siachen and Ashmore have no data.
- **Effort:** S per batch-2 country; M for F-30 and batch 3.

### F-25 Speed
Aim for good Core Web Vitals (LCP < 2.5 s, INP < 200 ms, CLS < 0.1). Change one thing at a
time and measure before and after with the browser's own tools (Lighthouse, the
Performance panel).

**Measured 2026-10-19** (headless Chrome, cold cache, median of 5; phone = 412×823 at 2.6×,
4× slower CPU, 4G at 150 ms and 9 Mbps). A first visit downloads 612 KB from Tally's own
site, and the basemap from OpenFreeMap, which is most of the bytes.

| First visit | Before (v40) | Now (v43) |
| --- | --- | --- |
| Phone: map ready | 8.1 s | 4.8 s |
| Phone: main thread blocked | 2.9 s | 1.4 s |
| Phone: basemap downloaded | 3.5 MB | 2.0 MB |
| Desktop: map ready | 3.0 s | 2.0 s |
| Desktop: basemap downloaded | 6.6 MB | 3.6 MB |

**Done:**
- **Open on the start view** (v43, live 2026-10-19): the map opened on Cameroon and
  jumped to the world once the data came, so every first visit fetched and drew
  Cameroon's tiles for nothing.
- **Preconnect to the basemap's host** (v43, live 2026-10-19).
- **Tried and dropped:** preloading the basemap style. The style came 0.9 s sooner, but the
  map was no sooner ready and the page finished parsing 0.3 s later.

**Not worth it now:** the no-data layer already loads after the map is up (108 KB);
loading Terra Draw only for mouse drawing saves 52 KB and about 20 ms of phone CPU; the
catalogue (66 KB) is not on the map's critical path; the fonts already swap in, and the
first paint is the panel's text at 0.6 s.

**Faster, but they change how the map looks (owner's call):**
- **Shaded relief at half resolution:** the basemap's relief images are most of its
  download (phone 1.5 of 2.0 MB, desktop 3.1 of 3.7 MB). At half resolution (the raster
  source's `tileSize` 512) the basemap falls to 1.2 MB on a phone and 0.7 MB on desktop, the
  map is ready 0.9 s sooner on a phone, and the relief is slightly softer when zoomed in.
  With no relief at all: 0.6 MB, 1.4 s sooner, and a flat map.
- **Cap the map's pixel ratio at 2:** on a 2.6× phone the main thread is blocked half as
  long (1.4 → 0.8 s) and the map is ready 0.3 s sooner; lines and labels are slightly
  softer on screens denser than 2×.

**The biggest remaining wait is data, not the page:** the Country tab downloads every part
of a country to draw a few thousand coarse hexagons: India 46 MB (43 s on the phone's 4G),
the USA 35 MB (34 s). A big shape at Normal detail does the same (a West Africa box, 15 s).
A small overview pack per country with only the coarse levels (a second `tier`; the pack
header already has one) would make both take about a second. That is a new item to shape.
Counting in a Web Worker (candidate 1 below) matters less than thought: the longest freeze
during those counts was 146 ms on the slowed phone, under INP's 200 ms.

Candidates still open:
1. **Count in a Web Worker,** so a large shape or a first download never blocks taps (M).
2. **Smaller first parts for big countries** (M, a pack rebuild), or the overview tier above.
3. **Self-host the basemap** (M; mostly for reliability, so the map still draws if
   OpenFreeMap is down).

### F-27 Continuous integration
- **Why:** today nothing runs unless someone runs `make check` and the browser checks by
  hand. CI runs them on GitHub's machines for every push to `wmclendon2/tally-maps` and
  every pull request: a tick or a cross on each commit, an email on failure, outside
  contributions checked before merging, and a clean machine that catches anything that
  only works because of local files or caches. Free for a public repo. It checks only.
- **Level 1, on every push and pull request (S):** a GitHub Actions workflow that installs
  uv and Python 3.12, runs `uv sync --locked`, `ruff check`, `ruff format --check` and
  `pytest`. The 150 tests take about 2 s, need no data files and fake their network calls,
  and lint is clean (2026-10-19), so the first run should pass. It includes the
  shell-version check.
- **Level 2, browser checks (M), by hand from GitHub or weekly, not on every push:**
  download CMR and CAF from HDX and their geoBoundaries outlines, build the packs and
  `dist/`, install headless Chromium, run `browser_parity.py` and `browser_countries.py`.
  About 5–10 min a run; fails when HDX or geoBoundaries is slow. Cache the downloads (Meta's
  files no longer change, so the cache rarely goes stale). Worth doing when a browser-side
  change such as F-02 makes it pay.
- **Not in CI:** the full 198-country build (6.3 GB of raw data, ~28 min locally), checks
  of every country.
- **Snags:** pushing a file under `.github/workflows/` needs a login with GitHub's
  `workflow` scope (`gh auth refresh -s workflow`, run by the owner, if the push is
  refused). The workflow file carries the copyright and SPDX header. "Require checks to
  pass before merging" is a repo setting the owner switches on.
- **Level 1 shipped 2026-10-20:** `.github/workflows/check.yml`, on every push and pull
  request; actions pinned by commit hash, uv 0.13.0, Python 3.12, read-only. Checked by a
  deliberately failing test on a throwaway branch (1 failed, 150 passed; the branch is
  deleted). Level 2 waits for a browser-side change such as F-02.

### F-30 Outline overlaps: no one counted in two countries
- **Why:** each country is clipped to its own geoBoundaries outline, and neighbouring
  outlines (from different national sources) disagree about where the border runs, so
  people in the strips between them are in both packs. Meta's files cover the country's
  bounding box, so both sides' rasters hold them.
- **Audit (built 2026-10-20):** `scripts/audit_overlaps.py`, `make overlaps` (~1 h, all
  packs); method and results in [VERIFICATION.md](VERIFICATION.md). 1,673,012 people
  (0.03%) counted twice across 161 pairs, 85 of them over 1,000; worst Bangladesh–India
  293,647, Cameroon–Nigeria 220,530, Argentina–Paraguay 108,023.
- **Not chosen: geoBoundaries CGAZ as the clipping outlines.** CGAZ is a global set with
  no overlaps (disputed areas follow US State Department lines), but measured on the packs
  (2026-10-20, by cell centre) it would drop about 20 M people (0.37%) outside its heavily
  simplified coasts (Philippines 3.6 M, Indonesia 3.4 M, Japan 1.9 M, Maldives 88%, Tuvalu
  96%), has no unit for 25 of Tally's territories (Puerto Rico, Hong Kong, Réunion, Guam
  ...; 14.8 M people), moves 3 M people between countries (7.7% of Bhutan to India), and
  was last updated 2024-04-20.
- **The fix (chosen 2026-10-20):** keep today's outlines and change only the overlaps.
  Split each overlap between two countries along CGAZ's border line (the part inside A's
  CGAZ unit goes to A, inside B's to B); a piece in neither, or in a third unit, goes to
  the side whose raster holds more people there, ties to the lower ISO3 code. Each
  country's clip is its outline minus the pieces given away. The union of all outlines is
  unchanged, so no one is lost; only double counts go.
- **Steps:** load CGAZ ADM0 (`geoBoundariesCGAZ_ADM0.gpkg`, ~150 MB, from
  `github.com/wmgeolab/geoBoundaries/raw/main/releaseData/CGAZ/`, cached under
  `data/`) in `ingest/boundaries.py`; build the clip outlines with tests; rebuild the
  affected countries' packs (`--rebuild`); rerun `make overlaps` (target: no pair over a
  few people, from pixel-edge rounding); `verify_country.py`, `verify_cities.py`, the
  browser checks and the coverage map; credit CGAZ in the docs and on About.
- **Effort:** S–M: about a day, plus 2–3 h of machine time.
- **Shipped 2026-10-20 (shell v44):** `ingest/overlaps.py` (the rule and
  the clips), `scripts/split_overlaps.py` (`make split`: decides each pair's rest from both
  rasters, writes the checked-in `reference/overlap_split.json`, 29 KB, builds every clip
  and checks that no pair's clips overlap or drop a piece), `build_packs.py` clips to
  them, `audit_overlaps.py` audits them. Two changes to the plan:
  - **A side with no CGAZ unit decides the whole overlap by people.** CGAZ folds Puerto
    Rico, Guam, the US Virgin Islands, the Northern Marianas, American Samoa, the British
    Virgin Islands and French Guiana into their parent, so a plain split would have given
    Puerto Rico's 3.6 M people (and 0.4 M more in the other US territories) to the USA's
    pack, whose raster has none of them.
  - **CGAZ is read at build time** (162 MB, pinned to the 2024-04-20 commit and checked by
    sha256). Checking in CGAZ's line through the overlaps instead came to 19–22 MB, and
    simplifying it enough to shrink it moved hundreds of km² off the line.
  - 217 pairs, 132 countries give something away (89,851 km² of outline in all: the
    USA 10,720, Libya 9,651, Argentina 8,259). The rest (in neither CGAZ
    unit) held people in 21 pairs, nearly all of them the territories above.
  - **Result:** the audit finds 0 people counted twice (was 1,673,012); the world total
    fell by 1,713,213; every check passes (VERIFICATION.md, "The split").

### F-31 A tab left open survives a deploy
- **Why:** a page reads the catalogue once, when it loads, and packs are named by their
  content, so a deploy that rebuilds a country renames its files. A tab opened before the
  deploy then asks for files that are gone: on 2026-10-20 Puerto Rico answered "Query
  failed: Puerto Rico data: HTTP 404" on tallymaps.org in a tab older than the 2026-10-19
  deploy, which made no sense to the person reading it. A shape over several countries
  says "At least N" and names the missing one. An installed app is the same, and stays
  open longest. Every data deploy does this (whole people renamed every pack, F-30 132
  countries'), and a release with new features but no new data reaches an open tab only
  when it is reloaded.
- **Decided 2026-10-20 (maintainer):** both parts below; keep the shape across the reload;
  never reload by itself; the notice shows on phones too.
- **Approach (all in the browser, only Tally's own files):**
  1. **Say a new version is ready, like a web app does.** A line in the panel, "A new
     version of Tally is available.", with a Reload button. It shows when a new service
     worker takes over the page (`controllerchange`; the worker already skips waiting and
     claims open tabs), which every release does, new data or not, since any change to the
     app bumps `sw.js`; and when the retry below finds a newer catalogue. An open tab rarely
     looks for a new worker, so the page calls `registration.update()` (one small request
     for `sw.js`) on returning to the tab (`visibilitychange`) and once an hour while it
     stays visible: a tab left on a second screen never comes back to. Offline, the checks
     fail quietly and nothing shows. On phones the line shows even with the bottom sheet
     folded, as a thin bar at the top of its visible part.
  2. **Retry on a renamed pack, so the 404 never reaches anyone.** When a part download
     returns 404, `static.js` fetches the manifest and catalogue again (`cache:
     "no-cache"`, a 304 when nothing changed), rebuilds its part index, prunes stored parts
     the new catalogue no longer lists (as at load), and runs the query once more. The
     user sees the right number, a moment late, and the notice. If it still fails, the
     message says so in plain words, never an HTTP status: "Tally's data has been updated.
     Reload to get the latest.", with the same Reload button. `StaleAppError`'s "App out of
     date. Reload to update." gets the same wording and button.
  3. **Reload keeps the shape.** The drawn shape or circle (and the tab: Shape, Radius,
     Country) is kept in `sessionStorage` for that tab and restored after the reload, so
     Reload comes back to the same count. Never reload by itself: a page that reloads while
     it is being read is jarring, and one tap costs nothing once the shape is kept.
- **Not changed:** the shell versioning stays; the retry is in `static.js` alone, so
  `ingest/resolve.py` and the parity checks are unaffected. A `static.js`/`app.js` change
  bumps the shell (v45).
- **Checks:** a browser check that serves a build and loads the page, then without
  reloading: (a) bumps the shell only (a new feature, no new data) and expects the notice
  after a visibility change; (b) swaps in a rebuilt country (new catalogue, new pack
  names, old files deleted) and expects the right total with no error, then the notice;
  (c) presses Reload and expects the same shape and count back. Run it with the other
  browser checks.
- **Effort:** S: about one session, plus the browser check.
- **Privacy:** ✅. The checks fetch Tally's own `sw.js`, manifest and catalogue; they add
  requests to the host's counts, but no page view (the hosted site's analytics count page
  loads only). The kept shape stays in that tab's `sessionStorage`, on the device.

## Notes on Inbox items

- **F-04:** PNG export (Save image) shipped 2026-09-18; this item is the data formats,
  CSV and GeoJSON, beside it.
- **F-06:** needs ADM1/ADM2 outlines from geoBoundaries (same licence) as a light vector
  layer per country; clicking an area runs the normal shape query on its outline. Whole
  countries already work through the Country tab.
- **F-07:** parked 2026-10-19: not wanted for now (maintainer's call). If revived, the
  extra demographic rasters become more columns in a pack: a format minor bump, so old
  packs keep working.
- **F-13:** builds on F-29, so "which download is this total from?" has an answer when
  Meta replaces a file.
- **F-26:** waits for feedback: watch a few people open Tally for the first time without
  help. If they find **Draw a shape** at once, drop it. Two shapes: a bubble by **Draw a
  shape** shown once (remembered on the device), or open on an example already counted
  (Cameroon, as on the link-preview card), which downloads data before anyone asks.
- **F-28:** `scripts/static_parity.py` covers Africa and Brazil only. Add a USA and an
  India shape, a European border, a Greenland coast, and Detroit–Windsor, the first border
  between a Meta country and a WorldPop one.
- **F-29:** record each pack's source file address (HDX or WorldPop) and the download's
  sha256 in its header and the catalogue: a pack format minor bump.
- **F-32:** for the maintainer's own understanding, and to inform whether Tally should
  eventually move to WorldPop altogether now that Meta's maps are no longer updated
  (background: [DATA_SOURCES.md](DATA_SOURCES.md)). A script beside `verify_country.py`
  builds both sources' cells for one country on the same grid and outline, and compares
  national totals (with a UN or census figure as a third reference), agreement by place
  at fine, district and regional levels, the people each finds where the other has none,
  the urban–rural split and totals around cities, plus a difference map. Use WorldPop's
  map for the same year as each Meta file, and compare at district level and coarser
  rather than at the finest level (30 m vs 100 m). Countries: a mix such as Cameroon, a
  dense one (Bangladesh or Nigeria), one with an old census (DR Congo), a European one
  (Switzerland), a small island, and the USA near Detroit. Results go in
  `docs/VERIFICATION.md`. Effort: S for the script, plus downloads and compute per country.
  GHS-POP could be added as an independent third view.
- **F-33:** a continent entry at the head of each continent group in the Country tab.
  The total is free: the sum of the catalogue's country totals (`continent` is already
  there), exact and with no download. The map is not: it would download every pack of the
  continent today (Asia 94 MB, Africa 50 MB, North America 48 MB). It needs the overview
  tier from F-25 (coarse levels only, one file per continent): continents hold 960 to
  2,806 cells at resolution 3 and 4,676 to 14,636 at resolution 4, so the world's
  resolutions 3–4 come to well under 1 MB gzipped, and the same files speed up the Country
  tab for India and the USA. Totals are only honest with every country in: missing today
  are about 40% of Asia (China, Pakistan, Iran, Myanmar…), 25% of Europe (Russia,
  Ukraine), 8% of Africa (Sudan, Morocco, Somalia, South Sudan, Western Sahara), 7% of
  South America (Venezuela), 2% of North America (Cuba and small islands), so Africa, the
  Americas and Oceania could ship first with a "not included" line, Asia and Europe after
  F-10 (or a move to WorldPop, F-32). Also: Natural Earth's continents by whole country
  (Russia in Europe, Turkey in Asia, Egypt in Africa), said in the app; Oceania and North
  America cross the 180° meridian (fitting the view, and `verify_country.py` refuses
  it); the level rule goes into `ingest/resolve.py` and `static.js` together, with
  parity; the answer has the same shape as a country's; overview files kept in IndexedDB
  for offline use. Effort: M for the overview tier, then S for continents.
- **F-34:** language and people-group areas are the hard real-world shapes (multipolygons,
  holes, coastlines, areas across borders, whole-country areas) and what upload (F-02)
  users will bring. Use open, CC BY datasets only: Glottography's digitised Atlas of the
  World's Languages (6,992 areas, github.com/glottography/asher2007world) and IMB's
  peoplegroups.org polygons (12,384 areas, a public ArcGIS service). (1) A handful of them,
  credited, as parity shapes (F-28) and F-02 test files. (2) A load test: every polygon
  through `ingest/resolve.py` and the packs, for time per country and the shapes that hit
  the 40,000-cell limit. (3) Agreement between sources for the same language (joined by
  ISO 639-3 or Glottocode). A first trial counted 600 IMB areas in Cameroon, Kenya and
  Brazil in about 30 s. Licensed polygon sets (WLMS, Ethnologue) never go in the repo;
  with F-02, licence holders count their own files on their own device.
- **F-35:** Normal uses the finest level whose cover stays within 4,000 cells
  (`AUTO_TARGET_CELLS`); Fine is one level finer (~7x, so at most ~28,000); the 40,000
  cap (`MAX_CELLS_LIMIT`) is a safety bound that refuses rather than answer coarser, and
  also bounds one query's memory. So in the app the cap rarely binds (thin or ragged
  shapes, such as the language areas of F-34, might), and the real limit is the 4,000
  target: it sets both the map's detail and the edge error of the total (each hexagon
  counts by its centre; Canada at resolution 7: -1.5%). Measure, on desktop and the
  slowed phone of F-25, at 4k, 28k, 40k, 100k and 200k cells: polyfill time, lookup
  time, memory against `MEMORY_BUDGET`, and map drawing (downloads do not change: parts
  hold every level). Measure what it gains: how the total moves between levels for a
  city, a district, a language area and a country-sized box. Then decide: keep, raise the
  cap, offer a third level (`MAX_DETAIL` already allows 2), raise the target, or count at
  a finer level than is drawn (a more accurate total without drawing more hexagons).
  Any change goes into `ingest/resolve.py` and `static.js` together, with
  `browser_parity.py`.
- **F-18:** parked because it needs a server and conflicts with the privacy promise.
  Revisit if F-02 shows demand beyond what a browser can handle.

## Shipped

| ID   | Feature                                                                                                                               | Date       |
| ---- | ------------------------------------------------------------------------------------------------------------------------------------- | ---------- |
| —    | Save image: a PNG of the map and the count, with credits                                                                              | 2026-09-18 |
| —    | Install as an app (PWA); the app shell opens offline                                                                                  | 2026-09-18 |
| —    | Static demo with no server (Cameroon and the Central African Republic)                                                                | 2026-09-23 |
| —    | Offline country packs, downloaded on demand                                                                                           | 2026-10-07 |
| F-14 | Fix: the heatmap vanished when zoomed out                                                                                             | 2026-10-13 |
| F-15 | Fix: Fine looked disabled after it was picked                                                                                         | 2026-10-13 |
| —    | Libraries and fonts vendored; no third-party CDN                                                                                      | 2026-10-13 |
| —    | All 196 countries and territories (Jersey from WorldPop 100 m)                                                                        | 2026-10-13 |
| F-11 | Memory cap: one level per part, least recently used out (763 MB → 150 MB peak)                                                        | 2026-10-13 |
| —    | Country picker: whole-country population, area and density                                                                            | 2026-10-13 |
| —    | Saved countries grouped by continent, with Save all for offline use                                                                   | 2026-10-13 |
| F-08 | Radius around a point; metric and imperial units                                                                                      | 2026-10-13 |
| —    | Panel redesign: Shape / Radius / Country tabs                                                                                         | 2026-10-13 |
| F-03 | Phones: crosshair drawing, folding bottom sheet                                                                                       | 2026-10-14 |
| F-20 | The ? sheet becomes About Tally                                                                                                       | 2026-10-14 |
| F-19 | New shapes and circles zoom to fill the map                                                                                           | 2026-10-14 |
| —    | Tap a hexagon for its own estimate                                                                                                    | 2026-10-14 |
| F-23 | A × on the count replaces the Clear button                                                                                            | 2026-10-14 |
| F-21 | The radius moves to a round number when the units change                                                                              | 2026-10-14 |
| F-06 | Whole countries (the Country tab); regions and districts parked                                                                       | 2026-10-14 |
| F-12 | Static-only app: the database and API retired, one way to run Tally                                                                   | 2026-10-15 |
| F-22 | Who made Tally: a personal project, the author in the copyright line, the address on exported images (contact email left out for now) | 2026-10-15 |
| —    | Every count of people says "(est.)"; About opens with "calculates an estimate"                                                        | 2026-10-16 |
| —    | Link previews: Open Graph tags and a Cameroon heatmap card                                                                            | 2026-10-16 |
| —    | favicon.ico, apple-touch-icon.png, robots.txt and sitemap.xml at the site root                                                        | 2026-10-16 |
| F-10 | Canada and Greenland from WorldPop 100 m (198 countries and territories)                                                              | 2026-10-18 |
| —    | Whole people in every hexagon: no "0 people" or fractions; packs 35% smaller                                                          | 2026-10-18 |
| F-27 | Continuous integration, level 1: lint and tests on every push and pull request | 2026-10-20 |
| F-30 | Outline overlaps split along CGAZ: no one counted in two countries (1.67 M were) | 2026-10-20 |
