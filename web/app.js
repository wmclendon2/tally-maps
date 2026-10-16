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
// The catalogue loads at once (a few KB); the packs come with the first polygon.
const staticData = TallyStatic.load(DATA_URL);
const fmtMB = (b) => (b > 0 && b < 5e4 ? "<0.1" : (b / 1e6).toFixed(b >= 1e7 ? 0 : 1));
const BASEMAP_STYLE = "https://tiles.openfreemap.org/styles/liberty";
const CAMEROON_CENTER = [12.35, 5.7];
const CAMEROON_ZOOM = 5.4;

// Sequential ramp, light -> dark (ColorBrewer PuRd, with the two palest steps dropped so
// the light end stays visible on the basemap's beige and green). Index = log10(density).
const RAMP = ["#dcc0e0", "#c994c7", "#df65b0", "#e7298a", "#ce1256", "#980043"];

const $ = (id) => document.getElementById(id);
const fmtInt = (n) => Math.round(n).toLocaleString("en-US");
// Every count of people says it is an estimate, so no one takes it for a census.
const EST = "(est.)";
const fmtDensity = (d) => (d >= 100 ? fmtInt(d) : d.toFixed(d >= 10 ? 0 : 1));
const fmtArea = (a) => (a >= 100 ? fmtInt(a) : a.toFixed(1));
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

// [[w, s], [e, n]] round a set of cells, across the antimeridian when they straddle it
// (Fiji, Kiribati): then e is past 180, which fitBounds takes as the short way round.
function cellsBounds(cells) {
  const pts = cells.map((c) => h3.cellToLatLng(c.h3));
  let lngs = pts.map((p) => p[1]);
  if (Math.max(...lngs) - Math.min(...lngs) > 180) lngs = lngs.map((x) => (x < 0 ? x + 360 : x));
  const lats = pts.map((p) => p[0]);
  // Pad by about a cell, so edge cells are not cut in half.
  const pad = cells.length ? Math.sqrt(h3.cellArea(cells[0].h3, "km2")) / 111 : 0;
  return [
    [Math.min(...lngs) - pad, Math.max(-85, Math.min(...lats) - pad)],
    [Math.max(...lngs) + pad, Math.min(85, Math.max(...lats) + pad)],
  ];
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
  let w = 180, s = 90, e = -180, n = -90;
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

const map = new maplibregl.Map({
  container: "map",
  style: BASEMAP_STYLE,
  center: CAMEROON_CENTER,
  zoom: CAMEROON_ZOOM,
  // The attribution control is added once the manifest says what to credit.
  attributionControl: false,
});
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
    .setHTML(`<b>${fmtInt(n)}</b> ${EST} ${n === 1 ? "person" : "people"} in this hexagon`)
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
  showStaticData();
});

function setDrawActive(on) {
  $("draw").classList.toggle("active", on);
  $("draw").classList.toggle("active", on);
  $("draw-label").textContent = on ? "Drawing… Esc to cancel" : "Draw a shape";
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

if (TOUCH)
  $("shape-how").textContent =
    "Outline an area corner by corner, with a crosshair, to count everyone inside.";

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

  $("sketch-add-label").textContent = closing ? "Close the shape" : "Add corner";
  $("sketch-undo").disabled = n === 0;
  $("sketch-finish").disabled = n < 3;
  $("shape-how").textContent = closing
    ? "The cross is on the first corner. Close the shape to count."
    : n === 0
      ? "Move the map until the cross is on the first corner, then Add corner. Or tap the map there."
      : n < 3
        ? `${n === 1 ? "One corner" : "Two corners"}. Move the cross to the next one.`
        : `${n} corners. Add more, or Finish to count everyone inside.`;
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
  $("shape-how").textContent =
    "Outline an area corner by corner, with a crosshair, to count everyone inside.";
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
  $("sheet-toggle").setAttribute("aria-label", on ? "Show more" : "Show less");
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
const MODES = ["shape", "radius", "country"];
function setMode(mode, { focus = false, remember = true } = {}) {
  if (!MODES.includes(mode) || $(`tab-${mode}`).hidden) mode = "shape";
  for (const m of MODES) {
    const on = m === mode;
    const tab = $(`tab-${m}`);
    tab.setAttribute("aria-selected", String(on));
    tab.tabIndex = on ? 0 : -1;
    $(`mode-${m}`).hidden = !on;
  }
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

// The points `km` from the centre on a sphere (the radius app.js measures areas with),
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
  map.addLayer({ id: "radius-centre", type: "circle", source: RADIUS_SOURCE,
    filter: ["==", ["geometry-type"], "Point"],
    paint: { "circle-radius": 4, "circle-color": "#1f6feb",
             "circle-stroke-color": "#ffffff", "circle-stroke-width": 2 } });
}

function clearCircle() {
  radiusCenter = null;
  map.getSource(RADIUS_SOURCE)?.setData(EMPTY);
}

function setPicking(on) {
  pickingPoint = on;
  $("pick-point").classList.toggle("active", on);
  $("pick-label").textContent = on
    ? TOUCH ? "Tap the map… or here to cancel" : "Click the map… Esc to cancel"
    : "Pick a point";
  map.getCanvas().style.cursor = on ? "crosshair" : "";
  if (on && draw && draw.getMode() === "polygon") {
    draw.setMode("static");
    setDrawActive(false);
  }
  if (on && sketch) endSketch();
  setToolOnly();
}

const fmtCoord = (v, pos, neg) => `${Math.abs(v).toFixed(3)}°${v >= 0 ? pos : neg}`;
const fmtDist = (km) => {
  const v = km / U().km;
  return `${v >= 10 ? fmtInt(v) : String(Math.round(v * 100) / 100)} ${U().dist}`;
};

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
  const [lng, lat] = radiusCenter;
  queryPopulation(
    geometry,
    () => `within ${fmtDist(radiusKm)} of ${fmtCoord(lat, "N", "S")}, ${fmtCoord(lng, "E", "W")}`,
  );
}

$("pick-point").addEventListener("click", () => setPicking(!pickingPoint));
map.on("click", (e) => {
  if (!pickingPoint) return;
  setPicking(false);
  radiusCenter = [e.lngLat.lng, e.lngLat.lat];
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
  $("density-unit").textContent = `per ${U().area}`;
  $("legend-title").textContent = `people per ${U().area} in each hexagon`;
  showRadius();
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
// The ? sheet: what Tally is and where its numbers come from, then saved data, settings
// and credits. It used to open on a world map of coverage, which the grey no-data layer on
// the map itself made redundant (F-20).
const SHELL = document.querySelector('script[src^="app.js?v="]')?.src.split("?v=")[1];

async function describeData() {
  const scope = $("about-scope");
  const live = $("coverage-live");
  try {
    const m = await staticData;
    const n = m.countries.length;
    const billions = m.total / 1e9;
    const people = billions >= 1 ? `${billions.toFixed(1)} billion` : fmtInt(m.total);
    scope.innerHTML =
      `It has data for <span class="fig">${fmtInt(n)}</span> ` +
      `${n === 1 ? "country or territory" : "countries and territories"}, ` +
      `<span class="fig">${people}</span> ${EST} people in all. Grey land on the map has no data yet.`;
    scope.hidden = false;
    $("about-version").textContent = `Version ${SHELL}, data built ${m.built}.`;
  } catch (err) {
    live.textContent = `Could not load the population data (${err.message}).`;
    live.hidden = false;
  }
}
// Only a hosted copy may add an analytics beacon (web/ and local builds never have one);
// say visits are counted only where one is on the page.
$("about-visits").hidden = !document.querySelector("script[data-cf-beacon]");

function toggleCoverage(open) {
  if (open === !$("coverage").hidden) return;
  $("coverage").hidden = !open;
  if (open) describeData();
  if (open) renderSaved();
  // Into the sheet on opening, and back to the ? button on closing, for keyboards.
  (open ? $("coverage-close") : $("coverage-open")).focus({ preventScroll: true });
}

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
    const groups = new Map();
    for (const c of await TallyStatic.countries()) {
      const k = c.continent ?? "Other";
      if (!groups.has(k)) groups.set(k, []);
      groups.get(k).push(c);
    }
    const byName = (a, b) => a.localeCompare(b, undefined, { sensitivity: "base" });
    const names = [...groups.keys()].sort((a, b) =>
      a === "Other" ? 1 : b === "Other" ? -1 : byName(a, b),
    );
    list.replaceChildren(
      ...names.map((k) => continentGroup(k, groups.get(k).sort((a, b) => byName(a.name, b.name)))),
    );
  } catch (err) {
    list.textContent = `Could not read this browser's saved data (${err.message}).`;
  }
}

function continentGroup(continent, countries) {
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
  name.textContent = continent;
  const state = document.createElement("span");
  state.className = "saved-state";
  const bytes = countries.reduce((a, c) => a + c.bytes, 0);
  const savedBytes = countries.reduce((a, c) => a + c.savedBytes, 0);
  const full = countries.filter((c) => c.savedParts === c.parts).length;
  const word = countries.length === 1 ? "country" : "countries";
  state.textContent =
    savingContinent === continent
      ? "Downloading…"
      : full === countries.length
        ? `${countries.length} ${word}, all saved, ${fmtMB(savedBytes)} MB`
        : `${countries.length} ${word}, ${full} saved, ${fmtMB(bytes - savedBytes)} MB to save`;
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
        state.textContent = `Failed: ${err.message}`;
        savingContinent = null;
        for (const x of summary.querySelectorAll("button")) x.disabled = false;
        return;
      }
      renderSaved();
    });
    summary.append(b);
  };
  if (full < countries.length)
    action("Save all", async () => {
      savingContinent = continent;
      const todo = countries.filter((c) => c.savedParts < c.parts);
      const total = todo.reduce((a, c) => a + c.bytes - c.savedBytes, 0);
      let done = 0;
      try {
        // One country at a time, so a failure says which one and keeps what finished.
        for (const [i, c] of todo.entries()) {
          await TallyStatic.saveCountry(c.iso3, ({ received }) => {
            state.textContent =
              `Downloading ${c.name} (${i + 1} of ${todo.length}), ` +
              `${fmtMB(done + received)} of ${fmtMB(total)} MB`;
          });
          done += c.bytes - c.savedBytes;
        }
      } finally {
        savingContinent = null;
      }
    });
  if (savedBytes)
    action("Remove all", async () => {
      for (const c of countries) if (c.savedParts) await TallyStatic.removeCountry(c.iso3);
    });

  const rows = document.createElement("ul");
  rows.className = "saved-list";
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
  name.textContent = c.name;
  const state = document.createElement("span");
  state.className = "saved-state";
  const full = c.savedParts === c.parts;
  state.textContent = full
    ? `Saved, ${fmtMB(c.savedBytes)} MB`
    : c.savedParts
      ? `Partly saved, ${fmtMB(c.savedBytes)} of ${fmtMB(c.bytes)} MB`
      : `Not saved, ${fmtMB(c.bytes)} MB`;
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
        state.textContent = `Failed: ${err.message}`;
        for (const x of li.querySelectorAll("button")) x.disabled = false;
        return;
      }
      renderSaved();
    });
    li.append(b);
  };
  if (!full)
    action(c.savedParts ? "Save the rest" : "Save", () =>
      TallyStatic.saveCountry(c.iso3, ({ received, total }) => {
        state.textContent = `Downloading… ${fmtMB(received)} of ${fmtMB(total)} MB`;
      }),
    );
  if (c.savedParts) action("Remove", () => TallyStatic.removeCountry(c.iso3));
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

// Grey out the land the bundle has no data for, and frame the map on the rest: in a demo
// the first question is "where can I draw", and a blank result elsewhere is not a bug.
async function showStaticData() {
  let m = null;
  try {
    m = await staticData;
  } catch (err) {
    setStatus(`Could not load the population data (${err.message}). Reload to retry.`, true);
  }
  // Added even without data: the basemap's own credits are a licence condition too.
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
    ? [...groups].map(([what, links]) => `${what}: ${links.join(", ")}`).join(" | ")
    : undefined;
  map.addControl(
    new maplibregl.AttributionControl({ compact: PHONE_SHEET.matches, customAttribution: credits }),
  );
  addScale();
  foldCredits();
  trackCreditsHeight();
  if (!m) return;
  // A wash over the countries with no data; dashed borders round the covered ones stopped
  // helping once they ran round most of the world (191 countries, 2026-10-13).
  // Under the heatmap and the place names, so neither is greyed.
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
  fillPicker().catch((err) => console.warn("country picker:", err));
}

// The country picker: every country in the data, by continent then name, as the ? panel
// lists them. Picking one shows all of it, counted from its own data (TallyStatic.country).
async function fillPicker() {
  const groups = new Map();
  for (const c of await TallyStatic.countries()) {
    const k = c.continent ?? "Other";
    if (!groups.has(k)) groups.set(k, []);
    groups.get(k).push(c);
  }
  const byName = (a, b) => a.localeCompare(b, undefined, { sensitivity: "base" });
  const names = [...groups.keys()].sort((a, b) =>
    a === "Other" ? 1 : b === "Other" ? -1 : byName(a, b),
  );
  const select = $("country-pick");
  for (const k of names) {
    const group = document.createElement("optgroup");
    group.label = k;
    for (const c of groups.get(k).sort((a, b) => byName(a.name, b.name)))
      group.append(new Option(c.name, c.iso3));
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

function listNames(countries) {
  const names = countries.map((c) => c.name ?? c); // country objects or plain names
  return names.length > 1 ? `${names.slice(0, -1).join(", ")} and ${names.at(-1)}` : names[0];
}

// ---- queries -----------------------------------------------------------------

let inflight = null;
let lastGeometry = null;
let lastCountry = null; // ISO3 when the result is a whole country from the picker
let lastLabel = null; // () => status text saying what was counted (around a point)
let lastResult = null;

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
}
setDetail(detail);
for (const b of document.querySelectorAll(".seg[data-detail]")) {
  b.addEventListener("click", () => setDetail(b.dataset.detail));
}

// Nothing is stored below the source's base resolution, so once the area is small enough
// that Normal already reaches it, Fine has nowhere to go. Say so instead of leaving a
// button that changes nothing when pressed. But never grey out the button that is
// selected: after picking Fine, a disabled Fine read as if the click had not taken (F-15).
function setFineAvailable(available) {
  const fineOn = detail === "fine";
  $("detail-fine").disabled = !available && !fineOn;
  $("detail-note").textContent = available ? "" : fineOn ? "Full detail" : "Already at full detail";
  $("detail-note").hidden = available;
}

// An answer missing fields this build expects means the page and static.js have drifted
// apart -- usually a cached shell surviving an update. Left alone that shows up as
// silently wrong output (a blank heatmap, a toggle that does nothing) rather than an
// error, so say it plainly instead.
class StaleAppError extends Error {
  constructor() {
    super("App out of date. Reload to update.");
    this.name = "StaleAppError";
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
function queryPopulation(geometry, label = null, { fresh = true } = {}) {
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
  lastCountry = iso3;
  lastGeometry = null;
  lastLabel = null;
  $("country-pick").value = iso3;
  const bounds = (body) => (body.cells.length ? cellsBounds(body.cells) : null);
  return runQuery(() => countryQuery(iso3), { frame: frame ? bounds : null, fresh, maxZoom: 11 });
}

// `frame(body)` gives the bounds to move the map onto once the answer is drawn, or null.
async function runQuery(ask, { frame = null, fresh = false, maxZoom = 16 } = {}) {
  if (inflight) inflight.abort();
  const controller = new AbortController();
  inflight = controller;
  $("draw").disabled = true;
  setStatus("Querying…");
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
    setStatus(
      err.name === "StaleAppError" ? err.message : `Query failed: ${err.message}`,
      true,
    );
  } finally {
    if (inflight === controller) {
      inflight = null;
      $("draw").disabled = false;
    }
  }
}

const showDownload = ({ received, total, names }) => {
  if (total) setStatus(`Downloading ${listNames(names)} data… ${fmtMB(received)} of ${fmtMB(total)} MB`);
};

async function staticQuery(geometry) {
  // Let "Querying…" paint before the synchronous polyfill and lookup run.
  await new Promise((r) => setTimeout(r, 0));
  return TallyStatic.query(geometry, DETAIL_STEP[detail], showDownload);
}

async function countryQuery(iso3) {
  await new Promise((r) => setTimeout(r, 0));
  return TallyStatic.country(iso3, DETAIL_STEP[detail], showDownload);
}

// Population sources as the result line and the exported image name them.
const SOURCE_NAMES = { meta_hrsl: "Meta", worldpop_100m: "WorldPop" };
const SOURCE_CREDITS = {
  meta_hrsl: "Meta Data for Good, High Resolution Population Density Maps (CC BY 4.0)",
  worldpop_100m: "WorldPop, Global2 R2025A constrained 100 m (CC BY 4.0)",
};
const sourceKeys = (source) => String(source).split(", ").filter((k) => k in SOURCE_CREDITS);

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
    const t = Math.min(1, Math.max(0, (Math.log10(density + 1) - lo) / span));
    return RAMP[Math.min(RAMP.length - 1, Math.floor(t * RAMP.length))];
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
  const ticks = [0, 0.5, 1].map((t) => showDensity(Math.pow(10, lo + t * span) - 1));
  $("legend-ticks").innerHTML = ticks.map((t) => `<span>${t}</span>`).join("");

  // Under the number, in words: what was counted.
  const people = Math.round(res.total) === 1 ? "person" : "people";
  $("counted").textContent = res.country
    ? `${people} in ${res.country.name}`
    : lastLabel
      ? `${people} ${lastLabel()}`
      : `${people} inside your shape`;
  // Which data a country's count is, since there is no drawn shape to explain it.
  $("counted-source").hidden = !res.country;
  if (res.country) {
    const from = sourceKeys(res.source).map((k) => SOURCE_NAMES[k]).join(" and ") || res.source;
    const year = String(res.country.data_version ?? "").slice(0, 4);
    $("counted-source").textContent = `Whole country, ${from} data${year ? ` from ${year}` : ""}`;
  }

  $("result").hidden = false;
  $("panel").classList.add("has-result");
  // A total that leaves a country out must say so: it is a floor, not an answer.
  if (res.missing?.length) {
    setStatus(
      `At least ${fmtInt(res.total)} ${EST}: could not download ${listNames(res.missing)} data, ` +
        "so it is left out. Check your connection and draw again.",
      true,
    );
    return;
  }
  setStatus(
    res.populated_cells === 0
      ? "No people here. Grey land on the map has no data."
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
    const t = Math.min(1, Math.max(0, (Math.log10(density + 1) - lo) / span));
    return RAMP[Math.min(RAMP.length - 1, Math.floor(t * RAMP.length))];
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

// Render a throwaway MapLibre map off-screen and resolve with a copy of its canvas.
function renderMap({ width, height, setup }) {
  return new Promise((resolve, reject) => {
    const el = document.createElement("div");
    el.style.cssText = `position:fixed;left:-20000px;top:0;width:${width}px;height:${height}px;`;
    document.body.appendChild(el);
    const m = new maplibregl.Map({
      container: el,
      style: BASEMAP_STYLE,
      interactive: false,
      attributionControl: false,
      preserveDrawingBuffer: true,
      pixelRatio: 2,
      fadeDuration: 0,
    });
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
    m.on("error", (e) => {
      if (e.error && /style/i.test(String(e.error.message))) reject(e.error);
    });
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
  setStatus("Rendering image…");
  try {
    const geometry = lastGeometry;
    const res = lastResult;
    const cells = buildCellFeatures(res);
    // A whole country has no drawn outline: its hexagons stand in for one.
    const outline = geometry ? { type: "Feature", properties: {}, geometry } : cells;
    const bbox = geometry ? bboxOf(geometry) : cellsBounds(res.cells);
    const center = [(bbox[0][0] + bbox[1][0]) / 2, (bbox[0][1] + bbox[1][1]) / 2];

    const DW = 1000, DH = 800, OW = 480, OH = 480;
    const [detailMap, overviewMap] = await Promise.all([
      renderMap({
        width: DW,
        height: DH,
        setup: (m) => {
          const firstSymbol = m.getStyle().layers.find((l) => l.type === "symbol")?.id;
          m.addSource("cells", { type: "geojson", data: cells });
          m.addLayer(
            { id: "cells-fill", type: "fill", source: "cells",
              paint: { "fill-color": ["get", "color"], "fill-opacity": 0.7 } },
            firstSymbol,
          );
          m.addLayer(
            { id: "cells-line", type: "line", source: "cells",
              paint: { "line-color": "#00000033", "line-width": 0.5 } },
            firstSymbol,
          );
          if (geometry) {
            m.addSource("outline", { type: "geojson", data: outline });
            m.addLayer({ id: "outline", type: "line", source: "outline",
              paint: { "line-color": "#1f6feb", "line-width": 3 } });
          }
          m.fitBounds(bbox, { padding: 90, duration: 0, maxZoom: 15 });
        },
      }),
      renderMap({
        width: OW,
        height: OH,
        setup: (m) => {
          m.addSource("outline", { type: "geojson", data: outline });
          m.addLayer({ id: "outline-fill", type: "fill", source: "outline",
            paint: { "fill-color": "#1f6feb", "fill-opacity": 0.45 } });
          m.addLayer({ id: "outline-line", type: "line", source: "outline",
            paint: { "line-color": "#1f6feb", "line-width": 2 } });
          // A country can be bigger than this view: then show all of it, a little wider.
          m.jumpTo({ center, zoom: 4.6 });
          const fit = m.cameraForBounds(bbox, { padding: 40 });
          if (fit && fit.zoom < 4.6) m.jumpTo({ center: fit.center, zoom: Math.max(0, fit.zoom - 1) });
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
    ctx.fillText(
      res.country
        ? `Population of ${res.country.name}`
        : lastLabel
          ? `Population ${lastLabel()}`
          : "Population inside a drawn shape",
      PAD, 62,
    );
    ctx.fillStyle = "#6b6577";
    ctx.font = `400 16px ${UI_FONT}`;
    const when = new Date().toLocaleString("en-GB", { dateStyle: "long", timeStyle: "short" });
    // The address, not the name: images travel without context, and it brings people back.
    ctx.fillText(`Counted at tallymaps.org on ${when}`, PAD, 90);

    const top = 120;
    ctx.drawImage(detailMap, PAD, top, DW, DH);
    ctx.strokeStyle = "#ddd";
    ctx.lineWidth = 1;
    ctx.strokeRect(PAD + 0.5, top + 0.5, DW - 1, DH - 1);

    const rx = PAD + DW + PAD;
    ctx.drawImage(overviewMap, rx, top, OW, OH);
    ctx.strokeRect(rx + 0.5, top + 0.5, OW - 1, OH - 1);

    let y = top + OH + 60;
    drawStat(ctx, rx, y, "People", fmtInt(res.total), EST, { size: 56, color: "#980043" });
    y += 12;
    y += 100;
    drawStat(ctx, rx, y, "Area", showArea(res.areaKm2), U().area);
    y += 100;
    drawStat(ctx, rx, y, "Density", showDensity(res.density), `people per ${U().area}`);

    ctx.fillStyle = "#888";
    ctx.font = `400 12px ${UI_FONT}`;
    const credits = sourceKeys(res.source).map((k) => SOURCE_CREDITS[k]);
    ctx.fillText(
      `Population: ${(credits.length ? credits : [SOURCE_CREDITS.meta_hrsl]).join("; ")}. ` +
        "Map: OpenFreeMap © OpenMapTiles, data © OpenStreetMap contributors.",
      PAD, H - 22,
    );

    const blob = await new Promise((r) => canvas.toBlob(r, "image/png"));
    const stamp = new Date().toISOString().slice(0, 16).replace(/[-:T]/g, "");
    const file = new File([blob], `tally-${stamp}.png`, { type: "image/png" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = file.name;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 10000);
    setStatus(`Exported ${file.name}`);
  } catch (err) {
    setStatus(`Export failed: ${err.message}`, true);
  } finally {
    btn.disabled = false;
  }
}

$("export").addEventListener("click", exportImage);

// Last, once everything it touches exists: labels, radius box and scale in the saved units.
setUnits(units);
setMode(savedMode(), { remember: false });
