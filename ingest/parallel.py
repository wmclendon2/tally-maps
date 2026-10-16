# Copyright (c) 2026 Woody McLendon
# SPDX-License-Identifier: MIT
"""Run one job per country across processes. Used by build_packs.

A country is the unit of work: its rasters are read straight out of a zip, which only
decompresses forwards, so one file cannot usefully be split between processes. Each job
uses one CPU (GDAL inflate, then a Python loop over h3), so processes, not threads.

Jobs start largest first (longest-processing-time scheduling): the run ends when the last
job does, so a big country picked up last would leave the other workers idle. Cost is the
rasters' uncompressed size, which with strips read in file order (ingest/raster.py) tracks
compute time at ~2.7 s/GB on an i7-7700K.

The parent does all the reporting and any shared writes (build_packs' catalogue), so
workers never race on a file. Results reach the parent in completion order.
"""

from __future__ import annotations

import multiprocessing as mp
import os
import zipfile
from collections.abc import Callable, Iterable
from concurrent.futures import FIRST_COMPLETED, Future, ProcessPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ingest.pipeline import is_geotiff_member

# Worker memory: ~1 GB on mid-size African countries once GDAL's cache is capped (below);
# 3.4 GB on Brazil (115 GB of raster in 4 tiles) before the cap. Budget 4 GB each so a
# run of big countries cannot exhaust memory.
GB_PER_WORKER = 4
# Each strip is read once, so GDAL's block cache (5% of RAM by default) buys nothing and
# would multiply by the worker count.
GDAL_CACHE_MB = "256"


def memory_gb() -> float:
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1e9
    except (ValueError, OSError, AttributeError):
        return 8.0


def default_workers() -> int:
    """One worker per hardware thread, if memory allows GB_PER_WORKER each.

    Hyperthreads count: on the i7-7700K (4 cores, 8 threads) the same 8 countries took
    297 s with 1 worker, 105 s with 4 and 80 s with 8. Each job runs ~1.6x slower when
    two share a core, but more of them run, so throughput still rises ~25%.
    """
    return max(1, min(os.cpu_count() or 1, int(memory_gb() // GB_PER_WORKER)))


def raw_bytes(paths: Iterable[str | Path]) -> int:
    """Uncompressed size of the GeoTIFFs in these zips (or plain files): the job's cost."""
    total = 0
    for p in paths:
        p = Path(p)
        if p.suffix.lower() == ".zip":
            with zipfile.ZipFile(p) as z:
                total += sum(i.file_size for i in z.infolist() if is_geotiff_member(i))
        elif p.exists():
            total += p.stat().st_size
    return total


@dataclass
class Outcome:
    key: str
    result: Any = None
    error: BaseException | None = None


def _init_worker() -> None:
    os.environ["GDAL_CACHEMAX"] = GDAL_CACHE_MB


def run(
    jobs: list[tuple[str, float, Callable[..., Any], tuple]],
    workers: int,
    on_done: Callable[[Outcome, int, int], None],
) -> list[Outcome]:
    """Run `(key, cost, fn, args)` jobs, largest cost first, `workers` at a time.

    `fn` must be a module-level function (it is pickled). `on_done(outcome, done, total)`
    runs in the parent as each job finishes; a job that raises is reported, not fatal.
    With one worker everything runs in this process, in the same order.
    """
    ordered = sorted(jobs, key=lambda j: -j[1])
    total = len(ordered)
    outcomes: list[Outcome] = []

    def finish(o: Outcome) -> None:
        outcomes.append(o)
        on_done(o, len(outcomes), total)

    if workers <= 1 or total <= 1:
        _init_worker()
        for key, _cost, fn, args in ordered:
            try:
                finish(Outcome(key, result=fn(*args)))
            except Exception as exc:  # noqa: BLE001 - reported per job, the run goes on
                finish(Outcome(key, error=exc))
        return outcomes

    # spawn, not fork: a forked child would inherit GDAL's and psycopg's state mid-flight.
    ctx = mp.get_context("spawn")
    with ProcessPoolExecutor(
        max_workers=min(workers, total), mp_context=ctx, initializer=_init_worker
    ) as pool:
        running: dict[Future, str] = {}
        queue = list(ordered)
        try:
            # Submit only as workers free up, so the order above is the order jobs start.
            while queue or running:
                while queue and len(running) < workers:
                    key, _cost, fn, args = queue.pop(0)
                    running[pool.submit(fn, *args)] = key
                done, _ = wait(running, return_when=FIRST_COMPLETED)
                for fut in done:
                    key = running.pop(fut)
                    try:
                        finish(Outcome(key, result=fut.result()))
                    except Exception as exc:  # noqa: BLE001
                        finish(Outcome(key, error=exc))
        except KeyboardInterrupt:
            pool.shutdown(wait=False, cancel_futures=True)
            raise
    return outcomes
