# Copyright (c) 2026 Innovation Trigger B.V.
#
# PDFluent is available under two licences, at your option: the GNU AGPLv3, or
# the PDFluent Commercial Licence. See the LICENSE file in this repository --
# that file travels with the copy you received, which a URL does not.
"""Run every scorer over the fixture cases, with whichever modules are given.

`scripts/make_goldens.py` calls `compute` with the ORIGINAL engine modules and
writes `fixtures/scoring/golden.json`; `tests/test_scorer_equivalence.py` calls
it with the vendored modules and requires the same JSON. The function is
plumbing only: every number in its output comes from a scorer.
"""
from __future__ import annotations

import copy
import importlib
import json
import pathlib
import sys
import types

ROOT = pathlib.Path(__file__).resolve().parents[1]
CASES = ROOT / "fixtures" / "scoring" / "cases.json"
GOLDEN = ROOT / "fixtures" / "scoring" / "golden.json"

# Module search paths inside an engine tree (original or vendored).
ENGINE_PATHS = ("scripts/ci/loop", "scripts/ci", "scripts/extraction")
MODULES = ("structured_bench", "structured_axes", "pubtables_cells", "structured_holdout_rule",
           "pb_summary", "parsebench_round")


def load_modules(engine_root: pathlib.Path) -> dict[str, types.ModuleType]:
    """Import the scorer modules of one engine tree, isolated from any other."""
    for name in list(sys.modules):
        if name in MODULES or name in ("fintabnet_archive", "fintabnet_split"):
            del sys.modules[name]
    saved = list(sys.path)
    sys.path[:0] = [str(engine_root / p) for p in ENGINE_PATHS]
    try:
        loaded = {name: importlib.import_module(name) for name in MODULES}
    finally:
        sys.path[:] = saved
    for name in loaded:  # do not leak this tree into the next import
        sys.modules.pop(name, None)
    return loaded


def _round(value):
    """JSON-stable floats; the scorers already round, this only guards repr noise."""
    if isinstance(value, float):
        return round(value, 12)
    if isinstance(value, dict):
        return {k: _round(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_round(v) for v in value]
    return value


def compute(mods: dict[str, types.ModuleType], cases: dict) -> dict:
    sb = mods["structured_bench"]
    axes = mods["structured_axes"]
    cells = mods["pubtables_cells"]
    rule = mods["structured_holdout_rule"]
    pbs = mods["pb_summary"]
    pbr = mods["parsebench_round"]
    out: dict = {"blocks": {}, "pages": {}, "summary": {}, "axes": {}}

    pages = cases["pages"]
    for tool, produced in cases["produced"].items():
        rows = []
        for page in pages:
            markdown = produced.get(page["name"])
            if markdown is None:
                continue
            blocks = sb.blocks_of(markdown)
            out["blocks"][f"{tool}/{page['name']}"] = blocks
            rows.append(sb.score_page(copy.deepcopy(page), blocks))
        out["pages"][tool] = rows
        out["summary"][tool] = {
            "summary": sb.summarise_tool(rows),
            "summary_v2": sb.summarise_tool_v2(rows),
            "macro_by_document": sb.macro_by_document(rows),
            "tails": sb.tails(rows),
        }
    out["order_provenance"] = sb.order_provenance(copy.deepcopy(pages))
    out["geometric"] = {p["name"]: [b["text"] for b in sb.geometric(p["blocks"])]
                        for p in pages}

    a = out["axes"]
    a["teds"] = [[axes.teds(t, p), axes.teds_struct(t, p)] for t, p in cases["teds_pairs"]]
    a["table_structure"] = [axes.table_structure_score(t, p) for t, p in cases["table_sets"]]
    a["text"] = [axes.text_score(t, p) for t, p in cases["text_pairs"]]
    a["category"] = [axes.category_score(t, p) for t, p in cases["category_sets"]]
    a["order"] = [axes.reading_order_score(t, p) for t, p in cases["order_sets"]]
    a["words"] = [axes.bag_of_words_score(c["truth"], c["produced"],
                                          furniture=c.get("furniture"))
                  for c in cases["word_sets"]]
    lexicon = axes.load_lexicon()
    a["normalise"] = [axes.normalise(t) for t in cases["normalise"]]
    a["normalise_v2"] = [axes.normalise_v2(t, lexicon, english=True) for t in cases["normalise"]]
    a["english"] = [axes.page_is_english(t, lexicon) for t in cases["english_pages"]]
    a["percentile"] = [axes.percentile(v, s) for v, s in cases["percentiles"]]

    out["pubtables_cells"] = [list(cells.html_of(t)) for t in cases["pubtables_tables"]]
    out["pubtables_by_page"] = {str(k): v for k, v in cells.tables_by_page(
        cases["pubtables_tables"], floor=1).items()}

    manifest = cases["split_manifest"]
    out["split"] = {}
    for seed in (rule.SEED, cases["split_seed"]):
        tuning, holdout = rule.split(manifest, seed=seed)
        out["split"][seed] = {
            "tuning": [e["name"] for e in tuning], "holdout": [e["name"] for e in holdout],
            "seal_tuning": rule.digest_of(tuning), "seal_holdout": rule.digest_of(holdout),
            "sample": [e["name"] for e in sb.sample(tuning, 5, seed)],
        }
    out["pages_digest"] = sb.pages_digest(manifest)
    out["seeds"] = dict(rule.SEEDS)

    report = pbs._fixture_report([pbs._row(r["test_id"], r["metrics"], r.get("success", True))
                                  for r in cases["parsebench_rows"]])
    out["pb_summary"] = pbs.summarize_report(copy.deepcopy(report))
    out["pb_guards"] = pbs.scan_guards(copy.deepcopy(report))
    evaluation = pbr.evaluation_of(copy.deepcopy(report))
    out["pb_evaluation"] = evaluation
    out["pb_headline"] = {g: pbr.headline_of(g, evaluation) for g in pbr.GROUPS}
    return _round(json.loads(json.dumps(out, sort_keys=True)))


def vendored() -> dict:
    return compute(load_modules(ROOT / "engine"), json.loads(CASES.read_text()))
