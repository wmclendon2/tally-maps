# Copyright (c) 2026 Woody McLendon
# SPDX-License-Identifier: MIT
"""Continent lookup for ISO3 country codes, from a checked-in Natural Earth extract.

Kept as a local fixture (reference/continents.json) rather than a lookup so that
`--continent africa` works with no network and no extra dependency.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

REFERENCE = Path(__file__).resolve().parent.parent / "reference" / "continents.json"


def slug(continent: str) -> str:
    """'North America' -> 'north-america'. What the CLI accepts."""
    return continent.strip().lower().replace(" ", "-").replace("_", "-")


@lru_cache(maxsize=1)
def _countries() -> dict[str, dict[str, str]]:
    return json.loads(REFERENCE.read_text())["countries"]


@lru_cache(maxsize=1)
def by_continent() -> dict[str, list[str]]:
    """{'africa': ['AGO', 'BDI', ...]} -- every country, whether or not it has data."""
    out: dict[str, list[str]] = {}
    for iso3, info in _countries().items():
        out.setdefault(slug(info["continent"]), []).append(iso3)
    return {k: sorted(v) for k, v in sorted(out.items())}


def by_continent_members() -> dict[str, dict[str, str]]:
    """Every country in the reference, keyed by ISO3. The universe the map draws from."""
    return dict(_countries())


def names() -> list[str]:
    """Continent slugs a user may pass, for help text and error messages."""
    return list(by_continent())


def countries_in(continent: str) -> list[str]:
    """ISO3 codes on a continent. Raises KeyError with the valid options if unknown."""
    table = by_continent()
    key = slug(continent)
    if key not in table:
        raise KeyError(f"unknown continent {continent!r}; choose from {', '.join(table)}")
    return table[key]


def continent_of(iso3: str) -> str | None:
    info = _countries().get(iso3.upper())
    return slug(info["continent"]) if info else None


def continent_name(iso3: str) -> str | None:
    """The continent as written for people ("North America"), not as a CLI slug."""
    info = _countries().get(iso3.upper())
    return info["continent"] if info else None


# Natural Earth's TYPE values that mean "a country", as opposed to a dependency,
# a disputed area or an unresolved boundary like the Siachen Glacier.
COUNTRY_TYPES = frozenset({"Sovereign country", "Country", "Sovereignty"})


def name_of(iso3: str) -> str:
    info = _countries().get(iso3.upper())
    return info["name"] if info else iso3.upper()


def split_by_kind(iso3s: set[str]) -> tuple[list[str], list[str]]:
    """Sort ISO3 codes into (country names, territory names), each alphabetical.

    Territories are kept apart so a list of "countries with no data" is not padded
    out with uninhabited dependencies and boundary disputes.
    """
    table = _countries()
    countries, territories = [], []
    for iso3 in iso3s:
        info = table.get(iso3.upper())
        if info is None:
            continue
        (countries if info["type"] in COUNTRY_TYPES else territories).append(info["name"])
    return sorted(countries, key=str.casefold), sorted(territories, key=str.casefold)
