# How Tally works

Tally answers one question: how many people live inside this shape? It does the expensive
work once, when data is prepared, so a question is answered by summing a few thousand
numbers in a browser, not by reading gigabytes of raster.

```
                     prepared once (scripts/build_packs.py)                  per question
 ┌──────────────────────────────────────────────────────────────────┐   ┌──────────────┐
 GeoTIFF ─clip to outline─▶ H3 cells, res 9 ─roll up─▶ res 8..3 ─▶ country packs ─▶ browser
 (Meta 30 m,                (sum per cell)                         (static files)   (h3-js sums)
  WorldPop 100 m)
```

There is no server and no database. `scripts/build_static.py` writes `dist/`: the app in
`web/` plus the chosen countries' packs, plain files that any web server can serve. The
browser downloads the parts of the packs a question needs and answers it with
`web/static.js`.

## Data

**Population.** Meta's [High Resolution Population Density Maps][hrsl] (30 m, CC BY 4.0),
from the Humanitarian Data Exchange. `ingest/hdx.py` finds each country's
general-population GeoTIFF through the HDX CKAN API and needs no key. Meta covers 196
countries and territories. Where Meta has no usable file, [WorldPop][worldpop] 100 m is used
(`ingest/worldpop_hub.py`): for Canada and Greenland, which Meta has no file for, and
Jersey, whose Meta file has no data over the island. Each pack records its source, so the app can say which one a number came from.

**Outlines.** Meta's country files cover the country's whole bounding box, not its
outline. Cameroon's file holds 106 million people, 79.6 million of whom live in Nigeria,
Chad and other neighbours. Every raster is therefore clipped to the country's
[geoBoundaries][gb] ADM0 outline (CC BY 4.0; GADM's licence was avoided on purpose). Six
territories with no usable geoBoundaries outline (HKG, MAC, JEY, VIR, PCN, GRL) are clipped
to Natural Earth's outline, grown slightly to catch the coast and cut at the neighbours
(`ingest/boundaries.py`). Greenland's geoBoundaries coast runs inland of its towns and kept
265 of its 55,581 people.

**Overlaps.** Neighbouring outlines come from different national sources and disagree
about where a border runs, so they overlap in strips along it, and Meta's bounding-box
files put the people there in both packs (1.67 million before the fix). So where two
outlines overlap, the overlap is split along [geoBoundaries CGAZ][cgaz], a global set with
no overlaps: the part in A's CGAZ unit goes to A, the part in B's to B, and the rest (in
neither, or in a disputed unit of its own) to the side whose raster holds more people
there. When CGAZ has no unit for one side (it folds Puerto Rico, Guam and other
territories into their parent, whose raster is empty there), the whole overlap goes by
people. Each raster is clipped to its outline minus what it gives away
(`ingest/overlaps.py`). Who gets the rest needs both rasters, so
`scripts/split_overlaps.py` decides it once and checks it in as
`reference/overlap_split.json`; a build reads CGAZ itself (162 MB, downloaded once,
pinned to one release and checked by hash). CGAZ alone was not used for clipping: its simplified coasts would
drop about 20 million people, and it has no unit for 25 of Tally's territories.

**Binning.** `ingest/raster.py` streams the raster window by window. Each populated pixel's
centre goes into the H3 cell that contains it, at a base resolution of 9 (about 0.1 km²).
The cells are then rolled up into their parents down to resolution 3 (`ingest/pyramid.py`),
so every level sums to the same total. Some Meta GeoTIFFs store their strips out of order;
these are read in file order, which makes Algeria 15 times faster with identical output.

**Whole people.** The models spread people in fractions: WorldPop gives each building in
rural Canada a share, so most of Canada's finest hexagons held under half a person and the
map showed coloured hexagons labelled "0 people". So the pyramid is converted to whole
people before it is packed (`whole_people` in `scripts/build_packs.py`). The country's
rounded total is shared among its resolution-3 cells, then each cell's whole people among
its seven children, in proportion to the model's figures, by largest remainder (ties to the
lower H3 index, so rebuilds are identical). Every level still sums to the same total, every
cell's children sum to it exactly, and no cell moves by as much as 2 people. Where people
live thinly, a parent's one person lands in its likeliest child instead of a tenth in each:
Canada's finest level went from 2.77 million cells to 0.94 million.

## The counting rule

A drawn shape is covered with the H3 cells whose **centres** lie inside it
(`polygon_to_cells`). That makes any set of shapes that do not overlap a clean partition:
adjacent shapes never count a cell twice and never leave a gap. The cost is that a cell on
the edge is either fully in or fully out, so totals near edges are approximate at coarse
resolutions.

The resolution is chosen per question (`web/static.js`, with the Python reference in
`ingest/resolve.py`):

- **Normal:** the finest resolution at which the shape needs about 4,000 cells or fewer.
  A city block and a continent both come back as a few thousand hexagons.
- **Fine:** exactly one level finer, but never finer than the source's base resolution,
  since nothing is stored below it.
- A shape that needs more than 40,000 cells is refused with a message. It is never quietly
  answered at a coarser level than the one asked for.

## Country packs

`scripts/build_packs.py` writes one pack per country into `data/packs/`, plus a
`catalog.json` that lists them, computed straight from the rasters.

- **Format.** A pack is self-describing, because a copy saved in a browser outlives the
  catalogue that listed it: the `TLYP` magic, a format major and minor version, a JSON
  header (country, source, data version, sections, total), padding to 8 bytes, then for
  each resolution the sorted H3 indexes and float32 populations. The full layout is in the
  `build_packs.py` docstring.
- **Parts.** Large countries are split into parts by their resolution-3 ancestor, taken in
  H3 index order and cut at 16 MB of raw data (about 3 MB gzipped). A part answers any
  cell whose resolution-3 parent is in its `footprint`, so the client knows exactly which
  parts a shape needs with no outlines at all. A first count over São Paulo downloads one
  part of Brazil, not the whole of it. Brazil has 6 parts, the USA 13 and India 16. All
  198 countries come to 269 MB (412 MB before whole people, which compress better).
- **Names.** Pack files are named by content hash and never change, so hosts and browsers
  can cache them forever.

## The browser client

`web/` is a static site with no build step: MapLibre GL with the OpenFreeMap basemap,
Terra Draw for drawing with a mouse, and h3-js. Libraries and fonts are vendored in
`web/vendor/` by `scripts/vendor_libs.py` (checked against the npm registry's hashes).

In static mode, `web/static.js`:

1. reads `catalog.json` and works out which parts a shape's cells fall in;
2. downloads the parts it does not have yet, checks their SHA-256, and stores them in
   IndexedDB, so later counts and offline use cost nothing;
3. sums the cells, keeping only the resolution a question reads in memory, up to 96 MB,
   least recently used first out;
4. if a part cannot be downloaded, says "At least N" and names the missing country rather
   than undercounting quietly.

The page greys out land it has no data for (`build_static.no_data`, from Natural Earth
minus the built countries' outlines). The **Country** tab sums a country's own packs at
one level, with no outline or polyfill.

**App shell and service worker.** `index.html` loads `static.js?v=N`, `app.js?v=N` and
`style.css?v=N`, and `sw.js` sets `SHELL_VERSION = "N"`. Both change together on every change
to one of those files (`tests/test_web_shell.py` fails if they drift). This exists because a
cached `app.js` once paired with a newer page that no longer sent a field it read: the map
went black and nothing said why. The app now says "Tally has been updated. Reload to get the
latest." when an answer is missing a field it needs. The service worker never caches packs
(they live in IndexedDB) or tiles.

**A tab left open across a release** (F-31). A page keeps the scripts and catalogue it
loaded, and a deploy that rebuilds a country renames its pack files. So a 404 on a pack
makes `static.js` read the catalogue again and run the query once more, and the panel says
"A new version of Tally is available." with a Reload button when the data changed or a
newer service worker took over (the page asks the worker its version: `sw.js` answers
"version"). It looks for a new worker when the tab comes back into view and hourly. Reload
keeps what was counted (`sessionStorage`); the page never reloads by itself.

**Earlier design.** Tally began with a PostGIS database and an API serving the same cells.
The static app replaced it: it answers the same questions with no server, works offline,
and keeps every drawn shape on the device. Its Python resolution rule lives on as
`ingest/resolve.py`, the reference the browser is checked against.

## Invariants

These are easy to break and hard to notice:

- `web/static.js` and `ingest/resolve.py` implement the same rules. Change both together;
  `scripts/browser_parity.py` checks 18 shapes in headless Chrome.
- Bump the shell version in both `index.html` and `sw.js` on any change to `static.js`,
  `app.js`, `style.css` or `sw.js`.
- Never remove a field from an answer that a cached client may read. To an installed PWA,
  that is a breaking change.
- Keep the `population-outline` layer and the drawn shape in any heatmap change.
- A pack's format major changes only when old readers must refuse it; additions bump the
  minor.

## Decisions

| Decision | Why |
| --- | --- |
| H3, assigned by cell centre | Hierarchical, so one roll-up serves every zoom; the centre rule makes shapes partition cleanly |
| Base resolution 9 | About 0.1 km², several source pixels per cell (30 m and 100 m data alike) |
| Clip at ingest, not at query | Each person belongs to exactly one country, so summing countries is always safe |
| geoBoundaries for outlines | CC BY 4.0; GADM's licence restricts redistribution and commercial use |
| Overlaps split along CGAZ | Keeps each country's detailed outline; only the strips two outlines share change hands |
| Static packs, no server | Nothing to run but a web server, works offline, and nothing a visitor draws leaves the browser |
| One pack per country, split by region | A first count downloads only the region it needs, and every file stays small |
| A PWA, not a native app | One code base; installs on phones and desktops |
| Vendored libraries and fonts | No third-party CDN that can break or track the site |

## Verification

Totals are checked against each country's own outline and total, and population positions are
checked against Natural Earth city locations. See [VERIFICATION.md](VERIFICATION.md).

[hrsl]: https://dataforgood.facebook.com/dfg/tools/high-resolution-population-density-maps
[worldpop]: https://www.worldpop.org
[gb]: https://www.geoboundaries.org
[cgaz]: https://www.geoboundaries.org/globalDownloads.html
