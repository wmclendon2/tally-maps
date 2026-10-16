# Copyright (c) 2026 Woody McLendon
# SPDX-License-Identifier: MIT
"""The per-country process pool build_packs runs countries in."""

import operator
import zipfile

import pytest

from ingest import parallel


def collect(jobs, workers):
    seen = []
    parallel.run(jobs, workers, lambda o, done, total: seen.append((o, done, total)))
    return seen


def test_one_worker_runs_largest_first_in_process():
    jobs = [
        ("small", 1, operator.add, (1, 1)),
        ("big", 9, operator.add, (2, 2)),
        ("mid", 5, operator.add, (3, 3)),
    ]
    seen = collect(jobs, 1)
    assert [o.key for o, *_ in seen] == ["big", "mid", "small"]
    assert [o.result for o, *_ in seen] == [4, 6, 2]
    assert [(d, t) for _, d, t in seen] == [(1, 3), (2, 3), (3, 3)]


@pytest.mark.parametrize("workers", [1, 3])
def test_a_failing_job_is_reported_and_the_rest_still_run(workers):
    jobs = [(f"ok{i}", i, operator.mul, (i, 10)) for i in range(5)]
    jobs.append(("bad", 3, operator.truediv, (1, 0)))
    seen = collect(jobs, workers)
    by_key = {o.key: o for o, *_ in seen}
    assert set(by_key) == {"ok0", "ok1", "ok2", "ok3", "ok4", "bad"}
    assert isinstance(by_key["bad"].error, ZeroDivisionError)
    assert {k: o.result for k, o in by_key.items() if k != "bad"} == {
        f"ok{i}": i * 10 for i in range(5)
    }
    assert sorted(d for _, d, _ in seen) == list(range(1, 7))


def test_worker_processes_return_results():
    jobs = [(str(i), i, pow, (i, 2)) for i in range(8)]
    seen = collect(jobs, 4)
    assert {o.key: o.result for o, *_ in seen} == {str(i): i * i for i in range(8)}


def test_defaults_are_sane():
    import os

    assert 1 <= parallel.default_workers() <= (os.cpu_count() or 1)


def test_raw_bytes_counts_uncompressed_tiffs_only(tmp_path):
    z = tmp_path / "x.zip"
    with zipfile.ZipFile(z, "w", compression=zipfile.ZIP_DEFLATED) as f:
        f.writestr("a.tif", b"\0" * 10_000)
        f.writestr("readme.txt", b"x" * 500)
    plain = tmp_path / "b.tif"
    plain.write_bytes(b"1" * 300)
    assert parallel.raw_bytes([z, plain, tmp_path / "missing.tif"]) == 10_300


def test_raw_bytes_skips_macos_appledouble_stubs(tmp_path):
    from ingest.pipeline import expand_rasters

    z = tmp_path / "tile.tif.zip"
    with zipfile.ZipFile(z, "w") as f:
        f.writestr("population_usa18_-100.tif", b"x" * 10)
        f.writestr("__MACOSX/._population_usa18_-100.tif", b"y" * 3)
        f.writestr("notes/._stray.tif", b"y" * 5)
    assert parallel.raw_bytes([z]) == 10
    assert expand_rasters([z]) == [f"zip://{z}!population_usa18_-100.tif"]
