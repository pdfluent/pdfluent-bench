# Copyright (c) 2026 Innovation Trigger B.V. All rights reserved.
# Licence pending owner decision: see LICENSE-TBD.md.
"""The vendored scorers give the engine's own answers on the fixture cases.

fixtures/scoring/golden.json was written by scripts/make_goldens.py from the
ORIGINAL engine files (read with git show at the commit it names). Every
scorer output here must match it exactly: blocks read from markdown, every
per-page axis, the run-file summaries, document means, tails, TEDS, the split
and its seals, and the ParseBench summary and headline.
"""
import json
import os
import pathlib

from bench import equivalence
from skipping import skip

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _golden() -> dict:
    golden = json.loads(equivalence.GOLDEN.read_text())
    golden.pop("_engine_ref")
    return golden


def test_vendored_modules_are_the_ones_measured():
    mods = equivalence.load_modules(ROOT / "engine")
    for name, module in mods.items():
        assert pathlib.Path(module.__file__).is_relative_to(ROOT / "engine"), name
    assert pathlib.Path(mods["structured_bench"].axes.__file__).is_relative_to(ROOT / "engine")


def test_golden_names_the_engine_commit_vendored():
    golden = json.loads(equivalence.GOLDEN.read_text())
    provenance = json.loads((ROOT / "engine" / "PROVENANCE.json").read_text())
    assert golden["_engine_ref"] == provenance["engine_ref"]


def test_vendored_scorers_match_engine_goldens():
    got = equivalence.vendored()
    want = _golden()
    assert set(got) == set(want)
    for key in want:
        assert got[key] == want[key], f"scorer output differs from the engine in `{key}`"


def test_cases_exercise_every_axis():
    golden = _golden()
    scored = set()
    for rows in golden["pages"].values():
        for row in rows:
            scored |= {axis for axis, cell in row.items()
                       if isinstance(cell, dict) and cell.get("scored")}
    for axis in ("text", "text_geometric", "words", "headings", "lists", "tables", "teds",
                 "order", "order_geometric", "text_v2", "words_v2", "headings_v2", "lists_v2"):
        assert axis in scored, f"no fixture case scores `{axis}`"


def test_against_a_live_engine_tree():
    """Optional: BENCH_ENGINE_SRC names a directory of original engine files."""
    src = os.environ.get("BENCH_ENGINE_SRC")
    if not src:
        skip("BENCH_ENGINE_SRC is not set; the committed golden file stands in for the engine")
    cases = json.loads(equivalence.CASES.read_text())
    live = equivalence.compute(equivalence.load_modules(pathlib.Path(src)), cases)
    assert live == equivalence.vendored()
