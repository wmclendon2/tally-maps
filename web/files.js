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
      const props = Object.fromEntries(table[0].map((h, c) => [h.trim(), cells[c] ?? ""]));
      const row = {
        name: cells[nameAt]?.trim() || null,
        fallback: ["file.unnamedPoint", { n: i + 1 }],
        kind: "circle",
        props,
      };
      const y = number(cells[lat]);
      const x = number(cells[lng]);
      if (!(Math.abs(y) <= 90 && Math.abs(x) <= 540)) {
        row.problem = { key: "file.problemBadPoint" };
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
        const row = { ...base, kind: "circle", fallback: ["file.unnamedPoint", { n: i + n + 1 }] };
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
      return featureRows(f, i).map((row) => ({ ...row, name: own || null }));
    });
    // Every row's coordinates out of range is the file's coordinate system, not its rows.
    if (rows.length && rows.every((r) => r.problem?.key === "file.problemNotLatLng"))
      throw new FileProblem("file.errNotLatLng", { name });
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
    if (inner.size > MAX_BYTES) throw new FileProblem("file.errTooBig", tooBig(inner.size, name));
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

  async function read(fileList) {
    const files = [...fileList];
    const main = files.find((f) => extension(f.name) === "shp") ?? files[0];
    const name = main.name;
    const size = files.reduce((sum, f) => sum + f.size, 0);
    if (size > MAX_BYTES) throw new FileProblem("file.errTooBig", tooBig(size, name));
    if (["dbf", "shx", "prj", "cpg"].includes(extension(name)))
      throw new FileProblem("file.errNoShp", { name });
    const rows =
      extension(name) === "shp"
        ? await fromLoose(files, name)
        : await fromBytes(name, await main.arrayBuffer(), name);
    if (!rows.length) throw new FileProblem("file.errEmpty", { name });
    if (rows.length > MAX_ROWS)
      throw new FileProblem("file.errTooMany", { name, n: rows.length, limit: MAX_ROWS });
    return { name, rows };
  }

  return { read, FileProblem, MAX_ROWS, MAX_BYTES };
})();
