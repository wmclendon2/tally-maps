# Copyright (c) 2026 Woody McLendon
# SPDX-License-Identifier: MIT
import h3
import numpy as np

from ingest.pyramid import rollup


def test_rollup_conserves_mass_and_nests():
    base = [h3.str_to_int(h3.latlng_to_cell(3.8 + i * 0.001, 11.5, 8)) for i in range(50)]
    cells = np.array(base, dtype=np.uint64)
    pops = np.arange(1, 51, dtype=np.float64)
    levels = rollup(cells, pops, 8, 3)
    assert list(levels) == [7, 6, 5, 4, 3]
    for res, (c, p) in levels.items():
        assert p.sum() == pops.sum()
        assert all(h3.get_resolution(h3.int_to_str(int(x))) == res for x in c)
        assert c.size <= cells.size
    # every base cell's res-5 parent is present at level 5
    parents5 = {h3.str_to_int(h3.cell_to_parent(h3.int_to_str(int(x)), 5)) for x in base}
    assert parents5 == set(levels[5][0].tolist())


def test_rollup_empty():
    levels = rollup(np.empty(0, np.uint64), np.empty(0, np.float64), 8, 6)
    assert list(levels) == [7, 6] and all(c.size == 0 for c, _ in levels.values())
