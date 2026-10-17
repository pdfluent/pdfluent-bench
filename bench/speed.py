# Copyright (c) 2026 Innovation Trigger B.V. All rights reserved.
# Licence pending owner decision: see LICENSE-TBD.md.
"""Speed: per-page wall time, throughput, peak memory and cold start.

Each measurement is one process running the column's own batch path (the
ported adapter, `--batch`), timed by the ported launcher (`structured_bench.
run_tool`), which reads the process tree's peak RSS after it is reaped.

  warm per page   A batch over the fixed page list, preceded by a warm-up page
                  that is not counted. Per-page laps come from the adapter's
                  own clock (`timings.tsv`), so model loading is in the warm-up
                  lap only. Reported as p50 and p95 over all laps of all
                  repetitions, and pages/s as pages over the summed laps.
  cold start      A fresh process converting one page: interpreter start,
                  imports, model load and the page. Measured separately, once
                  per repetition.
  threads         1 (thread-count variables set to 1, and `taskset -c 0` where
                  it exists) and all cores (the variables set to the CPU count).
"""
from __future__ import annotations

import contextlib
import json
import os
import pathlib
import shutil
import statistics

from bench import configs, engine_api

THREAD_VARS = ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS",
               "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "RAYON_NUM_THREADS")


@contextlib.contextmanager
def environment(values: dict[str, str]):
    saved = {k: os.environ.get(k) for k in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def thread_setting(threads: int) -> tuple[dict[str, str], list[str], str]:
    env = {name: str(threads) for name in THREAD_VARS}
    if threads == 1 and shutil.which("taskset"):
        return env, ["taskset", "-c", "0"], "taskset -c 0 and thread variables = 1"
    if threads == 1:
        return env, [], "thread variables = 1 (no taskset on this machine)"
    return env, [], f"thread variables = {threads}"


def _laps(out_dir: pathlib.Path) -> list[tuple[str, int, str]]:
    timings = out_dir / "timings.tsv"
    rows = []
    if timings.is_file():
        for line in timings.read_text().splitlines():
            parts = line.split("\t")
            if len(parts) == 3 and parts[1].isdigit():
                rows.append((parts[0], int(parts[1]), parts[2]))
    return rows


def _batch(python: str, tool: str, paths: list[str], scratch: pathlib.Path, prefix: list[str],
           timeout: int) -> tuple[dict, list[tuple[str, int, str]]]:
    sb = engine_api.sb()
    scratch.mkdir(parents=True, exist_ok=True)
    listing = scratch / "pages.txt"
    listing.write_text("\n".join(paths) + "\n")
    out = scratch / "out"
    probe: dict = {}
    sb.run_tool([*prefix, python, str(engine_api.ADAPTER), "--tool", tool, "--batch",
                 str(listing), "--out-dir", str(out)], timeout=timeout, probe=probe)
    return probe, _laps(out)


def speed_one(config: dict, pages: list[dict], reps: int, scratch: pathlib.Path,
              all_cores: int | None = None) -> dict:
    sb, axes = engine_api.sb(), engine_api.module("structured_axes")
    python = configs.tool_python(config)
    why = configs.unavailable(config, python)
    if why:
        raise SystemExit(f"[bench] FATAL: {config['name']} cannot run here: {why}")
    if not pages:
        raise SystemExit("[bench] FATAL: no pages to time")
    cores = all_cores or os.cpu_count() or 1
    tool = config["tool"]
    first = pathlib.Path(pages[0]["path"])
    warm_dir = scratch / "warmup"
    warm_dir.mkdir(parents=True, exist_ok=True)
    warm = warm_dir / f"warmup-{first.name}"
    shutil.copyfile(first, warm)
    timeout = sb.TIMEOUT_S * (len(pages) + 2)
    result: dict = {"config": config["name"], "tool": tool, "pages": len(pages),
                    "page_names": [p["name"] for p in pages], "reps": reps,
                    "version": configs.version_record(config, python), "threads": {}}
    for threads in (1, cores):
        env, prefix, how = thread_setting(threads)
        runs = []
        with environment(env):
            for rep in range(reps):
                probe, laps = _batch(python, tool, [str(warm)] + [p["path"] for p in pages],
                                     scratch / f"t{threads}" / f"rep{rep}", prefix, timeout)
                counted = [(n, ms, o) for n, ms, o in laps if n != warm.name]
                ok = [ms for _n, ms, o in counted if o == "ok"]
                runs.append({"outcome": probe.get("outcome"), "wall_ms": probe.get("ms"),
                             "peak_rss_mb": round(probe["peak_rss_kb"] / 1024, 1)
                             if isinstance(probe.get("peak_rss_kb"), (int, float)) else None,
                             "laps_ms": ok, "answered": len(ok), "failed": len(pages) - len(ok),
                             "pages_per_s": round(len(ok) / (sum(ok) / 1000), 3) if sum(ok) else None})
        pooled = [ms for run in runs for ms in run["laps_ms"]]
        if not pooled:
            raise SystemExit(f"[bench] FAIL: {config['name']} answered none of the "
                             f"{len(pages)} timed page(s) with {how}")
        rates = [r["pages_per_s"] for r in runs if r["pages_per_s"]]
        peaks = [r["peak_rss_mb"] for r in runs if r["peak_rss_mb"] is not None]
        result["threads"][str(threads)] = {
            "label": "1" if threads == 1 else f"all ({threads})", "how": how,
            "ms_p50": axes.percentile(pooled, 0.5) if pooled else None,
            "ms_p95": axes.percentile(pooled, 0.95) if pooled else None,
            "pages_per_s": round(statistics.median(rates), 3) if rates else None,
            "peak_rss_mb": max(peaks) if peaks else None,
            "failed_pages": sum(r["failed"] for r in runs), "runs": runs}
    env, prefix, how = thread_setting(cores)
    cold = []
    with environment(env):
        for rep in range(reps):
            probe, _laps_ = _batch(python, tool, [str(first)], scratch / "cold" / f"rep{rep}",
                                   prefix, sb.TIMEOUT_S * 2)
            cold.append(probe.get("ms") if probe.get("outcome") == "ok" else None)
    good = [ms for ms in cold if ms is not None]
    result["cold_start_ms"] = {"values": cold, "median": statistics.median(good) if good else None,
                               "what": "one fresh process, one page, start-up and model load included"}
    return result


def write(result: dict, out: pathlib.Path, machine_kind: str) -> pathlib.Path:
    target = out / "speed" / machine_kind / f"{result['config']}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, indent=1) + "\n")
    return target
