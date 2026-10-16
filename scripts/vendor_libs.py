# Copyright (c) 2026 Woody McLendon
# SPDX-License-Identifier: MIT
"""Copy the web app's JavaScript libraries into web/vendor/ from their npm releases.

    uv run python scripts/vendor_libs.py

The page used to load these from unpkg, a third party that could break, change or
track the site. Each library now ships with the shell instead.

For every pinned package this downloads the release tarball named in the npm registry,
checks it against the registry's published sha512 `integrity`, and copies the few files
the page loads, plus the licence, into `web/vendor/<name>@<version>/`. The files are
byte-identical to the npm release (and to what unpkg served). `web/vendor/MANIFEST.json`
records each file's sha256; tests/test_vendor.py checks the tree against it.

To upgrade a library: change its version in PACKAGES, rerun this, then update the paths
in web/index.html and web/sw.js and bump the shell version (tests fail until you do).
This script never deletes anything. A version already vendored is checked against the
release instead of rewritten, and a folder no longer pinned is reported for you to
remove with `git rm -r`.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import sys
import tarfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ingest.fetch import get_bytes

VENDOR = Path(__file__).resolve().parent.parent / "web" / "vendor"
REGISTRY = "https://registry.npmjs.org/{name}/{version}"

# name -> (version, files the page loads, licence: a path in the tarball or a URL).
# Terra Draw's tarballs carry no LICENSE file (package.json says MIT), so it comes from
# the project's repository.
TERRA_DRAW_LICENCE = "https://raw.githubusercontent.com/JamesLMilner/terra-draw/main/LICENSE"
PACKAGES: dict[str, tuple[str, list[str], str]] = {
    "maplibre-gl": ("5.24.0", ["dist/maplibre-gl.js", "dist/maplibre-gl.css"], "LICENSE.txt"),
    "h3-js": ("4.5.0", ["dist/h3-js.umd.js"], "LICENSE"),
    "terra-draw": ("1.34.0", ["dist/terra-draw.umd.js"], TERRA_DRAW_LICENCE),
    "terra-draw-maplibre-gl-adapter": (
        "1.4.1",
        ["dist/terra-draw-maplibre-gl-adapter.umd.js"],
        TERRA_DRAW_LICENCE,
    ),
    # The panel's typefaces (SIL OFL 1.1), Latin subset only; style.css declares them.
    "@fontsource/barlow": (
        "5.3.0",
        [f"files/barlow-latin-{w}-normal.woff2" for w in (400, 500, 600)],
        "LICENSE",
    ),
    "@fontsource/barlow-semi-condensed": (
        "5.3.0",
        ["files/barlow-semi-condensed-latin-600-normal.woff2"],
        "LICENSE",
    ),
}


def sri(data: bytes) -> str:
    return "sha512-" + base64.b64encode(hashlib.sha512(data).digest()).decode()


def vendor_one(name: str, version: str, files: list[str], licence: str) -> dict:
    meta = json.loads(get_bytes(REGISTRY.format(name=name, version=version)))
    tgz = get_bytes(meta["dist"]["tarball"])
    if sri(tgz) != meta["dist"]["integrity"]:
        raise SystemExit(f"{name}@{version}: tarball does not match the registry's integrity")
    out = VENDOR / f"{name}@{version}"
    exists = out.exists()
    out.mkdir(parents=True, exist_ok=True)
    record = {"version": version, "tarball": meta["dist"]["tarball"], "files": {}}
    with tarfile.open(fileobj=io.BytesIO(tgz)) as tar:
        wanted = files + ([] if licence.startswith("https://") else [licence])
        for rel in wanted:
            data = tar.extractfile(f"package/{rel}").read()
            record["files"][Path(rel).name] = place(out / Path(rel).name, data, exists)
    if licence.startswith("https://"):
        record["files"]["LICENSE"] = place(out / "LICENSE", get_bytes(licence), exists)
        record["licence_from"] = licence
    return record


def place(dest: Path, data: bytes, check_only: bool) -> str:
    """Write a vendored file, or for a version already vendored, check it is unchanged."""
    digest = hashlib.sha256(data).hexdigest()
    if check_only and dest.exists():
        if hashlib.sha256(dest.read_bytes()).hexdigest() != digest:
            raise SystemExit(f"{dest} differs from the release; restore it with git checkout")
    else:
        dest.write_bytes(data)
    return digest


def main() -> None:
    VENDOR.mkdir(parents=True, exist_ok=True)
    manifest = {}
    for name, (version, files, licence) in PACKAGES.items():
        manifest[f"{name}@{version}"] = vendor_one(name, version, files, licence)
        print(f"{name}@{version}: integrity OK, {len(files)} file(s)")
    (VENDOR / "MANIFEST.json").write_text(json.dumps(manifest, indent=2) + "\n")
    # Scoped packages (@fontsource/barlow) sit one level down, in their scope's folder.
    dirs = []
    for d in VENDOR.iterdir():
        if d.is_dir():
            dirs += [c for c in d.iterdir() if c.is_dir()] if d.name.startswith("@") else [d]
    for d in sorted(d for d in dirs if d.relative_to(VENDOR).as_posix() not in manifest):
        print(f"no longer pinned: {d.relative_to(VENDOR.parent.parent)} (git rm -r it)")


if __name__ == "__main__":
    main()
