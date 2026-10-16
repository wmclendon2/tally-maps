# Copyright (c) 2026 Woody McLendon
# SPDX-License-Identifier: MIT
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))


@pytest.fixture(autouse=True)
def no_outline_downloads(monkeypatch):
    """Catalogue writes look up each country's area from its outline, which can mean a
    download; tests build made-up countries, so they get a fixed area instead."""
    import build_packs

    monkeypatch.setattr(build_packs, "country_area", lambda iso3: 1000.0)
