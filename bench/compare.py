# Copyright (c) 2026 Innovation Trigger B.V.
#
# PDFluent is available under two licences, at your option: the GNU AGPLv3, or
# the PDFluent Commercial Licence. See the LICENSE file in this repository --
# that file travels with the copy you received, which a URL does not.
"""Paired comparisons per document, with a bootstrap 95% interval.

    python -m bench compare results/<run> [results/<run> ...] [--out DIR]

For every set, split and quality axis, each PDFluent configuration is paired
with each reference (Docling standard, Docling minimal, pymupdf4llm) over the
documents both of them scored. The number is the mean over those documents of
(PDFluent - reference), each document's value being its own page mean, as in
the report's headline. The interval is the paired bootstrap the engine uses
(`pb_summary._bootstrap`: 2000 resampled means, seed 0, the 2.5th and 97.5th
percentiles), and a document counts up or down past +-0.005
(`pb_summary.PAIR_DELTA`).

Two columns are only paired when they read the same pages: for the three
page-scored sets the run files' `bench.pages_digest` must agree, or the pair
is refused and named. ParseBench examples are grouped into documents through
the sealed manifest (engine/corpus/structured/PARSEBENCH.json).

Numbers here are measurements, not claims.
"""
from __future__ import annotations

import json
import pathlib

from bench import engine_api, report

OURS_PREFIX = "pdfluent-"
REFERENCES = ("docling-standard", "docling-minimal", "pymupdf4llm")
PARSEBENCH_MANIFEST = (engine_api.ENGINE / "corpus" / "structured" / "PARSEBENCH.json")


def _load(dirs: list[pathlib.Path]) -> dict:
    """(set, split, config) -> run file, CPU runs first, the first one seen kept."""
    found: dict = {}
    for d in dirs:
        for path in sorted((d / "quality").glob("*/*/*.json")):
            key = (path.parts[-3], path.parts[-2], path.stem)
            if key not in found:
                found[key] = {"run_dir": str(d), "file": json.loads(path.read_text())}
    return found


def _tool_row(run_file: dict) -> dict:
    return report._tool_row(run_file) or {}


def _page_axes() -> list[tuple[str, str]]:
    return [(axis, key) for axis, key, _label in engine_api.sb().AXES]


def per_document(run_file: dict, set_name: str, documents_of: dict | None = None) -> dict:
    """axis -> {document: mean of its pages' scores}, scored pages only."""
    row = _tool_row(run_file)
    out: dict[str, dict[str, float]] = {}
    if set_name == "parsebench":
        groups = row.get("groups") or {}
        for group, metric, _label in report.PARSEBENCH:
            per_page = (groups.get(group) or {}).get("per_page") or {}
            values: dict[str, list[float]] = {}
            for test_id, cells in per_page.items():
                value = cells.get(metric)
                if cells.get("success") and isinstance(value, (int, float)):
                    document = (documents_of or {}).get(test_id, test_id)
                    values.setdefault(document, []).append(float(value))
            out[f"{group}.{metric}"] = {d: sum(v) / len(v) for d, v in values.items()}
        return out
    for axis, key in _page_axes():
        values = {}
        for page in row.get("documents") or []:
            cell = page.get(axis) or {}
            if cell.get("scored") and isinstance(cell.get(key), (int, float)):
                values.setdefault(page.get("document") or page["name"], []).append(float(cell[key]))
        out[axis] = {d: sum(v) / len(v) for d, v in values.items()}
    return out


def parsebench_documents(test_ids: set[str]) -> tuple[dict, list[str]]:
    """test_id -> document through the sealed manifest, and the ids it could not place."""
    manifest = json.loads(PARSEBENCH_MANIFEST.read_text())
    keys: dict[str, str] = {}
    for page in manifest["pages"]:
        name = page["name"]
        stem = name[:-4] if name.endswith(".pdf") else name
        short = stem[5:] if stem.startswith("docs/") else stem
        for key in (name, stem, short, name.rsplit("/", 1)[-1], stem.rsplit("/", 1)[-1]):
            keys.setdefault(key, page["document"])
    placed, unplaced = {}, []
    for test_id in sorted(test_ids):
        stem = test_id[:-4] if test_id.endswith(".pdf") else test_id
        for key in (test_id, stem, stem.rsplit("/", 1)[-1]):
            if key in keys:
                placed[test_id] = keys[key]
                break
        else:
            unplaced.append(test_id)
    return placed, unplaced


def pair(ours: dict[str, float], ref: dict[str, float]) -> dict:
    pb = engine_api.module("pb_summary")
    common = sorted(set(ours) & set(ref))
    diffs = [ours[d] - ref[d] for d in common]
    up = sum(1 for v in diffs if v > pb.PAIR_DELTA)
    down = sum(1 for v in diffs if v < -pb.PAIR_DELTA)
    boot = pb._bootstrap(diffs)
    return {"documents": len(common),
            "only_ours": len(set(ours) - set(ref)), "only_ref": len(set(ref) - set(ours)),
            "mean_ours": sum(ours[d] for d in common) / len(common) if common else None,
            "mean_ref": sum(ref[d] for d in common) / len(common) if common else None,
            "diff": sum(diffs) / len(diffs) if diffs else None,
            "ci_low": boot["ci_low"] if boot else None, "ci_high": boot["ci_high"] if boot else None,
            "p_ours_better": boot["p_b_better"] if boot else None,
            "up": up, "down": down, "tied": len(common) - up - down,
            "bootstrap": {"draws": pb.BOOTSTRAP_DRAWS, "seed": pb.BOOTSTRAP_SEED}}


def build(dirs: list[pathlib.Path]) -> dict:
    found = _load(dirs)
    rows, refused, notes = [], [], []
    for set_name, split in sorted({(k[0], k[1]) for k in found}):
        configs_here = {k[2]: v for k, v in found.items() if k[0] == set_name and k[1] == split}
        ours_names = sorted(n for n in configs_here if n.startswith(OURS_PREFIX))
        ref_names = [n for n in REFERENCES if n in configs_here]
        documents_of = None
        if set_name == "parsebench":
            ids = {t for v in configs_here.values()
                   for g in (_tool_row(v["file"]).get("groups") or {}).values()
                   for t in (g.get("per_page") or {})}
            documents_of, unplaced = parsebench_documents(ids)
            if unplaced:
                notes.append(f"parsebench {split}: {len(unplaced)} example(s) not in the manifest, "
                             f"each counted as its own document, e.g. {unplaced[0]}")
        scores = {n: per_document(v["file"], set_name, documents_of) for n, v in configs_here.items()}
        for ours in ours_names:
            for ref in ref_names:
                if set_name != "parsebench":
                    a = configs_here[ours]["file"].get("bench", {}).get("pages_digest")
                    b = configs_here[ref]["file"].get("bench", {}).get("pages_digest")
                    if not a or a != b:
                        refused.append(f"{set_name} {split}: {ours} vs {ref}: pages_digest "
                                       f"{a} != {b}; not the same pages")
                        continue
                for axis in scores[ours]:
                    result = pair(scores[ours][axis], scores[ref].get(axis, {}))
                    if not result["documents"]:
                        continue
                    rows.append({"set": set_name, "split": split, "axis": axis, "ours": ours,
                                 "ref": ref, **result})
    return {"runs": [str(d) for d in dirs], "rows": rows, "refused": refused, "notes": notes,
            "method": __doc__.split("\n\n")[2].replace("\n", " ")}


def _f(value, sign: bool = False) -> str:
    if value is None:
        return "-"
    return f"{value:+.3f}" if sign else f"{value:.3f}"


def markdown(result: dict) -> str:
    lines = ["# Paired comparisons per document", "", result["method"], ""]
    for note in result["notes"]:
        lines.append(f"- note: {note}")
    for why in result["refused"]:
        lines.append(f"- REFUSED: {why}")
    for split in ("holdout", "tuning"):
        rows = [r for r in result["rows"] if r["split"] == split]
        if not rows:
            continue
        lines += ["", f"## {split}", "",
                  "| set | axis | PDFluent | reference | docs | PDFluent | reference | diff | 95% CI | up/down/tied |",
                  "|---|---|---|---|---|---|---|---|---|---|"]
        for r in rows:
            lines.append(f"| {report.SET_TITLE.get(r['set'], r['set'])} | {r['axis']} | {r['ours']} | "
                         f"{r['ref']} | {r['documents']} | {_f(r['mean_ours'])} | {_f(r['mean_ref'])} | "
                         f"{_f(r['diff'], True)} | [{_f(r['ci_low'], True)}; {_f(r['ci_high'], True)}] | "
                         f"{r['up']}/{r['down']}/{r['tied']} |")
    lines += ["", "Numbers here are measurements, not claims."]
    return "\n".join(lines) + "\n"


def write(dirs: list[pathlib.Path], out: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    result = build(dirs)
    out.mkdir(parents=True, exist_ok=True)
    md, js = out / "compare.md", out / "compare.json"
    md.write_text(markdown(result))
    js.write_text(json.dumps(result, indent=1) + "\n")
    return md, js
