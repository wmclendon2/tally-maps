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
| F-07 | Age and sex breakdown (Meta's women, under-5s, youth, 60+ layers) | Inbox | L | ★★★ | ✅ | Pack format minor bump |
| F-09 | Compare two shapes, or one area across data years | Inbox | M | ★★ | ✅ | F-13 for years |
| F-10 | WorldPop 100 m for countries Meta lacks | Shaped (Jersey, Canada, Greenland done) | M | ★★★ | ✅ | F-30 for disputed borders |
| F-13 | Data versioning and refresh of saved packs | Inbox | M | ★★ | ✅ | F-29 |
| F-16 | Embeddable widget for articles | Inbox | M | ★★ | ✅ | F-05 |
| F-17 | Printable one-page report (PDF) for an area | Inbox | M | ★★ | ✅ | F-04 |
| F-18 | Public batch API for developers | Parked | L | ★★ | ⚠️ | — |
| F-25 | Make the app faster, first visit and large shapes | Shaped | M | ★★ | ✅ | — |
| F-26 | First-visit welcome: a one-time hint, or open on an example count | Inbox | S | ★ | ✅ | Feedback from first-time users |
| F-27 | Continuous integration: tests and lint on every push | Inbox | S–M | ★★ | ✅ | — |
| F-28 | Parity polygons outside Africa and Brazil | Inbox | S | ★★ | ✅ | — |
| F-29 | Provenance per pack: the source file's address and hash | Inbox | S–M | ★ | ✅ | — |
| F-30 | Outline-overlap audit: no one counted in two countries | Inbox | S–M | ★★ | ✅ | — |
| F-31 | "New version available" notice for a tab left open | Inbox | S | ★ | ✅ | — |

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

A first visit is about 774 KB over the wire before map tiles: MapLibre 276 KB, the no-data
layer 157 KB, the catalogue 76 KB, fonts 90 KB, h3-js 66 KB, Terra Draw 50 KB, the app
28 KB. A count then downloads the parts it needs, up to about 3.4 MB each, once.

Candidates, most likely payoff first:
1. **Count in a Web Worker,** so a large shape or a first download never blocks taps (M).
2. **Load the no-data layer after the map is up,** or tile it (S).
3. **Load Terra Draw only for mouse drawing;** touch screens no longer use it (S).
4. **Slim the catalogue at start-up** to what the first view needs (S).
5. **Preload the count's font and preconnect to the tile host** (S).
6. **Smaller first parts for big countries** (M, a pack rebuild).
7. **Self-host the basemap** (M; mostly for reliability, so the map still draws if
   OpenFreeMap is down).

## Notes on Inbox items

- **F-04:** PNG export (Save image) shipped 2026-09-18; this item is the data formats,
  CSV and GeoJSON, beside it.
- **F-06:** needs ADM1/ADM2 outlines from geoBoundaries (same licence) as a light vector
  layer per country; clicking an area runs the normal shape query on its outline. Whole
  countries already work through the Country tab.
- **F-07:** the extra Meta demographic rasters become more columns in a pack: a format
  minor bump, so old packs keep working.
- **F-13:** builds on F-29, so "which download is this total from?" has an answer when
  Meta replaces a file.
- **F-26:** waits for feedback: watch a few people open Tally for the first time without
  help. If they find **Draw a shape** at once, drop it. Two shapes: a bubble by **Draw a
  shape** shown once (remembered on the device), or open on an example already counted
  (Cameroon, as on the link-preview card), which downloads data before anyone asks.
- **F-27:** run `make check` (lint and tests, including the shell-version check) on every
  push; the browser checks (`browser_parity.py`, `browser_countries.py`) need Chrome and a
  build, so perhaps on a small build or by hand before a release.
- **F-28:** `scripts/static_parity.py` covers Africa and Brazil only. Add a USA and an
  India shape, a European border, a Greenland coast, and Detroit–Windsor, the first border
  between a Meta country and a WorldPop one.
- **F-29:** record each pack's source file address (HDX or WorldPop) and the download's
  sha256 in its header and the catalogue: a pack format minor bump.
- **F-30:** for every pair of built countries whose outlines overlap, the people each pack
  holds inside the overlap; fail where both hold people there. Meta's files cover their
  country's bounding box, so a Meta pack can hold people inside a neighbour's claim (India's
  outline takes in Pakistan-administered Kashmir).
- **F-31:** a tab keeps the version it loaded until it is reloaded. Show a small "New
  version available, reload" notice when a new service worker takes over, or when the
  catalogue changes.
- **F-18:** parked because it needs a server and conflicts with the privacy promise.
  Revisit if F-02 shows demand beyond what a browser can handle.

## Shipped

| ID | Feature | Date |
| --- | --- | --- |
| — | Save image: a PNG of the map and the count, with credits | 2026-09-18 |
| — | Install as an app (PWA); the app shell opens offline | 2026-09-18 |
| — | Static demo with no server (Cameroon and the Central African Republic) | 2026-09-23 |
| — | Offline country packs, downloaded on demand | 2026-10-07 |
| F-14 | Fix: the heatmap vanished when zoomed out | 2026-10-13 |
| F-15 | Fix: Fine looked disabled after it was picked | 2026-10-13 |
| — | Libraries and fonts vendored; no third-party CDN | 2026-10-13 |
| — | All 196 countries and territories (Jersey from WorldPop 100 m) | 2026-10-13 |
| F-11 | Memory cap: one level per part, least recently used out (763 MB → 150 MB peak) | 2026-10-13 |
| — | Country picker: whole-country population, area and density | 2026-10-13 |
| — | Saved countries grouped by continent, with Save all for offline use | 2026-10-13 |
| F-08 | Radius around a point; metric and imperial units | 2026-10-13 |
| — | Panel redesign: Shape / Radius / Country tabs | 2026-10-13 |
| F-03 | Phones: crosshair drawing, folding bottom sheet | 2026-10-14 |
| F-20 | The ? sheet becomes About Tally | 2026-10-14 |
| F-19 | New shapes and circles zoom to fill the map | 2026-10-14 |
| — | Tap a hexagon for its own estimate | 2026-10-14 |
| F-23 | A × on the count replaces the Clear button | 2026-10-14 |
| F-21 | The radius moves to a round number when the units change | 2026-10-14 |
| F-06 | Whole countries (the Country tab); regions and districts parked | 2026-10-14 |
| F-12 | Static-only app: the database and API retired, one way to run Tally | 2026-10-15 |
| F-22 | Who made Tally: a personal project, the author in the copyright line, the address on exported images (contact email left out for now) | 2026-10-15 |
| — | Every count of people says "(est.)"; About opens with "calculates an estimate" | 2026-10-16 |
| — | Link previews: Open Graph tags and a Cameroon heatmap card | 2026-10-16 |
| — | favicon.ico, apple-touch-icon.png, robots.txt and sitemap.xml at the site root | 2026-10-16 |
| F-10 | Canada and Greenland from WorldPop 100 m (198 countries and territories) | 2026-10-18 |
| — | Whole people in every hexagon: no "0 people" or fractions; packs 35% smaller | 2026-10-18 |
