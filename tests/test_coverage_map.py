# Copyright (c) 2026 Woody McLendon
# SPDX-License-Identifier: MIT
"""Projection and geometry handling for the coverage map."""

import pytest

from scripts.coverage_map import LAT_MAX, LAT_MIN, project

W, H = 1000, 500


def test_projection_corners():
    assert project(-180, LAT_MAX, W, H) == pytest.approx((0, 0))
    assert project(180, LAT_MIN, W, H) == pytest.approx((W, H))


def test_greenwich_and_equator_land_where_expected():
    x, _ = project(0, 0, W, H)
    assert x == pytest.approx(W / 2)
    _, y = project(0, (LAT_MAX + LAT_MIN) / 2, W, H)
    assert y == pytest.approx(H / 2)


def test_longitude_is_linear_and_latitude_is_inverted():
    assert project(90, 0, W, H)[0] > project(-90, 0, W, H)[0]
    assert project(0, 60, W, H)[1] < project(0, -30, W, H)[1], "north is up"


def test_points_outside_the_bounds_fall_outside_the_canvas():
    """Antarctica projects below the sheet; the renderer clips it by pasting."""
    _, y = project(0, -85, W, H)
    assert y > H
