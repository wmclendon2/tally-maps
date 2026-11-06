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
| F-05 | Shareable link to a shape (in the URL fragment) | Inbox | S | ★★ | ✅ | — |
| F-06 | Pick a region or district instead of drawing | Parked (country level shipped) | M | ★★★ | ✅ | ADM1/ADM2 outlines |
| F-07 | Age and sex breakdown (Meta's women, under-5s, youth, 60+ layers) | Parked | L | ★★★ | ✅ | Pack format minor bump |
| F-09 | Compare two shapes, or one area across data years | Inbox | M | ★★ | ✅ | F-13 for years |
| F-13 | Data versioning and refresh of saved packs | Inbox | M | ★★ | ✅ | F-29 |
| F-16 | Embeddable widget for articles | Inbox | M | ★★ | ✅ | F-05 |
| F-17 | Printable one-page report (PDF) for an area | Inbox | M | ★★ | ✅ | F-04 |
| F-18 | Public batch API for developers | Parked | L | ★★ | ⚠️ | — |
| F-25 | Make the app faster, first visit and large shapes | Shaped (big countries and shapes shipped) | M | ★★ | ✅ | — |
| F-26 | First-visit welcome: a one-time hint, or open on an example count | Inbox | S | ★ | ✅ | Feedback from first-time users |
| F-27 | Continuous integration: tests and lint on every push | Shaped (level 1 shipped) | S–M | ★★ | ✅ | — |
| F-28 | Parity polygons outside Africa and Brazil | Inbox | S | ★★ | ✅ | — |
| F-29 | Provenance per pack: the source file's address and hash | Inbox | S–M | ★ | ✅ | — |
| F-32 | Compare Meta and WorldPop where both have data | Inbox | S–M | ★★ | ✅ | — |
| F-34 | Tests with real language and people-group areas (open data) | Inbox | S–M | ★★ | ✅ | — |
| F-35 | Detail limits: measure the cost and accuracy of more cells | Inbox | S (M if it leads to a change) | ★★ | ✅ | After F-34 |
| F-36 | WorldPop everywhere: one source for every country | Inbox (for a future decision) | M–L | ★★ | ✅ | F-32 |
| F-38 | Atolls and small islands: people outside a coarse outline (Tarawa: 4,740) | Inbox | S–M | ★★ | ✅ | — |
| F-40 | Open GeoPackage files in the File tab (F-02), with `sql.js` loaded only when one is opened | Inbox | M | ★★ | ✅ | — |

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
- **Decided 2026-11-02:** French, Spanish, Portuguese and Indonesian first (Arabic waits for
  its right-to-left pass); the About sheet is translated too, disclaimer included; the
  translations ship as drafts, and About says so in each language and invites corrections
  on GitHub (`_draft` in each file; drop it once a native speaker has checked one).
- **Shipped 2026-11-02 (shell v58):** `web/i18n.js` loads `web/i18n/<lang>.json` (en.json is
  the reference and the fallback for a missing key); plurals by `Intl.PluralRules`, numbers,
  lists and dates by `Intl`, country names by `Intl.DisplayNames` (a language may name a
  country itself, `country.HKG`, where the browser's name reads badly in a list; English
  keeps the catalog's names). The language is the one picked (a globe beside the ?, or
  About), else the browser's, else English (picking the browser's own forgets the choice);
  picking one reloads the page with the count kept. `index.html` keeps its English, marked
  with `data-i18n*`; static.js's errors a person reads carry a key. The CSV and GeoJSON keep
  English keys (tools read them); their `name` and the image are in the visitor's language.
  `tests/test_i18n.py` checks keys, placeholders, links, en.json against index.html, keys
  the code uses, and a country code for every place.
- **v59:** the header is one row (mark, name, globe, ?) on one centerline, the tagline under
  it at full width; phones show the tagline too (the row was already as tall as the tap
  targets). On v58 an iPhone set the ? lower than the globe (40 px tap targets).
- **Basemap labels:** OpenFreeMap's tiles carry each place's name in many languages
  (`name:fr`, ...); `localizeLabels` in app.js makes each label read the visitor's first,
  falling back to what the style showed (English, or Latin and local scripts), on the map
  and the saved image. OpenStreetMap's `name:pt` is mostly European Portuguese (Polónia,
  Moscovo) where the panel, from `Intl`, says Polônia: the tiles have no `name:pt-BR`.
- **Not translated:** the `?debug=1` line, the shared-link preview,
  `manifest.webmanifest`, and the basemap's own credits line.
- **Strings to check first:** the disclaimer (`about.disclaimer`), Safari's menu names in
  `about.installHow` (Indonesian especially), and Portuguese, written for Brazil
  ("bilhões", "baixar"), which Angola and Mozambique read the European way.

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
    (people-group centers, clinics, villages, project sites). A CSV of name, latitude,
    longitude and radius (or one radius for all), each counted as a circle, in the same
    table. Added 2026-10-20.
- **Limits to decide:** features per batch (500 to start), vertices, and features over
  areas with no data, which are reported and never dropped silently.
- **Risks:** multipolygons and holes need parity tests; large batches should run in a Web
  Worker so the map stays responsive.
- **Effort:** M for GeoJSON + KML + SHP with a table and CSV; L with GeoPackage and a worker.
- **Decided 2026-11-05:** GeoJSON, KML, zipped Shapefile and CSV of points first (KMZ and loose
  Shapefiles came cheaply; GeoPackage waits). A fourth tab, **File**, with the list in the
  panel: name and people on each line, and a picked row's area, density and heatmap in the
  usual count below it. A sum of the rows, with a note that people in overlapping shapes
  count once per shape. Each row is counted at its own level, the same number as drawing it:
  across 649 zones around Douala and Yaoundé that summed to within 0.22% of the finest level
  with a typical zone 0.0% off, where one shared level (res 8, res 7) put a typical zone 1.4%
  and 8.6% off. Limits: 500 rows and 64 MB, refused with a message rather than cut short.
- **Built:** on the main thread, a row at a time; 500 shapes took 9.1 s and left the page at
  19 MB, so no Web Worker yet. Revisit if a real file stalls the map.

### F-10 WorldPop for the countries Meta lacks
- **Why:** the largest gap in coverage. About 30 countries and 12 territories have no Meta
  data, among them China, Pakistan, Russia, Iran and Myanmar: over 2 billion people.
- **Approach:** one `SOURCE_FOR` entry per country in `scripts/build_packs.py` (WorldPop
  Global2 R2025A, constrained, 2020), clipped to geoBoundaries, or to Natural Earth where
  geoBoundaries has no usable outline. Verified from the packs like every other country,
  and each clipped total compared with WorldPop's own total for the country: Canada lost
  0.02% to clipping, while Greenland's geoBoundaries coast lost 99.5% and needed Natural
  Earth.
- **Batches** (order agreed 2026-10-20: large undisputed, then disputed, then small):
  1. **Done 2026-10-18:** Canada and Greenland (Jersey 2026-10-13).
  2. **Built 2026-10-20, shipped 2026-10-30 (shell v49):** Iran, Myanmar, Afghanistan,
     Yemen and North Korea, 240 M people, each within 0.007% of WorldPop's own total;
     Canada given the grown clip too (+9,428 people); 0 counted twice (VERIFICATION.md).
     Decided with the maintainer: Canada gets the same rule; the general "estimates"
     wording is enough for the old censuses; the line under a country's count already
     names its source ("Whole country, WorldPop data from 2020"), so the picker is left
     as it is; geoBoundaries is credited as CC BY 4.0 with each outline's source under its
     own license (NOTICE.md lists them). Shaped as follows. Measured on WorldPop's files:

     | Country | WorldPop 2020 | Download | geoBoundaries outline | Lost to that outline | Lost to the grown clip |
     | --- | ---: | ---: | --- | ---: | ---: |
     | Iran | 87,481,767 | 55 MB | Natural Earth 2009, public domain | 501,590 (0.57%) | 1,150 (0.001%) |
     | Myanmar | 52,825,300 | 35 MB | OpenStreetMap 2021, CC BY-SA 2.0 | 108,343 (0.21%) | 4,591 (0.009%) |
     | Afghanistan | 38,429,950 | 24 MB | geoBoundaries 2014, public domain | 1,215 (0.003%) | 0 |
     | Yemen | 35,640,680 | 12 MB | OpenStreetMap 2017, ODbL | 14,144 (0.04%) | 7,454 (0.02%) |
     | North Korea | 26,089,153 | 8 MB | WFP/OCHA 2018, CC BY 3.0 IGO | 130,089 (0.50%) | 392 (0.002%) |

     - **The outlines lose people at the coast and the land borders.** Iran's
       geoBoundaries outline is Natural Earth's, coarse enough to cut off Bandar Abbas
       (25 k in one res-7 cell) and Iranian towns on its land borders (Parsabad, Zabol);
       North Korea's misses Nampo, Myanmar's parts of the Irrawaddy delta. Natural Earth
       grown out to sea (as for HKG and the others) fixes Iran's coast but loses more at
       Myanmar's and North Korea's land borders (0.8%, 0.65%).
     - **So: a grown clip, cut along CGAZ.** WorldPop's files hold only their own
       country's people (they are cut at LSIB, the US State Department's line), and CGAZ
       draws borders along the same US lines. So a WorldPop country's clip is its outline
       grown by 0.1° (about 10 km north-south, 5.5 km east-west at 60° N), and the overlap
       split (F-30) cuts it at each built neighbor along CGAZ, as any overlap. Everyone WorldPop counts stays in
       (losses above, at most 0.02%), and nothing of a neighbor's comes in: its raster has
       no one there. The outline drawn on the map, the no-data mask and the country's area
       stay the plain geoBoundaries outline.
     - **Neighbors:** their packs now count small numbers inside the new clips, which the
       split gives to the new country: Bangladesh 8,852, Thailand 5,222, India 3,561, South
       Korea 3,220, Iraq 432, Saudi Arabia 238, Turkmenistan 265, Tajikistan 156, Armenia
       122, Turkey 16. Those packs are rebuilt; F-31 keeps open tabs working through it.
     - **Outline licenses:** Myanmar's is CC BY-SA 2.0 and Yemen's ODbL, share-alike like
       those of 81 built countries already (73 ODbL, 8 CC BY-SA); nothing new.
     - **Steps:** `SOURCE_FOR` entries and a per-country grow (`CLIP_GROW_DEG`) that
       `split_overlaps.py` and `clip_path` both use; `make split COUNTRIES="IRN MMR AFG YEM
       PRK"`; build the five and rebuild the neighbors it names; the overlap audit (target
       0 counted twice); each total against WorldPop's (target: under 0.03% lost);
       `verify_country.py`, `verify_cities.py` (new cities to test), the browser checks;
       the coverage map; About's list of WorldPop countries.
     - **Effort:** S–M: one session, plus about an hour of machine time.
  3. and 4. **Built 2026-10-22/23, shipped with batch 2 on 2026-10-30:** all 30
     remaining places; Christmas Island and Cocos stay in Australia's pack and Norfolk
     Island's WorldPop people are added to it. Decided with the maintainer, each cause
     separately: two WorldPop countries' overlap is not cut, as no pixel is in both files
     (measured pixel by pixel for all 15 such pairs); China's clip adds Natural Earth's coast
     (geoBoundaries' outline lost 2.45 M) and keeps its overlap with Hong Kong and Macau
     uncut (at most 5.4 k counted twice, by res-9 cell); a WorldPop place's Natural Earth
     clip is not cut back at other units (Sint Maarten lost 34%); Western Sahara is the whole
     territory (Natural Earth's is the strip east of the wall: 81% lost); Åland is
     Finland's ADM1 unit; the Palestinian Territories' file holds East Jerusalem, which the
     outlines draw in Israel, so its 261,554 people inside Israel's outline are in Israel's
     pack, and the name is "Palestinian Territories". Every WorldPop country is now within 0.11% of WorldPop's own
     total; the rest (VERIFICATION.md) is left as a known limit, with Dongxing, the southern
     Kurils and the Halayib triangle. Where each disputed area's people are counted:
     VERIFICATION.md.
  3. **Disputed borders, after F-30 (as planned):** Pakistan and China (Kashmir, Aksai Chin,
     Arunachal), Azerbaijan (Karabakh), Morocco and Western Sahara, Russia and Ukraine
     (Crimea), Israel, the Palestinian Territories and Syria (the Golan), Sudan and South Sudan (Abyei),
     Cyprus, Somalia, Åland (inside Finland's outline), Venezuela (Guyana's Essequibo),
     Kosovo (`KOS` at WorldPop, `XKX` at geoBoundaries). The split follows CGAZ, and so US
     State Department lines, in disputed areas: decide that, and say it on About, first.
  4. **Small places with undisputed borders:** Andorra, Luxembourg, Isle of Man,
     Montenegro, Cuba, Curaçao, Sint Maarten, Bermuda, the Falklands, Niue, Norfolk
     Island, Saint Barthélemy, Saint-Martin, Saint Pierre and Miquelon, Christmas Island
     and Cocos. Several need Natural Earth outlines; Natural Earth draws Christmas Island
     and Cocos as one shape. The grown clip may serve them too.
- **Known limits:** Russia crosses the 180° meridian, which `verify_country.py` refuses,
  so it needs another total check. China's pack will be the largest. The Vatican has no
  WorldPop file; Somaliland and Northern Cyprus have no code of their own and come with
  Somalia's and Cyprus's outlines; Siachen and Ashmore have no data.
- **Effort:** S–M for batch 2; M for batch 3; S per batch-4 country.

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
catalog (66 KB) is not on the map's critical path; the fonts already swap in, and the
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

**The biggest wait was data, not the page. Done (shell v63, live 2026-11-05):** the Country tab downloaded every
part of a country to draw a few thousand coarse hexagons, and a big shape at Normal detail did
the same. A country of 250 KB or more now has an overview pack with only its coarse levels
(129 countries, 7.7 MB, written by `build_static.py`, so no country's pack changed). It holds
every level down to the picker's Fine and at least to res 6, and it stands in for the country's
parts whenever it costs fewer bytes than the parts still missing. Every total is unchanged
(`browser_countries.py`, `browser_parity.py`).

| On the slowed phone, from nothing stored | Before (v62) | Now (v63) |
| --- | --- | --- |
| Country tab: China | 52 s, 56 MB | 2.0 s, 0.6 MB |
| Country tab: India | 45 s, 46 MB | 1.9 s, 0.3 MB |
| Country tab: USA | 36 s, 35 MB | 1.8 s, 0.5 MB |
| Country tab: Brazil | 18 s, 18 MB | 1.7 s, 0.4 MB |
| Box over the USA, Normal | 38 s, 42 MB | 1.0 s, 0.7 MB |
| Box over West Africa, Normal | 15 s, 16 MB | 1.8 s, 1.0 MB |
| Box the size of Bihar, Normal | 15 s, 16 MB | 1.5 s, 1.2 MB |

Measured 2026-11-05, median of 2 or 3, the same phone profile, served locally; the catalog grows
by about 15 KB gzipped and the time to a ready map did not move. China, Brazil and Nigeria
(6.3 s to 1.6 s) were timed at an earlier cutoff of 1 MB, which does not change their files. A
city still downloads its part, as it needs the fine levels: Fine over a Bihar-sized box, at res 6
here, reads the overview, and a shape read at res 7 or finer reads parts.
Counting in a Web Worker (candidate 1 below) matters less than thought: the longest freeze
during those counts was 146 ms on the slowed phone, under INP's 200 ms.

Candidates still open:
1. **Count in a Web Worker,** so a large shape or a first download never blocks taps (M).
2. **Self-host the basemap** (M; mostly for reliability, so the map still draws if
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
- **Why:** each country is clipped to its own geoBoundaries outline, and neighboring
  outlines (from different national sources) disagree about where the border runs, so
  people in the strips between them are in both packs. Meta's files cover the country's
  bounding box, so both sides' rasters hold them.
- **Audit (built 2026-10-20):** `scripts/audit_overlaps.py`, `make overlaps` (~1 h, all
  packs); method and results in [VERIFICATION.md](VERIFICATION.md). 1,673,012 people
  (0.03%) counted twice across 161 pairs, 85 of them over 1,000; worst Bangladesh–India
  293,647, Cameroon–Nigeria 220,530, Argentina–Paraguay 108,023.
- **Not chosen: geoBoundaries CGAZ as the clipping outlines.** CGAZ is a global set with
  no overlaps (disputed areas follow US State Department lines), but measured on the packs
  (2026-10-20, by cell center) it would drop about 20 M people (0.37%) outside its heavily
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
- **Why:** a page reads the catalog once, when it loads, and packs are named by their
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
- **Shipped 2026-10-20 (shell v45):** as below. `scripts/browser_update.py` (`make
  update-check`) runs the checks; with the retry switched off, the rebuilt-country and
  lost-pack cases fail as they should. Tabs opened before v45 have none of this; the first
  release after v45 is the first a tab can be told about.
- **Approach (all in the browser, only Tally's own files):**
  1. **Say a new version is ready, like a web app does.** A line in the panel, "A new
     version of Tally is available.", with a Reload button. It shows when a new service
     worker takes over the page (`controllerchange`; the worker already skips waiting and
     claims open tabs), which every release does, new data or not, since any change to the
     app bumps `sw.js`; and when the retry below finds a newer catalog. An open tab rarely
     looks for a new worker, so the page calls `registration.update()` (one small request
     for `sw.js`) on returning to the tab (`visibilitychange`) and once an hour while it
     stays visible: a tab left on a second screen never comes back to. Offline, the checks
     fail quietly and nothing shows. On phones the line shows even with the bottom sheet
     folded, as a thin bar at the top of its visible part.
  2. **Retry on a renamed pack, so the 404 never reaches anyone.** When a part download
     returns 404, `static.js` fetches the manifest and catalog again (`cache:
     "no-cache"`, a 304 when nothing changed), rebuilds its part index, prunes stored parts
     the new catalog no longer lists (as at load), and runs the query once more. The
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
  after a visibility change; (b) swaps in a rebuilt country (new catalog, new pack
  names, old files deleted) and expects the right total with no error, then the notice;
  (c) presses Reload and expects the same shape and count back. Run it with the other
  browser checks.
- **Effort:** S: about one session, plus the browser check.
- **Privacy:** ✅. The checks fetch Tally's own `sw.js`, manifest and catalog; they add
  requests to the host's counts, but no page view (the hosted site's analytics count page
  loads only). The kept shape stays in that tab's `sessionStorage`, on the device.

### F-04 Export results: CSV and GeoJSON
- **Why:** a count could leave Tally only as a picture (Save image, 2026-09-18). People who
  want the numbers in a spreadsheet or the hexagons in QGIS had no way to get them.
- **Shipped 2026-11-01 (shell v55):** a **Save** row under the count: Image, CSV and
  GeoJSON, all made in the browser (nothing is uploaded).
  - **CSV:** one row for the count: name, kind (shape, circle or country), country code,
    `population_estimate`, area and density in km² and mi², countries left out when a
    download failed, source, data year (countries), H3 level, hexagons, time counted.
    UTF-8 with a byte-order mark, so Excel reads accents; RFC 4180 quoting.
  - **GeoJSON:** every populated hexagon (`kind: "hexagon"`, `h3`, `population`, density),
    then the shape with the same summary, last so map tools draw it on top. A whole
    country's feature has no geometry (RFC 7946 allows it). `population` is the property
    F-02's upload will read back. Coordinates to 6 decimals; hexagons across 180° keep
    longitudes past 180, as on the map.
- **Found on the way (fixed):** a country is framed on the cells nearest its people-weighted
  middle that hold 99% of its people (`peopleBounds`; Rotuma, 0.2% of Fiji, had pushed the
  main islands to the bottom of the view). The image: `tolerance: 0` on its sources, as on
  the live map (a country at Fine was not drawn at all); hexagons under 6 px wide lose
  their gray borders (at ~2 px the heatmap read as dark noise), and Fine stays Fine; the
  inset is always 2.5 zoom levels further out than the main map (at most 4.6), so a country
  sits in its region, and fills the counted area in the heatmap's crimson, not the drawn
  shape's blue. Fiji's missing people east of 180° became F-37.

### F-37 People across the 180° line
- **Why:** found 2026-11-01 while building F-04: Fiji's pack has no one east of 180°.
  Meta's FJI raster runs 176.9° to 181.8° E, so it writes Taveuni's east side and the
  Lau islands as longitudes past 180; the geoBoundaries outline it is clipped to writes
  the same land as -180 to -179. The two never meet, so the clip drops 14,776 people
  (860,594 of the raster's people lie west of 180; the pack holds 853,556). On the map
  those islands are bare land.
- **Plan:** (1) Check: list every country whose raster, outline or
  clip touches 180° (Fiji, New Zealand's Chatham Islands, the USA's Aleutians, Kiribati,
  Russia's Chukotka; Tuvalu, Tonga and Wallis and Futuna sit near it) and compare the
  raster's people either side of 180 with the pack. (2) Repair: unwrap the clip (or
  the raster) onto one side of 180 before clipping, for Meta's past-180 grids and
  WorldPop's alike; the overlap split, the no-data mask and `audit_overlaps.py` need the
  same care. (3) A test with a cell either side of 180, then rebuild the countries hit and
  the overlap step for their neighbors. Effort: S to check, M with the rebuilds.
- **Shipped 2026-11-01:** checked: of all 233 rasters, three run past 180 (FJI to
  181.8°, KIR 202.8°, NZL 183.8°); RUS (WorldPop, −180 to 180), USA (Aleutian tiles) and TUV stay in
  −180 to 180, as their outlines do. **Repaired:** `raster.clip_for_grid` adds the
  outline moved by 360° where a grid runs past an end (a test with pixels either side,
  both ways round). Fiji 853,556 → 867,854 (+14,298: Taveuni's east side and Lau), New
  Zealand 4,519,164 → 4,519,791 (+627, the Chatham Islands); Kiribati is unchanged, as
  Meta's file has no one east of 180 (no Phoenix or Line Islands, Kiritimati included).
  `verify_country.py` covered these outlines piece by piece for the first time (it had
  refused them) and all three pass. No overlap lies near 180, so the overlap split and
  audit need no change; `audit_overlaps.py` would need the same move if one ever did.

## Notes on Inbox items

- **F-06:** needs ADM1/ADM2 outlines from geoBoundaries (same license) as a light vector
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
  sha256 in its header and the catalog: a pack format minor bump.
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
- **F-33:** a whole continent at the head of each group in the Country tab. Decided
  2026-11-06 from the shaping above and F-10's completion: every continent is complete, so
  all six ship together. The total is the catalog's country totals added up. The map comes
  from one overview pack per continent (`tier: "overview"`, resolutions 3 and 4, written by
  `build_static.py`): 22 to 101 KB each, 350 KB for all six, where the countries' own packs
  would be 5 to 170 MB. Normal is res 3 (1,105 to 3,221 cells), Fine res 4 (5,062 to 19,192),
  by the rule countries use (`countryResolution`). The overview's cells have lost which
  country they belong to, so they cannot speed up the Country tab: India and the USA still
  wait on F-25. Countries count whole, by Natural Earth's continent (Russia in Europe, Turkey
  in Asia, Egypt in Africa), and the line under the count says so. Fitting Europe's view
  needed longitudes cut at the widest empty stretch (`lngsInLine`): the old rule, "past 180
  if negative", threw Portugal round the world once Chukotka was in the same set.
- **F-34:** language and people-group areas are the hard real-world shapes (multipolygons,
  holes, coastlines, areas across borders, whole-country areas) and what upload (F-02)
  users will bring. Use open, CC BY datasets only: Glottography's digitized Atlas of the
  World's Languages (6,992 areas, github.com/glottography/asher2007world) and IMB's
  peoplegroups.org polygons (12,384 areas, a public ArcGIS service). (1) A handful of them,
  credited, as parity shapes (F-28) and F-02 test files. (2) A load test: every polygon
  through `ingest/resolve.py` and the packs, for time per country and the shapes that hit
  the 40,000-cell limit. (3) Agreement between sources for the same language (joined by
  ISO 639-3 or Glottocode). A first trial counted 600 IMB areas in Cameroon, Kenya and
  Brazil in about 30 s. Licensed polygon sets (WLMS, Ethnologue) never go in the repo;
  with F-02, license holders count their own files on their own device.
- **F-35:** Normal uses the finest level whose cover stays within 4,000 cells
  (`AUTO_TARGET_CELLS`); Fine is one level finer (~7x, so at most ~28,000); the 40,000
  cap (`MAX_CELLS_LIMIT`) is a safety bound that refuses rather than answer coarser, and
  also bounds one query's memory. So in the app the cap rarely binds (thin or ragged
  shapes, such as the language areas of F-34, might), and the real limit is the 4,000
  target: it sets both the map's detail and the edge error of the total (each hexagon
  counts by its center; Canada at resolution 7: -1.5%). Measure, on desktop and the
  slowed phone of F-25, at 4k, 28k, 40k, 100k and 200k cells: polyfill time, lookup
  time, memory against `MEMORY_BUDGET`, and map drawing (downloads do not change: parts
  hold every level). Measure what it gains: how the total moves between levels for a
  city, a district, a language area and a country-sized box. Then decide: keep, raise the
  cap, offer a third level (`MAX_DETAIL` already allows 2), raise the target, or count at
  a finer level than is drawn (a more accurate total without drawing more hexagons).
  Any change goes into `ingest/resolve.py` and `static.js` together, with
  `browser_parity.py`.
- **F-36:** raised 2026-10-22 while building every WorldPop country (F-10). WorldPop's
  files are cut at LSIB, so each holds only its own people and two WorldPop neighbors
  never count the same person: their overlaps need no cut (the overlap split keeps them
  in both clips; the overlap audit measures what both rasters fill). Meta's files cover
  each country's bounding box, so every Meta border needs the clip and the overlap split,
  and a border between Meta and WorldPop is where people get lost: China's coast by Hong
  Kong and Macau, Dongxing on the Vietnam border, the band handed across a CGAZ line.
  For: no overlap split at most borders, one source and year everywhere, and a source
  still updated (Meta's maps are not). Against: 100 m instead of 30 m (Meta traces
  buildings), every pack rebuilt and every country's total changes (a visible step for
  people who noted one), disputed areas follow LSIB everywhere (as CGAZ does today), and
  the checks and docs redone. Decide after F-32's comparison. Effort: M–L, mostly machine
  time (downloads and a full rebuild).
- **F-38:** found 2026-11-01 while checking F-37: Kiribati's raster holds 98,163 people
  and its pack 93,423. The 4,740 the clip drops are on Tarawa's atoll strips (1,583 by
  Betio and Bairiki, more along North Tarawa) and lie outside geoBoundaries' outline, as
  Greenland's coastal towns once did. Check every island country for raster people its
  clip drops with no neighbor to take them (the build's `clipped_total` already counts
  them), then, where the outline is too coarse, grow the clip as WorldPop's are grown
  (`boundaries.CLIP_GROW_DEG`) or use Natural Earth's coast. Likely candidates: Kiribati,
  the Marshall Islands, Tuvalu, the Maldives, Micronesia, Tokelau. Effort: S to check,
  S–M to repair and rebuild.
- **F-39:** found 2026-11-05 while shaping F-02. A polygon fill leaves out whatever lies past
  180° with no error, and a Radius circle or a drawn shape across the line runs past it: a
  50 km circle on Taveuni counted 9,272 of 15,103 people (res 8), and a shape wholly past
  the line counted nobody. `static.js` and `ingest/resolve.py` now wrap longitudes before
  the fill; two new parity cases cover it (20 in all). Uploaded files (F-02) go through the
  same path. Effort: S.
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
| F-31 | A tab left open survives a release: "new version" notice, retry on a renamed pack, the count kept across Reload | 2026-10-20 |
| F-10 | WorldPop 100 m for the other 35 places Meta lacks (233 countries and territories); disputed borders through the overlap split | 2026-10-30 |
| F-04 | Export results as CSV and GeoJSON; countries framed on their people; clearer saved images | 2026-11-01 |
| F-37 | People across the 180° line: Fiji +14,298 (Taveuni, Lau), New Zealand +627 (Chatham Islands) | 2026-11-01 |
| F-01 | French, Spanish, Portuguese and Indonesian (drafts, corrections invited): the panel, About, errors, the saved image and the map's place names; a globe beside the ? to switch | 2026-11-02 |
| F-39 | People past the 180° line: a Radius circle or drawn shape across it is counted in full (50 km on Taveuni: 15,103, was 9,272) | 2026-11-05 |
| F-02 | Open a file of shapes or points (GeoJSON, KML, KMZ, Shapefile, CSV): up to 500 rows counted on the device, each listed with a sum, click one for its heatmap, save all as CSV or GeoJSON | 2026-11-05 |
| F-33 | Whole continents in the Country tab: total, density map and saving, from small overview packs (350 KB for all six) | 2026-11-05 |
| F-25 | Big countries and big shapes in seconds, from small overview packs (India 46 MB and 45 s on a slowed phone, now 0.3 MB and 2 s) | 2026-11-05 |
| F-41 | Round trip: a saved circle reopens as a circle, with its center, radius and count; a saved country or continent reopens in the Country tab; several files open as one list; saved files say what they hold (tally-radius-..., tally-country-cameroon-...) | 2026-11-06 |
| F-42 | Ten fixes from a first deep code review: a formula in a name stays text in the single-count CSV; counts go on when browser storage fails or an overview is missing; a zipped Shapefile is limited by what it unpacks to; kept overviews show in the ? panel and Remove takes them away; a label Tally gave a row with no name is said afresh when its saved file reopens (`auto_named`) | 2026-11-06 |
