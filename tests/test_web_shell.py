# Copyright (c) 2026 Woody McLendon
# SPDX-License-Identifier: MIT
"""The shell version lives in two files; nothing but a test keeps them in step.

A freshly fetched index.html paired with a stale cached app.js is the failure this
guards: it renders a black map and a dead detail toggle with no error, because the old
script reads a field the new API no longer sends. Versioning the asset URLs makes that
pair impossible -- but only while index.html and sw.js agree on the number.
"""

import re
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "web"
INDEX = (WEB / "index.html").read_text()
SW = (WEB / "sw.js").read_text()


def sw_version() -> str:
    m = re.search(r'SHELL_VERSION\s*=\s*"([^"]+)"', SW)
    assert m, "sw.js must define SHELL_VERSION"
    return m.group(1)


VERSIONED_ASSETS = ("app.js", "static.js", "style.css")


def index_versions() -> dict[str, str]:
    return {
        asset: m.group(1)
        for asset in VERSIONED_ASSETS
        if (m := re.search(rf'{re.escape(asset)}\?v=([^"\']+)', INDEX))
    }


def test_index_versions_every_cacheable_asset():
    assert set(index_versions()) == set(VERSIONED_ASSETS), (
        "index.html must request app.js and style.css with a ?v= so a new page cannot "
        "pair with an old cached script"
    )


def test_index_and_service_worker_agree():
    for asset, version in index_versions().items():
        assert version == sw_version(), (
            f"index.html asks for {asset}?v={version} but sw.js precaches "
            f"v{sw_version()}; bump both together"
        )


def test_service_worker_precaches_the_versioned_urls():
    for asset in ("app.js", "static.js", "style.css"):
        assert f"./{asset}?v=${{SHELL_VERSION}}" in SW, (
            f"sw.js must precache {asset} at the versioned URL the page requests, "
            "or the first offline load misses"
        )


def test_cache_name_tracks_the_version():
    assert "tally-shell-v${SHELL_VERSION}" in SW, "a new shell must land in a new cache"


def test_only_documents_ignore_the_query_string_offline():
    """Matching assets loosely offline would serve the previous app.js and undo all this."""
    assert 'ignoreSearch: req.mode === "navigate"' in SW
    assert "ignoreSearch: true" not in SW


def test_link_preview_tags_match_the_image():
    from PIL import Image

    tags = dict(re.findall(r'<meta property="(og:[a-z:_]+)" content="([^"]*)"', INDEX))
    for key in ("og:title", "og:description", "og:url", "og:image", "og:image:alt"):
        assert tags.get(key), f"index.html needs {key} for link previews"
    image = WEB / tags["og:image"].rsplit("/", 1)[-1]
    assert image.exists(), f"{image.name} is missing; make it with scripts/og_image.py"
    with Image.open(image) as im:
        assert (im.width, im.height) == (int(tags["og:image:width"]), int(tags["og:image:height"]))
        assert im.format.lower() in tags["og:image:type"]
    assert image.stat().st_size < 300_000, "WhatsApp drops large preview images"
    assert 'name="twitter:card" content="summary_large_image"' in INDEX


def test_the_files_browsers_and_crawlers_ask_for_by_name_exist():
    # Browsers, link previews and search engines request these at fixed paths, whatever
    # the page declares; without them every visit logs a 404.
    for name in ("favicon.ico", "apple-touch-icon.png", "robots.txt", "sitemap.xml"):
        assert (WEB / name).exists(), f"web/{name} is missing"
    site = re.search(r'<meta property="og:url" content="([^"]+)"', INDEX).group(1)
    assert f"Sitemap: {site}sitemap.xml" in (WEB / "robots.txt").read_text()
    assert f"<loc>{site}</loc>" in (WEB / "sitemap.xml").read_text()
