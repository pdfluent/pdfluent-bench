# Copyright (c) 2026 Innovation Trigger B.V.
#
# PDFluent is available under two licences, at your option: the GNU AGPLv3, or
# the PDFluent Commercial Licence. See the LICENSE file in this repository --
# that file travels with the copy you received, which a URL does not.
"""Hand-checked values for each scorer, independent of the golden file.

The golden test proves the vendored scorers equal the engine's. These pin
what the numbers mean, so a scorer that is broken in both places at once, or
a golden file regenerated from a broken tree, still fails here.
"""
import pathlib

import pytest

from bench import equivalence

ROOT = pathlib.Path(__file__).resolve().parents[1]
MODS = equivalence.load_modules(ROOT / "engine")
SB, AXES, RULE, PBS = (MODS["structured_bench"], MODS["structured_axes"],
                       MODS["structured_holdout_rule"], MODS["pb_summary"])

TABLE = ("<table><tr><th>Year</th><th>Total</th></tr>"
         "<tr><td>2024</td><td>12</td></tr><tr><td>2025</td><td>14</td></tr></table>")


def test_teds_is_one_for_the_same_table_and_less_for_another():
    assert AXES.teds(TABLE, TABLE) == 1.0
    other = "<table><tr><td>Year</td></tr><tr><td>2024 12</td></tr></table>"
    assert 0.0 <= AXES.teds(TABLE, other) < 0.6
    assert AXES.teds_struct(TABLE, TABLE) == 1.0


def test_table_structure_pairs_tables_and_counts_a_missing_one_as_zero():
    one = AXES.table_structure_score([TABLE], [TABLE])
    assert one["scored"] and one["teds"] == 1.0
    missing = AXES.table_structure_score([TABLE, TABLE], [TABLE])
    assert missing["teds"] == 0.5


def test_category_f1():
    row = AXES.category_score(["Annual Report", "Outlook"], ["Annual Report", "Outlook", "Extra"])
    assert row["matched"] == 2
    assert row["precision"] == pytest.approx(2 / 3, abs=1e-6)
    assert row["recall"] == 1.0
    assert row["f1"] == pytest.approx(0.8, abs=1e-6)
    assert AXES.category_score([], ["x"])["scored"] is False


def test_reading_order_reversed_is_zero_and_identical_is_one():
    items = ["alpha one", "beta two", "gamma three"]
    assert AXES.reading_order_score(items, items)["score"] == 1.0
    assert AXES.reading_order_score(items, list(reversed(items)))["score"] == 0.0


def test_text_similarity_and_its_floor():
    text = "The quick brown fox jumps over the lazy dog near the river bank today."
    assert AXES.text_score(text, text)["similarity"] == 1.0
    worse = AXES.text_score(text, text.replace("quick brown", "slow"))["similarity"]
    assert 0.5 < worse < 1.0
    assert AXES.text_score("short", "short")["scored"] is False


def test_words_axis():
    same = AXES.bag_of_words_score(["the cat sat on the mat"], ["the cat sat on the mat"])
    assert same["f1"] == 1.0
    half = AXES.bag_of_words_score(["alpha beta gamma delta"], ["alpha beta"])
    assert half["precision"] == 1.0 and half["recall"] == 0.5


def test_normalise():
    assert AXES.normalise("  a ﬁne\tday ") == "a fine day"
    assert AXES.normalise("x$_{1}$") == "x1"
    assert AXES.normalise("Case Kept") == "Case Kept"


def test_blocks_of_reads_markdown_literally():
    blocks = SB.blocks_of("# Title\n\nSome text\nmore text\n\n- item one\n    - nested\n\n"
                          "| A | B |\n|---|---|\n| 1 | 2 |\n\n<!-- image -->\n")
    kinds = [b["kind"] for b in blocks]
    assert kinds == ["heading", "paragraph", "list_item", "list_item", "table"]
    assert blocks[1]["text"] == "Some text more text"
    assert blocks[4]["html"] == "<table><tr><th>A</th><th>B</th></tr><tr><td>1</td><td>2</td></tr></table>"
    assert blocks[4]["text"] == "A B 1 2"


def test_score_page_scores_only_what_the_set_annotates():
    page = {"name": "p.pdf", "document": "p.pdf", "annotates": ["table"],
            "blocks": [{"kind": "table", "text": "Year Total 2024 12 2025 14", "html": TABLE}]}
    row = SB.score_page(page, SB.blocks_of(TABLE))
    assert row["teds"]["teds"] == 1.0
    assert row["text"]["scored"] is False and row["headings"]["scored"] is False


def test_document_means_weigh_each_document_once():
    rows = [{"name": "a1", "document": "a", **{ax: {"scored": False} for ax, _k, _l in SB.AXES}},
            {"name": "a2", "document": "a", **{ax: {"scored": False} for ax, _k, _l in SB.AXES}},
            {"name": "b1", "document": "b", **{ax: {"scored": False} for ax, _k, _l in SB.AXES}}]
    for row, value in zip(rows, (1.0, 0.0, 1.0)):
        row["teds"] = {"scored": True, "teds": value}
    assert SB.macro_by_document(rows)["teds"] == {"mean": 0.75, "documents": 2}


def test_split_is_by_document_seeded_and_at_most_the_share():
    entries = [{"source": "s", "name": f"d{d}-p{p}", "document": f"d{d}", "sha256": f"{d}{p}"}
               for d in range(50) for p in range(1 + d % 4)]
    tuning, holdout = RULE.split(entries, seed="seed-a")
    again = RULE.split(entries, seed="seed-a")
    assert [e["name"] for e in holdout] == [e["name"] for e in again[1]]
    assert len(holdout) <= RULE.HOLDOUT_SHARE * len(entries)
    assert {e["document"] for e in tuning}.isdisjoint({e["document"] for e in holdout})
    assert len(tuning) + len(holdout) == len(entries)
    assert RULE.digest_of(holdout) == RULE.digest_of(again[1])
    other = RULE.split(entries, seed="seed-b")[1]
    assert [e["name"] for e in other] != [e["name"] for e in holdout]


def test_engine_seeds_are_the_published_ones():
    assert RULE.SEEDS == {
        "doclaynet": "pdfluent-structured-holdout-2026-09-17",
        "pubtables-1m": "pdfluent-structured-tables-holdout-2026-09-20",
        "parsebench": "pdfluent-structured-parsebench-holdout-2026-09-23",
        "fintabnet": "pdfluent-structured-fintabnet-holdout-2026-09-30",
    }


def test_parsebench_summary_is_survivor_only():
    report = PBS._fixture_report([PBS._row("a", {"grits_trm_composite": 0.8}),
                                  PBS._row("b", {"grits_trm_composite": 0.6}),
                                  PBS._row("c", {}, success=False)])
    summary = PBS.summarize_report(report)
    assert summary["metrics"]["grits_trm_composite"]["mean"] == pytest.approx(0.7)
    assert summary["metrics"]["grits_trm_composite"]["n_scored"] == 2
