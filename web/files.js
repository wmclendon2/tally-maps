// Copyright (c) 2026 Woody McLendon
// SPDX-License-Identifier: MIT
/* Reading a file of shapes or points in the browser (F-02): nothing is uploaded. GeoJSON and
 * CSV are read here; KML, KMZ and Shapefiles with @tmcw/togeojson and shpjs, loaded when
 * such a file is opened so that a first visit never pays for them. app.js counts the rows.
 *
 * read(files) gives { name, rows }. One row per shape or point, each with:
 *   name, kind ("shape" or "circle"), props (the file's own columns), and one of
 *   geometry (a Polygon or MultiPolygon, its rings continuous across 180°),
 *   center + radiusKm (a point; radiusKm null when the file gives none),
 *   problem ({ key, vars }: why this row cannot be counted, said in its place in the list).
 * A row is never dropped silently. What makes a whole file unreadable throws FileProblem,
 * whose key and vars app.js's errText says in the visitor's language. */

const TallyFiles = (() => {
  // How many rows one file may hold, and how large it may be. Beyond either, the file is
  // refused with a message rather than counted in part.
  const MAX_ROWS = 500;
  const MAX_BYTES = 64e6;
  const RADIUS_KM_MAX = 3000;

  class FileProblem extends Error {
    constructor(key, vars = {}) {
      super(key);
      this.name = "FileProblem";
      this.key = key;
      this.vars = vars;
    }
  }

  const SCRIPTS = {
    toGeoJSON: "vendor/@tmcw/togeojson@7.1.3/togeojson.umd.js",
    shp: "vendor/shpjs@6.2.0/shp.min.js",
  };
  const loading = new Map();
  function library(name) {
    if (!loading.has(name))
      loading.set(
        name,
        new Promise((resolve, reject) => {
          const s = document.createElement("script");
          s.src = SCRIPTS[name];
          s.onload = () => resolve(globalThis[name]);
          s.onerror = () => {
            loading.delete(name);
            reject(new Error(`could not load ${SCRIPTS[name]}`));
          };
          document.head.appendChild(s);
        }),
      );
    return loading.get(name);
  }

  const extension = (name) => name.toLowerCase().match(/\.([a-z0-9]+)$/)?.[1] ?? "";

  // Excel saves CSV in the Windows code page unless told otherwise: Côte d'Ivoire would
  // come out as C?te. UTF-8 is strict enough that text passing as it really is it.
  function decode(buf) {
    try {
      return new TextDecoder("utf-8", { fatal: true }).decode(buf);
    } catch (_) {
      return new TextDecoder("windows-1252").decode(buf);
    }
  }
  const stripBom = (text) => text.replace(/^﻿/, "");

  // ---- zip: KMZ, and whatever a zip holds besides a Shapefile -------------------------

  async function inflate(bytes) {
    const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream("deflate-raw"));
    return new Response(stream).arrayBuffer();
  }

  // The members of a zip, from its central directory: [{ name, read() }]. macOS's
  // __MACOSX/._* stubs are left out.
  function zipMembers(buf) {
    const view = new DataView(buf);
    let end = -1;
    for (let i = buf.byteLength - 22; i >= Math.max(0, buf.byteLength - 66000); i--)
      if (view.getUint32(i, true) === 0x06054b50) {
        end = i;
        break;
      }
    if (end < 0) return null;
    const out = [];
    let at = view.getUint32(end + 16, true);
    for (let n = view.getUint16(end + 10, true); n > 0; n--) {
      if (at + 46 > buf.byteLength || view.getUint32(at, true) !== 0x02014b50) return null;
      const method = view.getUint16(at + 10, true);
      const packed = view.getUint32(at + 20, true);
      const size = view.getUint32(at + 24, true);
      const nameLen = view.getUint16(at + 28, true);
      const skip = nameLen + view.getUint16(at + 30, true) + view.getUint16(at + 32, true);
      const local = view.getUint32(at + 42, true);
      const name = new TextDecoder().decode(new Uint8Array(buf, at + 46, nameLen));
      at += 46 + skip;
      if (name.startsWith("__MACOSX/") || name.endsWith("/")) continue;
      out.push({
        name,
        size,
        read: async () => {
          const start = local + 30 + view.getUint16(local + 26, true) + view.getUint16(local + 28, true);
          const bytes = new Uint8Array(buf, start, packed);
          return method === 0 ? bytes.slice().buffer : inflate(bytes);
        },
      });
    }
    return out;
  }

  // ---- CSV ------------------------------------------------------------------------------

  // Excel writes semicolons (and decimal commas) where the comma is the decimal mark, as in
  // French, Spanish and Portuguese: the header line says which it is.
  function delimiterOf(text) {
    const line = text.split(/\r?\n/, 1)[0];
    let best = ",";
    let most = 0;
    for (const d of [",", ";", "\t"]) {
      const n = line.split(d).length - 1;
      if (n > most) {
        best = d;
        most = n;
      }
    }
    return best;
  }

  // RFC 4180: a quoted field may hold the delimiter, a line break or a doubled quote.
  function csvRows(text, delimiter) {
    const rows = [];
    let row = [];
    let field = "";
    let quoted = false;
    for (let i = 0; i < text.length; i++) {
      const c = text[i];
      if (quoted) {
        if (c !== '"') field += c;
        else if (text[i + 1] === '"') {
          field += '"';
          i++;
        } else quoted = false;
      } else if (c === '"' && field === "") quoted = true;
      else if (c === delimiter) {
        row.push(field);
        field = "";
      } else if (c === "\n" || c === "\r") {
        if (c === "\r" && text[i + 1] === "\n") i++;
        row.push(field);
        field = "";
        if (row.some((v) => v.trim() !== "")) rows.push(row);
        row = [];
      } else field += c;
    }
    row.push(field);
    if (row.some((v) => v.trim() !== "")) rows.push(row);
    return rows;
  }

  const COLUMNS = {
    name: ["name", "label", "title", "site", "place", "nom", "nombre", "nome", "nama"],
    lat: ["latitude", "lat", "latitud", "lintang", "y"],
    lng: ["longitude", "lon", "long", "lng", "longitud", "bujur", "x"],
  };
  // Radius columns and what one of their units is in km. A bare "radius" is in km.
  const RADIUS_COLUMNS = {
    radius_km: 1,
    radius: 1,
    rayon: 1,
    rayon_km: 1,
    radio: 1,
    radio_km: 1,
    raio: 1,
    raio_km: 1,
    radius_m: 0.001,
    radius_mi: 1.609344,
    radius_miles: 1.609344,
  };

  const headerKey = (h) => h.trim().toLowerCase().replace(/[\s-]+/g, "_");

  // ---- Tally's own files (F-04, F-41) -----------------------------------------------------
  // The columns Tally writes are not the file's own: counting again writes them fresh, and
  // would otherwise carry the old ones along as file_area_km2 and so on, generation on generation.
  const COUNTED_WITH = "tallymaps.org";
  const OWN_COLUMNS = new Set([
    "name", "kind", "country_iso3", "population", "population_estimate", "area_km2", "area_mi2",
    "density_per_km2", "density_per_mi2", "left_out", "source", "data_year", "h3_resolution",
    "hexagons", "counted_at", "counted_with", "note", "latitude", "longitude", "radius_km", "drawn",
    "country_code", "auto_named",
  ]);
  const withoutOwn = (props) =>
    Object.fromEntries(Object.entries(props).filter(([k]) => !OWN_COLUMNS.has(k)));

  // A spreadsheet rewrites `true` as TRUE, VRAI, WAHR and so on. Tally only ever writes `true`
  // or nothing, so anything there means set.
  const isSet = (v) => v !== undefined && v !== null && v !== "" && v !== false;

  // What Tally itself calls a row, in each language it may have been saved in (file.unnamed,
  // file.unnamedPoint, file.drawnShape and file.drawnShapeNumber; tests/test_i18n.py checks the
  // lists against them). A marker only says Tally named the row when it was saved: a name a
  // person has typed over that since is theirs, and stays.
  const NUMBERED_LABELS = ["Shape", "Forme", "Forma", "Bentuk", "Point", "Punto", "Ponto", "Titik"];
  const DRAWN_LABELS = ["Drawn shape", "Forme dessinée", "Forma dibujada", "Forma desenhada", "Bentuk yang digambar"];
  const NUMBERED = new RegExp(`^(?:${NUMBERED_LABELS.join("|")}) \\d+$`);
  const DRAWN = new RegExp(`^(?:${DRAWN_LABELS.join("|")})(?: \\d+)?$`);
  const COORDINATES = /\d°.*,.*\d°/; // a drawn circle is named by where it is: 3.900°N, 11.500°E
  const isOurNumber = (name) => !name || NUMBERED.test(name);
  const isOurDrawnName = (name) => !name || DRAWN.test(name) || COORDINATES.test(name);

  // The apostrophe safeCell (app.js) put before a word a spreadsheet would run, taken off again:
  // it would otherwise become part of the name once the file is reopened.
  const unguarded = (v) => (/^'[=+\-@\t\r]/.test(v) ? v.slice(1) : v);

  // Before F-41 a count's name was its image's title, in the language it was saved in.
  const OLD_SHAPE_TITLES = new Set([
    "Population inside a drawn shape",
    "Population à l'intérieur d'une forme dessinée",
    "Población dentro de una forma dibujada",
    "População dentro de uma forma desenhada",
    "Penduduk di dalam bentuk yang digambar",
  ]);
  const OLD_CIRCLE_TITLE = /^(?:Population|Población|População|Penduduk) (.*\d°.*)$/;

  // Is this polygon the circle these numbers describe? Each corner within 2% of the radius
  // leaves room for the rounding of a saved outline. A map tool may have moved or reshaped
  // the outline since, and then the outline is what the file says: it stays a shape.
  function isOutlineOf(geometry, [lng, lat], km) {
    if (geometry?.type !== "Polygon" || geometry.coordinates.length !== 1) return false;
    if (![lng, lat, km].every(Number.isFinite)) return false;
    if (!(Math.abs(lat) <= 90 && Math.abs(lng) <= 540 && km > 0 && km <= RADIUS_KM_MAX)) return false;
    const rad = Math.PI / 180;
    return geometry.coordinates[0].every(([x, y]) => {
      const a =
        Math.sin(((y - lat) * rad) / 2) ** 2 +
        Math.cos(y * rad) * Math.cos(lat * rad) * Math.sin(((x - lng) * rad) / 2) ** 2;
      const d = 2 * 6371.0088 * Math.asin(Math.min(1, Math.sqrt(a)));
      return Math.abs(d - km) <= km * 0.02;
    });
  }

  // A row of Tally's own GeoJSON comes back as it was saved: a circle as a circle (F-41), and
  // a count Tally named itself without that name, so it is said afresh.
  function savedRow(row, props) {
    if (props?.counted_with !== COUNTED_WITH) return row;
    // Only Tally's number for a row with no name: it is numbered afresh, in today's language.
    if (isSet(props.auto_named) && isOurNumber(row.name)) row = { ...row, name: null };
    const out = { ...row, props: withoutOwn(props) };
    // A whole country or continent is saved with its hexagons and no outline: Tally picks it
    // again if it is all that was opened, and a list says where to find it.
    if (row.problem?.key === "file.problemNoShape" && ["country", "continent"].includes(props.kind)) {
      out.kind = props.kind;
      out.problem = { key: "file.problemWhole" };
      out.whole =
        props.country_code ||
        props.country_iso3 ||
        (props.kind === "continent" ? `continent:${props.name}` : null);
      return out;
    }
    const center = [props.longitude, props.latitude];
    if (!row.problem && props.kind === "circle" && isOutlineOf(row.geometry, center, props.radius_km)) {
      delete out.geometry;
      out.kind = "circle";
      out.fallback = ["file.unnamedPoint", row.fallback[1]];
      out.center = center;
      out.radiusKm = props.radius_km;
    }
    const oldCircle = row.name?.match(OLD_CIRCLE_TITLE);
    if ((isSet(props.drawn) && isOurDrawnName(row.name)) || OLD_SHAPE_TITLES.has(row.name)) {
      out.drawn = true;
      out.name = null;
    } else if (oldCircle) {
      out.said = oldCircle[1];
      out.name = oldCircle[1].charAt(0).toLocaleUpperCase() + oldCircle[1].slice(1);
    }
    return out;
  }

  function fromCsv(text, name) {
    const delimiter = delimiterOf(text);
    const table = csvRows(text, delimiter);
    if (!table.length) throw new FileProblem("file.errEmpty", { name });
    const header = table[0].map(headerKey);
    const column = (names) => names.map((n) => header.indexOf(n)).find((i) => i >= 0) ?? -1;
    const lat = column(COLUMNS.lat);
    const lng = column(COLUMNS.lng);
    if (lat < 0 || lng < 0)
      throw new FileProblem("file.errColumns", { name, columns: table[0].join(", ") });
    const nameAt = column(COLUMNS.name);
    const radiusAt = column(Object.keys(RADIUS_COLUMNS));
    const toKm = radiusAt >= 0 ? RADIUS_COLUMNS[header[radiusAt]] : 1;
    const number = (v) => {
      const s = String(v ?? "").trim();
      if (s === "") return NaN;
      return Number(delimiter === "," ? s : s.replace(",", "."));
    };
    return table.slice(1).map((cells, i) => {
      let props = Object.fromEntries(table[0].map((h, c) => [h.trim(), cells[c] ?? ""]));
      const own = props.counted_with === COUNTED_WITH;
      if (own) props = Object.fromEntries(Object.entries(props).map(([k, v]) => [unguarded(k), unguarded(v)]));
      const typed = cells[nameAt]?.trim() || null;
      const row = {
        name: own && typed ? unguarded(typed) : typed,
        fallback: ["file.unnamedPoint", { n: i + 1 }],
        kind: "circle",
        props: own ? withoutOwn(props) : props,
      };
      if (own && isSet(props.drawn) && isOurDrawnName(row.name)) {
        row.drawn = true;
        row.name = null;
      }
      if (own && isSet(props.auto_named) && isOurNumber(row.name)) row.name = null;
      const y = number(cells[lat]);
      const x = number(cells[lng]);
      if (!(Math.abs(y) <= 90 && Math.abs(x) <= 540)) {
        row.problem = { key: "file.problemBadPoint" };
        // A shape has no coordinates in a CSV, so it stays a row that says so, and still a shape.
        if (own && props.kind) row.kind = props.kind;
        if (own && props.kind === "shape") row.fallback = ["file.unnamed", { n: i + 1 }];
        return row;
      }
      row.center = [x, y];
      row.radiusKm = null;
      const r = String(cells[radiusAt] ?? "").trim();
      if (radiusAt >= 0 && r !== "") {
        const km = number(r) * toKm;
        if (!(km > 0 && km <= RADIUS_KM_MAX))
          row.problem = { key: "file.problemBadRadius", vars: { limit: RADIUS_KM_MAX } };
        else row.radiusKm = km;
      }
      return row;
    });
  }

  // ---- GeoJSON features -> rows ---------------------------------------------------------

  // Drops any height, and refuses what cannot be a position.
  function cleanRing(ring) {
    if (!Array.isArray(ring) || ring.length < 4) return null;
    const out = [];
    for (const p of ring) {
      if (!Array.isArray(p) || !Number.isFinite(p[0]) || !Number.isFinite(p[1])) return null;
      out.push([p[0], p[1]]);
    }
    return out;
  }

  // Corner to corner a ring may jump from +179 to -179 across the line; made continuous,
  // it measures and frames as one piece. static.js wraps longitudes back before the fill.
  function continuous(ring) {
    const out = [];
    let shift = 0;
    for (const [x, y] of ring) {
      if (out.length) {
        const step = x + shift - out[out.length - 1][0];
        if (step > 180) shift -= 360;
        else if (step < -180) shift += 360;
      }
      out.push([x + shift, y]);
    }
    return out;
  }

  const meanLng = (ring) => ring.reduce((sum, p) => sum + p[0], 0) / ring.length;

  // One polygon's rings on one side of the line: each hole beside its outer ring.
  function continuousPolygon(rings) {
    const made = rings.map(continuous);
    for (const ring of made.slice(1)) {
      const off = Math.round((meanLng(made[0]) - meanLng(ring)) / 360) * 360;
      if (off) for (const p of ring) p[0] += off;
    }
    return made;
  }

  // Latitude and longitude, or some other coordinate system's meters?
  const inRange = (rings) =>
    rings.every((ring) => ring.every(([x, y]) => Math.abs(y) <= 90 && Math.abs(x) <= 540));

  function polygonParts(geometry, out) {
    if (!geometry) return out;
    const { type, coordinates, geometries } = geometry;
    if (type === "Polygon") out.polygons.push(coordinates);
    else if (type === "MultiPolygon") out.polygons.push(...coordinates);
    else if (type === "Point") out.points.push(coordinates);
    else if (type === "MultiPoint") out.points.push(...coordinates);
    else if (type === "LineString" || type === "MultiLineString") out.lines = true;
    else if (type === "GeometryCollection") for (const g of geometries ?? []) polygonParts(g, out);
    return out;
  }

  // The rows of one feature: normally one, but one per point of a MultiPoint. A KML
  // placemark may hold a polygon with a pin for its label: the polygon is what counts.
  function featureRows(feature, i) {
    const props = feature?.properties ?? {};
    const parts = polygonParts(feature?.geometry, { polygons: [], points: [], lines: false });
    const base = { props, fallback: ["file.unnamed", { n: i + 1 }], kind: "shape" };
    if (parts.polygons.length) {
      const polys = [];
      for (const rings of parts.polygons) {
        const clean = Array.isArray(rings) && rings.length ? rings.map(cleanRing) : [null];
        if (clean.some((r) => !r)) return [{ ...base, problem: { key: "file.problemBadShape" } }];
        polys.push(clean);
      }
      if (!polys.every(inRange)) return [{ ...base, problem: { key: "file.problemNotLatLng" } }];
      const parted = polys.map(continuousPolygon);
      // Pieces of one shape on either side of the line (Fiji, the Aleutians) sit together.
      for (const rings of parted) {
        const off = Math.round((meanLng(parted[0][0]) - meanLng(rings[0])) / 360) * 360;
        if (off) for (const ring of rings) for (const p of ring) p[0] += off;
      }
      const geometry =
        parted.length === 1
          ? { type: "Polygon", coordinates: parted[0] }
          : { type: "MultiPolygon", coordinates: parted };
      return [{ ...base, geometry }];
    }
    if (parts.points.length) {
      return parts.points.map((p, n) => {
        const row = { ...base, kind: "circle", fallback: ["file.unnamedPoint", base.fallback[1]] };
        if (!Array.isArray(p) || !Number.isFinite(p[0]) || !Number.isFinite(p[1]))
          return { ...row, problem: { key: "file.problemBadPoint" } };
        if (Math.abs(p[1]) > 90 || Math.abs(p[0]) > 540)
          return { ...row, problem: { key: "file.problemNotLatLng" } };
        return { ...row, center: [p[0], p[1]], radiusKm: radiusFrom(props) };
      });
    }
    return [{ ...base, problem: { key: parts.lines ? "file.problemLine" : "file.problemNoShape" } }];
  }

  function radiusFrom(props) {
    for (const [k, v] of Object.entries(props)) {
      const toKm = RADIUS_COLUMNS[headerKey(k)];
      if (toKm && Number(v) * toKm > 0 && Number(v) * toKm <= RADIUS_KM_MAX) return Number(v) * toKm;
    }
    return null;
  }

  // The column that names each row: a name-like column most rows fill, else the first
  // text column whose values all differ (shapeName, NAME_2, zone_label, nom_zs, ...).
  function nameColumn(features) {
    const counts = new Map();
    for (const f of features)
      for (const [k, v] of Object.entries(f.properties ?? {}))
        if (typeof v === "string" && v.trim()) {
          if (!counts.has(k)) counts.set(k, new Set());
          counts.get(k).add(v.trim());
        }
    const filled = features.filter((f) => f.properties).length * 0.8;
    const keys = [...counts.keys()].filter((k) => counts.get(k).size > 0);
    const exact = new Set(COLUMNS.name);
    const nameLike = /(^|[_\s.-])(name|nom|nombre|nome|nama|label|title)($|[_\s.-]|\d)/i;
    const has = (k) =>
      features.filter((f) => typeof f.properties?.[k] === "string" && f.properties[k].trim()).length >=
      filled;
    return (
      keys.find((k) => exact.has(k.toLowerCase()) && has(k)) ??
      keys.find((k) => nameLike.test(k) && has(k)) ??
      keys.find((k) => counts.get(k).size === features.length) ??
      null
    );
  }

  function fromFeatures(features, name) {
    // Tally's own GeoJSON (F-04) lists every hexagon, then the shape: only the shape is a row.
    const kept = features.filter((f) => !(f?.properties?.kind === "hexagon" && f.properties.h3));
    const nameKey = nameColumn(kept);
    const rows = kept.flatMap((f, i) => {
      const own = nameKey ? String(f.properties?.[nameKey] ?? "").trim() : "";
      return featureRows(f, i).map((row) => savedRow({ ...row, name: own || null }, f?.properties));
    });
    // Every row's coordinates out of range is the file's coordinate system, not its rows.
    if (rows.length && rows.every((r) => r.problem?.key === "file.problemNotLatLng"))
      throw new FileProblem("file.errNotLatLng", { name });
    // Down the list, as a MultiPoint makes several rows of one feature: no two "Point 2".
    rows.forEach((row, i) => (row.fallback = [row.fallback[0], { n: i + 1 }]));
    return rows;
  }

  function fromGeoJson(text, name) {
    let data;
    try {
      data = JSON.parse(stripBom(text));
    } catch (_) {
      throw new FileProblem("file.errRead", { name });
    }
    // A named system other than longitude and latitude (RFC 7946 dropped `crs`, but QGIS
    // still writes it): the numbers are meters, and small ones could even pass for degrees.
    const crs = data?.crs?.properties?.name;
    if (crs && !/CRS84|EPSG:+4326|\bWGS\s*84\b/i.test(crs))
      throw new FileProblem("file.errNotLatLng", { name });
    if (data?.type === "FeatureCollection" && Array.isArray(data.features))
      return fromFeatures(data.features, name);
    if (data?.type === "Feature") return fromFeatures([data], name);
    if (typeof data?.type === "string" && (data.coordinates || data.geometries))
      return fromFeatures([{ type: "Feature", properties: {}, geometry: data }], name);
    throw new FileProblem("file.errRead", { name });
  }

  async function fromKml(text, name) {
    const doc = new DOMParser().parseFromString(text, "application/xml");
    if (doc.querySelector("parsererror")) throw new FileProblem("file.errRead", { name });
    const toGeoJSON = await library("toGeoJSON");
    return fromFeatures(toGeoJSON.kml(doc).features, name);
  }

  // shpjs answers a zip with one collection, or a list of them when it holds several layers.
  const layersOf = (data) => (Array.isArray(data) ? data : [data]).flatMap((c) => c.features ?? []);

  async function fromZip(buf, name) {
    const members = zipMembers(buf);
    if (!members) throw new FileProblem("file.errRead", { name });
    const of = (...exts) => members.filter((m) => exts.includes(extension(m.name)));
    if (of("shp").length) {
      // shpjs unpacks all of these at once and leaves the rest of the zip alone (its filter is
      // the same list), so a Shapefile beside a large photo opens.
      const size = of("shp", "dbf", "json", "prj", "cpg").reduce((sum, m) => sum + m.size, 0);
      if (size > MAX_BYTES) throw new FileProblem("file.errTooBigUnzipped", tooBig(size, name));
      const shp = await library("shp");
      try {
        return fromFeatures(layersOf(await shp(buf)), name);
      } catch (err) {
        if (err instanceof FileProblem) throw err;
        console.warn("shapefile:", err);
        throw new FileProblem("file.errRead", { name });
      }
    }
    // A KMZ is a zip with a .kml inside; a zip may just as well hold any other format here.
    const inner = of("kml")[0] ?? of("geojson", "json")[0] ?? of("csv", "tsv")[0];
    if (!inner) throw new FileProblem("file.errZip", { name });
    if (inner.size > MAX_BYTES) throw new FileProblem("file.errTooBigUnzipped", tooBig(inner.size, name));
    return fromBytes(inner.name, await inner.read(), name);
  }

  const tooBig = (bytes, name) => ({ name, mb: Math.ceil(bytes / 1e6), limit: MAX_BYTES / 1e6 });

  function fromBytes(file, buf, name) {
    const ext = extension(file);
    if (ext === "geojson" || ext === "json") return fromGeoJson(decode(buf), name);
    if (ext === "kml") return fromKml(decode(buf), name);
    if (ext === "kmz" || ext === "zip") return fromZip(buf, name);
    if (ext === "csv" || ext === "tsv" || ext === "txt") return fromCsv(stripBom(decode(buf)), name);
    if (ext === "gpkg") throw new FileProblem("file.errGeoPackage", { name });
    throw new FileProblem("file.errType", { name });
  }

  // A Shapefile picked as its loose files rather than zipped.
  async function fromLoose(files, name) {
    const of = (ext) => files.find((f) => extension(f.name) === ext);
    const shp = await library("shp");
    const text = async (f) => (f ? decode(await f.arrayBuffer()) : undefined);
    try {
      const shapes = shp.parseShp(await of("shp").arrayBuffer(), await text(of("prj")));
      const dbf = of("dbf");
      const table = dbf ? shp.parseDbf(await dbf.arrayBuffer(), await text(of("cpg"))) : [];
      return fromFeatures(shp.combine([shapes, table]).features, name);
    } catch (err) {
      if (err instanceof FileProblem) throw err;
      console.warn("shapefile:", err);
      throw new FileProblem("file.errRead", { name });
    }
  }

  const SHAPEFILE_PARTS = ["shp", "dbf", "shx", "prj", "cpg"];

  // What was picked, as things to read: each file on its own, and a Shapefile's loose parts
  // together. One .shp takes every part picked with it; several go by their names.
  function sourcesOf(files) {
    const isPart = (f) => SHAPEFILE_PARTS.includes(extension(f.name));
    const stem = (f) => f.name.slice(0, f.name.lastIndexOf(".")).toLowerCase();
    const parts = files.filter(isPart);
    const shps = parts.filter((f) => extension(f.name) === "shp");
    const partsOf = (shp) => (shps.length === 1 ? parts : parts.filter((p) => stem(p) === stem(shp)));
    const stray = parts.find((p) => !shps.some((shp) => partsOf(shp).includes(p)));
    if (stray) throw new FileProblem("file.errNoShp", { name: stray.name });
    return files
      .filter((f) => !isPart(f) || shps.includes(f))
      .map((f) => ({ name: f.name, parts: shps.includes(f) ? partsOf(f) : null, file: f }));
  }

  async function readSource({ name, parts, file }) {
    try {
      const rows = parts ? await fromLoose(parts, name) : await fromBytes(name, await file.arrayBuffer(), name);
      if (!rows.length) throw new FileProblem("file.errEmpty", { name });
      return rows;
    } catch (err) {
      if (err instanceof FileProblem) throw err;
      console.warn("file:", err);
      throw new FileProblem("file.errRead", { name });
    }
  }

  // Everything picked makes one list. A file that cannot be read stops it, and is named: a
  // list quietly missing a file would look complete.
  async function read(fileList) {
    const files = [...fileList];
    const sources = sourcesOf(files);
    const several = sources.length > 1;
    const name = sources.map((s) => s.name).join(", ");
    const size = files.reduce((sum, f) => sum + f.size, 0);
    if (size > MAX_BYTES)
      throw new FileProblem(several ? "file.errTooBigFiles" : "file.errTooBig", tooBig(size, name));
    const rows = [];
    for (const source of sources) rows.push(...(await readSource(source)));
    if (rows.length > MAX_ROWS)
      throw new FileProblem(several ? "file.errTooManyFiles" : "file.errTooMany", {
        name,
        n: rows.length,
        limit: MAX_ROWS,
      });
    // "Shape 1" once, not once for each file.
    if (several) rows.forEach((row, i) => (row.fallback = [row.fallback[0], { n: i + 1 }]));
    return { name, rows };
  }

  return { read, FileProblem, MAX_ROWS, MAX_BYTES, OWN_COLUMNS, COUNTED_WITH };
})();
