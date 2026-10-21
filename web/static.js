// Copyright (c) 2026 Woody McLendon
// SPDX-License-Identifier: MIT
/* Tally's engine: answers "how many people are in this shape?" in the browser, from
 * downloaded country packs. index.html's <meta name="tally-data"> says where the data is.
 *
 * resolve() is a port of ingest/resolve.py and must stay in step with it: same automatic
 * resolution, same detail step, same cell limit, so the browser's answers equal the Python
 * reference's (scripts/browser_parity.py checks them in headless Chrome).
 *
 * Data: dist/packs/catalog.json lists each country's packs (parts) with their res-3
 * footprints (scripts/build_packs.py). A query works out which parts its cells fall in,
 * downloads the ones this browser does not have yet, checks their SHA-256, keeps them in
 * IndexedDB and sums from there. So the first polygon over a country costs a download and
 * every later one, including offline, does not. Two parts of the same H3 cell (a cell on
 * a border) are both summed.
 *
 * Memory: only the one resolution a query reads is kept from each part, and only up to
 * MEMORY_BUDGET (see there). country() answers for a whole country straight from its
 * packs, with no outline: the country picker in app.js.
 *
 * A deploy that rebuilds a country renames its pack files (they are named by content), so
 * a page loaded before it asks for files that are gone. A 404 on a pack makes a query read
 * the catalogue again and run once more (F-31); the page gets a "tally:data-updated"
 * event, and app.js offers a reload. */

const TallyStatic = (() => {
  // ingest/resolve.py
  const AUTO_TARGET_CELLS = 4000;
  const MAX_CELLS_LIMIT = 40000;
  const MAX_DETAIL = 2;
  // scripts/build_packs.py FORMAT[0]: a pack with another major is refused, not misread.
  const PACK_MAJOR = 1;

  // Up to shell v25 every part a session touched stayed unpacked in memory, all its
  // resolutions, for good: a box over the whole USA kept ~250 MB, India another ~270 MB,
  // and scripts/browser_countries.py's stress pass ended at 763 MB. Now a query
  // keeps just the resolution it reads from each part, copied out of the unpacked file
  // (the finest level is ~85% of a part, the coarse ones a few percent), and the least
  // recently used of those go once they pass this. The file stays in IndexedDB, so
  // another resolution later costs an unpack (~50 ms a part), not a download. One query
  // can still briefly need more: its 40,000-cell limit bounds how much that can be.
  const MEMORY_BUDGET = 96e6;
  // Parts downloaded or unpacked at once. Each holds its download (~5 MB) and the
  // unpacked file (up to ~20 MB) until its tables are copied out, so this bounds the peak
  // while India's 16 parts load, and three keep a connection busy.
  const PART_CONCURRENCY = 3;

  let ready = null;
  let manifest = null;
  let catalog = null;
  let catalogBase = null;
  const byRoot = new Map(); // res-3 cell -> parts whose footprint holds it
  const parts = new Map(); // url -> part (catalogue entry plus iso3, name, maxRes)
  const tables = new Map(); // `${url}|${res}` -> {hi, lo, pop, bytes}, least recently used first
  let tableBytes = 0;
  const pendingTables = new Map(); // `${url}|${res}` -> Promise<table>, while loading
  const pendingFiles = new Map(); // url -> Promise<stored file bytes>, while downloading

  // ---- IndexedDB: one record per part, keyed by its content-hashed file name ----------

  const DB_NAME = "tally-packs";
  const STORE = "parts";
  let dbPromise = null;

  // Resolves to null where storage is unavailable (some private windows): the app then
  // still works, it just downloads again next visit.
  function db() {
    dbPromise ??= new Promise((resolve) => {
      try {
        const req = indexedDB.open(DB_NAME, 1);
        req.onupgradeneeded = () => req.result.createObjectStore(STORE, { keyPath: "url" });
        req.onsuccess = () => resolve(req.result);
        req.onerror = () => {
          console.warn("IndexedDB unavailable:", req.error);
          resolve(null);
        };
      } catch (err) {
        console.warn("IndexedDB unavailable:", err);
        resolve(null);
      }
    });
    return dbPromise;
  }

  async function idb(mode, op) {
    const d = await db();
    if (!d) return undefined;
    return new Promise((resolve, reject) => {
      const tx = d.transaction(STORE, mode);
      const req = op(tx.objectStore(STORE));
      tx.oncomplete = () => resolve(req?.result);
      tx.onerror = tx.onabort = () => reject(tx.error);
    });
  }

  const storedKeys = () => idb("readonly", (s) => s.getAllKeys()).then((k) => k ?? []);

  // Ask once for storage the browser will not clear under pressure. Chrome grants it to
  // installed or often-used sites, Firefox asks the user, Safari treats it as a hint.
  let persistAsked = false;
  function askPersist() {
    if (persistAsked) return;
    persistAsked = true;
    navigator.storage?.persist?.().catch(() => {});
  }

  // ---- reading packs --------------------------------------------------------------

  const isGzip = (buf) => {
    const b = new Uint8Array(buf, 0, 2);
    return b[0] === 0x1f && b[1] === 0x8b;
  };

  // Hosts differ on .gz files: most serve the bytes as they are, some decode them on the
  // way out. Check for the gzip magic number rather than trusting either.
  async function gunzipIfNeeded(buf) {
    if (!isGzip(buf)) return buf;
    const stream = new Blob([buf]).stream().pipeThrough(new DecompressionStream("gzip"));
    return new Response(stream).arrayBuffer();
  }

  async function sha256Hex(buf) {
    if (!crypto.subtle) return null; // insecure context (http://<lan-ip>): cannot check
    const d = new Uint8Array(await crypto.subtle.digest("SHA-256", buf));
    return Array.from(d, (b) => b.toString(16).padStart(2, "0")).join("");
  }

  async function verify(buf, part) {
    const want = isGzip(buf) ? part.sha256 : part.raw_sha256;
    const got = await sha256Hex(buf);
    if (got && got !== want) throw new Error(`${part.name} data failed its checksum`);
  }

  // Layout: scripts/build_packs.py. The one section wanted is copied out of the unpacked
  // file, so the rest of it can be freed; a part without that resolution reads as empty.
  function extract(raw, res) {
    const dv = new DataView(raw);
    const magic = String.fromCharCode(...new Uint8Array(raw, 0, 4));
    if (magic !== "TLYP") throw new Error("not a Tally data pack");
    if (dv.getUint16(4, true) !== PACK_MAJOR)
      throw new DataUpdatedError("Tally has been updated. Reload to get the latest.");
    const n = dv.getUint32(8, true);
    const header = JSON.parse(new TextDecoder().decode(new Uint8Array(raw, 12, n)));
    const start = Math.ceil((12 + n) / 8) * 8;
    const s = header.sections.find((x) => x.resolution === res);
    const count = s ? s.count : 0;
    const buf = s ? raw.slice(start + s.offset, start + s.offset + 12 * count) : new ArrayBuffer(0);
    return {
      hi: new Uint32Array(buf, 0, count),
      lo: new Uint32Array(buf, 4 * count, count),
      pop: new Float32Array(buf, 8 * count, count),
      bytes: buf.byteLength,
    };
  }

  // A small semaphore: see PART_CONCURRENCY.
  let active = 0;
  const waiting = [];
  async function limited(fn) {
    while (active >= PART_CONCURRENCY) await new Promise((r) => waiting.push(r));
    active++;
    try {
      return await fn();
    } finally {
      active--;
      waiting.shift()?.();
    }
  }

  // A pack the catalogue this page read lists, but the host no longer has: renamed by a
  // later deploy. Never shown as it is; withFreshCatalog() reads the catalogue again.
  class PackGoneError extends Error {
    constructor(part) {
      super(`${part.name} data has moved`);
      this.name = "PackGoneError";
      this.part = part;
    }
  }
  // What a query ends with when the data changed under the page and a retry could not
  // make up for it: app.js shows the message with a Reload button.
  class DataUpdatedError extends Error {
    constructor(message = "Tally's data has been updated. Reload to get the latest.") {
      super(message);
      this.name = "DataUpdatedError";
    }
  }
  const isGone = (err) => err?.name === "PackGoneError";

  // Stream the file so the caller can show progress; `no-store` keeps a second copy out
  // of the HTTP cache, since IndexedDB already holds one.
  async function download(part, onBytes) {
    const r = await fetch(new URL(part.url, catalogBase), { cache: "no-store" });
    if (r.status === 404 || r.status === 410) throw new PackGoneError(part);
    if (!r.ok) {
      console.warn(`${part.url}: HTTP ${r.status}`);
      throw new Error(`Could not download ${part.name} data. Check the connection and try again.`);
    }
    const chunks = [];
    let got = 0;
    const reader = r.body.getReader();
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      chunks.push(value);
      got += value.length;
      onBytes(got);
    }
    const buf = new Uint8Array(got);
    let at = 0;
    for (const c of chunks) {
      buf.set(c, at);
      at += c.length;
    }
    await verify(buf.buffer, part);
    return buf.buffer;
  }

  // The part's file as stored (gzipped, or not if the host unpacked it): from IndexedDB,
  // else downloaded, checked and stored. onBytes(received) is called only on a download.
  // `fresh` skips a stored copy that turned out to be unreadable.
  function fileFor(part, onBytes = () => {}, fresh = false) {
    if (!pendingFiles.has(part.url)) {
      const p = (async () => {
        if (!fresh) {
          const rec = await idb("readonly", (s) => s.get(part.url)).catch(() => undefined);
          if (rec?.buf) return { buf: rec.buf, stored: true };
        }
        onBytes(0);
        const buf = await download(part, onBytes);
        const record = { url: part.url, iso3: part.iso3, bytes: buf.byteLength, saved: Date.now(), buf };
        await idb("readwrite", (s) => s.put(record))
          .then(askPersist)
          .catch((err) => console.warn("could not keep the download:", err));
        return { buf, stored: false };
      })();
      pendingFiles.set(part.url, p);
      p.finally(() => pendingFiles.delete(part.url)).catch(() => {});
    }
    return pendingFiles.get(part.url);
  }

  function remember(key, table) {
    tables.set(key, table);
    tableBytes += table.bytes;
    for (const [k, t] of tables) {
      if (tableBytes <= MEMORY_BUDGET || k === key) break;
      tables.delete(k);
      tableBytes -= t.bytes;
    }
  }

  function forget(url) {
    for (const [k, t] of tables)
      if (k.startsWith(`${url}|`)) {
        tables.delete(k);
        tableBytes -= t.bytes;
      }
  }

  // One resolution of one part: from memory, else unpacked from its file.
  function tableFor(part, res, onBytes = () => {}) {
    const key = `${part.url}|${res}`;
    const hit = tables.get(key);
    if (hit) {
      tables.delete(key); // most recently used goes last
      tables.set(key, hit);
      return Promise.resolve(hit);
    }
    if (!pendingTables.has(key)) {
      const p = limited(async () => {
        let file = await fileFor(part, onBytes);
        let table;
        try {
          table = extract(await gunzipIfNeeded(file.buf), res);
        } catch (err) {
          if (!file.stored) throw err;
          console.warn(`stored ${part.url} unreadable, downloading again:`, err);
          file = await fileFor(part, onBytes, true);
          table = extract(await gunzipIfNeeded(file.buf), res);
        }
        file = null; // the download and the unpacked file can go now
        remember(key, table);
        return table;
      });
      pendingTables.set(key, p);
      p.finally(() => pendingTables.delete(key)).catch(() => {});
    }
    return pendingTables.get(key);
  }

  // Fetch the parts' tables at `res`, reporting downloads as { received, total, names }.
  // Returns the tables by url and the parts that failed, with their errors.
  async function gather(needed, res, onProgress) {
    const fetching = new Map(); // url -> bytes received so far
    const report = () => {
      const going = needed.filter((p) => fetching.has(p.url));
      onProgress({
        received: [...fetching.values()].reduce((a, b) => a + b, 0),
        total: going.reduce((a, p) => a + p.bytes, 0),
        names: [...new Set(going.map((p) => p.name))],
      });
    };
    const settled = await Promise.allSettled(
      needed.map((p) =>
        tableFor(p, res, (n) => {
          fetching.set(p.url, n);
          report();
        }),
      ),
    );
    const got = new Map();
    const failed = [];
    settled.forEach((s, i) => {
      if (s.status === "fulfilled") got.set(needed[i].url, s.value);
      else failed.push({ part: needed[i], error: s.reason });
    });
    // A renamed pack is not "missing": the caller reads the catalogue again and retries.
    const gone = failed.find((f) => isGone(f.error));
    if (gone) throw gone.error;
    if (failed.length && !got.size) throw failed[0].error;
    return { got, failed };
  }

  // Drop stored parts the catalogue no longer lists (a rebuilt country gets new names).
  async function pruneStored() {
    const stale = (await storedKeys()).filter((k) => !parts.has(k));
    if (stale.length) await idb("readwrite", (s) => stale.forEach((k) => s.delete(k)));
  }

  // `cache: "no-cache"` asks the host whether the file changed (a 304 if not), so a
  // page never reads a catalogue its browser kept from an earlier deploy.
  async function getJson(url) {
    const r = await fetch(url, { cache: "no-cache" });
    if (!r.ok) throw new Error(`${url.pathname}: HTTP ${r.status}`);
    return r.json();
  }

  const dataUrl = () => document.querySelector('meta[name="tally-data"]').content;

  // The manifest and catalogue, read and checked; nothing is changed until both are in.
  async function readCatalog(manifestUrl) {
    const base = new URL(manifestUrl, location.href);
    const m = await getJson(base);
    const cBase = new URL(m.catalog, base);
    const c = await getJson(cBase);
    if (c.format?.[0] !== PACK_MAJOR)
      throw new DataUpdatedError("Tally has been updated. Reload to get the latest.");
    m.min_resolution = c.min_resolution;
    m.nodataUrl = new URL(m.nodata, base).href;
    return { m, cBase, c };
  }

  // Index a catalogue's parts, replacing any read before. Tables already unpacked stay:
  // they are keyed by file name, and a name always means the same content.
  function install({ m, cBase, c }) {
    manifest = m;
    catalogBase = cBase;
    catalog = c;
    parts.clear();
    byRoot.clear();
    for (const country of catalog.countries) {
      for (const p of country.parts) {
        const part = {
          ...p,
          iso3: country.iso3,
          name: country.name,
          maxRes: country.max_resolution,
          source: country.source,
        };
        parts.set(part.url, part);
        for (const root of p.footprint) {
          if (!byRoot.has(root)) byRoot.set(root, []);
          byRoot.get(root).push(part);
        }
      }
    }
    pruneStored().catch((err) => console.warn("could not tidy stored data:", err));
  }

  function load(manifestUrl) {
    if (ready) return ready;
    ready = (async () => {
      install(await readCatalog(manifestUrl));
      return manifest;
    })();
    ready.catch(() => (ready = null)); // let a later call retry after a network blip
    return ready;
  }

  // Read the manifest and catalogue again; true when they are not the ones this page has
  // (another build date, or other pack files), and then tell the page.
  async function refresh() {
    await load(dataUrl());
    const fresh = await readCatalog(dataUrl());
    const names = (c) => c.countries.flatMap((x) => x.parts.map((p) => p.url)).join(" ");
    const changed = fresh.m.built !== manifest.built || names(fresh.c) !== names(catalog);
    if (changed) {
      install(fresh);
      window.dispatchEvent(new CustomEvent("tally:data-updated"));
    }
    return changed;
  }

  // Run `ask`; if a pack it needs has been renamed, read the catalogue again and run it
  // once more. A second miss, or a 404 with nothing new in the catalogue, ends in words a
  // person can act on, never an HTTP status.
  async function withFreshCatalog(ask) {
    try {
      return await ask();
    } catch (err) {
      if (!isGone(err)) throw err;
      let changed;
      try {
        changed = await refresh();
      } catch (_) {
        throw new DataUpdatedError();
      }
      if (!changed) throw new DataUpdatedError(`Could not download ${err.part.name} data. Reload to try again.`);
      try {
        return await ask();
      } catch (err2) {
        throw isGone(err2) ? new DataUpdatedError() : err2;
      }
    }
  }

  // ---- the resolver (ingest/resolve.py) ------------------------------------------------

  // Population of one cell (hex string), or 0 when the table has no row for it.
  function lookup(t, hex) {
    const hi = parseInt(hex.slice(0, -8), 16);
    const lo = parseInt(hex.slice(-8), 16);
    let a = 0;
    let b = t.hi.length - 1;
    while (a <= b) {
      const m = (a + b) >>> 1;
      const mh = t.hi[m];
      if (mh < hi || (mh === hi && t.lo[m] < lo)) a = m + 1;
      else if (mh > hi || t.lo[m] > lo) b = m - 1;
      else return t.pop[m];
    }
    return 0;
  }

  // h3.geo_to_cells: cells whose centers fall inside, unioned over a MultiPolygon.
  function cover(geometry, res) {
    if (geometry.type === "Polygon") return h3.polygonToCells(geometry.coordinates, res, true);
    const all = new Set();
    for (const rings of geometry.coordinates)
      for (const c of h3.polygonToCells(rings, res, true)) all.add(c);
    return [...all];
  }

  function unwrap(g) {
    if (g.type === "Feature") return unwrap(g.geometry);
    if (g.type === "FeatureCollection") {
      if (g.features?.length !== 1) throw new Error("FeatureCollection must contain exactly one feature");
      return unwrap(g.features[0].geometry);
    }
    if (g.type !== "Polygon" && g.type !== "MultiPolygon")
      throw new Error(`geometry must be a Polygon or MultiPolygon, got ${g.type}`);
    return g;
  }

  function polygonAreaKm2(geometry) {
    for (let res = manifest.min_resolution; res < 10; res++) {
      const cells = cover(geometry, res);
      if (cells.length >= 20 || res === 9)
        return cells.reduce((s, c) => s + h3.cellArea(c, "km2"), 0);
    }
    return 0;
  }

  function chooseResolution(area, baseRes, maxCells) {
    for (let res = baseRes; res >= manifest.min_resolution; res--)
      if (area / h3.getHexagonAreaAvg(res, "km2") <= maxCells) return res;
    return manifest.min_resolution;
  }

  function autoResolution(geometry, baseRes, area) {
    let res = chooseResolution(area, baseRes, AUTO_TARGET_CELLS);
    while (res > manifest.min_resolution && cover(geometry, res).length > AUTO_TARGET_CELLS) res--;
    return res;
  }

  // The cells at `resolution` for this polygon, or the cell under its first vertex when the
  // polygon is smaller than one cell (ingest/resolve.py does the same).
  function cellsFor(geometry, resolution) {
    const cells = cover(geometry, resolution);
    if (cells.length > MAX_CELLS_LIMIT) {
      const hint =
        resolution <= manifest.min_resolution ? "Try a smaller area." : "Try less detail, or a smaller area.";
      throw new Error(
        `That area needs ${cells.length.toLocaleString("en-US")} cells at H3 resolution ` +
          `${resolution}, over the ${MAX_CELLS_LIMIT.toLocaleString("en-US")} cell limit. ${hint}`,
      );
    }
    if (cells.length) return cells;
    const ring = geometry.type === "Polygon" ? geometry.coordinates[0] : geometry.coordinates[0][0];
    const [lng, lat] = ring[0];
    return [h3.latLngToCell(lat, lng, resolution)];
  }

  const rootOf = (hex) => h3.cellToParent(hex, catalog.min_resolution);

  function partsFor(cells) {
    const out = new Set();
    for (const hex of cells) for (const p of byRoot.get(rootOf(hex)) ?? []) out.add(p);
    return [...out];
  }

  // onProgress({ received, total, names }) reports downloads while they run.
  function query(geojson, detail = 0, onProgress = () => {}) {
    return withFreshCatalog(() => queryOnce(geojson, detail, onProgress));
  }

  async function queryOnce(geojson, detail, onProgress) {
    const t0 = performance.now();
    await load(dataUrl());
    const geometry = unwrap(geojson);
    if (!(detail >= 0 && detail <= MAX_DETAIL)) throw new Error(`detail must be between 0 and ${MAX_DETAIL}`);
    const area = polygonAreaKm2(geometry);

    // The ceiling is the finest data any pack holds, lowered to the coarsest of the
    // countries this polygon touches: summing res 9 where one side only has res 8 would
    // quietly drop that side.
    let baseRes = Math.max(...[...parts.values()].map((p) => p.maxRes));
    let resolution = Math.min(autoResolution(geometry, baseRes, area) + detail, baseRes);
    let cells = cellsFor(geometry, resolution);
    let needed = partsFor(cells);
    const ceiling = needed.length ? Math.min(...needed.map((p) => p.maxRes)) : baseRes;
    if (ceiling < baseRes) {
      baseRes = ceiling;
      resolution = Math.min(autoResolution(geometry, baseRes, area) + detail, baseRes);
      cells = cellsFor(geometry, resolution);
      needed = partsFor(cells);
    }

    const { got, failed } = await gather(needed, resolution, onProgress);

    const out = [];
    let total = 0;
    for (const hex of cells.sort()) {
      let p = 0;
      for (const part of byRoot.get(rootOf(hex)) ?? []) {
        const t = got.get(part.url);
        if (t) p += lookup(t, hex);
      }
      if (p > 0) {
        out.push({ h3: hex, population: p });
        total += p;
      }
    }
    return {
      total,
      source: [...new Set(needed.map((p) => p.source))].join(", ") || "none",
      resolution,
      base_resolution: baseRes,
      cell_count: cells.length,
      populated_cells: out.length,
      approx_area_km2: Math.round(area * 100) / 100,
      cells: out,
      elapsed_ms: Math.round((performance.now() - t0) * 10) / 10,
      // Static-only: countries left out because their data could not be fetched, and
      // whether the polygon touches any country this demo has at all.
      missing: [...new Set(failed.map((f) => f.part.name))],
      covered: needed.length > 0,
    };
  }

  // ---- saving whole countries for offline use (the ? panel) --------------------------

  async function countries() {
    await ready;
    const have = new Set(await storedKeys());
    return catalog.countries.map((c) => {
      const saved = c.parts.filter((p) => have.has(p.url));
      return {
        iso3: c.iso3,
        name: c.name,
        continent: c.continent ?? null, // older catalogues lack it
        bytes: c.bytes,
        parts: c.parts.length,
        savedParts: saved.length,
        savedBytes: saved.reduce((a, p) => a + p.bytes, 0),
      };
    });
  }

  // Saving only stores the files (checked against their hashes); nothing is unpacked
  // into memory until a query reads it.
  function saveCountry(iso3, onProgress = () => {}) {
    return withFreshCatalog(() => saveOnce(iso3, onProgress));
  }

  async function saveOnce(iso3, onProgress) {
    await ready;
    const have = new Set(await storedKeys());
    const todo = [...parts.values()].filter((p) => p.iso3 === iso3 && !have.has(p.url));
    const total = todo.reduce((a, p) => a + p.bytes, 0);
    const got = new Map();
    await Promise.all(
      todo.map((p) =>
        limited(() =>
          fileFor(p, (n) => {
            got.set(p.url, n);
            onProgress({ received: [...got.values()].reduce((a, b) => a + b, 0), total });
          }),
        ),
      ),
    );
  }

  async function removeCountry(iso3) {
    await ready;
    const urls = [...parts.values()].filter((p) => p.iso3 === iso3).map((p) => p.url);
    urls.forEach(forget);
    await idb("readwrite", (s) => urls.forEach((u) => s.delete(u)));
  }

  // ---- a whole country (the country picker) --------------------------------------------

  // The level to show a whole country at: the finest whose populated cells fit the same
  // budget a drawn polygon's Normal answer gets (ingest/resolve.py AUTO_TARGET_CELLS), so a
  // sparse country gets finer hexagons than its outline's area alone would allow. Detail
  // steps finer from there, never past the data or MAX_CELLS_LIMIT.
  // scripts/browser_countries.py works this out the same way in Python; keep them in step.
  function countryResolution(c, detail) {
    const counts = c.cells_by_resolution;
    const levels = Object.keys(counts).map(Number).sort((a, b) => a - b);
    let res = levels[0];
    for (const r of levels) if (counts[r] <= AUTO_TARGET_CELLS) res = r;
    res = Math.min(res + detail, c.max_resolution);
    while (res > levels[0] && counts[res] > MAX_CELLS_LIMIT) res--;
    return res;
  }

  const toHex = (hi, lo) => hi.toString(16) + lo.toString(16).padStart(8, "0");

  // Every populated cell of one country at one level, from its own packs: no outline and
  // no polyfill, so the total is the country's exactly (every level of a pack sums to it)
  // and a cell on the border carries only this country's people. Same answer shape as
  // query(), plus `country`.
  function country(iso3, detail = 0, onProgress = () => {}) {
    return withFreshCatalog(() => countryOnce(iso3, detail, onProgress));
  }

  async function countryOnce(iso3, detail, onProgress) {
    const t0 = performance.now();
    await load(dataUrl());
    const c = catalog.countries.find((x) => x.iso3 === iso3);
    if (!c) throw new Error(`No data for ${iso3}`);
    if (!c.cells_by_resolution || !c.area_km2)
      throw new Error("This data is older than the country picker. Reload to update.");
    if (!(detail >= 0 && detail <= MAX_DETAIL)) throw new Error(`detail must be between 0 and ${MAX_DETAIL}`);
    const resolution = countryResolution(c, detail);
    const needed = c.parts.map((p) => parts.get(p.url));
    const { got, failed } = await gather(needed, resolution, onProgress);
    const out = [];
    let total = 0;
    for (const t of got.values())
      for (let i = 0; i < t.pop.length; i++) {
        out.push({ h3: toHex(t.hi[i], t.lo[i]), population: t.pop[i] });
        total += t.pop[i];
      }
    return {
      // The catalogue's figure when every part came: the cells are float32, so their sum
      // can be a person off it (USA: 308,276,903 for 308,276,904).
      total: failed.length ? total : c.total,
      cells_total: total,
      source: c.source,
      resolution,
      base_resolution: c.max_resolution,
      cell_count: out.length,
      populated_cells: out.length,
      approx_area_km2: c.area_km2,
      cells: out,
      elapsed_ms: Math.round((performance.now() - t0) * 10) / 10,
      missing: failed.length ? [c.name] : [],
      covered: true,
      country: {
        iso3: c.iso3,
        name: c.name,
        area_km2: c.area_km2,
        total: c.total,
        data_version: c.data_version,
      },
    };
  }

  // What the memory cap is holding, for scripts/browser_countries.py and for debugging.
  const memory = () => ({ tables: tables.size, bytes: tableBytes, budget: MEMORY_BUDGET });

  // Whether the data changed since this page read it (app.js asks when the tab comes
  // back); fires "tally:data-updated" when it has. Quietly false offline.
  const checkForUpdate = () => refresh().catch(() => false);

  return { load, query, country, countries, saveCountry, removeCountry, memory, checkForUpdate };
})();
