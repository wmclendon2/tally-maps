# Copyright (c) 2026 Woody McLendon
# SPDX-License-Identifier: MIT
import io
import urllib.error

import pytest

from ingest import fetch


class _Resp(io.BytesIO):
    """Minimal stand-in for the object urlopen returns as a context manager."""

    def __init__(self, body: bytes):
        super().__init__(body)
        self.headers = {"Content-Length": str(len(body))}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("http://x", code, "Service Unavailable", {}, None)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(fetch.time, "sleep", lambda _s: None)


def test_transient_503_is_retried_then_succeeds(monkeypatch):
    """A 503 from GitHub's LFS host used to abort a whole country's ingest."""
    calls = []

    def fake_urlopen(req, timeout=None):
        calls.append(req)
        if len(calls) < 3:
            raise _http_error(503)
        return _Resp(b'{"ok": true}')

    monkeypatch.setattr(fetch.urllib.request, "urlopen", fake_urlopen)
    assert fetch._get_urllib("http://x", 5, 6) == b'{"ok": true}'
    assert len(calls) == 3


def test_permanent_404_is_not_retried(monkeypatch):
    calls = []

    def fake_urlopen(req, timeout=None):
        calls.append(req)
        raise _http_error(404)

    monkeypatch.setattr(fetch.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(urllib.error.HTTPError):
        fetch._get_urllib("http://x", 5, 6)
    assert len(calls) == 1


def test_gives_up_after_attempts(monkeypatch):
    monkeypatch.setattr(
        fetch.urllib.request,
        "urlopen",
        lambda req, timeout=None: (_ for _ in ()).throw(_http_error(503)),
    )
    with pytest.raises(RuntimeError, match="after 3 attempts"):
        fetch._get_urllib("http://x", 5, 3)


def test_short_body_is_retried(monkeypatch):
    """The filtering proxy truncates HTTP/1.1 bodies without signalling an error."""
    bodies = [b"short", b"full body!"]

    def fake_urlopen(req, timeout=None):
        body = bodies.pop(0)
        resp = _Resp(body)
        resp.headers = {"Content-Length": "10"}
        return resp

    monkeypatch.setattr(fetch.urllib.request, "urlopen", fake_urlopen)
    assert fetch._get_urllib("http://x", 5, 6) == b"full body!"
