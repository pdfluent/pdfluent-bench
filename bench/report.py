# Copyright (c) 2026 Innovation Trigger B.V.
#
# PDFluent is available under two licences, at your option: the GNU AGPLv3, or
# the PDFluent Commercial Licence. See the LICENSE file in this repository --
# that file travels with the copy you received, which a URL does not.
"""One overview table from one or more run directories: markdown and JSON.

Rules carried over from the engine's benchmark page and round scoreboard:

* every number is read from a run file; nothing is typed or carried over;
* quality cells are document means (each document weighs the same, as the
  engine scoreboard's headline), with the page mean in the JSON beside it;
* each quality cell carries its denominator: `0.861 ⟨67⟩` is a mean over 67
  documents;
* a cell that should be there and is not is written `missing` and named at
  the top, and the report then says DRAFT;
* a GPU cell for a configuration without a GPU route is `n/a` with the reason,
  never left out;
* rows of a configuration marked `reproducible: internal` say so.

Numbers in a report are not claims: anything published goes through the
claims register with a run ID and a measurement date.
"""
from __future__ import annotations

import json
import pathlib

from bench import configs, engine_api

AXES_BY_SET = {
    "fintabnet": (("teds", "TEDS"), ("tables", "tables F1"), ("words", "words F1")),
    "pubtables-1m": (("teds", "TEDS"), ("tables", "tables F1"), ("words", "words F1")),
    "doclaynet": (("headings", "headings F1"), ("lists", "lists F1"), ("tables", "tables F1"),
                  ("text", "text 1-NED"), ("text_geometric", "text 1-NED (geometric)"),
                  ("words", "words F1"), ("order", "order (annotated)"),
                  ("order_geometric", "order (geometric)")),
    "fixtures": (("headings", "headings F1"), ("lists", "lists F1"), ("tables", "tables F1"),
                 ("teds", "TEDS"), ("text", "text 1-NED"), ("words", "words F1"),
                 ("order", "order (annotated)")),
}
# ParseBench: group, metric, label (the engine scoreboard's headlines).
PARSEBENCH = (("table", "table_record_match", "record match"),
              ("table", "grits_trm_composite", "table GriTS"),
              ("text_content", "content_faithfulness", "text content"),
              ("text_formatting", "normalized_text_score", "text formatting"),
              ("chart", "rule_pass_rate", "chart"))
SET_TITLE = {"fintabnet": "FinTabNet", "pubtables-1m": "PubTables-1M", "doclaynet": "DocLayNet",
             "parsebench": "ParseBench", "fixtures": "fixtures"}
SPLITS = ("tuning", "holdout")
SPEED_ROWS = (("ms_p50", "ms/page p50"), ("ms_p95", "ms/page p95"),
              ("pages_per_s", "pages/s"), ("peak_rss_mb", "peak RSS MB"))


def _load_runs(dirs: list[pathlib.Path]) -> tuple[list[dict], dict, dict]:
    runs, quality, speed = [], {}, {}
    # pdfluent-bench: a configuration the run was asked for but did not
    # measure stays in the table, with the reason.
    for d in dirs:
        meta = d / "run.json"
        if not meta.is_file():
            raise SystemExit(f"[report] FATAL: {meta} is not there")
        run = json.loads(meta.read_text())
        runs.append(run)
        gpu = bool(run["machine"].get("gpus")) or bool(run["machine"]["spec"].get("gpu"))
        kind = "gpu" if gpu else "cpu"
        for path in sorted((d / "quality").glob("*/*/*.json")):
            set_name, split, config = path.parts[-3], path.parts[-2], path.stem
            quality.setdefault((kind, set_name, split, config), (run["run_id"], json.loads(path.read_text())))
        for path in sorted((d / "speed").glob("*/*.json")):
            speed.setdefault((kind, path.stem), (run["run_id"], json.loads(path.read_text())))
    return runs, quality, speed


def _tool_row(run_file: dict) -> dict | None:
    tools = run_file.get("tools") or {}
    tool = run_file.get("bench", {}).get("tool")
    return tools.get(tool) if tool else (next(iter(tools.values())) if len(tools) == 1 else None)


def quality_numbers(run_file: dict, set_name: str) -> dict:
    sb = engine_api.sb()
    row = _tool_row(run_file)
    if row is None:
        return {}
    out = {}
    if set_name == "parsebench":
        for group, metric, _label in PARSEBENCH:
            g = (row.get("groups") or {}).get(group) or {}
            value = (g.get("metrics") or {}).get(metric)
            out[f"{group}.{metric}"] = {"mean": value, "n": g.get("examples"), "unit": "examples"}
        return out
    macro = sb.macro_by_document(row.get("documents") or [])
    summary = row.get("summary") or {}
    for axis, _label in AXES_BY_SET.get(set_name, ()):
        out[axis] = {"mean": (macro.get(axis) or {}).get("mean"),
                     "n": (macro.get(axis) or {}).get("documents"), "unit": "documents",
                     "page_mean": (summary.get(axis) or {}).get("mean"),
                     "pages": (summary.get(axis) or {}).get("pages")}
    return out


def _answered(run_file: dict) -> bool:
    row = _tool_row(run_file) or {}
    return bool(row.get("pages"))


def _fmt(value, digits: int = 3) -> str:
    if value is None:
        return "missing"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def build(dirs: list[pathlib.Path]) -> dict:
    runs, quality, speed = _load_runs(dirs)
    not_run = {}
    for run in runs:
        for step in run.get("steps") or []:
            if step.get("kind") == "config" and step.get("status") in ("skipped", "failed"):
                not_run[step["config"]] = f"{step['status']}: {step.get('why')}"
    names = sorted({k[3] for k in quality} | {k[1] for k in speed}
                   | {n for run in runs for n in run.get("configs") or {}})
    chosen = {n: configs.load(n) for n in names}
    sets_seen = [s for s in ("fintabnet", "pubtables-1m", "doclaynet", "parsebench", "fixtures")
                 if any(k[1] == s for k in quality)]
    splits_seen = [s for s in SPLITS if any(k[2] == s for k in quality)]
    kinds = sorted({k[0] for k in quality} | {k[0] for k in speed})
    missing: list[str] = []
    table: list[dict] = []

    for set_name in sets_seen:
        axes = (PARSEBENCH if set_name == "parsebench" else
                [(None, a, label) for a, label in AXES_BY_SET.get(set_name, ())])
        for group, axis, label in axes:
            key = f"{group}.{axis}" if group else axis
            cells = {}
            for name in names:
                parts = []
                for split in splits_seen:
                    hit = quality.get(("cpu", set_name, split, name)) or quality.get(("gpu", set_name, split, name))
                    if hit is None:
                        why = " (not measured; reason below)" if name in not_run else ""
                        missing.append(f"{SET_TITLE[set_name]} {label} {split}: {name} has no run "
                                       f"file{why}")
                        parts.append({"split": split, "mean": None})
                        continue
                    numbers = quality_numbers(hit[1], set_name).get(key) or {}
                    if numbers.get("mean") is None and numbers.get("n") == 0 \
                            and _answered(hit[1]):
                        # Nothing in this half is annotated for the axis: not a hole.
                        parts.append({"split": split, "run_id": hit[0], **numbers,
                                      "not_annotated": True})
                        continue
                    if numbers.get("mean") is None:
                        missing.append(f"{SET_TITLE[set_name]} {label} {split}: {name} has no value "
                                       f"in run {hit[0]}")
                    parts.append({"split": split, "run_id": hit[0], **numbers})
                cells[name] = parts
            table.append({"kind": "quality", "set": set_name, "axis": key,
                          "label": f"{SET_TITLE[set_name]} {label}", "cells": cells})

    for kind in ("cpu", "gpu"):
        if kind not in kinds and not any(k[0] == kind for k in speed):
            continue
        for threads_key in ("all", "1"):
            for field, label in SPEED_ROWS:
                cells = {}
                for name in names:
                    config = chosen[name]
                    if kind == "gpu" and not config["gpu"]["route"]:
                        cells[name] = {"na": config["gpu"]["why"]}
                        continue
                    hit = speed.get((kind, name))
                    if hit is None:
                        why = " (not measured; reason below)" if name in not_run else ""
                        missing.append(f"{kind.upper()} speed {label} ({threads_key} threads): "
                                       f"{name} has no speed file{why}")
                        cells[name] = {"value": None}
                        continue
                    block = next((v for k, v in hit[1]["threads"].items()
                                  if (threads_key == "1") == (k == "1")), {})
                    value = block.get(field)
                    if value is None:
                        missing.append(f"{kind.upper()} speed {label} ({threads_key} threads): "
                                       f"{name} has no value in run {hit[0]}")
                    cells[name] = {"value": value, "run_id": hit[0], "how": block.get("how")}
                table.append({"kind": "speed", "machine": kind, "threads": threads_key,
                              "field": field,
                              "label": f"{kind.upper()} {label}, {'all cores' if threads_key == 'all' else '1 thread'}",
                              "cells": cells})
        cells = {}
        for name in names:
            config = chosen[name]
            if kind == "gpu" and not config["gpu"]["route"]:
                cells[name] = {"na": config["gpu"]["why"]}
                continue
            hit = speed.get((kind, name))
            value = (hit[1].get("cold_start_ms") or {}).get("median") if hit else None
            if value is None:
                missing.append(f"{kind.upper()} cold start: {name} has no value")
            cells[name] = {"value": value, "run_id": hit[0] if hit else None}
        table.append({"kind": "speed", "machine": kind, "field": "cold_start_ms",
                      "label": f"{kind.upper()} cold start ms (median)", "cells": cells})

    return {"draft": bool(missing), "missing": missing, "configs": names, "not_run": not_run,
            "splits": splits_seen,
            "internal": {n: chosen[n].get("reproducible_why", "") for n in names
                         if chosen[n]["reproducible"] == "internal"},
            "runs": [{"run_id": r["run_id"], "machine": r["machine"]["type"],
                      "cpu_model": r["machine"]["cpu_model"], "gpus": r["machine"]["gpus"],
                      "harness": r["harness"], "engine_ref": r["engine_ref"],
                      "datasets_hash": r["datasets"]["hash"], "started": r["started"],
                      "wall_clock_s": r["wall_clock_s"]} for r in runs],
            "rows": table}


def markdown(report: dict) -> str:
    names = report["configs"]
    lines = []
    if report["draft"]:
        lines += ["**DRAFT: this report is missing values.** Each is listed here and written "
                  "`missing` in the table.", ""]
        lines += [f"- {m}" for m in report["missing"]] + [""]
    lines += ["# Benchmark overview", ""]
    for name, why in sorted(report.get("not_run", {}).items()):
        lines.append(f"- not measured: {name}, {why}")
    for run in report["runs"]:
        gpu = ", ".join(g["name"] for g in run["gpus"]) or "no GPU"
        lines.append(f"- run `{run['run_id']}` on {run['machine']} ({run['cpu_model']}; {gpu}), "
                     f"harness {run['harness']['commit'][:12]}{' (dirty)' if run['harness']['dirty'] else ''}, "
                     f"engine scorers {run['engine_ref'][:12]}, datasets {run['datasets_hash'][:12]}, "
                     f"started {run['started']}, wall clock {run['wall_clock_s']} s")
    lines += ["", "Quality cells: document mean per split, "
              + " / ".join(report["splits"]) + ", with the number of documents (ParseBench: "
              "examples) in \u27e8\u27e9; \u2013 where no document of that half is annotated "
              "for the axis. Speed cells: one value per configuration.", ""]
    header = "| row | " + " | ".join(
        n + (" (internal)" if n in report["internal"] else "") for n in names) + " |"
    lines += [header, "|---|" + "---|" * len(names)]
    for row in report["rows"]:
        cells = []
        for name in names:
            cell = row["cells"].get(name)
            if row["kind"] == "quality":
                parts = []
                for part in cell or []:
                    if part.get("not_annotated"):
                        parts.append("\u2013")
                    elif part.get("mean") is None:
                        parts.append("missing")
                    else:
                        parts.append(f"{part['mean']:.3f} \u27e8{part.get('n')}\u27e9")
                cells.append(" / ".join(parts))
            elif "na" in cell:
                cells.append("n/a")
            else:
                digits = 0 if row["field"] in ("ms_p50", "ms_p95", "cold_start_ms") else (
                    1 if row["field"] == "peak_rss_mb" else 3)
                cells.append(_fmt(cell.get("value"), digits))
        lines.append(f"| {row['label']} | " + " | ".join(cells) + " |")
    na = {(n, c["na"]) for row in report["rows"] for n, c in row["cells"].items()
          if isinstance(c, dict) and "na" in c}
    if na:
        lines += ["", "n/a:"] + [f"- {n}: {why}" for n, why in sorted(na)]
    if report["internal"]:
        lines += ["", "Internal rows (not publicly reproducible yet):"]
        lines += [f"- {n}: {why}" for n, why in report["internal"].items()]
    lines += ["", "Numbers here are measurements, not claims. Anything published goes through "
              "the claims register with its run ID and measurement date."]
    return "\n".join(lines) + "\n"


def write(dirs: list[pathlib.Path], out: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    report = build(dirs)
    out.mkdir(parents=True, exist_ok=True)
    md, js = out / "report.md", out / "report.json"
    md.write_text(markdown(report))
    js.write_text(json.dumps(report, indent=1) + "\n")
    return md, js
