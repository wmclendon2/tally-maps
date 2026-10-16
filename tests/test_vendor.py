# Copyright (c) 2026 Woody McLendon
# SPDX-License-Identifier: MIT
"""Vendored JavaScript libraries: present, unmodified, and the only ones the page loads.

The page used to load its libraries from unpkg. scripts/vendor_libs.py now copies them
from their npm releases into web/vendor/ and records each file's sha256 in MANIFEST.json.
"""

import hashlib
import json
import re
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "web"
VENDOR = WEB / "vendor"
MANIFEST = json.loads((VENDOR / "MANIFEST.json").read_text())
INDEX = (WEB / "index.html").read_text()
SW = (WEB / "sw.js").read_text()


def test_vendored_files_match_the_manifest():
    for pkg, record in MANIFEST.items():
        for name, digest in record["files"].items():
            path = VENDOR / pkg / name
            assert path.exists(), f"{path} is missing; run scripts/vendor_libs.py"
            assert hashlib.sha256(path.read_bytes()).hexdigest() == digest, (
                f"{path} differs from the npm release; restore it, or rerun vendor_libs.py"
            )


def test_every_vendored_package_ships_a_licence():
    for pkg, record in MANIFEST.items():
        assert any(n.upper().startswith("LICENSE") for n in record["files"]), pkg


def page_assets() -> list[str]:
    return re.findall(r'<(?:script|link)[^>]+(?:src|href)="([^"]+)"', INDEX)


def test_page_loads_no_third_party_scripts_or_styles():
    external = [u for u in page_assets() if re.match(r"(https?:)?//", u)]
    assert not external, f"index.html loads from another host: {external}"


def test_every_vendored_asset_the_page_loads_exists_and_is_precached():
    vendored = [u for u in page_assets() if u.startswith("vendor/")]
    assert len(vendored) == 5
    for url in vendored:
        assert (WEB / url).exists(), url
        assert f'"./{url}"' in SW, f"sw.js must precache {url} for offline use"


def test_service_worker_no_longer_caches_unpkg():
    assert "unpkg" not in SW


def test_every_vendored_font_the_stylesheet_loads_exists_and_is_precached():
    css = (WEB / "style.css").read_text()
    fonts = re.findall(r'url\("(vendor/[^"]+)"\)', css)
    assert len(fonts) == 4
    for url in fonts:
        assert (WEB / url).exists(), url
        assert f'"./{url}"' in SW, f"sw.js must precache {url} for offline use"
