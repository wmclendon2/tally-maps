# Copyright (c) 2026 Woody McLendon
# SPDX-License-Identifier: MIT
"""Roll fine H3 cells up to coarser parents (the zoom pyramid): every resolution from
base-1 down to MIN_RESOLUTION, each summing to the same total."""

from __future__ import annotations

import numpy as np
from h3.api import numpy_int as h3

from ingest.config import MIN_RESOLUTION


def to_parents(cells: np.ndarray, resolution: int) -> np.ndarray:
    out = np.empty(cells.size, dtype=np.uint64)
    parent = h3.cell_to_parent
    for i, c in enumerate(cells.tolist()):
        out[i] = parent(c, resolution)
    return out


def rollup_one(
    cells: np.ndarray, pops: np.ndarray, resolution: int
) -> tuple[np.ndarray, np.ndarray]:
    """Sum `pops` into the parents of `cells` at `resolution`."""
    if cells.size == 0:
        return cells.astype(np.uint64), pops.astype(np.float64)
    parents = to_parents(cells, resolution)
    uniq, inverse = np.unique(parents, return_inverse=True)
    return uniq, np.bincount(inverse, weights=pops, minlength=uniq.size)


def rollup(
    cells: np.ndarray, pops: np.ndarray, base_resolution: int, min_resolution: int = MIN_RESOLUTION
) -> dict[int, tuple[np.ndarray, np.ndarray]]:
    """Return {resolution: (cells, pops)} for every level below the base, coarsest last.

    Each level is built from the one above it, so the work shrinks ~7x per step.
    """
    levels: dict[int, tuple[np.ndarray, np.ndarray]] = {}
    for res in range(base_resolution - 1, min_resolution - 1, -1):
        cells, pops = rollup_one(cells, pops, res)
        levels[res] = (cells, pops)
    return levels
