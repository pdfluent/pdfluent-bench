# Copyright (c) 2026 Innovation Trigger B.V. All rights reserved.
# Licence pending owner decision: see LICENSE-TBD.md.
"""Paired per-document comparisons: pairing, the interval, and the refusals."""
import json
import pathlib

import pytest

from bench import compare, engine_api


def _page(name: str, document: str, teds: float | None) -> dict:
    return {"name": name, "document": document,
            "teds": {"scored": teds is not None, "teds": teds},
            "words": {"scored": True, "f1": 0.9}}


def _write(root: pathlib.Path, set_name: str, split: str, config: str, tool: str,
           pages: list[dict], digest: str = "d1") -> None:
    path = root / "quality" / set_name / split / f"{config}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"tools": {tool: {"pages": len(pages), "documents": pages}},
                                "bench": {"tool": tool, "pages_digest": digest}}))


def test_pairs_by_document_over_common_documents(tmp_path):
    ours = [_page("a-p1", "a", 0.8), _page("a-p2", "a", 0.6), _page("b-p1", "b", 0.9),
            _page("c-p1", "c", 0.5)]
    ref = [_page("a-p1", "a", 0.5), _page("a-p2", "a", 0.5), _page("b-p1", "b", 0.9),
           _page("c-p1", "c", None)]
    _write(tmp_path, "fintabnet", "holdout", "pdfluent-balanced-tatr", "pdfluent_balanced_tatr", ours)
    _write(tmp_path, "fintabnet", "holdout", "docling-standard", "docling", ref)
    result = compare.build([tmp_path])
    row = next(r for r in result["rows"] if r["axis"] == "teds")
    # documents a (0.7 vs 0.5) and b (0.9 vs 0.9); c is scored for ours only
    assert row["documents"] == 2 and row["only_ours"] == 1 and row["only_ref"] == 0
    assert row["diff"] == pytest.approx(0.1)
    assert row["up"] == 1 and row["tied"] == 1 and row["down"] == 0
    assert row["ci_low"] <= row["diff"] <= row["ci_high"]
    # the engine's bootstrap, the same draws as pb_summary
    pb = engine_api.module("pb_summary")
    boot = pb._bootstrap([0.7 - 0.5, 0.0])
    assert (row["ci_low"], row["ci_high"]) == (boot["ci_low"], boot["ci_high"])
    md = compare.markdown(result)
    assert "## holdout" in md and "+0.100" in md


def test_refuses_columns_that_read_different_pages(tmp_path):
    _write(tmp_path, "doclaynet", "tuning", "pdfluent-fast", "pdfluent_fast",
           [_page("a-p1", "a", 0.5)], digest="x")
    _write(tmp_path, "doclaynet", "tuning", "pymupdf4llm", "pymupdf4llm",
           [_page("a-p1", "a", 0.5)], digest="y")
    result = compare.build([tmp_path])
    assert not result["rows"]
    assert result["refused"] and "not the same pages" in result["refused"][0]


def test_parsebench_examples_are_grouped_into_manifest_documents(tmp_path):
    manifest = json.loads(compare.PARSEBENCH_MANIFEST.read_text())
    by_doc: dict = {}
    for page in manifest["pages"]:
        if page["category"] == "table":
            by_doc.setdefault(page["document"], []).append(page["name"])
    document, names = next((d, n) for d, n in by_doc.items() if len(n) >= 2)
    ids = [n[len("docs/"):-len(".pdf")] for n in names[:2]]

    def run(values):
        per_page = {i: {"success": True, "table_record_match": v} for i, v in zip(ids, values)}
        return {"tools": {"t": {"groups": {"table": {"per_page": per_page}}}},
                "bench": {"tool": "t"}}

    for config, values in (("pdfluent-balanced", [1.0, 0.5]), ("pymupdf4llm", [0.5, 0.5])):
        path = tmp_path / "quality" / "parsebench" / "holdout" / f"{config}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(run(values)))
    result = compare.build([tmp_path])
    row = next(r for r in result["rows"] if r["axis"] == "table.table_record_match")
    assert row["documents"] == 1  # two pages, one document
    assert row["diff"] == pytest.approx(0.25)
    assert not result["notes"], result["notes"]
