// Copyright (c) 2026 Woody McLendon
// SPDX-License-Identifier: MIT
/* Tally's languages (F-01). Loaded in <head>, before everything else, so the strings are
 * on their way while the map library downloads.
 *
 * The words live in i18n/<lang>.json, one flat table per language; en.json is the
 * reference, and any key a language lacks falls back to it. index.html keeps its English
 * (for link previews, search engines and the moment before this runs) and marks each
 * piece with data-i18n (text), data-i18n-html (text with links) or data-i18n-attr
 * ("aria-label=key;title=key"); tests/test_i18n.py keeps en.json in step with it.
 *
 * A value is a string with {placeholders}, or {"one": ..., "other": ...} chosen by
 * Intl.PluralRules on `n`. Numbers, lists and country names come from Intl, so no
 * language needs a table of them; a language may still name a country itself
 * ("country.HKG"), where the browser's name reads badly in a list.
 *
 * The language is the visitor's choice (the globe in the panel, or About), else the browser's first
 * language Tally has, else English. Until the strings are in, the panel stays hidden
 * (style.css, .i18n-wait), so no one sees English flip to French; a slow or failed load
 * shows the English after WAIT_MS. */

const TallyI18n = (() => {
  // Each language in its own words, as the picker lists them.
  const LANGUAGES = {
    en: "English",
    es: "Español",
    fr: "Français",
    id: "Bahasa Indonesia",
    pt: "Português",
  };
  // Old browsers still report Indonesian by its retired code.
  const ALIASES = { in: "id" };
  const WAIT_MS = 4000;

  const primary = (tag) => {
    const p = String(tag).toLowerCase().split(/[-_]/)[0];
    return ALIASES[p] ?? p;
  };
  const browserTags = () => (navigator.languages?.length ? navigator.languages : [navigator.language || "en"]);

  function chosen() {
    try {
      const saved = localStorage.getItem("lang");
      if (saved in LANGUAGES) return saved;
    } catch (_) {}
    for (const tag of browserTags()) if (primary(tag) in LANGUAGES) return primary(tag);
    return "en";
  }

  const lang = chosen();
  // Numbers and dates follow the browser's own variant of the language (pt-BR, fr-CA),
  // English always en-US, as Tally has shown them from the start.
  const locale =
    lang === "en" ? "en-US" : (browserTags().find((t) => primary(t) === lang) ?? lang);

  // i18n.js?v=N asks for the strings of the same release, which sw.js precaches.
  const version = new URL(document.currentScript.src).searchParams.get("v") ?? "";
  const getTable = (l) =>
    fetch(`i18n/${l}.json?v=${version}`).then((r) => {
      if (!r.ok) throw new Error(`i18n/${l}.json: HTTP ${r.status}`);
      return r.json();
    });

  let english = {};
  let table = {};
  let active = "en";
  let plural = new Intl.PluralRules(locale);
  let number = new Intl.NumberFormat(locale);

  document.documentElement.classList.add("i18n-wait");
  const reveal = () => document.documentElement.classList.remove("i18n-wait");
  const timer = setTimeout(reveal, WAIT_MS);
  const domReady = new Promise((r) =>
    document.readyState === "loading" ? document.addEventListener("DOMContentLoaded", r) : r(),
  );

  const ready = Promise.all([
    getTable("en"),
    lang === "en" ? null : getTable(lang).catch((err) => console.warn("strings:", err)),
  ])
    .then(([en, other]) => {
      english = en;
      table = other ?? en;
      active = other ? lang : "en";
      if (active === "en") {
        plural = new Intl.PluralRules("en-US");
        number = new Intl.NumberFormat("en-US");
      }
    })
    .catch((err) => console.warn("strings:", err))
    .then(() => domReady)
    .then(() => {
      document.documentElement.lang = active;
      if (active !== "en") applyTo(document);
      clearTimeout(timer);
      reveal();
    });

  // t("key", {name: value}): the active language's words, else English's, else the key.
  // Numbers among the values are written the language's way; `n` also picks the plural.
  function t(key, vars = {}) {
    let s = table[key] ?? english[key] ?? key;
    if (typeof s === "object") s = s[plural.select(Number(vars.n) || 0)] ?? s.other;
    return s.replace(/\{(\w+)\}/g, (m, k) => {
      const v = vars[k];
      return v === undefined ? m : typeof v === "number" ? number.format(v) : String(v);
    });
  }

  function applyTo(root) {
    for (const el of root.querySelectorAll("[data-i18n]")) el.textContent = t(el.dataset.i18n);
    for (const el of root.querySelectorAll("[data-i18n-html]")) el.innerHTML = t(el.dataset.i18nHtml);
    for (const el of root.querySelectorAll("[data-i18n-attr]"))
      for (const pair of el.dataset.i18nAttr.split(";")) {
        const [attr, key] = pair.split("=").map((x) => x.trim());
        if (attr && key) el.setAttribute(attr, t(key));
      }
  }

  // ISO3 -> the ISO 3166 code Intl.DisplayNames knows, for every place Tally may have
  // (reference/continents.json; tests/test_i18n.py checks the list covers it). Kosovo is
  // XK, as CLDR has it; Tally's KOS and XKX are both Kosovo.
  const ISO2 = Object.fromEntries(
    (
      "ABW:AW AFG:AF AGO:AO AIA:AI ALA:AX ALB:AL AND:AD ARE:AE ARG:AR ARM:AM ASM:AS ATG:AG " +
      "AUS:AU AUT:AT AZE:AZ BDI:BI BEL:BE BEN:BJ BES:BQ BFA:BF BGD:BD BGR:BG BHR:BH BHS:BS " +
      "BIH:BA BLM:BL BLR:BY BLZ:BZ BMU:BM BOL:BO BRA:BR BRB:BB BRN:BN BTN:BT BWA:BW CAF:CF " +
      "CAN:CA CHE:CH CHL:CL CHN:CN CIV:CI CMR:CM COD:CD COG:CG COK:CK COL:CO COM:KM CPV:CV " +
      "CRI:CR CUB:CU CUW:CW CYM:KY CYP:CY CZE:CZ DEU:DE DJI:DJ DMA:DM DNK:DK DOM:DO DZA:DZ " +
      "ECU:EC EGY:EG ERI:ER ESH:EH ESP:ES EST:EE ETH:ET FIN:FI FJI:FJ FLK:FK FRA:FR FRO:FO " +
      "FSM:FM GAB:GA GBR:GB GEO:GE GGY:GG GHA:GH GIB:GI GIN:GN GLP:GP GMB:GM GNB:GW GNQ:GQ " +
      "GRC:GR GRD:GD GRL:GL GTM:GT GUF:GF GUM:GU GUY:GY HKG:HK HND:HN HRV:HR HTI:HT HUN:HU " +
      "IDN:ID IMN:IM IND:IN IRL:IE IRN:IR IRQ:IQ ISL:IS ISR:IL ITA:IT JAM:JM JEY:JE JOR:JO " +
      "JPN:JP KAZ:KZ KEN:KE KGZ:KG KHM:KH KIR:KI KNA:KN KOR:KR KOS:XK KWT:KW LAO:LA LBN:LB " +
      "LBR:LR LBY:LY LCA:LC LIE:LI LKA:LK LSO:LS LTU:LT LUX:LU LVA:LV MAC:MO MAF:MF MAR:MA " +
      "MCO:MC MDA:MD MDG:MG MDV:MV MEX:MX MHL:MH MKD:MK MLI:ML MLT:MT MMR:MM MNE:ME MNG:MN " +
      "MNP:MP MOZ:MZ MRT:MR MSR:MS MTQ:MQ MUS:MU MWI:MW MYS:MY MYT:YT NAM:NA NCL:NC NER:NE " +
      "NFK:NF NGA:NG NIC:NI NIU:NU NLD:NL NOR:NO NPL:NP NRU:NR NZL:NZ OMN:OM PAK:PK PAN:PA " +
      "PCN:PN PER:PE PHL:PH PLW:PW PNG:PG POL:PL PRI:PR PRK:KP PRT:PT PRY:PY PSE:PS PYF:PF " +
      "QAT:QA REU:RE ROU:RO RUS:RU RWA:RW SAU:SA SDN:SD SEN:SN SGP:SG SHN:SH SLB:SB SLE:SL " +
      "SLV:SV SMR:SM SOM:SO SPM:PM SRB:RS SSD:SS STP:ST SUR:SR SVK:SK SVN:SI SWE:SE SWZ:SZ " +
      "SXM:SX SYC:SC SYR:SY TCA:TC TCD:TD TGO:TG THA:TH TJK:TJ TKM:TM TLS:TL TON:TO TTO:TT " +
      "TUN:TN TUR:TR TUV:TV TWN:TW TZA:TZ UGA:UG UKR:UA URY:UY USA:US UZB:UZ VAT:VA VCT:VC " +
      "VEN:VE VGB:VG VIR:VI VNM:VN VUT:VU WLF:WF WSM:WS XKX:XK YEM:YE ZAF:ZA ZMB:ZM ZWE:ZW"
    )
      .split(" ")
      .map((pair) => pair.split(":")),
  );
  let regions = null;

  // A country's name in the active language: the language's own entry, else the
  // browser's, else `fallback` (the catalog's English name). English keeps the catalog's
  // names exactly ("Democratic Republic of the Congo", not CLDR's "Congo - Kinshasa").
  function countryName(iso3, fallback = iso3) {
    if (active === "en") return fallback;
    const own = table[`country.${iso3}`];
    if (own) return own;
    try {
      regions ??= new Intl.DisplayNames([locale, active], { type: "region", fallback: "none" });
      return regions.of(ISO2[iso3]) ?? fallback;
    } catch (_) {
      return fallback;
    }
  }

  // Remembered, and read on the next load: the page reloads to change language. Going
  // back to the browser's own language forgets the choice, so the browser leads again.
  function choose(l) {
    if (!(l in LANGUAGES)) return;
    const browsers = browserTags().map(primary).find((p) => p in LANGUAGES) ?? "en";
    try {
      if (l === browsers) localStorage.removeItem("lang");
      else localStorage.setItem("lang", l);
    } catch (_) {}
  }

  return {
    LANGUAGES,
    ready,
    t,
    countryName,
    choose,
    applyTo,
    get lang() {
      return active;
    },
    // The language chosen, known at once (before the strings load): the basemap's labels
    // use it, as the tiles carry their own names.
    get wanted() {
      return lang;
    },
    get locale() {
      return active === "en" ? "en-US" : locale;
    },
    // Whether the active language's strings are still a draft no native speaker has checked.
    get draft() {
      return Boolean(table._draft);
    },
  };
})();
