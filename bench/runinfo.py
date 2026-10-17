# Copyright (c) 2026 Innovation Trigger B.V. All rights reserved.
# Licence pending owner decision: see LICENSE-TBD.md.
"""run.json: what a run ran on, with what, against which data."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import pathlib
import platform
import shutil
import subprocess

from bench import configs

ROOT = pathlib.Path(__file__).resolve().parents[1]
MANIFESTS = ROOT / "engine" / "corpus" / "structured"
FIXTURE_MANIFEST = ROOT / "fixtures" / "MANIFEST.json"
MANIFEST_OF = {
    "doclaynet": MANIFESTS / "DOCLAYNET_TEST.json",
    "pubtables-1m": MANIFESTS / "PUBTABLES1M.json",
    "parsebench": MANIFESTS / "PARSEBENCH.json",
    # FinTabNet's split is drawn from the pinned pages at fetch time; the pin
    # is the committed half, the drawn file is hashed when it exists.
    "fintabnet": MANIFESTS / "FINTABNET_DRAW.json",
    "fixtures": FIXTURE_MANIFEST,
}


def _out(command: list[str]) -> str:
    if not shutil.which(command[0]):
        return ""
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return done.stdout.strip() if done.returncode == 0 else ""


def cpu_model() -> str:
    cpuinfo = pathlib.Path("/proc/cpuinfo")
    if cpuinfo.is_file():
        for line in cpuinfo.read_text(errors="replace").splitlines():
            if line.lower().startswith("model name"):
                return line.split(":", 1)[1].strip()
    return _out(["sysctl", "-n", "machdep.cpu.brand_string"]) or platform.processor() or "unknown"


def memory_gb() -> float | None:
    meminfo = pathlib.Path("/proc/meminfo")
    if meminfo.is_file():
        for line in meminfo.read_text().splitlines():
            if line.startswith("MemTotal:"):
                return round(int(line.split()[1]) / 1024 / 1024, 1)
    raw = _out(["sysctl", "-n", "hw.memsize"])
    return round(int(raw) / 1024 ** 3, 1) if raw.isdigit() else None


def gpus() -> list[dict]:
    raw = _out(["nvidia-smi", "--query-gpu=name,driver_version,memory.total",
                "--format=csv,noheader"])
    out = []
    for line in raw.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) == 3:
            out.append({"name": parts[0], "driver": parts[1], "memory": parts[2]})
    return out


def harness_commit() -> dict:
    sha = _out(["git", "-C", str(ROOT), "rev-parse", "HEAD"])
    dirty = _out(["git", "-C", str(ROOT), "status", "--porcelain", "--untracked-files=no"])
    return {"commit": sha or "unknown", "dirty": bool(dirty)}


def sha256_file(path: pathlib.Path) -> str | None:
    if not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def manifests(sets: list[str]) -> dict:
    rows = {}
    for name in sets:
        path = MANIFEST_OF[name]
        rows[name] = {"file": str(path.relative_to(ROOT)), "sha256": sha256_file(path)}
        if name == "fintabnet":
            drawn = MANIFESTS / "FINTABNET.json"
            rows[name]["drawn"] = {"file": str(drawn.relative_to(ROOT)),
                                   "sha256": sha256_file(drawn)}
    joined = "\n".join(f"{k}\0{rows[k]['sha256']}" for k in sorted(rows))
    return {"sets": rows, "hash": hashlib.sha256(joined.encode()).hexdigest()}


def images(work: pathlib.Path) -> dict:
    path = work / "images.json"
    recorded = json.loads(path.read_text()) if path.is_file() else {}
    if os.environ.get("BENCH_IMAGE_ID"):
        recorded["_current"] = os.environ["BENCH_IMAGE_ID"]
    return recorded


def machine_record(machine_type: str) -> dict:
    spec = configs.machine(machine_type)
    return {"type": machine_type, "spec": spec, "platform": platform.platform(),
            "cpu_model": cpu_model(), "cpus": os.cpu_count(), "memory_gb": memory_gb(),
            "gpus": gpus(), "instance": os.environ.get("BENCH_INSTANCE", "")}


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def run_id(machine_type: str) -> str:
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"bench-{machine_type}-{stamp}"


def start(run: str, machine_type: str, work: pathlib.Path, sets: list[str],
          chosen: list[dict], argv: list[str]) -> dict:
    provenance = json.loads((ROOT / "engine" / "PROVENANCE.json").read_text())
    return {
        "run_id": run, "schema": 1, "started": now(), "finished": None, "wall_clock_s": None,
        "command": argv, "machine": machine_record(machine_type), "images": images(work),
        "harness": harness_commit(), "engine_ref": provenance["engine_ref"],
        "datasets": manifests(sets), "configs": {c["name"]: {"tool": c["tool"]} for c in chosen},
        "steps": [], "cost_eur_estimate": None,
    }


def finish(record: dict, started_monotonic: float, ended_monotonic: float) -> dict:
    record["finished"] = now()
    record["wall_clock_s"] = round(ended_monotonic - started_monotonic, 1)
    spec = record["machine"]["spec"]
    price = (spec.get("eur_per_hour") or {})
    if price:
        hours = record["wall_clock_s"] / 3600
        record["cost_eur_estimate"] = {
            "spot": round(hours * price["spot"], 2), "on_demand": round(hours * price["on_demand"], 2),
            "basis": "wall clock x configs/machines.yaml price (an estimate; the bill is the record)"}
    return record
