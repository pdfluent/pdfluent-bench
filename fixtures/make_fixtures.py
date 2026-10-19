#!/usr/bin/env python3
# Copyright (c) 2026 Innovation Trigger B.V.
#
# PDFluent is available under two licences, at your option: the GNU AGPLv3, or
# the PDFluent Commercial Licence. See the LICENSE file in this repository --
# that file travels with the copy you received, which a URL does not.
"""Write the tiny fixture set: five one-page PDFs and their ground truth.

    python fixtures/make_fixtures.py        (needs pymupdf; the output is committed)

The pages are synthetic and drawn here, so they carry no third-party content
and no licence question. The ground truth uses the same page format as the
ported harness (`blocks` with kind, text, order, bbox; `html` for a table with
cell structure), and fixtures/MANIFEST.json splits the set with the engine's
rule under its own seed, so the smoke run exercises tuning and holdout apart.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import sys

import pymupdf

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
PDF = HERE / "pdf"
SEED = "pdfluent-bench-fixtures-2026-10-17"

PAGES: list[dict] = []


def page(name: str, document: str, category: str, annotates: list[str] | None = None):
    doc = pymupdf.open()
    pg = doc.new_page(width=595, height=842)
    blocks: list[dict] = []

    def text(kind: str, body: str, x: float, y: float, size: float = 10.5, width: float = 495,
             bold: bool = False) -> None:
        rect = pymupdf.Rect(x, y, x + width, y + 400)
        font = "hebo" if bold else "helv"
        left = pg.insert_textbox(rect, body, fontsize=size, fontname=font)
        used = 400 - left if left >= 0 else 400
        if kind == "list_item":  # the bullet is drawn, not part of the block's text
            pg.insert_text((x - 10, y + size), "\u2022", fontsize=size, fontname="helv")
        blocks.append({"kind": kind, "text": body, "order": len(blocks),
                       "bbox": [round(x, 1), round(y, 1), round(width, 1), round(used, 1)]})

    def table(rows: list[list[str]], x: float, y: float, widths: list[float],
              height: float = 18) -> None:
        total = sum(widths)
        for r, row in enumerate(rows):
            cx = x
            for c, cell in enumerate(row):
                rect = pymupdf.Rect(cx, y + r * height, cx + widths[c], y + (r + 1) * height)
                pg.draw_rect(rect, width=0.5)
                pg.insert_text((cx + 4, y + r * height + 13), cell, fontsize=9.5,
                               fontname="hebo" if r == 0 else "helv")
                cx += widths[c]
        head = "".join(f"<th>{c}</th>" for c in rows[0])
        body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in rows[1:])
        blocks.append({"kind": "table", "text": " ".join(c for row in rows for c in row),
                       "order": len(blocks), "html": f"<table><tr>{head}</tr>{body}</table>",
                       "bbox": [x, y, total, height * len(rows)]})

    yield text, table
    path = PDF / name
    doc.set_metadata({"producer": "pdfluent-bench fixtures", "creator": "",
                      "creationDate": "", "modDate": ""})
    doc.save(path, garbage=4, deflate=True, no_new_id=True)
    data = path.read_bytes()
    row = {"source": f"fixtures/{category}", "name": name, "document": document,
           "page_no": 1, "category": category, "sha256": hashlib.sha256(data).hexdigest(),
           "path": f"fixtures/pdf/{name}", "blocks": blocks}
    if annotates:
        row["annotates"] = annotates
    PAGES.append(row)


def build() -> None:
    PDF.mkdir(parents=True, exist_ok=True)
    for text, table in page("report-p1.pdf", "report.pdf", "financial_reports"):
        text("furniture", "Northwind Holdings annual report 2025", 50, 30, size=8)
        text("heading", "Annual Report 2025", 50, 70, size=20, bold=True)
        text("paragraph", "Northwind grew revenue in every segment during the year. Retail demand "
             "was strong in the second half, and lower input costs lifted the operating margin "
             "across the group.", 50, 110)
        text("list_item", "Revenue rose to 1,450 million.", 62, 175)
        text("list_item", "The operating margin improved to 14 percent.", 62, 192)
        table([["Year", "Revenue", "Margin"], ["2024", "1,200", "12%"], ["2025", "1,450", "14%"]],
              50, 230, [120, 120, 120])
        text("furniture", "Page 3", 280, 800, size=8, width=60)
    for text, _table in page("report-p2.pdf", "report.pdf", "financial_reports"):
        text("heading", "Outlook", 50, 70, size=16, bold=True)
        text("paragraph", "The left column is read first. Management expects demand to stay firm "
             "and plans to open four new stores in the coming year.", 50, 110, width=230)
        text("paragraph", "The right column is read second. Capital spending will rise to fund "
             "the new stores and a second distribution centre.", 315, 110, width=230)
    for _text, table in page("tables-p1.pdf", "tables.pdf", "tables", annotates=["table"]):
        table([["Segment", "Revenue", "Share"], ["Retail", "1,020", "70%"],
               ["Wholesale", "290", "20%"], ["Online", "140", "10%"]], 50, 100, [150, 110, 110])
    for text, _table in page("memo-p1.pdf", "memo.pdf", "government"):
        text("heading", "Annual plan of the municipality", 50, 70, size=16, bold=True)
        text("paragraph", "The council will spend more on schools and on care for older residents "
             "than in previous years, and it will publish a progress report every quarter.", 50, 105)
        text("furniture", "Page 1", 280, 800, size=8, width=60)
    for text, _table in page("paper-p1.pdf", "paper.pdf", "scientific_articles"):
        text("heading", "2 Related work", 50, 70, size=14, bold=True)
        text("paragraph", "Earlier systems extract structure from documents with layout models "
             "and hand-written rules. Evaluating them needs reference annotations drawn by "
             "people, page by page.", 50, 100)
        text("list_item", "Layout detection with a trained model.", 62, 160)
        text("list_item", "Table structure recognition from cell boxes.", 62, 177)


def main() -> int:
    build()
    sys.path.insert(0, str(ROOT / "engine" / "scripts" / "ci"))
    import structured_holdout_rule as rule

    tuning, holdout = rule.split(PAGES, share=0.4, seed=SEED)
    held = {p["name"] for p in holdout}
    manifest = {
        "set": "fixtures", "version": "synthetic, fixtures/make_fixtures.py", "revision": "",
        "source": "fixtures/make_fixtures.py", "licence": "synthetic; part of this repository",
        "seed": SEED, "rule": "engine/scripts/ci/structured_holdout_rule.py", "share": 0.4,
        "counts": {"pages": len(PAGES), "tuning": len(tuning), "holdout": len(holdout),
                   "documents": len(rule.documents_of(PAGES))},
        "seal": {"tuning": rule.digest_of(tuning), "holdout": rule.digest_of(holdout)},
        "pages": [{k: p[k] for k in ("source", "name", "document", "page_no", "category", "sha256")}
                  | {"split": "holdout" if p["name"] in held else "tuning"} for p in PAGES],
    }
    (HERE / "MANIFEST.json").write_text(json.dumps(manifest, indent=1) + "\n")
    (HERE / "pages.json").write_text(json.dumps(PAGES, indent=1) + "\n")
    print(f"[fixtures] {len(PAGES)} page(s): tuning {len(tuning)}, holdout {len(holdout)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
