# Population data sources

A briefing on the gridded population datasets Tally could use: how Meta's and WorldPop's
maps are made, where they should agree and differ, and what else exists. Written
2026-10-19 from public documentation. Apart from the facts about Tally's own packs, nothing
here has been measured by Tally yet; the measured comparison is F-32 in
[ROADMAP.md](ROADMAP.md).

## In short

- **Meta's maps are frozen.** 192 of Meta's 205 population datasets on HDX now carry the
  caveat *"as of 2024, Meta's high resolution population density maps are no longer being
  updated."* The one exception since is India, whose 2025 file was published in June 2026.
  Tally's 195 Meta packs are dated 2018 (40 countries), 2019 (19), 2020 (135) and 2025
  (India).
- **WorldPop is maintained.** Its Global2 release (R2025A, September 2025) has annual maps
  for 2015 to 2030 for 242 countries and territories, under the same licence (CC BY 4.0).
  It is labelled an *alpha* release that "may change over the coming year".
- **They ask the same question differently.** Meta finds buildings in 0.5 m satellite
  imagery and spreads census counts over the settled 30 m pixels. WorldPop predicts a
  density surface at 100 m from a library of covariates (buildings, night lights, roads,
  land cover, terrain…) and spreads census counts in proportion to it, only in cells
  mapped as built.
- **Both are scaled to UN national totals**, but to different editions and years, so the
  national totals will differ even where the maps agree on where people live.
- **No other open dataset is a like-for-like alternative.** The EU's GHS-POP is global at
  100 m but weaker in rural areas; LandScan is 1 km and measures a different thing;
  bottom-up estimates (GRID3) are better but exist for only a few countries. Details at the
  end.

## Side by side

| | Meta (HRSL) | WorldPop Global2 |
| --- | --- | --- |
| Made by | Meta's Data for Good with CIESIN (Columbia University) | WorldPop, University of Southampton |
| Method | Machine-learning building detection on satellite imagery, then census counts allocated to the settled pixels | Random-forest density model (`popRF`) used as weights to split census counts ("top-down dasymetric"), only in built cells ("constrained") |
| Where buildings come from | Meta's own detection on commercial Maxar (DigitalGlobe) imagery; the citation says © 2016 imagery | GHSL built surface and volume (EU), Google Open Buildings v3, Microsoft footprints, World Settlement Footprint; modelled year by year |
| Resolution | 1 arc-second, about 30 m | 3 arc-seconds, about 100 m |
| Years | One snapshot per country: 2018, 2019 or 2020 (India 2025) | Every year 2015–2030 (Tally uses 2020) |
| Countries | 196 files in Tally's list (Jersey's has no data over the island); Meta says "160+" | 242; 9 uninhabited territories and the Vatican not modelled |
| Census input | Census and other official statistics, one per country | Two census rounds per country (around 2010 and 2020) at the finest admin level available; US Census Bureau projections or UN CODs where the census is old or there is conflict |
| National totals | Scaled to the UN estimate for the map's year by one national factor | Aligned to the UN World Population Prospects 2024, as of 1 January |
| Age and sex | 6 layers besides the total: women, men, under-5s, youth 15–24, 60+, women 15–49 | Every 5-year age group by sex (0–1, 1–4, 5–9 … 90+), every year |
| File extent | The country's bounding box, neighbours included (CMR's holds 79.6 M people outside Cameroon) | The country only, split by the US State Department's LSIB boundaries |
| Licence | CC BY 4.0 | CC BY 4.0 |
| Status | No longer updated (2024) | Alpha; documented changes promised as they come |
| In Tally | 195 countries | Canada, Greenland, Jersey |

## Where they should differ

These are expectations from the methods, to be checked by F-32.

- **Rural homes.** Meta's detection works building by building on 0.5 m imagery, which
  was its whole point: finding scattered rural settlements that coarser maps miss.
  WorldPop's built mask comes from GHSL plus Google's and Microsoft's footprints, so a
  hamlet missing from all of them gets no one. A 2025 study (Nature Communications) found
  that all five global datasets it tested undercount rural populations, WorldPop the least
  (−53%) and GHS-POP the most (−84%). Meta's maps were not in that study.
- **Inside cities.** Meta's allocation is close to proportional to settled area within each
  admin unit, so a dense centre and a suburb of the same unit get similar densities.
  WorldPop's model weighs building volume, night lights and roads, so it should show more
  variation inside cities. WorldPop warns, though, that where only coarse admin units have
  two census rounds, its maps can "look less spatially detailed" and miss "high urban
  population densities".
- **The year.** Comparing Meta's 2018 map with WorldPop's 2020 one measures growth as well
  as method; fast-growing countries add several percent a year. The comparison should use
  WorldPop's map for the same year as each Meta file.
- **The national total.** Different UN editions and target dates (Meta's year vs WorldPop's
  1 January, WPP 2024) shift the whole country up or down. Comparing shares (each place's
  share of the country) separates "where" from "how many".
- **Borders.** Tally clips both to geoBoundaries. WorldPop's files are already cut at LSIB,
  which reflects US policy on disputed borders; where LSIB and geoBoundaries disagree,
  people can fall outside every pack, or (with Meta's bounding boxes) inside two. This is
  what the overlap audit (F-30) is for.
- **Resolution at Tally's scale.** Tally's finest hexagon (H3 resolution 9, about 0.1 km²)
  holds about 117 Meta pixels or 11 WorldPop pixels, so at the finest level the 30 m vs
  100 m difference shows mostly at edges: coasts, borders, the edge of a town.

## Moving Tally to WorldPop

**For it:**
- It is maintained, and the year is a choice: Tally could show 2025 or 2026 rather than a
  mix of 2018–2020 snapshots, and move forward with each release.
- One method everywhere: no Meta–WorldPop seam at borders (Detroit–Windsor today).
- It covers the countries Meta lacks (China, Pakistan, Russia, Iran, Myanmar…), so F-10
  would no longer be a separate job.
- Files are already cut to the country, so there is no bounding-box surplus to clip away,
  and the downloads are smaller.

**Against it, or to watch:**
- It is an alpha release: numbers may change within the year, every pack would be rebuilt
  and renamed at each release, and saved packs would go stale (F-13 and F-29 become
  necessary, not nice to have).
- 100 m instead of 30 m, and possibly less detail where a country's census is coarse.
- Years after a country's last census are projections, not observations; the further from
  the census, the less certain (WorldPop says so).
- One academic team's funding; Meta's maps, though frozen, stay downloadable.
- LSIB boundaries are a US government view of disputed borders.

**What a switch would take:** `build_packs.SOURCE_FOR` already picks a source per country,
so it is a change of default, a rebuild of every pack (about half an hour on this machine),
the verification and parity checks, and a new no-data mask. About and the attribution line
would name WorldPop and the year.

**Questions for the comparison (F-32) to answer:**
1. How far apart are the national totals, and how much of that is the UN edition and year?
2. With totals equalised, how much of each country's population sits in the same places, at
   the finest, district and regional levels?
3. Who finds people where the other has none, and is that rural homes, new suburbs, or
   noise?
4. Is the difference systematic (always WorldPop lower in villages, higher in city centres)
   or country by country?
5. For a typical Tally question (a town, a district, a 10 km circle), how different is the
   answer?

## Other datasets

| Dataset | Made by | Resolution | Years | Licence | For Tally |
| --- | --- | --- | --- | --- | --- |
| GHS-POP R2023A | EU Joint Research Centre | 100 m (Mollweide projection) and 1 km | Every 5 years 1975–2020; 2025 and 2030 projected | CC BY 4.0 | A useful third opinion in the comparison, not a replacement: it spreads GPW's census counts over GHSL built-up areas and had the largest rural undercount in the 2025 study |
| LandScan Global | Oak Ridge National Laboratory | 1 km | Yearly, 2000 on | CC BY 4.0 since 2022 | No: it maps *ambient* population (where people are over 24 hours, workplaces included), not where they live, and 1 km is too coarse for small shapes. A rough national cross-check at most |
| GPW v4.11 | CIESIN (NASA SEDAC) | 1 km | 2000–2020 | CC BY 4.0 | No: census counts spread evenly over each admin unit. It is the census input behind GHS-POP and Meta's maps |
| Bottom-up estimates (GRID3, WorldPop) | WorldPop and GRID3 with national governments | About 100 m | Country by country (e.g. Nigeria around 2019) | Mostly CC BY 4.0, per release | Maybe later, per country: built from household survey counts rather than old censuses (Nigeria's last census was 2006), with uncertainty ranges. Only a handful of countries, mostly in Africa |
| Kontur Population | Kontur | H3 hexagons, "400 m" (resolution 8) | 2023 | CC BY 4.0 | No: already on H3, but coarser than Tally's base and built *from* GHS-POP and Meta's maps, so not independent |
| Building footprints (Microsoft, Google Open Buildings, Overture) | — | Buildings | Various | Open | Not population: inputs that WorldPop already uses |

So, for a global, open, building-scale map of where people live, Meta and WorldPop are the
two real choices, and only WorldPop is still being made. GHS-POP is worth adding to the
comparison as an independent third view; bottom-up estimates are worth revisiting for a few
countries whose censuses are old.

## Sources

- HDX, Meta's country datasets and their caveats, e.g.
  [Togo](https://data.humdata.org/dataset/highresolutionpopulationdensitymaps-tgo),
  [India](https://data.humdata.org/dataset/india-high-resolution-population-density-maps)
  (metadata read through the HDX API on 2026-10-19).
- Meta, [High Resolution Population Density Maps](https://ai.meta.com/ai-for-good/datasets/high-resolution-population-density-maps/)
  and its [methodology page](https://ai.meta.com/ai-for-good/docs/methodology-high-resolution-population-density-maps/).
- Tiecke et al., [Mapping the world population one building at a time](https://arxiv.org/abs/1712.05839), 2017.
- Yetman and Gill, [Detailed settlement and population mapping: the High Resolution Settlement Layer](https://ui.adsabs.harvard.edu/abs/2018AGUFMIN44A..09Y/abstract), AGU 2018.
- WorldPop, [Global2 release statement R2025A v1](https://data.worldpop.org/repo/prj/Global_2015_2030/R2025A/doc/Global2_Release_Statement_R2025A_v1.pdf), September 2025,
  and [the Global2 announcement](https://www.worldpop.org/blog/worldpop-global2-global-high-resolution-population-estimates-for-2015-2030/).
- Láng-Ritter et al., [Global gridded population datasets systematically underrepresent rural population](https://www.nature.com/articles/s41467-025-56906-7), Nature Communications, 2025.
- JRC, [GHS-POP R2023A](https://data.jrc.ec.europa.eu/dataset/2ff68a52-5b5b-4a22-8f40-c41da8332cfe).
- ORNL, [LandScan public release](https://www.ornl.gov/news/public-release-ornl-global-population-distribution-data-aids-humanitarian-support).
- GRID3, [high-resolution population estimates](https://grid3.org/solution/high-resolution-population-estimates).
- Kontur, [Kontur Population on HDX](https://data.humdata.org/dataset/kontur-population-dataset).
