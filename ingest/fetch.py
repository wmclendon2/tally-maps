# Copyright (c) 2026 Woody McLendon
# SPDX-License-Identifier: MIT
"""HTTP fetching for every download: curl first, urllib as a fallback.

curl is preferred because some filtering proxies truncate HTTP/1.1 bodies from
urllib a few KB short without signalling an error (CLAUDE.md); curl speaks HTTP/2,
follows redirects, and resumes partial files.

Everything here retries. geoBoundaries' GeoJSON lives on GitHub's Git LFS media
host, which answers a transient 503 ("Service Unavailable") under load; a single
one used to abort a whole country's ingest.
"""

from __future__ import annotations

import http.client
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from rich.console import Console

USER_AGENT = "tally-maps/0.1 (+https://github.com/wmclendon2/tally-maps)"
CURL = shutil.which("curl")

console = Console()

# Status codes worth another try: overload, rate limiting, flaky gateways.
TRANSIENT_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})


def curl_to_file(url: str, dest: Path, *, resume: bool = False, timeout: int = 600) -> None:
    """Fetch a URL to a file with curl, retrying transient failures."""
    cmd = [
        CURL,
        "--silent",
        "--show-error",
        "--location",
        "--fail",
        "--retry",
        "5",
        "--retry-all-errors",
        "--retry-delay",
        "2",
        "--max-time",
        str(timeout),
        "--user-agent",
        USER_AGENT,
        "--output",
        str(dest),
    ]
    if resume:
        cmd += ["--continue-at", "-"]
    subprocess.run(cmd + [url], check=True, capture_output=True, text=True)


def get_bytes(url: str, timeout: int = 60, attempts: int = 6) -> bytes:
    """GET a small response as bytes, via curl when available."""
    if CURL:
        return _get_curl(url, timeout, attempts)
    return _get_urllib(url, timeout, attempts)


def _get_curl(url: str, timeout: int, attempts: int) -> bytes:
    """curl already retries internally; wrap it so a hard failure still gets another go."""
    with tempfile.NamedTemporaryFile(delete=False, suffix=".http") as tmp:
        pass
    tmp_path = Path(tmp.name)
    try:
        for attempt in range(1, attempts + 1):
            try:
                curl_to_file(url, tmp_path, timeout=timeout)
                return tmp_path.read_bytes()
            except subprocess.CalledProcessError as exc:
                reason = (exc.stderr or "").strip() or f"curl exit {exc.returncode}"
                if attempt == attempts:
                    raise RuntimeError(f"GET {url}: {reason} after {attempts} attempts") from exc
                console.print(f"[yellow]retry {attempt} after {reason}[/yellow]")
                time.sleep(2 * attempt)
    finally:
        tmp_path.unlink(missing_ok=True)
    raise AssertionError("unreachable")


def _get_urllib(url: str, timeout: int, attempts: int) -> bytes:
    """urllib fallback with retries and a Content-Length check."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                expected = int(resp.headers.get("Content-Length") or 0)
                buf = bytearray()
                while chunk := resp.read(1 << 16):
                    buf += chunk
            if not expected or len(buf) == expected:
                return bytes(buf)
            reason = f"short body {len(buf):,}/{expected:,}"
        except urllib.error.HTTPError as exc:
            reason = f"HTTP {exc.code} {exc.reason}"
            if exc.code not in TRANSIENT_STATUS:
                raise
        except (OSError, http.client.HTTPException) as exc:
            reason = type(exc).__name__
        if attempt == attempts:
            raise RuntimeError(f"GET {url}: {reason} after {attempts} attempts")
        console.print(f"[yellow]retry {attempt} after {reason}[/yellow]")
        time.sleep(2 * attempt)
    raise AssertionError("unreachable")
