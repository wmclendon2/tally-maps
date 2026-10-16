# Copyright (c) 2026 Woody McLendon
# SPDX-License-Identifier: MIT
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from browser_countries import country_resolution


def entry(counts, max_res=9):
    return {
        "cells_by_resolution": {str(r): n for r, n in counts.items()},
        "max_resolution": max_res,
    }


def test_picks_the_finest_level_within_the_normal_budget():
    e = entry({3: 10, 4: 60, 5: 400, 6: 3000, 7: 20000, 8: 90000, 9: 300000})
    assert country_resolution(e) == 6
    assert country_resolution(e, 1) == 7


def test_fine_never_passes_the_data_or_the_cell_limit():
    small = entry({3: 1, 4: 2, 5: 5, 6: 20, 7: 90, 8: 400, 9: 1500})
    assert country_resolution(small) == country_resolution(small, 1) == 9
    steep = entry({3: 300, 4: 3900, 5: 45000, 6: 300000, 7: 2e6, 8: 9e6, 9: 3e7})
    assert country_resolution(steep, 1) == 4  # res 5 is over MAX_CELLS_LIMIT


def test_a_huge_country_still_gets_its_coarsest_level():
    assert country_resolution(entry({3: 5000, 4: 30000})) == 3
