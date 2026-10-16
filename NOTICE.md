# Notices and attribution

The **code** in this repository is released under the MIT License (see `LICENSE`).
That licence covers the code only.

The MIT License does **not** cover the data the code downloads, processes or serves.
That includes population rasters, boundaries, basemap tiles, and the derived H3 cells
and country packs. Each dataset keeps its own licence, and most of them require
attribution. If you run your own copy of Tally or redistribute its data packs, you must
credit the sources below.

## Data

| Source | Used for | Licence | Credit line |
| --- | --- | --- | --- |
| Meta High Resolution Settlement Layer (HRSL), via the Humanitarian Data Exchange | Population, 30 m | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) | Data for Good at Meta and CIESIN, Columbia University — High Resolution Population Density Maps |
| [WorldPop](https://www.worldpop.org) | Population, 100 m (where Meta has no data) | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) | WorldPop (www.worldpop.org), University of Southampton |
| [geoBoundaries](https://www.geoboundaries.org) | Country outlines for clipping and display | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) | Runfola et al. (2020), geoBoundaries, PLoS ONE 15(4): e0231866 |
| [Natural Earth](https://www.naturalearthdata.com) | City points, continents, the no-data mask, outlines for five territories (HKG, MAC, JEY, VIR, PCN), the coverage map | Public domain | Made with Natural Earth |
| [OpenStreetMap](https://www.openstreetmap.org/copyright), served by [OpenFreeMap](https://openfreemap.org) | Basemap | [ODbL 1.0](https://opendatacommons.org/licenses/odbl/) | © OpenStreetMap contributors, OpenFreeMap |

**Derived data:**
- The country packs and H3 cells are adaptations of the population data. They are
  shared under CC BY 4.0 and carry the same attribution.
- Tally changes the source data in three ways: it bins the rasters into H3 hexagons,
  sums them, and clips them to geoBoundaries outlines.
- The figures are modelled estimates, not census counts.

## Software used by the web app

These libraries and fonts are vendored into the browser app (`web/vendor/`, each with its
licence file):

| Library | Licence |
| --- | --- |
| [MapLibre GL JS](https://github.com/maplibre/maplibre-gl-js) | BSD-3-Clause |
| [h3-js](https://github.com/uber/h3-js) | Apache-2.0 |
| [Terra Draw](https://github.com/JamesLMilner/terra-draw) and its MapLibre adapter | MIT |
| [Barlow and Barlow Semi Condensed](https://github.com/jpt/barlow) fonts, packaged by [Fontsource](https://fontsource.org) | SIL Open Font License 1.1 |

The build and ingest tools use further open-source Python packages, listed in
`pyproject.toml` / `uv.lock`, each under its own licence. They are not redistributed.

## No warranty

Population figures are estimates derived from third-party models and can be wrong,
especially near coasts, borders and in areas of recent change. Tally is provided as is,
without warranty of any kind. Do not rely on it alone for safety-critical or legal
decisions.

## Name

"Tally" is the name of this personal, non-commercial project. It is not affiliated with
Tally Solutions (TallyPrime), Tally (tally.so), or any other product of the same name.
