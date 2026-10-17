# Copyright (c) 2026 Innovation Trigger B.V. All rights reserved.
# Licence pending owner decision: see LICENSE-TBD.md.
"""The cost guard for a cloud round, and the time model behind its estimate.

The guard prices a round at the ON-DEMAND rate (a spot price can rise to it),
for the full `max_hours` the VM is allowed to live -- the VM is created with
that limit and deletes itself when it runs out, so the guarded figure is a
ceiling and not a forecast. It refuses when the ceiling exceeds BUDGET_EUR.

The time model is a planning aid with every assumption printed. Seconds per
page are rough, from the engine's earlier rounds and not from a measurement
in this repository; replace them with this harness's own speed results once
a round has run.
"""
from __future__ import annotations

import math

from bench import configs

# Assumed seconds per page, warm, 8 vCPU. CPU, then GPU where a route exists.
S_PER_PAGE = {
    "pdfluent_fast": {"cpu": 0.3},
    "pdfluent_balanced": {"cpu": 1.5},
    "pdfluent_balanced_tatr": {"cpu": 2.5},
    "teacher_ens": {"cpu": 6.0},
    "docling": {"cpu": 4.0, "gpu": 1.0},
    "docling_minimal": {"cpu": 2.5, "gpu": 0.8},
    "pymupdf4llm": {"cpu": 1.0},
}
# structured_bench.measure reads every page, then every multi-page document
# again (the furniture and in-document passes), then a cold start.
PASSES = 2.2
# Fixed time per round: boot, image builds, dataset fetch, teardown.
OVERHEAD_H = {"cpu": 1.5, "gpu": 1.5}
SPEED_FACTOR = 2 * 3 + 1  # two thread settings x three repetitions, plus cold starts


def round_hours(chosen: list[dict], sets: list[str], splits: list[str], profile: str,
                kind: str, reps: int = 3, speed: bool = True) -> dict:
    lines = []
    seconds = 0.0
    for config in chosen:
        rate = S_PER_PAGE.get(config["tool"], {}).get(kind)
        if rate is None:
            lines.append(f"{config['name']}: no {kind} route, not run")
            continue
        pages = 0
        speed_pages = 0
        for set_name in sets:
            per_split = configs.pages_for(set_name, profile)
            if set_name == "parsebench":
                per_split *= 3  # per category: table, chart, text
            pages += per_split * len(splits)
            if set_name != "parsebench":  # timed inside its quality run only
                speed_pages += configs.pages_for(set_name, profile, "speed_pages")
        q = pages * rate * PASSES
        s = speed_pages * rate * (2 * reps + 1) if speed else 0.0
        seconds += q + s
        lines.append(f"{config['name']}: {pages} quality page(s) x {rate} s x {PASSES} passes"
                     f" + {speed_pages} speed page(s) x {rate} s x {2 * reps + 1} = "
                     f"{(q + s) / 3600:.1f} h")
    hours = seconds / 3600 + OVERHEAD_H[kind]
    lines.append(f"overhead (boot, images, fetch, teardown): {OVERHEAD_H[kind]} h")
    return {"hours": round(hours, 2), "lines": lines}


def ceiling(machine_name: str, max_hours: float) -> dict:
    spec = configs.machine(machine_name)
    price = spec.get("eur_per_hour")
    if not price:
        return {"machine": machine_name, "eur": 0.0, "why": "local machine, no charge"}
    disk = spec.get("disk_gb", 100) * configs.machines()["disk_eur_per_gb_month"] * max_hours / 730
    on_demand = max_hours * price["on_demand"] + disk
    spot = max_hours * price["spot"] + disk
    return {"machine": machine_name, "max_hours": max_hours, "eur": round(on_demand, 2),
            "spot_eur": round(spot, 2), "disk_eur": round(disk, 2),
            "basis": f"{max_hours} h x EUR {price['on_demand']}/h on demand + disk "
                     f"(prices from configs/machines.yaml, checked "
                     f"{configs.machines()['price_checked_on']})"}


def guard(machine_name: str, max_hours: float, budget_eur: float) -> tuple[bool, dict]:
    if max_hours <= 0 or not math.isfinite(max_hours):
        raise SystemExit("[cost] FATAL: max hours must be a positive number")
    estimate = ceiling(machine_name, max_hours)
    return estimate["eur"] <= budget_eur, estimate
