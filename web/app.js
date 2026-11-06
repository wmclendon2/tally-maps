// Copyright (c) 2026 Woody McLendon
// SPDX-License-Identifier: MIT
/* Tally: draw a polygon, count the people inside, paint the H3 heatmap.
 * Static site, no build step, no server. Libraries are vendored under web/vendor/
 * (scripts/vendor_libs.py) and load as UMD globals:
 * maplibregl, h3, terraDraw, terraDrawMaplibreGlAdapter.
 *
 * static.js answers every query in the browser from country packs it downloads and keeps
 * (index.html's <meta name="tally-data"> says where the data is; scripts/build_static.py
 * puts it there in dist/). This file draws, shows download progress, says when a country's
 * data could not be fetched, lists saved countries in the ? panel, and offers a
 * whole-country picker. */

const DATA_URL = document.querySelector('meta[name="tally-data"]').content;
// What the ?debug=1 status line reports as the backend.
const BACKEND = "Data: downloaded packs";
// The catalog loads at once (a few KB); the packs come with the first polygon.
const staticData = TallyStatic.load(DATA_URL);
// Words in the visitor's language (i18n.js, web/i18n/). Numbers are written the
// language's way: 1 234 567 in French, 1.234.567 in Spanish and Indonesian.
const { t } = TallyI18n;
const fmtFixed = (x, places) =>
  x.toLocaleString(TallyI18n.locale, { minimumFractionDigits: places, maximumFractionDigits: places });
const fmtMB = (b) => (b > 0 && b < 5e4 ? `<${fmtFixed(0.1, 1)}` : fmtFixed(b / 1e6, b >= 1e7 ? 0 : 1));
// index.html preconnects to this host, so keep the two in step.
const BASEMAP_STYLE = "https://tiles.openfreemap.org/styles/liberty";
// What the map starts on when that style cannot be read: a browser keeps its copy for a day
// at most, and the host can be down. Everything but the streets and place names works without it.
const BLANK_STYLE = {
  version: 8,
  sources: {},
  layers: [{ id: "background", type: "background", paint: { "background-color": "#efece4" } }],
};
const STYLE_WAIT_MS = 15000; // a connection that hangs says nothing, so it gets this long
let basemapStyle = BASEMAP_STYLE;
// The map opens on the view the data's bbox will fit, so a first visit loads one set of
// tiles: it used to open on Cameroon and jump to the world once the manifest came, which
// fetched and drew Cameroon's tiles for nothing (F-25). The bbox of every country; a build
// of fewer countries still opens here and then fits its own.
const START_BOUNDS = [[-180, -55.9], [180, 84.1]];

// The basemap's place names in the visitor's language. OpenFreeMap's tiles carry each
// name in many languages ("name:fr": "Pékin"); its style shows the English one, or the
// Latin and local scripts together. Each label asks for the language's name first and
// falls back to what the style showed; English keeps the style exactly. Done on
// style.load, before any tile is drawn, for the map and the saved image alike.
function localizeLabels(m) {
  const lang = TallyI18n.wanted;
  if (lang === "en") return;
  const own = (field) => ["coalesce", ["get", `name:${lang}`], ["get", field]];
  const swap = (e) =>
    !Array.isArray(e)
      ? e
      : e[0] === "get" && (e[1] === "name_en" || e[1] === "name:latin") && e.length === 2
        ? own(e[1])
        : e.map(swap);
  for (const layer of m.getStyle().layers) {
    if (layer.type !== "symbol") continue;
    const field = m.getLayoutProperty(layer.id, "text-field");
    const local = swap(field);
    if (JSON.stringify(local) !== JSON.stringify(field)) m.setLayoutProperty(layer.id, "text-field", local);
  }
}

// Sequential ramp, light -> dark (ColorBrewer PuRd, with the two palest steps dropped so
// the light end stays visible on the basemap's beige and green). Index = log10(density).
const RAMP = ["#dcc0e0", "#c994c7", "#df65b0", "#e7298a", "#ce1256", "#980043"];

const $ = (id) => document.getElementById(id);
const fmtInt = (n) => Math.round(n).toLocaleString(TallyI18n.locale);
// Every count of people says it is an estimate, so no one takes it for a census.
const est = () => t("est");
const fmtDensity = (d) => (d >= 100 ? fmtInt(d) : fmtFixed(d, d >= 10 ? 0 : 1));
const fmtArea = (a) => (a >= 100 ? fmtInt(a) : fmtFixed(a, 1));
const DEBUG = new URLSearchParams(location.search).get("debug") === "1";

// ---- units -------------------------------------------------------------------
// Everything is computed in km and km²; the ? sheet only changes what is shown (areas,
// densities, the point radius, the scale bar and the exported image). Remembered.
const UNITS = {
  metric: { dist: "km", area: "km²", km: 1, km2: 1 },
  imperial: { dist: "mi", area: "mi²", km: 1.609344, km2: 2.589988110336 },
};
let units = "metric";
try {
  if (localStorage.getItem("units") in UNITS) units = localStorage.getItem("units");
} catch (_) {}
const U = () => UNITS[units];
const showArea = (km2) => fmtArea(km2 / U().km2);
const showDensity = (perKm2) => fmtDensity(perKm2 * U().km2);

// Geodesic polygon area in km^2 (spherical excess; Chamberlain & Duquette 2007).
// Holes are subtracted. Good to well under 1% for polygons this size.
function polygonAreaKm2(geometry) {
  const R = 6371.0088;
  const rad = Math.PI / 180;
  const ringArea = (ring) => {
    let sum = 0;
    const n = ring.length;
    if (n < 3) return 0;
    for (let i = 0; i < n; i++) {
      const [lon1, lat1] = ring[i];
      const [lon2, lat2] = ring[(i + 1) % n];
      sum += (lon2 - lon1) * rad * (2 + Math.sin(lat1 * rad) + Math.sin(lat2 * rad));
    }
    return Math.abs((sum * R * R) / 2);
  };
  const polys = geometry.type === "MultiPolygon" ? geometry.coordinates : [geometry.coordinates];
  let area = 0;
  for (const rings of polys) {
    area += ringArea(rings[0]);
    for (const hole of rings.slice(1)) area -= ringArea(hole);
  }
  return area;
}

// A hexagon across the antimeridian comes back with corners at +179 and -179, which MapLibre
// draws as a band round the world. Keep its corners on one side (lng > 180 is fine there).
function hexRing(h) {
  const ring = h3.cellToBoundary(h, true);
  const lngs = ring.map((p) => p[0]);
  if (Math.max(...lngs) - Math.min(...lngs) > 180) for (const p of ring) if (p[0] < 0) p[0] += 360;
  return ring;
}

// Longitudes on one line, cut where the widest stretch of the globe holds none, so what sits
// together stays together. Usually that stretch holds the 180th meridian and nothing moves.
// Fiji and Kiribati straddle it: their west goes past 180. Europe with all of Russia runs from
// the Azores round to Chukotka: "past 180 if negative" would throw Portugal round the world
// (F-33), where this moves Chukotka alone.
function lngsInLine(lngs) {
  const sorted = [...new Set(lngs)].sort((a, b) => a - b);
  let cut = sorted[0];
  let widest = sorted[0] + 360 - sorted[sorted.length - 1];
  for (let i = 1; i < sorted.length; i++)
    if (sorted[i] - sorted[i - 1] > widest) {
      widest = sorted[i] - sorted[i - 1];
      cut = sorted[i];
    }
  return (x) => (x < cut ? x + 360 : x);
}

// [[w, s], [e, n]] round a set of cells, across the antimeridian when they straddle it: then
// e is past 180, which fitBounds takes as the short way round.
function cellsBounds(cells) {
  const pts = cells.map((c) => h3.cellToLatLng(c.h3));
  const inLine = lngsInLine(pts.map((p) => p[1]));
  const lngs = pts.map((p) => inLine(p[1]));
  const lats = pts.map((p) => p[0]);
  // Pad by about a cell, so edge cells are not cut in half.
  const pad = cells.length ? Math.sqrt(h3.cellArea(cells[0].h3, "km2")) / 111 : 0;
  return [
    [Math.min(...lngs) - pad, Math.max(-85, Math.min(...lats) - pad)],
    [Math.max(...lngs) + pad, Math.min(85, Math.max(...lats) + pad)],
  ];
}

// Where a country's people are, for framing it: the cells nearest its people-weighted
// middle that hold FRAME_SHARE of them. Framing every cell let a far island set the view
// (Rotuma, 460 km north with 0.2% of Fiji, pushed the main islands to the bottom; Easter
// Island, Alaska and Hawaii do the same). The far cells are still counted and drawn.
const FRAME_SHARE = 0.99;
function peopleBounds(cells) {
  if (!cells.length) return null;
  const pts = cells.map((c) => h3.cellToLatLng(c.h3));
  // As in cellsBounds, so an island across 180 degrees is near, not far.
  const lng = lngsInLine(pts.map((p) => p[1]));
  let total = 0, mx = 0, my = 0;
  cells.forEach((c, i) => {
    total += c.population;
    mx += lng(pts[i][1]) * c.population;
    my += pts[i][0] * c.population;
  });
  if (!(total > 0)) return cellsBounds(cells);
  mx /= total;
  my /= total;
  const kx = Math.cos((my * Math.PI) / 180);
  const order = cells
    .map((c, i) => ({ c, d: ((lng(pts[i][1]) - mx) * kx) ** 2 + (pts[i][0] - my) ** 2 }))
    .sort((a, b) => a.d - b.d);
  const kept = [];
  let sum = 0;
  for (const { c } of order) {
    kept.push(c);
    sum += c.population;
    if (sum >= total * FRAME_SHARE) break;
  }
  return cellsBounds(kept);
}

// fitBounds padding that keeps the framed area clear of the panel: beside it on a wide
// screen, a sheet at the bottom on a phone. The sheet with a count on it is taller than
// half a phone screen, so it pads by the sheet's real height, leaving MIN_MAP_PX of map
// (padding that swallows the whole map makes MapLibre ignore the fit).
const MIN_MAP_PX = 140;
const PHONE_SHEET = matchMedia("(max-width: 600px)");
function panelPadding() {
  const panel = $("panel").getBoundingClientRect();
  const most = innerHeight - MIN_MAP_PX - 24;
  let padding = { top: 40, bottom: 40, left: 32, right: 56 };
  if (innerWidth - panel.right > panel.width) padding = { ...padding, left: panel.right + 40 };
  // The same breakpoint style.css turns the panel into a bottom sheet at.
  else if (PHONE_SHEET.matches) padding.bottom = Math.min(innerHeight - panel.top + 24, most);
  else padding.top = Math.min(panel.bottom + 24, most);
  return padding;
}

function bboxOf(geometry) {
  let w = Infinity, s = 90, e = -Infinity, n = -90;
  const polys = geometry.type === "MultiPolygon" ? geometry.coordinates : [geometry.coordinates];
  for (const rings of polys)
    for (const [x, y] of rings[0]) {
      if (x < w) w = x;
      if (x > e) e = x;
      if (y < s) s = y;
      if (y > n) n = y;
    }
  return [[w, s], [e, n]];
}

function setStatus(text, isError = false) {
  const el = $("status");
  el.textContent = text;
  el.classList.toggle("error", isError);
}

// ---- map ---------------------------------------------------------------------

// A map that never reads its style never fires "load", and the app hangs off that event. An
// error with no source or tile is the style's own: a tile that fails is not a reason to give up.
function startWithoutBasemap(m, gaveUp) {
  let settled = false;
  const blank = () => {
    if (settled) return;
    settled = true;
    clearTimeout(wait);
    m.setStyle(BLANK_STYLE, { diff: false });
    gaveUp?.();
  };
  const wait = setTimeout(blank, STYLE_WAIT_MS);
  m.on("style.load", () => {
    settled = true;
    clearTimeout(wait);
  });
  m.on("error", (e) => {
    if (!e.sourceId && !e.tile) blank();
  });
}

const map = new maplibregl.Map({
  container: "map",
  style: basemapStyle,
  bounds: START_BOUNDS,
  fitBoundsOptions: { padding: panelPadding() },
  // The attribution control is added once the manifest says what to credit.
  attributionControl: false,
});
startWithoutBasemap(map, () => {
  basemapStyle = BLANK_STYLE; // a saved image starts where the map did
  $("no-basemap").hidden = false;
});
map.on("style.load", () => localizeLabels(map));
map.addControl(new maplibregl.NavigationControl(), "top-right");
// On a phone the panel is a bottom sheet; keep it above the credits bar, whose height
// depends on how many lines the credits wrap to.
function trackCreditsHeight() {
  const el = map.getContainer().querySelector(".maplibregl-ctrl-attrib");
  if (!el) return;
  const update = () =>
    document.documentElement.style.setProperty("--credits-h", `${el.offsetHeight}px`);
  update(); // now, so the map framing that follows already sees the sheet in place
  new ResizeObserver(update).observe(el);
}
// MapLibre opens folded credits and folds them on the first drag; on a phone start folded,
// as its own drag handler does it (the (i) button opens them).
const foldCredits = () =>
  map.getContainer().querySelector(".maplibregl-compact")?.classList.remove("maplibregl-compact-show");

// Bottom controls stack newest on top, so the scale goes in after the attribution to
// leave the credits flush with the bottom edge. Both are added once the data has loaded.
let scale = null;
const addScale = () => {
  scale = new maplibregl.ScaleControl({ unit: units });
  map.addControl(scale, "bottom-right");
};
// Safety net: embedded/emulated viewports can resize without firing MapLibre's observer.
window.addEventListener("resize", () => map.resize());

const HEAT_SOURCE = "population-cells";
const EMPTY = { type: "FeatureCollection", features: [] };

function addHeatLayers() {
  if (map.getSource(HEAT_SOURCE)) return;
  // tolerance 0: MapLibre's default (0.375) simplifies away shapes smaller than about a
  // third of a pixel, and at a zoom where one hex is that small, every hex is, so the
  // whole heatmap vanished at once while the polygon and the totals stayed (F-14).
  map.addSource(HEAT_SOURCE, { type: "geojson", data: EMPTY, tolerance: 0 });
  // Insert below the basemap's label layers so place names stay readable.
  const firstSymbol = map.getStyle().layers.find((l) => l.type === "symbol")?.id;
  map.addLayer(
    {
      id: "population-fill",
      type: "fill",
      source: HEAT_SOURCE,
      paint: { "fill-color": ["get", "color"], "fill-opacity": 0.7 },
    },
    firstSymbol,
  );
  map.addLayer(
    {
      id: "population-outline",
      type: "line",
      source: HEAT_SOURCE,
      paint: { "line-color": "#00000033", "line-width": 0.5 },
    },
    firstSymbol,
  );
}

// One hexagon's own estimate: on hover with a mouse, on a tap with a finger. Browsers turn a
// tap into mouse events, but iOS Safari does not always, so a tap (click) shows it too, and
// a tap off the heatmap hides it. Not while placing corners or a point: those taps are theirs.
const popup = new maplibregl.Popup({ closeButton: false, closeOnClick: false });
const showHex = (e) => {
  const n = Math.round(e.features[0].properties.population);
  popup
    .setLngLat(e.lngLat)
    .setHTML(t("hex.people", { n, est: est() }))
    .addTo(map);
};
map.on("mousemove", "population-fill", showHex);
map.on("mouseleave", "population-fill", () => popup.remove());
map.on("click", (e) => {
  if (sketch || pickingPoint || draw?.getMode() === "polygon") return;
  const features = map.getLayer("population-fill")
    ? map.queryRenderedFeatures(e.point, { layers: ["population-fill"] })
    : [];
  if (features.length) showHex({ ...e, features });
  else popup.remove();
});

// ---- drawing -----------------------------------------------------------------

let draw;
let polygonMode;

map.on("load", () => {
  addHeatLayers();
  addFileLayers();
  addRadiusLayers();
  addSketchLayers();

  polygonMode = new terraDraw.TerraDrawPolygonMode({
    // Terra Draw finishes the polygon on any click within pointerDistance px of the
    // first OR the previous vertex (default 40). That swallows short jagged segments,
    // so keep the close target small; Finish / Enter close the shape explicitly.
    pointerDistance: 12,
    styles: {
      fillColor: "#1f6feb",
      fillOpacity: 0.08,
      outlineColor: "#1f6feb",
      outlineWidth: 2,
      closingPointColor: "#1f6feb",
      closingPointOutlineColor: "#ffffff",
    },
  });
  draw = new terraDraw.TerraDraw({
    adapter: new terraDrawMaplibreGlAdapter.TerraDrawMapLibreGLAdapter({ map, maplibregl }),
    modes: [polygonMode],
  });
  draw.start();
  draw.setMode("static");

  draw.on("finish", (id, context) => {
    if (context.action !== "draw") return;
    // Keep one polygon on the map at a time.
    const others = draw
      .getSnapshot()
      .filter((f) => f.id !== id)
      .map((f) => f.id);
    if (others.length) draw.removeFeatures(others);
    draw.setMode("static");
    setDrawActive(false);
    clearCircle();
    const feature = draw.getSnapshotFeature(id);
    if (feature?.geometry?.type === "Polygon") queryPopulation(feature.geometry);
  });

  setStatus(DEBUG ? BACKEND : "");
  foldCredits();
  // The credits and the country names need the visitor's language.
  TallyI18n.ready.then(showStaticData);
});

function setDrawActive(on) {
  $("draw").classList.toggle("active", on);
  $("draw-label").textContent = t(on ? "draw.active" : "draw.start");
  $("finish").hidden = !on;
}

// Same as Terra Draw's Enter key: close the polygon in progress (needs >= 3 vertices).
function finishDrawing() {
  if (draw && draw.getMode() === "polygon") polygonMode.close();
}

$("draw").addEventListener("click", () => {
  if (!draw) return;
  setPicking(false);
  if (TOUCH) return startSketch();
  const on = draw.getMode() !== "polygon";
  draw.setMode(on ? "polygon" : "static");
  setDrawActive(on);
});

$("finish").addEventListener("click", finishDrawing);
// The × leaves with the count it closed, so keyboard focus goes to the chosen tab.
$("close-count").addEventListener("click", () => {
  clearAll();
  document.querySelector(".mode[aria-selected=true]")?.focus({ preventScroll: true });
});

// ---- drawing with a finger ---------------------------------------------------------
// Terra Draw places corners where you tap, which failed with a finger (F-03): the finger
// hides the spot, closing needs a tap within a few pixels of the first corner, and two
// quick taps zoom the map instead. So touch screens draw with a crosshair: move the map
// until the cross is on a corner and press Add corner, which is as precise as the map is
// zoomed in. A tap on the map still places a corner there, for anyone who tries that first.
// "hover: none" keeps touchscreen laptops, which have a mouse too, on Terra Draw.
const TOUCH = matchMedia("(hover: none) and (pointer: coarse)").matches;
const SKETCH_SOURCE = "sketch";
const CLOSE_PX = 24; // a corner placed this near the first one closes the shape
let sketch = null; // the corners placed so far, [lng, lat], while drawing; else null

function addSketchLayers() {
  map.addSource(SKETCH_SOURCE, { type: "geojson", data: EMPTY });
  const is = (type) => ["==", ["geometry-type"], type];
  map.addLayer({ id: "sketch-fill", type: "fill", source: SKETCH_SOURCE, filter: is("Polygon"),
    paint: { "fill-color": "#1f6feb", "fill-opacity": 0.08 } });
  map.addLayer({ id: "sketch-line", type: "line", source: SKETCH_SOURCE,
    filter: ["all", ["!", is("Point")], ["!=", ["get", "next"], true]],
    paint: { "line-color": "#1f6feb", "line-width": 2 } });
  // From the last corner to the cross (and on back to the first): where the next side goes.
  map.addLayer({ id: "sketch-next", type: "line", source: SKETCH_SOURCE,
    filter: ["==", ["get", "next"], true],
    paint: { "line-color": "#1f6feb", "line-width": 2, "line-dasharray": [2, 2] } });
  map.addLayer({ id: "sketch-corners", type: "circle", source: SKETCH_SOURCE, filter: is("Point"),
    paint: { "circle-radius": ["case", ["==", ["get", "first"], true], 7, 5], "circle-color": "#1f6feb",
             "circle-stroke-color": "#ffffff", "circle-stroke-width": 2 } });
}

// The middle of the biggest stretch of map the panel leaves free: on a phone that is above
// the bottom sheet, on a tablet beside the side panel. The cross sits there.
function crossPoint() {
  const p = $("panel").getBoundingClientRect();
  const W = innerWidth, H = innerHeight;
  const free = [[p.right, 0, W, H], [0, p.bottom, W, H], [0, 0, W, p.top], [0, 0, p.left, H]];
  const size = ([x0, y0, x1, y1]) => Math.max(0, x1 - x0) * Math.max(0, y1 - y0);
  const [x0, y0, x1, y1] = free.reduce((a, b) => (size(b) > size(a) ? b : a));
  return { x: (x0 + x1) / 2, y: (y0 + y1) / 2 };
}

const nearFirst = (pt) => {
  if (!sketch || sketch.length < 3) return false;
  const f = map.project(sketch[0]);
  return Math.hypot(f.x - pt.x, f.y - pt.y) <= CLOSE_PX;
};

function drawSketch() {
  if (!sketch) return;
  const pt = crossPoint();
  const cross = $("crosshair");
  cross.style.left = `${pt.x}px`;
  cross.style.top = `${pt.y}px`;
  const closing = nearFirst(pt);
  cross.classList.toggle("closing", closing);
  const at = map.unproject([pt.x, pt.y]);
  const next = closing ? sketch[0] : [at.lng, at.lat];
  const n = sketch.length;
  const features = sketch.map((c, i) => ({
    type: "Feature", properties: { first: i === 0 }, geometry: { type: "Point", coordinates: c },
  }));
  if (n >= 2)
    features.push({ type: "Feature", properties: {},
      geometry: { type: "Polygon", coordinates: [[...sketch, next, sketch[0]]] } },
    { type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: sketch } });
  if (n >= 1)
    features.push({ type: "Feature", properties: { next: true },
      geometry: { type: "LineString", coordinates: n >= 2 ? [sketch[n - 1], next, sketch[0]] : [sketch[0], next] } });
  map.getSource(SKETCH_SOURCE)?.setData({ type: "FeatureCollection", features });

  $("sketch-add-label").textContent = t(closing ? "sketch.close" : "sketch.add");
  $("sketch-undo").disabled = n === 0;
  $("sketch-finish").disabled = n < 3;
  $("shape-how").textContent = closing
    ? t("sketch.howClosing")
    : n === 0
      ? t("sketch.howFirst")
      : n < 3
        ? t(n === 1 ? "sketch.howOne" : "sketch.howTwo")
        : t("sketch.howMore", { n });
}

function startSketch() {
  if (sketch) return endSketch(); // the button doubles as Cancel, as Draw does with a mouse
  sketch = [];
  clearShape();
  // Two quick taps would zoom in rather than place two corners.
  map.doubleClickZoom.disable();
  $("shape-actions").hidden = true;
  $("sketch-tools").hidden = false;
  $("crosshair").hidden = false;
  setToolOnly();
  drawSketch();
}

// Stop drawing; `shape` is the finished polygon to leave on the map, if any.
function endSketch(shape = null) {
  sketch = null;
  map.doubleClickZoom.enable();
  $("shape-actions").hidden = false;
  $("sketch-tools").hidden = true;
  $("crosshair").hidden = true;
  $("shape-how").textContent = t("shape.howTouch");
  map.getSource(SKETCH_SOURCE)?.setData(shape
    ? { type: "FeatureCollection", features: [{ type: "Feature", properties: {}, geometry: shape }] }
    : EMPTY);
  setToolOnly();
}

function addCorner(lngLat, pt) {
  if (!sketch) return;
  if (nearFirst(pt)) return finishSketch();
  sketch.push(lngLat);
  drawSketch();
}

function finishSketch() {
  if (!sketch || sketch.length < 3) return;
  const geometry = { type: "Polygon", coordinates: [[...sketch, sketch[0]]] };
  endSketch(geometry);
  clearCircle();
  queryPopulation(geometry);
}

// Removes the drawn shape, whichever way it was drawn.
function clearShape() {
  if (draw) draw.clear();
  if (!sketch) map.getSource(SKETCH_SOURCE)?.setData(EMPTY);
}

$("sketch-add").addEventListener("click", () => {
  const pt = crossPoint();
  const at = map.unproject([pt.x, pt.y]);
  addCorner([at.lng, at.lat], pt);
});
$("sketch-undo").addEventListener("click", () => {
  sketch?.pop();
  drawSketch();
});
$("sketch-finish").addEventListener("click", finishSketch);
$("sketch-cancel").addEventListener("click", () => endSketch());
map.on("move", drawSketch);
map.on("click", (e) => {
  if (sketch) addCorner([e.lngLat.lng, e.lngLat.lat], e.point);
});
new ResizeObserver(() => drawSketch()).observe($("panel"));

// While a shape or a point is being placed, a phone's sheet shows only that tool, so
// the map is free to work on (style.css, #panel.tool-only).
function setToolOnly() {
  const on = Boolean(sketch) || pickingPoint;
  $("panel").classList.toggle("tool-only", on);
  if (on) setCollapsed(false);
}

// ---- the phone's bottom sheet -------------------------------------------------------
// With a count on it, the full sheet covered most of a phone's map (F-03). Folded, it shows
// just the number and what was counted; the handle on top, or a tap on the number, opens
// it again. The handle only shows at the phone breakpoint (style.css).
function setCollapsed(on) {
  $("panel").classList.toggle("collapsed", on);
  $("sheet-toggle").setAttribute("aria-expanded", String(!on));
  $("sheet-toggle").setAttribute("aria-label", t(on ? "sheet.more" : "sheet.less"));
}
$("sheet-toggle").addEventListener("click", () =>
  setCollapsed(!$("panel").classList.contains("collapsed")),
);
$("result").addEventListener("click", (e) => {
  if (e.target.closest("#close-count")) return;
  if ($("panel").classList.contains("collapsed") && e.target.closest(".count")) setCollapsed(false);
});
// Turning a phone sideways past the breakpoint leaves no handle to unfold it with.
PHONE_SHEET.addEventListener("change", (e) => {
  if (!e.matches) setCollapsed(false);
});

// ---- PWA ---------------------------------------------------------------------

if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("sw.js").catch((err) => {
      console.warn("service worker registration failed:", err);
    });
  });
}

// ---- a new version (F-31) ---------------------------------------------------------
// A tab keeps the scripts and the catalog it loaded. Every release changes sw.js, and the
// new worker takes over open tabs (it skips waiting), so when the worker in charge is newer
// than this page's scripts, or the data changed under the page (static.js), the panel says
// so with a Reload button. Reload keeps the count. The page never reloads by itself: it
// may be being read. An open tab rarely looks for a new worker, so it asks on coming back
// to the tab and hourly while it stays in view.
const PAGE_VERSION = Number(
  new URL(document.querySelector('script[src^="app.js"]').src).searchParams.get("v"),
);
const UPDATE_EVERY_MS = 60 * 60 * 1000;
const UPDATE_ON_RETURN_MS = 5 * 60 * 1000; // not on every switch between tabs
let lastUpdateCheck = Date.now();

function showUpdate(text = t("update.available")) {
  $("update-text").textContent = text;
  $("update").hidden = false;
}

// The version of the worker in charge (sw.js answers "version"), or null.
function workerVersion(worker) {
  return new Promise((resolve) => {
    const channel = new MessageChannel();
    channel.port1.onmessage = (e) => resolve(Number(e.data));
    worker.postMessage("version", [channel.port2]);
    setTimeout(() => resolve(null), 3000);
  });
}

// A first visit, or a reload that fetched this version, also hands the page to a worker:
// only one newer than the page means there is something to reload for.
async function checkWorker() {
  const worker = navigator.serviceWorker?.controller;
  const v = worker && (await workerVersion(worker));
  if (v > PAGE_VERSION) showUpdate();
}

function checkForUpdate() {
  lastUpdateCheck = Date.now();
  navigator.serviceWorker
    ?.getRegistration()
    .then((reg) => reg?.update())
    .catch(() => {}); // offline: nothing to say
  TallyStatic.checkForUpdate();
}

navigator.serviceWorker?.addEventListener("controllerchange", checkWorker);
window.addEventListener("tally:data-updated", () => showUpdate());
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible" && Date.now() - lastUpdateCheck > UPDATE_ON_RETURN_MS)
    checkForUpdate();
});
setInterval(() => {
  if (document.visibilityState === "visible") checkForUpdate();
}, UPDATE_EVERY_MS);

// What is counted, kept for this tab across the reload: the shape, the circle or the
// country. The tab (Shape, Radius, Country) and the settings are kept already.
const KEPT_COUNT = "tally-kept-count";
function keepCount() {
  keepFile();
  // A row of a file comes back with its file, which knows which row it was.
  if (fileSet?.selected != null) return;
  const kept = lastCountry
    ? { country: lastCountry }
    : radiusCenter
      ? { center: radiusCenter, km: radiusKm }
      : lastGeometry
        ? { geometry: lastGeometry }
        : null;
  try {
    if (kept) sessionStorage.setItem(KEPT_COUNT, JSON.stringify(kept));
  } catch (_) {}
}

function restoreCount() {
  restoreFile();
  let kept = null;
  try {
    kept = JSON.parse(sessionStorage.getItem(KEPT_COUNT));
    sessionStorage.removeItem(KEPT_COUNT);
  } catch (_) {}
  if (kept?.country) queryCountry(kept.country, { frame: true });
  else if (kept?.center) {
    radiusCenter = kept.center;
    radiusKm = kept.km;
    showRadius();
    queryRadius();
  } else if (kept?.geometry) {
    const feature = { type: "Feature", properties: {}, geometry: kept.geometry };
    map.getSource(SKETCH_SOURCE)?.setData({ type: "FeatureCollection", features: [feature] });
    queryPopulation(kept.geometry);
  }
}

$("update-reload").addEventListener("click", () => {
  keepCount();
  location.reload();
});

// Install lives in the ? sheet, not the toolbar: next to Draw and Clear it read as part of
// the map tools, and "Install" was easy to confuse with saving a country's data.
// Chromium fires beforeinstallprompt when the page qualifies; elsewhere the sheet says
// how to install from the browser's own menu.
let installPrompt = null;
function showInstall() {
  const installed =
    matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;
  $("install-app").hidden = installed;
  $("install").hidden = !installPrompt;
  $("install-how").hidden = Boolean(installPrompt);
}
window.addEventListener("beforeinstallprompt", (e) => {
  e.preventDefault();
  installPrompt = e;
  showInstall();
});
$("install").addEventListener("click", async () => {
  if (!installPrompt) return;
  installPrompt.prompt();
  await installPrompt.userChoice;
  installPrompt = null;
  showInstall();
});
window.addEventListener("appinstalled", () => {
  installPrompt = null;
  $("install-app").hidden = true;
});
showInstall();

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && pickingPoint) setPicking(false);
  if (e.key === "Escape" && sketch) endSketch();
  if (e.key === "Escape" && draw && draw.getMode() === "polygon") {
    draw.setMode("static");
    setDrawActive(false);
  }
});

// ---- what to count: Shape, Radius or Country ----------------------------------------
// One tab per way of counting, each with one sentence and one action, so the panel stays
// short as ways are added (it replaced a roll-open "More" box, 2026-10-13, which hid the
// options and mixed three styles of control). The choice is remembered; switching only
// cancels a shape or point in progress and keeps the last count on the map.
const MODES = ["shape", "radius", "country", "file"];
function setMode(mode, { focus = false, remember = true } = {}) {
  if (!MODES.includes(mode) || $(`tab-${mode}`).hidden) mode = "shape";
  for (const m of MODES) {
    const on = m === mode;
    const tab = $(`tab-${m}`);
    tab.setAttribute("aria-selected", String(on));
    tab.tabIndex = on ? 0 : -1;
    $(`mode-${m}`).hidden = !on;
  }
  showFileLayers(mode === "file");
  if (focus) $(`tab-${mode}`).focus();
  setPicking(false);
  if (sketch) endSketch();
  if (draw && draw.getMode() === "polygon") {
    draw.setMode("static");
    setDrawActive(false);
  }
  // Restoring at start-up must not save: it may fall back to Shape before the Country tab
  // exists, and saving that would forget the visitor's choice.
  if (remember)
    try {
      localStorage.setItem("mode", mode);
    } catch (_) {}
}
for (const m of MODES) $(`tab-${m}`).addEventListener("click", () => setMode(m));
// Arrow keys move between tabs, as a tab list should.
$("tab-shape").parentElement.addEventListener("keydown", (e) => {
  if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
  const shown = MODES.filter((m) => !$(`tab-${m}`).hidden);
  const at = shown.indexOf(document.activeElement?.dataset.mode);
  if (at < 0) return;
  const next = shown[(at + (e.key === "ArrowRight" ? 1 : shown.length - 1)) % shown.length];
  setMode(next, { focus: true });
});
const savedMode = () => {
  try {
    return localStorage.getItem("mode");
  } catch (_) {
    return null;
  }
};

// ---- around a point ----------------------------------------------------------------
// Pick a point, and Tally counts everyone within the radius: the circle is just a
// polygon (geodesic, RADIUS_VERTICES corners) sent through the same query as a drawn one.
const RADIUS_SOURCE = "radius";
const RADIUS_VERTICES = 96;
const RADIUS_KM_MIN = 0.05;
const RADIUS_KM_MAX = 3000;
let radiusKm = 5;
try {
  const v = parseFloat(localStorage.getItem("radiusKm"));
  if (v >= RADIUS_KM_MIN && v <= RADIUS_KM_MAX) radiusKm = v;
} catch (_) {}
let radiusCenter = null; // [lng, lat] once a point is picked
let pickingPoint = false;

// The points `km` from the center on a sphere (the radius app.js measures areas with),
// counter-clockwise as GeoJSON wants an outer ring.
function circle([lng, lat], km) {
  const rad = Math.PI / 180;
  const d = km / 6371.0088;
  const p1 = lat * rad;
  const ring = [];
  for (let i = 0; i < RADIUS_VERTICES; i++) {
    const bearing = (-2 * Math.PI * i) / RADIUS_VERTICES;
    const p2 = Math.asin(Math.sin(p1) * Math.cos(d) + Math.cos(p1) * Math.sin(d) * Math.cos(bearing));
    const dl = Math.atan2(
      Math.sin(bearing) * Math.sin(d) * Math.cos(p1),
      Math.cos(d) - Math.sin(p1) * Math.sin(p2),
    );
    ring.push([lng + dl / rad, p2 / rad]);
  }
  ring.push(ring[0]);
  return { type: "Polygon", coordinates: [ring] };
}

function addRadiusLayers() {
  map.addSource(RADIUS_SOURCE, { type: "geojson", data: EMPTY });
  const area = ["==", ["geometry-type"], "Polygon"];
  map.addLayer({ id: "radius-fill", type: "fill", source: RADIUS_SOURCE, filter: area,
    paint: { "fill-color": "#1f6feb", "fill-opacity": 0.08 } });
  map.addLayer({ id: "radius-line", type: "line", source: RADIUS_SOURCE, filter: area,
    paint: { "line-color": "#1f6feb", "line-width": 2 } });
  map.addLayer({ id: "radius-center", type: "circle", source: RADIUS_SOURCE,
    filter: ["==", ["geometry-type"], "Point"],
    paint: { "circle-radius": 4, "circle-color": "#1f6feb",
             "circle-stroke-color": "#ffffff", "circle-stroke-width": 2 } });
}

function clearCircle() {
  radiusCenter = null;
  map.getSource(RADIUS_SOURCE)?.setData(EMPTY);
}

function setPicking(on) {
  // Only on a change: at start-up the words may not be in yet, and the page's own are right.
  if (on !== pickingPoint)
    $("pick-label").textContent = t(on ? (TOUCH ? "pick.activeTouch" : "pick.active") : "pick.start");
  pickingPoint = on;
  $("pick-point").classList.toggle("active", on);
  map.getCanvas().style.cursor = on ? "crosshair" : "";
  if (on && draw && draw.getMode() === "polygon") {
    draw.setMode("static");
    setDrawActive(false);
  }
  if (on && sketch) endSketch();
  setToolOnly();
}

// "4.050°N" in English, "4,050°N" in French, "4,050°LU" in Indonesian.
const fmtCoord = (v, pos, neg) => t(`coord.${v >= 0 ? pos : neg}`, { v: fmtFixed(Math.abs(v), 3) });
const fmtDist = (km) => {
  const v = km / U().km;
  const shown = v >= 10 ? fmtInt(v) : (Math.round(v * 100) / 100).toLocaleString(TallyI18n.locale);
  return `${shown} ${U().dist}`;
};

const whereRadius = ([lng, lat], km) =>
  t("where.radius", { dist: fmtDist(km), at: `${fmtCoord(lat, "n", "s")}, ${fmtCoord(lng, "e", "w")}` });

function queryRadius() {
  if (!radiusCenter) return;
  const geometry = circle(radiusCenter, radiusKm);
  map.getSource(RADIUS_SOURCE)?.setData({
    type: "FeatureCollection",
    features: [
      { type: "Feature", properties: {}, geometry },
      { type: "Feature", properties: {}, geometry: { type: "Point", coordinates: radiusCenter } },
    ],
  });
  clearShape();
  queryPopulation(geometry, () => whereRadius(radiusCenter, radiusKm));
}

$("pick-point").addEventListener("click", () => setPicking(!pickingPoint));
map.on("click", (e) => {
  if (!pickingPoint) return;
  setPicking(false);
  // On a copy of the world beside the first, a click reads 373.258°: a place is said once.
  const { lng, lat } = e.lngLat.wrap();
  radiusCenter = [lng, lat];
  queryRadius();
});

// The box shows the radius in the chosen unit; radiusKm is what counts.
const showRadius = () => {
  const v = radiusKm / U().km;
  $("radius").value = String(Math.round(v * 100) / 100);
  $("radius-unit").textContent = U().dist;
};
$("radius").addEventListener("change", () => {
  const v = parseFloat($("radius").value);
  if (!(v > 0)) return showRadius();
  radiusKm = Math.min(RADIUS_KM_MAX, Math.max(RADIUS_KM_MIN, v * U().km));
  showRadius();
  try {
    localStorage.setItem("radiusKm", String(radiusKm));
  } catch (_) {}
  queryRadius();
});

// Radii a person would type, in either unit. Switching units moves the radius to the one
// nearest (on a log scale) to the old radius converted, so 5 km becomes 3 mi rather than
// 3.11 mi, and 20 mi becomes 30 km (F-21).
const ROUND_RADII = [
  0.1, 0.2, 0.25, 0.5, 1, 2, 3, 5, 10, 15, 20, 25, 30, 40, 50, 75, 100, 150, 200, 250, 300,
  400, 500, 750, 1000, 1500, 2000,
];
const roundRadius = (v) =>
  ROUND_RADII.reduce((a, b) => (Math.abs(Math.log(b / v)) < Math.abs(Math.log(a / v)) ? b : a));

let unitSwitch = null; // the last switch: {from: {units, km}, to: km}

// `fromUser` is a click on Metric or Imperial; at start-up the saved radius stays as it is.
function setUnits(u, { fromUser = false } = {}) {
  const changed = fromUser && u !== units;
  const units0 = units;
  units = u;
  if (changed) {
    // Switching straight back, without touching the radius, restores it: 20 km -> 15 mi
    // -> 20 km, not 25 km.
    if (unitSwitch && unitSwitch.to === radiusKm && unitSwitch.from.units === u) {
      radiusKm = unitSwitch.from.km;
      unitSwitch = null;
    } else {
      const from = { units: units0, km: radiusKm };
      radiusKm = Math.min(RADIUS_KM_MAX, roundRadius(radiusKm / U().km) * U().km);
      unitSwitch = { from, to: radiusKm };
    }
    try {
      localStorage.setItem("radiusKm", String(radiusKm));
    } catch (_) {}
  }
  try {
    localStorage.setItem("units", u);
  } catch (_) {}
  for (const b of document.querySelectorAll(".seg[data-units]")) {
    const on = b.dataset.units === u;
    b.classList.toggle("active", on);
    b.setAttribute("aria-pressed", String(on));
  }
  $("area-unit").textContent = U().area;
  $("density-unit").textContent = t("unit.per", { area: U().area });
  $("legend-title").textContent = t("legend.title", { area: U().area });
  showRadius();
  showFileRadius();
  // A circle Tally named says its radius in the chosen unit.
  fileSet?.rows.forEach((_, i) => showRow(i));
  scale?.setUnit(u);
  // A circle on the map is counted again at its new radius; any other count is just
  // shown in the new units.
  if (changed && radiusCenter) queryRadius();
  else if (lastResult) render(lastResult);
}
for (const b of document.querySelectorAll(".seg[data-units]"))
  b.addEventListener("click", () => setUnits(b.dataset.units, { fromUser: true }));

function clearAll() {
  if (sketch) endSketch();
  clearShape();
  if (draw) draw.setMode("static");
  setDrawActive(false);
  setPicking(false);
  clearCircle();
  lastGeometry = null;
  lastCountry = null;
  lastLabel = null;
  lastResult = null;
  selectRow(null);
  $("country-pick").value = "";
  map.getSource(HEAT_SOURCE)?.setData(EMPTY);
  $("result").hidden = true;
  $("panel").classList.remove("has-result");
  setCollapsed(false);
  setFineAvailable(true);
  popup.remove();
  setStatus(DEBUG ? BACKEND : "");
}

// ---- about sheet -------------------------------------------------------------
// The ? sheet: what Tally is, how far to trust it, settings, where its numbers come from,
// how to use it, saved data and credits. It used to open on a world map of coverage, which the gray no-data layer on
// the map itself made redundant (F-20).
const SHELL = document.querySelector('script[src^="app.js?v="]')?.src.split("?v=")[1];

async function describeData() {
  const scope = $("about-scope");
  const live = $("coverage-live");
  try {
    const m = await staticData;
    const n = m.countries.length;
    const billions = m.total / 1e9;
    const people = billions >= 1 ? t("about.billion", { n: fmtFixed(billions, 1) }) : fmtInt(m.total);
    const fig = (x) => `<span class="fig">${x}</span>`;
    // `n` picks the plural; {count} is the same number, set as a figure.
    scope.innerHTML = t("about.scope", { n, count: fig(fmtInt(n)), people: fig(people), est: est() });
    scope.hidden = false;
    $("about-version").textContent = t("about.version", { version: SHELL, built: m.built });
  } catch (err) {
    live.textContent = t("about.loadFailed", { error: errText(err) });
    live.hidden = false;
  }
}
function toggleCoverage(open) {
  if (open === !$("coverage").hidden) return;
  $("coverage").hidden = !open;
  // Only a hosted copy may add an analytics beacon (web/ and local builds never have one), so
  // visits are said to be counted only where one is on the page. Asked here, not as this file
  // loads: a hosted copy's beacon tag comes after this script, so it is not there yet.
  $("about-visits").hidden = !document.querySelector("script[data-cf-beacon]");
  if (open) describeData();
  if (open) renderSaved();
  // Into the sheet on opening, and back to the ? button on closing, for keyboards.
  (open ? $("coverage-close") : $("coverage-open")).focus({ preventScroll: true });
}

// A dialog: Esc closes it, and Tab stays inside it rather than wandering behind it.
document.addEventListener("keydown", (e) => {
  if ($("coverage").hidden) return;
  if (e.key === "Escape") return toggleCoverage(false);
  if (e.key !== "Tab") return;
  const inside = [...$("coverage").querySelectorAll("a[href], button, select, input, summary")].filter(
    (el) => el.offsetParent && !el.disabled,
  );
  const [first, last] = [inside[0], inside[inside.length - 1]];
  if (e.shiftKey && document.activeElement === first) {
    e.preventDefault();
    last.focus();
  } else if (!e.shiftKey && document.activeElement === last) {
    e.preventDefault();
    first.focus();
  }
});

// Static mode: which countries this browser holds, with Save / Remove for each.
// Grouped by continent, each a collapsible section with one button for all of it: a flat
// list of 191 countries in ISO3 order was hard to scan. Open sections stay open across the
// re-render that follows every Save or Remove.
const openContinents = new Set();
let savingContinent = null; // one bulk download at a time

async function renderSaved() {
  $("saved").hidden = false;
  const list = $("saved-list");
  try {
    const [countries, continents] = await Promise.all([TallyStatic.countries(), TallyStatic.savedContinents()]);
    const groups = byContinent(countries);
    const overview = (k) => continents.find((c) => c.key === k);
    list.replaceChildren(...[...groups].map(([k, inIt]) => continentGroup(k, inIt, overview(k))));
  } catch (err) {
    list.textContent = t("saved.readFailed", { error: errText(err) });
  }
}

// `overview`: the continent's own pack, when a count of all of it left one in this browser.
function continentGroup(continent, countries, overview) {
  const li = document.createElement("li");
  li.className = "continent";
  const details = document.createElement("details");
  details.open = openContinents.has(continent);
  details.addEventListener("toggle", () => {
    if (details.open) openContinents.add(continent);
    else openContinents.delete(continent);
  });
  const summary = document.createElement("summary");
  const name = document.createElement("span");
  name.className = "saved-name";
  name.textContent = continentName(continent);
  const state = document.createElement("span");
  state.className = "saved-state";
  const bytes = countries.reduce((a, c) => a + c.bytes, 0);
  const savedBytes = countries.reduce((a, c) => a + c.savedBytes, 0);
  const storedBytes = countries.reduce((a, c) => a + c.savedBytes + c.overviewBytes, overview?.bytes ?? 0);
  const full = countries.filter((c) => c.savedParts === c.parts).length;
  const n = countries.length;
  state.textContent =
    savingContinent === continent
      ? t("saved.downloading")
      : full === n
        ? t("saved.groupAll", { n, mb: fmtMB(storedBytes) })
        : t("saved.groupSome", { n, saved: full, mb: fmtMB(bytes - savedBytes) });
  summary.append(textBlock(name, state));

  // Buttons inside <summary> would also open or close the section; stop that.
  const action = (label, run) => {
    const b = document.createElement("button");
    b.className = "ghost small";
    b.textContent = label;
    b.addEventListener("click", async (e) => {
      e.preventDefault();
      if (savingContinent) return;
      for (const x of summary.querySelectorAll("button")) x.disabled = true;
      try {
        await run();
      } catch (err) {
        state.textContent = t("saved.failed", { error: errText(err) });
        if (err.name === "DataUpdatedError") showUpdate(errText(err));
        savingContinent = null;
        for (const x of summary.querySelectorAll("button")) x.disabled = false;
        return;
      }
      renderSaved();
    });
    summary.append(b);
  };
  if (full < countries.length)
    action(t("saved.saveAll"), async () => {
      savingContinent = continent;
      const todo = countries.filter((c) => c.savedParts < c.parts);
      const total = todo.reduce((a, c) => a + c.bytes - c.savedBytes, 0);
      let done = 0;
      try {
        // One country at a time, so a failure says which one and keeps what finished.
        for (const [i, c] of todo.entries()) {
          await TallyStatic.saveCountry(c.iso3, ({ received }) => {
            state.textContent = t("saved.groupProgress", {
              country: c.shownName,
              i: i + 1,
              n: todo.length,
              got: fmtMB(done + received),
              total: fmtMB(total),
            });
          });
          done += c.bytes - c.savedBytes;
        }
      } finally {
        savingContinent = null;
      }
    });
  if (storedBytes)
    action(t("saved.removeAll"), async () => {
      for (const c of countries) if (c.savedParts || c.overviewBytes) await TallyStatic.removeCountry(c.iso3);
      if (overview) await TallyStatic.removeCountry(overview.iso3);
    });

  const rows = document.createElement("ul");
  rows.className = "saved-list";
  // A continent's overview has no parts of its own to save, so it only ever says Saved.
  if (overview)
    rows.append(
      savedRow({
        iso3: overview.iso3,
        shownName: t("continent.all", { continent: continentName(continent) }),
        parts: 0,
        savedParts: 0,
        savedBytes: 0,
        overviewBytes: overview.bytes,
      }),
    );
  rows.append(...countries.map(savedRow));
  details.append(summary, rows);
  li.append(details);
  return li;
}

// Name over status, so a row fits a phone with its buttons beside it.
function textBlock(name, state) {
  const div = document.createElement("div");
  div.className = "saved-text";
  div.append(name, state);
  return div;
}

function savedRow(c) {
  const li = document.createElement("li");
  const name = document.createElement("span");
  name.className = "saved-name";
  name.textContent = c.shownName;
  const state = document.createElement("span");
  state.className = "saved-state";
  const full = c.savedParts === c.parts;
  state.textContent = full
    ? t("saved.full", { mb: fmtMB(c.savedBytes + c.overviewBytes) })
    : c.savedParts
      ? t("saved.partly", { got: fmtMB(c.savedBytes), total: fmtMB(c.bytes) })
      : c.overviewBytes
        ? t("saved.overview", { got: fmtMB(c.overviewBytes), total: fmtMB(c.bytes) })
        : t("saved.none", { mb: fmtMB(c.bytes) });
  li.append(textBlock(name, state));

  const action = (label, run) => {
    const b = document.createElement("button");
    b.className = "ghost small";
    b.textContent = label;
    b.addEventListener("click", async () => {
      for (const x of li.querySelectorAll("button")) x.disabled = true;
      try {
        await run();
      } catch (err) {
        state.textContent = t("saved.failed", { error: errText(err) });
        if (err.name === "DataUpdatedError") showUpdate(errText(err));
        for (const x of li.querySelectorAll("button")) x.disabled = false;
        return;
      }
      renderSaved();
    });
    li.append(b);
  };
  if (!full)
    action(t(c.savedParts ? "saved.saveRest" : "saved.save"), () =>
      TallyStatic.saveCountry(c.iso3, ({ received, total }) => {
        state.textContent = t("saved.progress", { got: fmtMB(received), total: fmtMB(total) });
      }),
    );
  if (c.savedParts || c.overviewBytes) action(t("saved.remove"), () => TallyStatic.removeCountry(c.iso3));
  return li;
}

$("coverage-open").addEventListener("click", () => toggleCoverage(true));
for (const el of document.querySelectorAll("#coverage [data-close]")) {
  el.addEventListener("click", () => toggleCoverage(false));
}
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !$("coverage").hidden) toggleCoverage(false);
});

// ---- static demo -------------------------------------------------------------

// Gray out the land the bundle has no data for, and frame the map on the rest: in a demo
// the first question is "where can I draw", and a blank result elsewhere is not a bug.
async function showStaticData() {
  let m = null;
  try {
    m = await staticData;
  } catch (err) {
    setStatus(t("status.noData", { error: errText(err) }), true);
  }
  // Added even without data: the basemap's own credits are a license condition too.
  // One string, grouped by what each credit is for: given several, MapLibre reorders them,
  // which split the two population sources apart.
  const groups = new Map();
  for (const a of m?.attribution ?? []) {
    if (!groups.has(a.what)) groups.set(a.what, []);
    groups
      .get(a.what)
      .push(`<a href="${a.url}" target="_blank" rel="noopener">${a.name}</a> (${a.licence})`);
  }
  const credits = groups.size
    ? [...groups].map(([what, links]) => `${t(`credits.${what}`)}: ${links.join(", ")}`).join(" | ")
    : undefined;
  map.addControl(
    new maplibregl.AttributionControl({ compact: PHONE_SHEET.matches, customAttribution: credits }),
  );
  addScale();
  foldCredits();
  trackCreditsHeight();
  localizeControls();
  if (!m) return;
  // A wash over the countries with no data; dashed borders round the covered ones stopped
  // helping once they ran round most of the world (191 countries, 2026-10-13).
  // Under the heatmap and the place names, so neither is grayed.
  map.addSource("no-data", { type: "geojson", data: m.nodataUrl });
  const below = map.getLayer("population-fill")
    ? "population-fill"
    : map.getStyle().layers.find((l) => l.type === "symbol")?.id;
  map.addLayer(
    {
      id: "no-data",
      type: "fill",
      source: "no-data",
      paint: { "fill-color": "#5c5c66", "fill-opacity": 0.35 },
    },
    below,
  );
  const [w, s, e, n] = m.bbox;
  map.fitBounds([[w, s], [e, n]], { padding: panelPadding(), duration: 0 });
  // A count kept across a reload comes back once the picker is filled (a country needs it).
  fillPicker()
    .catch((err) => console.warn("country picker:", err))
    .finally(restoreCount);
  checkWorker();
}

// The country picker: every country in the data, by continent then name, as the ? panel
// lists them. Picking one shows all of it, counted from its own data (TallyStatic.country).
async function fillPicker() {
  const select = $("country-pick");
  const whole = new Set(await TallyStatic.continents());
  for (const [k, countries] of byContinent(await TallyStatic.countries())) {
    const group = document.createElement("optgroup");
    group.label = continentName(k);
    // The whole continent at the head of its group (F-33).
    if (whole.has(k)) group.append(new Option(t("continent.all", { continent: continentName(k) }), `continent:${k}`));
    for (const c of countries) group.append(new Option(c.shownName, c.iso3));
    select.append(group);
  }
  for (const el of document.querySelectorAll("[data-static-only]")) el.hidden = false;
  // The Country tab only exists from here on, so a remembered choice of it is restored now.
  if (savedMode() === "country") setMode("country", { remember: false });
}

$("country-pick").addEventListener("change", (e) => {
  const iso3 = e.target.value;
  if (!iso3) return;
  if (sketch) endSketch();
  clearShape();
  if (draw) draw.setMode("static");
  setDrawActive(false);
  setPicking(false);
  clearCircle();
  queryCountry(iso3, { frame: true });
});

// "Benin, Niger and Nigeria", in the visitor's language.
const listNames = (names) =>
  new Intl.ListFormat(TallyI18n.locale, { style: "long", type: "conjunction" }).format(names);

// Places in the visitor's language: the browser names countries (i18n.js); a language
// file names the continents.
const countryName = (iso3, fallback) => TallyI18n.countryName(iso3, fallback);
const continentName = (k) => t(`continent.${k}`);
// A country or, by its code ("continent:Africa"), a whole continent.
const placeName = (code, name) =>
  code?.startsWith("continent:") ? continentName(code.slice("continent:".length)) : countryName(code, name);
const byLocalName = (a, b) => a.localeCompare(b, TallyI18n.locale, { sensitivity: "base" });

// Countries by continent, each in name order as the visitor's language sorts them, with
// "Other" last: the picker and the ? panel list them the same way. Adds `shownName`.
function byContinent(countries) {
  const groups = new Map();
  for (const c of countries) {
    const k = c.continent ?? "Other";
    if (!groups.has(k)) groups.set(k, []);
    groups.get(k).push({ ...c, shownName: countryName(c.iso3, c.name) });
  }
  const keys = [...groups.keys()].sort((a, b) =>
    a === "Other" ? 1 : b === "Other" ? -1 : byLocalName(continentName(a), continentName(b)),
  );
  return new Map(keys.map((k) => [k, groups.get(k).sort((a, b) => byLocalName(a.shownName, b.shownName))]));
}

// An error in words the visitor can read: static.js names its own (`key`), app.js's
// carry theirs; anything else is shown as it came.
function errText(err) {
  if (!err?.key) return err?.message ?? String(err);
  const vars = { ...err.vars };
  // By its code in other languages; in English by the name the catalog gives.
  if (vars.country) vars.country = placeName(vars.country, vars.name ?? vars.country);
  return t(err.key, vars);
}

// ---- queries -----------------------------------------------------------------

let inflight = null;
let lastGeometry = null;
let lastCountry = null; // ISO3 when the result is a whole country from the picker
let lastLabel = null; // () => status text saying what was counted (around a point)
let lastResult = null;
let fileSet = null; // the file open in the File tab, with its rows (F-02)

// Detail is a step relative to the resolution static.js picks for the area: Fine is always
// one H3 level finer than Normal (~7x the cells). It used to be two different cell budgets,
// which meant that on a large enough shape both landed on the same level and the toggle
// silently did nothing. The one limit that remains is the data's own: nothing is stored
// below the source's base resolution, so on a small shape Normal is already as fine as
// it gets and Fine is disabled rather than being a no-op.
const DETAIL_STEP = { normal: 0, fine: 1 };
let detail = "normal";
try {
  if (localStorage.getItem("detail") in DETAIL_STEP) detail = localStorage.getItem("detail");
} catch (_) {}

function setDetail(level) {
  detail = level;
  try {
    localStorage.setItem("detail", level);
  } catch (_) {}
  for (const b of document.querySelectorAll(".seg[data-detail]")) {
    const on = b.dataset.detail === level;
    b.classList.toggle("active", on);
    b.setAttribute("aria-pressed", String(on));
  }
  if (lastCountry) queryCountry(lastCountry);
  else if (lastGeometry) queryPopulation(lastGeometry, lastLabel, { fresh: false });
  // A file's list counts at the same detail, so its numbers and the map always agree.
  if (fileSet) countFile();
}
setDetail(detail);
for (const b of document.querySelectorAll(".seg[data-detail]")) {
  b.addEventListener("click", () => setDetail(b.dataset.detail));
}

// Nothing is stored below the source's base resolution, so once the area is small enough
// that Normal already reaches it, Fine has nowhere to go. Say so instead of leaving a
// button that changes nothing when pressed. But never gray out the button that is
// selected: after picking Fine, a disabled Fine read as if the click had not taken (F-15).
function setFineAvailable(available) {
  const fineOn = detail === "fine";
  $("detail-fine").disabled = !available && !fineOn;
  $("detail-note").textContent = available ? "" : t(fineOn ? "detail.full" : "detail.already");
  $("detail-note").hidden = available;
}

// An answer missing fields this build expects means the page and static.js have drifted
// apart -- usually a cached shell surviving an update. Left alone that shows up as
// silently wrong output (a blank heatmap, a toggle that does nothing) rather than an
// error, so say it plainly instead.
class StaleAppError extends Error {
  constructor() {
    super("Tally has been updated. Reload to get the latest.");
    this.name = "StaleAppError";
    this.key = "err.updated";
  }
}

function assertCompatible(res) {
  const cell = res.cells?.[0];
  const bad =
    typeof res.base_resolution !== "number" ||
    typeof res.total !== "number" ||
    (cell && (typeof cell.h3 !== "string" || typeof cell.population !== "number"));
  if (bad) throw new StaleAppError();
}

// `label` names what was counted, for the status line (around a point says where).
// `fresh` is a new area rather than the same one at another detail level.
function queryPopulation(geometry, label = null, { fresh = true, row = null } = {}) {
  // A new count is no longer that row of the file; the same one at another detail still is.
  if (fresh) selectRow(row);
  lastGeometry = geometry;
  lastLabel = label;
  lastCountry = null;
  $("country-pick").value = "";
  // A new shape or circle is framed to fill the map beside the panel (F-19); the same one at
  // another detail level stays where the visitor has put it.
  return runQuery(() => staticQuery(geometry), {
    fresh,
    frame: fresh ? () => bboxOf(geometry) : null,
    maxZoom: 16,
  });
}

// `frame` moves the map onto the country: on picking it, not on a detail change.
function queryCountry(iso3, { frame = false, fresh = frame } = {}) {
  if (fresh) selectRow(null);
  lastCountry = iso3;
  lastGeometry = null;
  lastLabel = null;
  $("country-pick").value = iso3;
  const bounds = (body) => (body.cells.length ? peopleBounds(body.cells) : null);
  return runQuery(() => countryQuery(iso3), { frame: frame ? bounds : null, fresh, maxZoom: 11 });
}

// `frame(body)` gives the bounds to move the map onto once the answer is drawn, or null.
async function runQuery(ask, { frame = null, fresh = false, maxZoom = 16 } = {}) {
  if (inflight) inflight.abort();
  const controller = new AbortController();
  inflight = controller;
  $("draw").disabled = true;
  setStatus(t("status.querying"));
  try {
    const body = await ask(controller);
    // A static answer cannot be aborted mid-flight, so drop it if a newer query started.
    if (controller.signal.aborted) return;
    assertCompatible(body);
    render(body);
    // On a phone a new count folds the sheet down to the number, so the map shows.
    if (fresh && PHONE_SHEET.matches) setCollapsed(true);
    const bounds = frame?.(body);
    if (bounds) map.fitBounds(bounds, { padding: panelPadding(), maxZoom });
  } catch (err) {
    if (err.name === "AbortError") return;
    // Out of date, or the data changed under the page: said in the notice, with Reload.
    if (err.name === "StaleAppError" || err.name === "DataUpdatedError") {
      setStatus("");
      showUpdate(errText(err));
    } else setStatus(t("status.failed", { error: errText(err) }), true);
  } finally {
    if (inflight === controller) {
      inflight = null;
      $("draw").disabled = false;
    }
  }
}

// Older static.js (a cached shell) reports names only; it is never paired with this file.
const downloadText = ({ received, total, names, iso3s }) => {
  const shown = iso3s ? iso3s.map((c, i) => placeName(c, names[i])) : names;
  return t("status.downloading", { names: listNames(shown), got: fmtMB(received), total: fmtMB(total) });
};
// A download goes on after a newer count replaces the one that asked for it, and only the
// count in hand may speak: the old one's progress would stand under the new one's answer.
const downloadNotice = () => {
  const mine = inflight;
  return (progress) => {
    if (inflight === mine && progress.total) setStatus(downloadText(progress));
  };
};

async function staticQuery(geometry) {
  const notice = downloadNotice();
  // Let "Querying…" paint before the synchronous polyfill and lookup run.
  await new Promise((r) => setTimeout(r, 0));
  return TallyStatic.query(geometry, DETAIL_STEP[detail], notice);
}

async function countryQuery(iso3) {
  const notice = downloadNotice();
  await new Promise((r) => setTimeout(r, 0));
  return TallyStatic.country(iso3, DETAIL_STEP[detail], notice);
}

// Population sources as the result line and the exported image name them.
const SOURCE_NAMES = { meta_hrsl: "Meta", worldpop_100m: "WorldPop" };
const SOURCE_CREDITS = {
  meta_hrsl: "Meta Data for Good, High Resolution Population Density Maps (CC BY 4.0)",
  worldpop_100m: "WorldPop, Global2 R2025A constrained 100 m (CC BY 4.0)",
};
const sourceKeys = (source) => String(source).split(", ").filter((k) => k in SOURCE_CREDITS);
const sourceNames = (source) => listNames(sourceKeys(source).map((k) => SOURCE_NAMES[k])) || source;

// ---- rendering ---------------------------------------------------------------

// people / km2 for one cell. Cells carry population only; area is a property of the
// H3 index itself, so there is nothing to look up.
const cellDensity = (c) => c.population / h3.cellArea(c.h3, "km2");

function render(res) {
  const densities = res.cells.map(cellDensity).filter((d) => d > 0);
  // Every populated cell should yield a density. None doing so means the numbers or the
  // indexes are not what this build reads, and painting the map would hide that.
  if (res.cells.length && !densities.length) throw new StaleAppError();
  const lo = densities.length ? Math.log10(Math.min(...densities) + 1) : 0;
  const hi = densities.length ? Math.log10(Math.max(...densities) + 1) : 1;
  const span = Math.max(hi - lo, 1e-9);
  const colorFor = (density) => {
    const frac = Math.min(1, Math.max(0, (Math.log10(density + 1) - lo) / span));
    return RAMP[Math.min(RAMP.length - 1, Math.floor(frac * RAMP.length))];
  };

  const features = res.cells.map((c) => ({
    type: "Feature",
    properties: { population: c.population, color: colorFor(cellDensity(c)) },
    // cellToBoundary(..., true) returns closed [lng, lat] rings (GeoJSON order).
    geometry: { type: "Polygon", coordinates: [hexRing(c.h3)] },
  }));
  map.getSource(HEAT_SOURCE).setData({ type: "FeatureCollection", features });

  const areaKm2 = lastGeometry ? polygonAreaKm2(lastGeometry) : res.approx_area_km2;
  lastResult = { ...res, areaKm2, density: areaKm2 > 0 ? res.total / areaKm2 : 0 };

  $("total").textContent = fmtInt(res.total);
  $("area").firstChild.textContent = showArea(areaKm2);
  $("density").firstChild.textContent = showDensity(lastResult.density);
  $("source").textContent = res.source;
  $("resolution").textContent = String(res.resolution);
  setFineAvailable(res.resolution < res.base_resolution);
  $("cell-area").textContent =
    `${showArea(res.approx_area_km2)} ${U().area} ` +
    (res.country ? "(country outline)" : "(hex cover)");
  $("cells").textContent = `${fmtInt(res.populated_cells)} populated of ${fmtInt(res.cell_count)}`;
  $("elapsed").textContent = `${res.elapsed_ms} ms`;
  $("debug").hidden = !DEBUG;

  $("legend-bar").style.background = `linear-gradient(to right, ${RAMP.join(", ")})`;
  const ticks = [0, 0.5, 1].map((f) => showDensity(Math.pow(10, lo + f * span) - 1));
  $("legend-ticks").innerHTML = ticks.map((x) => `<span>${x}</span>`).join("");

  // Under the number, in words: what was counted.
  const n = Math.round(res.total);
  $("counted").textContent = res.country
    ? t("counted.country", { n, country: placeName(res.country.iso3, res.country.name) })
    : lastLabel
      ? t("counted.where", { n, where: lastLabel() })
      : t("counted.shape", { n });
  // Which data a country's count is, since there is no drawn shape to explain it.
  $("counted-source").hidden = !res.country;
  if (res.continent) {
    // Which countries are in it decides the number, so the line says how they were filed.
    $("counted-source").textContent = t("counted.sourceContinent", {
      n: res.continent_countries,
      source: sourceNames(res.source),
    });
  } else if (res.country) {
    const year = String(res.country.data_version ?? "").slice(0, 4);
    $("counted-source").textContent = t(year ? "counted.sourceYear" : "counted.source", {
      source: sourceNames(res.source),
      year,
    });
  }

  $("result").hidden = false;
  $("panel").classList.add("has-result");
  // A total that leaves a country out must say so: it is a floor, not an answer.
  if (res.missing?.length) {
    const left = res.missing_iso3?.map((c, i) => placeName(c, res.missing[i])) ?? res.missing;
    setStatus(t("status.leftOut", { n: fmtInt(res.total), est: est(), names: listNames(left) }), true);
    return;
  }
  setStatus(
    res.populated_cells === 0
      ? t("status.nobody")
      : DEBUG
        ? BACKEND
        : "",
  );
}

// ---- export ------------------------------------------------------------------

function buildCellFeatures(res) {
  const densities = res.cells.map(cellDensity).filter((d) => d > 0);
  const lo = densities.length ? Math.log10(Math.min(...densities) + 1) : 0;
  const hi = densities.length ? Math.log10(Math.max(...densities) + 1) : 1;
  const span = Math.max(hi - lo, 1e-9);
  const colorFor = (density) => {
    const frac = Math.min(1, Math.max(0, (Math.log10(density + 1) - lo) / span));
    return RAMP[Math.min(RAMP.length - 1, Math.floor(frac * RAMP.length))];
  };
  return {
    type: "FeatureCollection",
    features: res.cells.map((c) => ({
      type: "Feature",
      properties: { color: colorFor(cellDensity(c)) },
      geometry: { type: "Polygon", coordinates: [hexRing(c.h3)] },
    })),
  };
}

// The zoom at which fitBounds puts `box` in a width x height map with `pad` px round it
// (Web Mercator, 512 px tiles), without making a map to ask.
const mercY = (lat) => Math.log(Math.tan(Math.PI / 4 + (lat * Math.PI) / 360));
function fitZoom([[w, s], [e, n]], width, height, pad) {
  const dx = Math.max((e - w) / 360, 1e-9);
  const dy = Math.max((mercY(n) - mercY(s)) / (2 * Math.PI), 1e-9);
  return Math.log2(Math.min((width - 2 * pad) / (512 * dx), (height - 2 * pad) / (512 * dy)));
}
const INSET_ZOOM_OUT = 2.5;

// How wide (CSS px) a hexagon at level `r` is in the middle of map `m`.
function hexWidthPx(m, r) {
  const { lng, lat } = m.getCenter();
  const xs = hexRing(h3.latLngToCell(lat, lng, r)).map((p) => m.project(p).x);
  return Math.max(...xs) - Math.min(...xs);
}

// The image keeps the detail level picked (Fine stays Fine). Small hexagons lose their
// borders instead (OUTLINE_MIN_PX): at ~2 px a hexagon, as for a whole country at Fine,
// the gray border covered most of each one and the heatmap read as dark noise. Only
// hexagons too small to see at all (under MIN_HEX_PX) merge into their parents, which
// keeps every person, so totals stand.
const OUTLINE_MIN_PX = 6;
const MIN_HEX_PX = 1.5;
function cellsForScale(m, cells, resolution) {
  let r = resolution;
  while (r > 0 && hexWidthPx(m, r) < MIN_HEX_PX) r--;
  if (r === resolution) return { cells, outlined: hexWidthPx(m, r) >= OUTLINE_MIN_PX };
  const sums = new Map();
  for (const c of cells) {
    const p = h3.cellToParent(c.h3, r);
    sums.set(p, (sums.get(p) ?? 0) + c.population);
  }
  const merged = [...sums].map(([h, population]) => ({ h3: h, population }));
  return { cells: merged, outlined: hexWidthPx(m, r) >= OUTLINE_MIN_PX };
}

// Render a throwaway MapLibre map off-screen and resolve with a copy of its canvas.
function renderMap({ width, height, setup }) {
  return new Promise((resolve, reject) => {
    const el = document.createElement("div");
    el.style.cssText = `position:fixed;left:-20000px;top:0;width:${width}px;height:${height}px;`;
    document.body.appendChild(el);
    const m = new maplibregl.Map({
      container: el,
      style: basemapStyle,
      interactive: false,
      attributionControl: false,
      preserveDrawingBuffer: true,
      pixelRatio: 2,
      fadeDuration: 0,
    });
    startWithoutBasemap(m);
    m.on("style.load", () => localizeLabels(m));
    const done = () => {
      try {
        const src = m.getCanvas();
        const out = document.createElement("canvas");
        out.width = src.width;
        out.height = src.height;
        out.getContext("2d").drawImage(src, 0, 0);
        m.remove();
        el.remove();
        resolve(out);
      } catch (err) {
        reject(err);
      }
    };
    m.on("load", () => {
      setup(m);
      m.once("idle", done);
    });
  });
}

// The panel's faces (style.css), so the image reads like the app that made it.
const UI_FONT = '"Barlow", system-ui, -apple-system, sans-serif';
const FIGURE_FONT = '"Barlow Semi Condensed", "Barlow", system-ui, sans-serif';

function drawStat(ctx, x, y, label, value, unit, { size = 44, color = "#1d1a24" } = {}) {
  ctx.fillStyle = "#6b6577";
  ctx.font = `400 17px ${UI_FONT}`;
  ctx.fillText(label, x, y);
  ctx.fillStyle = color;
  ctx.font = `600 ${size}px ${FIGURE_FONT}`;
  ctx.fillText(value, x, y + size + 4);
  if (unit) {
    const w = ctx.measureText(value).width;
    ctx.fillStyle = "#6b6577";
    ctx.font = `400 18px ${UI_FONT}`;
    ctx.fillText(unit, x + w + 10, y + size + 4);
  }
}

// Overview inset: pin the polygon when it would be smaller than this on screen.
const PIN_BELOW_PX = 12;

async function exportImage() {
  if (!lastResult || !(lastGeometry || lastResult.country)) return;
  const btn = $("export");
  btn.disabled = true;
  setStatus(t("status.rendering"));
  try {
    const geometry = lastGeometry;
    const res = lastResult;
    const cells = buildCellFeatures(res);
    // A whole country has no drawn outline: its hexagons stand in for one.
    const outline = geometry ? { type: "Feature", properties: {}, geometry } : cells;
    // The inset shows all of it; the main map frames where the people are, as the live
    // map does (peopleBounds).
    const bbox = geometry ? bboxOf(geometry) : cellsBounds(res.cells);
    const detailBox = geometry ? bbox : peopleBounds(res.cells);
    const center = [(bbox[0][0] + bbox[1][0]) / 2, (bbox[0][1] + bbox[1][1]) / 2];

    // tolerance 0 on every source, as on the live map: by default MapLibre drops shapes
    // under about a pixel.
    const DW = 1000, DH = 800, OW = 480, OH = 480;
    const [detailMap, overviewMap] = await Promise.all([
      renderMap({
        width: DW,
        height: DH,
        setup: (m) => {
          m.fitBounds(detailBox, { padding: 90, duration: 0, maxZoom: 15 });
          const firstSymbol = m.getStyle().layers.find((l) => l.type === "symbol")?.id;
          const { cells: shown, outlined } = cellsForScale(m, res.cells, res.resolution);
          m.addSource("cells", { type: "geojson", data: buildCellFeatures({ cells: shown }), tolerance: 0 });
          m.addLayer(
            { id: "cells-fill", type: "fill", source: "cells",
              paint: { "fill-color": ["get", "color"], "fill-opacity": 0.7 } },
            firstSymbol,
          );
          if (outlined)
            m.addLayer(
              { id: "cells-line", type: "line", source: "cells",
                paint: { "line-color": "#00000033", "line-width": 0.5 } },
              firstSymbol,
            );
          if (geometry) {
            m.addSource("outline", { type: "geojson", data: outline, tolerance: 0 });
            m.addLayer({ id: "outline", type: "line", source: "outline",
              paint: { "line-color": "#1f6feb", "line-width": 3 } });
          }
        },
      }),
      renderMap({
        width: OW,
        height: OH,
        setup: (m) => {
          m.addSource("outline", { type: "geojson", data: outline, tolerance: 0 });
          // The heatmap's crimson (as the count and the pin), not the drawn shape's blue:
          // a whole country, with no blue on the main map, read as unconnected to it.
          m.addLayer({ id: "outline-fill", type: "fill", source: "outline",
            paint: { "fill-color": "#ce1256", "fill-opacity": 0.45 } });
          m.addLayer({ id: "outline-line", type: "line", source: "outline",
            paint: { "line-color": "#980043", "line-width": 2 } });
          // Always INSET_ZOOM_OUT levels further out than the main map (~5.7x wider), so a
          // country sits in its region; a fixed zoom, widened only when the area did not
          // fit, showed a country at about the main map's own scale. Never closer than 4.6,
          // so a small shape still shows where in the world it is.
          const mainZoom = Math.min(15, fitZoom(detailBox, DW, DH, 90));
          const mid = [(detailBox[0][0] + detailBox[1][0]) / 2, (detailBox[0][1] + detailBox[1][1]) / 2];
          m.jumpTo({ center: mid, zoom: Math.max(0, Math.min(4.6, mainZoom - INSET_ZOOM_OUT)) });
          // At this zoom a pixel is ~6.5 km, so a city-sized polygon is a speck. Pin it
          // only then: on a shape big enough to see, a dot in the middle reads as marking
          // something inside the area.
          const [sw, ne] = bbox.map((p) => m.project(p));
          if (Math.max(Math.abs(ne.x - sw.x), Math.abs(ne.y - sw.y)) < PIN_BELOW_PX) {
            m.addSource("pin", { type: "geojson",
              data: { type: "Feature", properties: {}, geometry: { type: "Point", coordinates: center } } });
            m.addLayer({ id: "pin", type: "circle", source: "pin",
              paint: { "circle-radius": 7, "circle-color": "#ce1256",
                       "circle-stroke-color": "#ffffff", "circle-stroke-width": 2 } });
          }
        },
      }),
    ]);

    // Compose at 2x for a crisp file.
    const W = 1600, H = 1000, S = 2, PAD = 40;
    const canvas = document.createElement("canvas");
    canvas.width = W * S;
    canvas.height = H * S;
    const ctx = canvas.getContext("2d");
    ctx.scale(S, S);
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, W, H);

    await Promise.all(
      [`600 34px ${UI_FONT}`, `400 16px ${UI_FONT}`, `600 44px ${FIGURE_FONT}`].map((f) =>
        document.fonts.load(f).catch(() => {}),
      ),
    );
    ctx.fillStyle = "#1d1a24";
    ctx.font = `600 34px ${UI_FONT}`;
    ctx.fillText(countTitle(res), PAD, 62);
    ctx.fillStyle = "#6b6577";
    ctx.font = `400 16px ${UI_FONT}`;
    // English has always been dated the British way, "2 November 2026 at 14:30".
    const dateLocale = TallyI18n.lang === "en" ? "en-GB" : TallyI18n.locale;
    const when = new Date().toLocaleString(dateLocale, { dateStyle: "long", timeStyle: "short" });
    // The address, not the name: images travel without context, and it brings people back.
    ctx.fillText(t("image.counted", { when }), PAD, 90);

    const top = 120;
    ctx.drawImage(detailMap, PAD, top, DW, DH);
    ctx.strokeStyle = "#ddd";
    ctx.lineWidth = 1;
    ctx.strokeRect(PAD + 0.5, top + 0.5, DW - 1, DH - 1);

    const rx = PAD + DW + PAD;
    ctx.drawImage(overviewMap, rx, top, OW, OH);
    ctx.strokeRect(rx + 0.5, top + 0.5, OW - 1, OH - 1);

    let y = top + OH + 60;
    drawStat(ctx, rx, y, t("image.people"), fmtInt(res.total), est(), { size: 56, color: "#980043" });
    y += 12;
    y += 100;
    drawStat(ctx, rx, y, t("fact.area"), showArea(res.areaKm2), U().area);
    y += 100;
    drawStat(ctx, rx, y, t("fact.density"), showDensity(res.density), t("image.densityUnit", { area: U().area }));

    ctx.fillStyle = "#888";
    ctx.font = `400 12px ${UI_FONT}`;
    const credits = sourceKeys(res.source).map((k) => SOURCE_CREDITS[k]);
    ctx.fillText(
      t("image.credits", { population: (credits.length ? credits : [SOURCE_CREDITS.meta_hrsl]).join("; ") }),
      PAD, H - 22,
    );

    const blob = await new Promise((r) => canvas.toBlob(r, "image/png"));
    setStatus(t("status.saved", { file: saveFile(blob, "png", countFileLabel()) }));
  } catch (err) {
    setStatus(t("status.imageFailed", { error: errText(err) }), true);
  } finally {
    btn.disabled = false;
  }
}

// A name made safe for a file's name: "Côte d'Ivoire" becomes "cote-d-ivoire". A row's name
// comes from someone's file, so nothing but letters and digits survives.
const fileSlug = (name) =>
  String(name ?? "")
    .normalize("NFD")
    .replace(/\p{M}/gu, "")
    .toLowerCase()
    .replace(/[^\p{L}\p{N}]+/gu, "-")
    .slice(0, 40)
    .replace(/^-+|-+$/g, "");

// What a saved count holds, for its file's name: tally-radius-..., tally-country-cameroon-...
// A circle is "radius" as on its tab, though its `kind` column says "circle". Only a name
// someone gave goes in: Tally's own "Drawn shape" or "Within 5 km of ..." would add nothing.
function countFileLabel(res = lastResult, row = selectedRow()) {
  const { kind, name } = exportSummary(res, row);
  const word = kind === "circle" ? "radius" : kind;
  const given = res.country || (row?.name && !row.drawn && !row.said);
  const slug = given ? fileSlug(name) || fileSlug(res.country?.iso3) : "";
  return slug ? `${word}-${slug}` : word;
}

// Hand the visitor a file made here: nothing is uploaded. Returns the file's name.
function saveFile(blob, ext, label) {
  const stamp = new Date().toISOString().slice(0, 16).replace(/[-:T]/g, "");
  const name = `tally-${label}-${stamp}.${ext}`;
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
  return name;
}

// ---- data export (F-04) --------------------------------------------------------
// Files are for other tools, so they hold plain machine-readable values: km² and mi²
// both, whole people, ISO dates. Names still say "estimate", as every count on screen does.

const round = (x, places) => Math.round(x * 10 ** places) / 10 ** places;

// The image's title and the files' `name`: what was counted, in the visitor's language.
const countTitle = (res) =>
  res.country
    ? t("image.titleCountry", { country: placeName(res.country.iso3, res.country.name) })
    : lastLabel
      ? t("image.titleWhere", { where: lastLabel() })
      : t("image.titleShape");

const upperFirst = (s) => s.charAt(0).toLocaleUpperCase(TallyI18n.locale) + s.slice(1);

// What was counted, in the same words as the panel and the image. The keys and `kind`
// stay English whatever the language: other tools read them.
function exportSummary(res, row = selectedRow()) {
  const kind = res.continent ? "continent" : res.country ? "country" : row ? row.kind : lastLabel ? "circle" : "shape";
  const name = res.country
    ? placeName(res.country.iso3, res.country.name)
    : row
      ? rowName(row)
      : lastLabel
        ? upperFirst(lastLabel())
        : t("file.drawnShape");
  const year = res.country ? String(res.country.data_version ?? "").slice(0, 4) : "";
  return {
    name,
    kind,
    country_iso3: res.continent ? "" : (res.country?.iso3 ?? ""),
    population_estimate: Math.round(res.total),
    area_km2: round(res.areaKm2, 3),
    area_mi2: round(res.areaKm2 / UNITS.imperial.km2, 3),
    density_per_km2: round(res.density, 1),
    density_per_mi2: round(res.density * UNITS.imperial.km2, 1),
    // A total that leaves a country out is a floor: the file says so, as the panel does.
    left_out: (res.missing ?? []).join("; "),
    source: sourceKeys(res.source).map((k) => SOURCE_NAMES[k]).join(" and ") || res.source,
    data_year: year,
    h3_resolution: res.resolution,
    hexagons: res.populated_cells,
    counted_at: new Date().toISOString(),
    counted_with: "tallymaps.org",
  };
}

// RFC 4180: quote a field that holds a comma, quote or line break; double its quotes.
const csvField = (v) => {
  const s = String(v ?? "");
  return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
};

function exportCsv() {
  if (!lastResult) return;
  const row = exportSummary(lastResult);
  const lines = [Object.keys(row), Object.values(row).map(safeCell)].map((r) => r.map(csvField).join(","));
  // The byte-order mark makes Excel read the file as UTF-8 (Côte d'Ivoire, km²).
  const blob = new Blob(["﻿" + lines.join("\r\n") + "\r\n"], { type: "text/csv;charset=utf-8" });
  setStatus(t("status.saved", { file: saveFile(blob, "csv", countFileLabel()) }));
}

// ~10 cm: enough for any hexagon or drawn vertex, and it keeps 40,000 cells to a few MB.
const roundCoords = (c) => (typeof c[0] === "number" ? c.map((x) => round(x, 6)) : c.map(roundCoords));

// A row with no name of its own: the label in a saved file is only Tally's number for it.
const autoNamed = (row) => row && !row.drawn && !row.name;

// What a saved count needs to come back as it was (F-41): a circle's center and radius,
// `drawn` when Tally named it rather than a person or a file, and `auto_named` when a file
// gave it no name and Tally numbered it. Unrounded, for the same count.
function reopenColumns(res, row) {
  if (res.country) return { country_code: lastCountry };
  const circled = row?.center
    ? [row.center, row.radiusKm ?? fileSet.radiusKm]
    : !row && radiusCenter && lastLabel
      ? [radiusCenter, radiusKm]
      : null;
  return {
    ...(circled && { latitude: circled[0][1], longitude: circled[0][0], radius_km: circled[1] }),
    ...((!row || row.drawn) && { drawn: true }),
    ...(autoNamed(row) && { auto_named: true }),
  };
}

// One FeatureCollection: every populated hexagon (`kind: "hexagon"`, its H3 index and its
// people), then the shape with the summary (`kind` "shape" or "circle"). The shape comes
// last so map tools draw it on top. A whole country has no drawn outline: its feature
// carries the summary with no geometry, which GeoJSON allows (RFC 7946, 3.2).
function exportGeojson() {
  if (!lastResult) return;
  const res = lastResult;
  // `population` on every feature, the property F-02's upload will read back.
  const { population_estimate: population, ...summary } = exportSummary(res);
  const hexagons = res.cells.map((c) => ({
    type: "Feature",
    properties: {
      kind: "hexagon",
      h3: c.h3,
      population: Math.round(c.population),
      density_per_km2: round(cellDensity(c), 1),
    },
    geometry: { type: "Polygon", coordinates: roundCoords([hexRing(c.h3)]) },
  }));
  const shape = {
    type: "Feature",
    properties: {
      name: summary.name,
      kind: summary.kind,
      population,
      ...summary,
      ...reopenColumns(res, selectedRow()),
    },
    geometry: lastGeometry && !res.country
      ? { type: lastGeometry.type, coordinates: roundCoords(lastGeometry.coordinates) }
      : null,
  };
  const text = JSON.stringify({ type: "FeatureCollection", features: [...hexagons, shape] });
  const blob = new Blob([text], { type: "application/geo+json" });
  setStatus(t("status.saved", { file: saveFile(blob, "geojson", countFileLabel(res)) }));
}

$("export").addEventListener("click", exportImage);
$("export-csv").addEventListener("click", exportCsv);
$("export-geojson").addEventListener("click", exportGeojson);

// ---- a file of shapes and points (F-02) -------------------------------------------
// Each row is counted just as if it had been drawn, at its own level, so a row's number
// and the same shape drawn by hand are the same. Adjoining zones still sum to about the
// whole: 649 zones around Douala and Yaounde came to within 0.22% of a count at the finest
// level, where one shared level would have put a typical zone's own number 1.4 to 8.6% off.
// A list keeps only each row's numbers: a row's hexagons are counted again when it is
// picked, so 500 rows hold no more memory than one.
const FILE_SOURCE = "file-shapes";
const FILE_LAYERS = ["file-fill", "file-line", "file-selected", "file-dot"];
const KEPT_FILE = "tally-kept-file";
let fileRun = 0; // the count under way; a newer one, or Stop, makes an older one give up

// Alone it is just a drawn shape; in a list its row number tells it from the others.
const drawnShapeName = (row) =>
  fileSet.rows.length > 1 ? t("file.drawnShapeNumber", { n: row.fallback[1].n }) : t("file.drawnShape");

// A row Tally named itself when it was saved (`drawn`) is named again in today's language
// and units; the rest keep the name in the file.
const rowName = (row) =>
  row.drawn
    ? row.center
      ? upperFirst(whereRadius(row.center, row.radiusKm))
      : drawnShapeName(row)
    : (row.name ?? t(row.fallback[0], row.fallback[1]));
const selectedRow = () => (fileSet?.selected != null ? fileSet.rows[fileSet.selected] : null);
// A point with no radius of its own takes the one box in the tab.
const rowGeometry = (row) =>
  row.geometry ?? (row.center ? circle(row.center, row.radiusKm ?? fileSet.radiusKm) : null);

function addFileLayers() {
  map.addSource(FILE_SOURCE, { type: "geojson", data: EMPTY, tolerance: 0 });
  const area = ["==", ["geometry-type"], "Polygon"];
  map.addLayer({ id: "file-fill", type: "fill", source: FILE_SOURCE, filter: area,
    paint: { "fill-color": "#1f6feb", "fill-opacity": 0.05 } });
  map.addLayer({ id: "file-line", type: "line", source: FILE_SOURCE, filter: area,
    paint: { "line-color": "#1f6feb", "line-width": 1, "line-opacity": 0.65 } });
  map.addLayer({ id: "file-selected", type: "line", source: FILE_SOURCE,
    filter: ["==", ["get", "i"], -1],
    paint: { "line-color": "#1f6feb", "line-width": 3 } });
  map.addLayer({ id: "file-dot", type: "circle", source: FILE_SOURCE,
    filter: ["==", ["geometry-type"], "Point"],
    paint: { "circle-radius": 3, "circle-color": "#1f6feb",
             "circle-stroke-color": "#ffffff", "circle-stroke-width": 1 } });
}

function showFileLayers(on) {
  for (const id of FILE_LAYERS)
    if (map.getLayer(id)) map.setLayoutProperty(id, "visibility", on ? "visible" : "none");
}

function drawFileShapes() {
  const features = [];
  fileSet?.rows.forEach((row, i) => {
    const geometry = rowGeometry(row);
    if (!geometry) return;
    // Area, so a click where shapes overlap takes the smallest.
    const properties = { i, area: polygonAreaKm2(geometry) };
    features.push({ type: "Feature", properties, geometry });
    if (row.center)
      features.push({ type: "Feature", properties, geometry: { type: "Point", coordinates: row.center } });
  });
  map.getSource(FILE_SOURCE)?.setData({ type: "FeatureCollection", features });
}

// The view that holds every row. Rows on both sides of the 180° line come together across
// it, as cellsBounds does for hexagons, rather than stretching round the world. Each box's
// whole width counts as taken, every 10°, so the cut never falls inside a wide shape, and a
// box moves as one.
function fileBounds() {
  const boxes = fileSet.rows.map(rowGeometry).filter(Boolean).map(bboxOf);
  if (!boxes.length) return null;
  const inLine = lngsInLine(
    boxes.flatMap(([[w], [e]]) => [e, ...Array.from({ length: Math.ceil((e - w) / 10) }, (_, i) => w + i * 10)]),
  );
  const spans = boxes.map(([[w], [e]]) => [inLine(w), inLine(w) - w + e]);
  return [
    [Math.min(...spans.map((s) => s[0])), Math.min(...boxes.map((b) => b[0][1]))],
    [Math.max(...spans.map((s) => s[1])), Math.max(...boxes.map((b) => b[1][1]))],
  ];
}

const rowElement = (i) => $("file-rows").children[i];

function rowValue(row) {
  if (row.problem) return { text: t("file.notCounted"), note: errText(row.problem) };
  // Only what static.js says in words: anything else is not a sentence for a person.
  if (row.failure)
    return { text: t("file.notCounted"), note: row.failure.key ? errText(row.failure) : t("file.problemFailed") };
  const r = row.result;
  if (!r) return { text: "–", note: "" };
  if (r.missing.length) {
    const left = r.missing_iso3.map((c, i) => placeName(c, r.missing[i]));
    return {
      // Nothing downloaded is no count at all, not "at least 0".
      text: r.total ? t("file.atLeast", { n: fmtInt(r.total) }) : t("file.notCounted"),
      note: t("file.leftOut", { names: listNames(left) }),
    };
  }
  if (!r.covered) return { text: t("file.noData"), note: "" };
  if (!r.total) return { text: t("file.nobody"), note: "" };
  return { text: fmtInt(r.total), note: "" };
}

function showRow(i) {
  const row = fileSet.rows[i];
  const value = rowValue(row);
  const el = rowElement(i);
  const name = el.querySelector(".file-name");
  name.textContent = rowName(row);
  name.title = rowName(row);
  el.querySelector(".file-value").textContent = value.text;
  el.querySelector(".file-note").textContent = value.note;
  el.querySelector(".file-note").hidden = !value.note;
  el.classList.toggle("muted", !row.result || !row.result.total || Boolean(row.failure));
}

function fillFileRows() {
  const list = $("file-rows");
  list.replaceChildren(
    ...fileSet.rows.map((row, i) => {
      const li = document.createElement("li");
      const button = document.createElement("button");
      button.type = "button";
      button.className = "file-row";
      button.disabled = !rowGeometry(row);
      const name = document.createElement("span");
      name.className = "file-name";
      const value = document.createElement("span");
      value.className = "file-value";
      const note = document.createElement("span");
      note.className = "file-note";
      button.append(name, value, note);
      button.addEventListener("click", () => pickRow(i));
      li.append(button);
      return li;
    }),
  );
  fileSet.rows.forEach((_, i) => showRow(i));
}

// The panel's account of the list: how far it has got, and the sum once there is one.
function showFileSum() {
  const rows = fileSet.rows;
  const counted = rows.filter((r) => r.result && !r.failure);
  const total = counted.reduce((sum, r) => sum + r.result.total, 0);
  const partial = counted.some((r) => r.result.missing.length);
  const running = fileSet.running;
  const left = rows.filter((r) => !r.problem && !r.failure && !r.result).length;
  $("file-sum-row").hidden = counted.length < 2;
  $("file-sum-label").textContent = t("file.sum", { n: counted.length });
  $("file-sum").textContent = partial ? t("file.atLeast", { n: fmtInt(total) }) : fmtInt(total);
  $("file-overlap").hidden = counted.length < 2;
  const skipped = rows.filter((r) => r.problem || r.failure).length;
  $("file-skipped").textContent = skipped ? t("file.skipped", { n: skipped }) : "";
  $("file-skipped").hidden = !skipped;
  $("file-stop").hidden = !running;
  $("file-rest").hidden = running || !left;
  // One row has nothing to add up: the Save row under its count holds all of it.
  $("file-save").hidden = running || rows.length < 2;
  showRowChoice();
  if (running) return;
  $("file-progress").textContent = left ? t("file.stopped", { done: rows.length - left, n: rows.length }) : "";
}

const yieldToPage = () =>
  new Promise((resolve) => {
    // A hidden tab's timers slow to one a minute, which would stall a long list for hours.
    if (!document.hidden) return setTimeout(resolve, 0);
    const { port1, port2 } = new MessageChannel();
    port1.onmessage = () => resolve();
    port2.postMessage(0);
  });

// Counts every row that has no answer yet at this detail, one at a time: each downloads only
// the packs it touches, and the packs stay on this device for the rows after it.
async function countFile() {
  const run = ++fileRun;
  const set = fileSet;
  const step = DETAIL_STEP[detail];
  for (const row of set.rows) {
    if (row.result?.step !== step) row.result = null;
    if (!row.result) row.failure = null;
  }
  set.running = true;
  set.rows.forEach((_, i) => showRow(i));
  showFileSum();
  const todo = set.rows.filter((r) => !r.problem && !r.result).length;
  let done = 0;
  let paused = performance.now();
  try {
    for (let i = 0; i < set.rows.length; i++) {
      const row = set.rows[i];
      if (row.problem || row.result) continue;
      const counting = t("file.counting", { done: done + 1, n: todo });
      $("file-progress").textContent = counting;
      if (performance.now() - paused > 30) {
        await yieldToPage();
        paused = performance.now();
      }
      if (run !== fileRun) return;
      try {
        const geometry = rowGeometry(row);
        const res = await TallyStatic.query(geometry, step, (progress) => {
          if (progress.total && run === fileRun)
            $("file-progress").textContent = `${counting} ${downloadText(progress)}`;
        });
        if (run !== fileRun) return;
        assertCompatible(res);
        const areaKm2 = polygonAreaKm2(geometry);
        row.result = {
          step,
          total: res.total,
          areaKm2,
          density: areaKm2 > 0 ? res.total / areaKm2 : 0,
          source: res.source,
          resolution: res.resolution,
          populated_cells: res.populated_cells,
          missing: res.missing ?? [],
          missing_iso3: res.missing_iso3 ?? [],
          covered: res.covered,
        };
      } catch (err) {
        if (run !== fileRun) return;
        if (err.name === "StaleAppError" || err.name === "DataUpdatedError") throw err;
        row.failure = err;
      }
      done++;
      showRow(i);
      showFileSum();
    }
  } catch (err) {
    if (run !== fileRun) return;
    showUpdate(errText(err));
  }
  if (run !== fileRun) return;
  set.running = false;
  showFileSum();
}

// With several rows, which Save is which, and that a row has hexagons to show.
function showRowChoice() {
  const several = fileSet.rows.length > 1;
  const picked = several && fileSet.selected != null;
  $("save-count").hidden = picked;
  $("save-row").hidden = !picked;
  $("save-label").classList.toggle("own-line", picked);
  $("file-hint").hidden = !several || picked || !fileSet.rows.some((r) => r.geometry || r.center);
}

function selectRow(i) {
  if (!fileSet) return;
  const before = fileSet.selected;
  if (before != null) rowElement(before)?.querySelector("button")?.removeAttribute("aria-current");
  fileSet.selected = i;
  if (map.getLayer("file-selected")) map.setFilter("file-selected", ["==", ["get", "i"], i ?? -1]);
  if (i != null) {
    const button = rowElement(i)?.querySelector("button");
    button?.setAttribute("aria-current", "true");
    button?.scrollIntoView({ block: "nearest" });
  }
  showRowChoice();
}

// Under the number, in words. A row Tally named itself reads as it did when first counted;
// `said` is the same words from a file saved before `drawn` existed.
function rowLabel(row) {
  if (row.drawn) return row.center ? () => whereRadius(row.center, row.radiusKm) : null;
  if (row.said) return () => row.said;
  return () => t("where.file", { name: rowName(row) });
}

function pickRow(i) {
  const row = fileSet.rows[i];
  const geometry = rowGeometry(row);
  if (!geometry) return;
  clearShape();
  clearCircle();
  queryPopulation(geometry, rowLabel(row), { row: i });
}

// A click on a shape picks its row, unless the click belongs to drawing or to placing a point.
map.on("click", (e) => {
  if (!fileSet || sketch || pickingPoint || draw?.getMode() === "polygon") return;
  if (!map.getLayer("file-fill") || map.getLayoutProperty("file-fill", "visibility") === "none") return;
  const hits = map.queryRenderedFeatures(e.point, { layers: ["file-fill", "file-dot"] });
  if (!hits.length) return;
  const smallest = hits.reduce((a, b) => (b.properties.area < a.properties.area ? b : a));
  if (smallest.properties.i !== fileSet.selected) pickRow(smallest.properties.i);
});

function showFileRadius() {
  const box = $("file-radius-row");
  const needed = Boolean(fileSet?.rows.some((r) => r.center && r.radiusKm == null));
  box.hidden = !needed;
  if (!needed) return;
  $("file-radius").value = String(Math.round((fileSet.radiusKm / U().km) * 100) / 100);
  $("file-radius-unit").textContent = U().dist;
}

$("file-radius").addEventListener("change", () => {
  const v = parseFloat($("file-radius").value);
  if (v > 0) {
    fileSet.radiusKm = Math.min(RADIUS_KM_MAX, Math.max(RADIUS_KM_MIN, v * U().km));
    for (const row of fileSet.rows)
      if (row.center && row.radiusKm == null) {
        row.result = null;
        row.failure = null;
      }
    drawFileShapes();
    if (selectedRow()?.center && selectedRow().radiusKm == null) pickRow(fileSet.selected);
    countFile();
  }
  showFileRadius();
});

function showFile({ frame = true } = {}) {
  $("file-start").hidden = true;
  $("file-list").hidden = false;
  $("file-name").textContent = fileSet.name;
  $("file-name").title = fileSet.name;
  $("file-size").textContent = t("file.rows", { n: fileSet.rows.length });
  fillFileRows();
  showFileRadius();
  drawFileShapes();
  if (frame) {
    const bounds = fileBounds();
    if (bounds) map.fitBounds(bounds, { padding: panelPadding(), maxZoom: 14 });
  }
  return countFile();
}

function closeFile() {
  fileRun++;
  const wasRow = selectedRow();
  selectRow(null);
  fileSet = null;
  map.getSource(FILE_SOURCE)?.setData(EMPTY);
  $("file-list").hidden = true;
  $("file-start").hidden = false;
  $("file-rows").replaceChildren();
  $("file-progress").textContent = "";
  if (wasRow) clearAll();
}

// A file of one row has nothing to choose between, so its heatmap comes up on its own, unless
// the visitor has moved on while it was counted.
function showOnlyRow(set) {
  const [row, more] = set.rows;
  if (set !== fileSet || more || set.selected != null || $("mode-file").hidden) return;
  if (row.result && !row.failure) pickRow(0);
}

function pickWhole(code) {
  const pick = $("country-pick");
  pick.value = code;
  if (pick.value !== code) return false; // not in this build, or an older file's own word for it
  setMode("country");
  pick.dispatchEvent(new Event("change"));
  return true;
}

async function openFiles(files) {
  if (!files.length) return;
  setMode("file");
  setStatus(t("file.reading"));
  try {
    const { name, rows } = await TallyFiles.read(files);
    closeFile();
    // A saved whole country has no shape to list: it opens where it is picked.
    if (rows.length === 1 && rows[0].whole && pickWhole(rows[0].whole)) return setStatus("");
    fileSet = { name, rows, radiusKm, selected: null, running: false };
    setStatus("");
    const set = fileSet;
    showFile().then(() => showOnlyRow(set));
  } catch (err) {
    console.warn("file:", err);
    setStatus(err.key ? errText(err) : t("file.errRead", { name: files[0].name }), true);
  }
}

$("file-open").addEventListener("click", () => $("file-input").click());
$("file-input").addEventListener("change", (e) => {
  openFiles([...e.target.files]);
  e.target.value = ""; // so that the same file, changed and saved, can be opened again
});
$("file-close").addEventListener("click", closeFile);
$("file-stop").addEventListener("click", () => {
  fileRun++;
  fileSet.running = false;
  showFileSum();
});
$("file-rest").addEventListener("click", countFile);

// Anywhere on the page takes a file dropped on it.
let dragDepth = 0;
const carriesFiles = (e) => [...(e.dataTransfer?.types ?? [])].includes("Files");
window.addEventListener("dragenter", (e) => {
  if (!carriesFiles(e)) return;
  e.preventDefault();
  dragDepth++;
  $("file-drop").hidden = false;
});
window.addEventListener("dragover", (e) => {
  if (carriesFiles(e)) e.preventDefault();
});
window.addEventListener("dragleave", (e) => {
  if (!carriesFiles(e)) return;
  dragDepth = Math.max(0, dragDepth - 1);
  if (!dragDepth) $("file-drop").hidden = true;
});
window.addEventListener("drop", (e) => {
  if (!carriesFiles(e)) return;
  e.preventDefault();
  dragDepth = 0;
  $("file-drop").hidden = true;
  openFiles([...e.dataTransfer.files]);
});

// Saved beside what the file already said about each row. A file's own column of the same
// name as any Tally reads back as its own keeps its values as file_<name>, or reopening the
// saved file would take them for Tally's and drop them.
function fileColumns(row) {
  const res = row.result;
  const counted = res
    ? exportSummary(
        { total: res.total, areaKm2: res.areaKm2, density: res.density, missing: res.missing,
          source: res.source, resolution: res.resolution, populated_cells: res.populated_cells },
        row,
      )
    : { name: rowName(row), kind: row.kind, counted_with: TallyFiles.COUNTED_WITH };
  const value = rowValue(row);
  const note = value.note || (res && !res.covered ? t("file.noData") : "");
  const summary = { ...counted, note };
  if (res && !res.covered) summary.population_estimate = "";
  if (row.center) {
    summary.latitude = row.center[1];
    summary.longitude = row.center[0];
    summary.radius_km = round(row.radiusKm ?? fileSet.radiusKm, 6);
  }
  if (row.drawn) summary.drawn = true;
  if (autoNamed(row)) summary.auto_named = true;
  return summary;
}

const ownColumns = (row) =>
  Object.fromEntries(
    Object.entries(row.props ?? {})
      .filter(([, v]) => v === null || typeof v !== "object")
      .map(([k, v]) => [TallyFiles.OWN_COLUMNS.has(k) ? `file_${k}` : k, v]),
  );

// A file's words go into a spreadsheet that may run them: =, +, - or @ opens a formula, so
// a file passed along could carry one. A leading ' makes it text; numbers stay numbers.
const safeCell = (v) =>
  typeof v === "string" && /^[=+\-@\t\r]/.test(v) && !Number.isFinite(Number(v)) ? `'${v}` : v;

function exportFileCsv() {
  const ours = Object.keys(fileColumnKeys());
  const rows = fileSet.rows.map((row) => ({ summary: fileColumns(row), own: ownColumns(row) }));
  const keys = [...new Set([...ours, ...rows.flatMap((r) => Object.keys(r.own))])];
  const lines = [
    keys.map(safeCell),
    ...rows.map((r) => keys.map((k) => safeCell((k in r.summary ? r.summary : r.own)[k]))),
  ];
  const text = lines.map((line) => line.map(csvField).join(",")).join("\r\n");
  const blob = new Blob(["﻿" + text + "\r\n"], { type: "text/csv;charset=utf-8" });
  setStatus(t("status.saved", { file: saveFile(blob, "csv", "list") }));
}

// Every column a row of the list can have, in the order a single count's file has them.
function fileColumnKeys() {
  const sample = exportSummary(
    { total: 0, areaKm2: 0, density: 0, missing: [], source: "", resolution: 0, populated_cells: 0 },
    fileSet.rows[0],
  );
  return {
    ...sample,
    note: "",
    latitude: "",
    longitude: "",
    radius_km: "",
    ...(fileSet.rows.some((r) => r.drawn) && { drawn: "" }),
    ...(fileSet.rows.some(autoNamed) && { auto_named: "" }),
  };
}

// The file's shapes with what was counted in each: no hexagons, which would run to hundreds
// of MB across a list; picking a row saves its own (Save, under its count).
function exportFileGeojson() {
  const features = fileSet.rows.map((row) => {
    const { population_estimate: population = null, ...summary } = fileColumns(row);
    const geometry = rowGeometry(row);
    return {
      type: "Feature",
      properties: { ...ownColumns(row), name: summary.name, kind: summary.kind,
                    population: population === "" ? null : population, ...summary },
      geometry: geometry && { type: geometry.type, coordinates: roundCoords(geometry.coordinates) },
    };
  });
  const blob = new Blob([JSON.stringify({ type: "FeatureCollection", features })], {
    type: "application/geo+json",
  });
  setStatus(t("status.saved", { file: saveFile(blob, "geojson", "list") }));
}
$("file-csv").addEventListener("click", exportFileCsv);
$("file-geojson").addEventListener("click", exportFileGeojson);

// Reload and a change of language keep the list, as they keep a count. Its answers are not
// kept: the packs are on this device, so counting again is quick and always current. A file
// too large for the session's storage comes back as a line asking to open it again.
function keepFile() {
  if (!fileSet) return;
  const rows = fileSet.rows.map(({ result, failure, ...row }) => row);
  const kept = { name: fileSet.name, rows, radiusKm: fileSet.radiusKm, selected: fileSet.selected };
  try {
    sessionStorage.setItem(KEPT_FILE, JSON.stringify(kept));
  } catch (_) {
    try {
      sessionStorage.setItem(KEPT_FILE, JSON.stringify({ name: fileSet.name }));
    } catch (_) {}
  }
}

function restoreFile() {
  let kept = null;
  try {
    kept = JSON.parse(sessionStorage.getItem(KEPT_FILE));
    sessionStorage.removeItem(KEPT_FILE);
  } catch (_) {}
  if (!kept) return;
  setMode("file");
  if (!kept.rows) return setStatus(t("file.openAgain", { name: kept.name }));
  fileSet = { name: kept.name, rows: kept.rows, radiusKm: kept.radiusKm, selected: null, running: false };
  showFile({ frame: kept.selected == null });
  if (kept.selected != null) pickRow(kept.selected);
}

// ---- language (F-01) ------------------------------------------------------------
// The ? sheet lists every language in its own words. Picking one reloads the page in it,
// keeping the count, as Reload does after an update: every word on the page, the map's
// buttons and the country names change at once, with nothing left in the old language.
// Two pickers, one choice: the globe in the panel's corner and the one in About.
function fillLanguages() {
  for (const pick of [$("language-pick"), $("language-quick")]) {
    for (const [code, name] of Object.entries(TallyI18n.LANGUAGES)) pick.append(new Option(name, code));
    pick.value = TallyI18n.lang;
  }
  $("language-code").textContent = TallyI18n.lang.toUpperCase();
  $("language-draft").hidden = !TallyI18n.draft;
}
for (const id of ["language-pick", "language-quick"])
  $(id).addEventListener("change", (e) => {
    if (e.target.value === TallyI18n.lang) return;
    TallyI18n.choose(e.target.value);
    keepCount();
    location.reload();
  });

// MapLibre's own buttons: their tooltips and screen-reader names.
function localizeControls() {
  const c = map.getContainer();
  const label = (sel, key) => {
    for (const el of c.querySelectorAll(sel)) {
      el.title = t(key);
      el.setAttribute("aria-label", t(key));
    }
  };
  label(".maplibregl-ctrl-zoom-in", "map.zoomIn");
  label(".maplibregl-ctrl-zoom-out", "map.zoomOut");
  label(".maplibregl-ctrl-compass", "map.north");
  label(".maplibregl-ctrl-attrib-button", "map.credits");
}

setMode(savedMode(), { remember: false });
// Last, once everything it touches exists and the words are in: labels, radius box and
// scale in the saved units.
TallyI18n.ready.then(() => {
  if (TOUCH) $("shape-how").textContent = t("shape.howTouch");
  fillLanguages();
  setUnits(units);
});
