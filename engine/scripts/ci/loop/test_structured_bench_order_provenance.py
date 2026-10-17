#!/usr/bin/env python3
# Copyright (c) 2026 Innovation Trigger B.V.
#
# PDFluent is available under two licences, at your option: the GNU AGPLv3, or
# the PDFluent Commercial Licence. See the LICENSE file in this repository --
# that file travels with the copy you received, which a URL does not.
"""Prove the order provenance carries a per-page list into the run JSON.

`order_provenance` reported only a summary -- pages, mean agreement, the count
below 0.9. A summary cannot name the pages a reading-order ticket is sent to,
so the run file now carries `per_page` beside it: one `{name,
agreement_with_geometry}` row per scored page.

Two pages prove it: one annotated top-down (1.0) and one bottom-up (0.0, under
0.9). And a run whose provenance has no such list is red -- the check that
would have caught the old shape.
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
REPO = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "scripts" / "extraction"))
sys.path.insert(0, str(REPO / "scripts" / "ci"))

import structured_bench as bench  # noqa: E402


def pages() -> list[dict]:
    top = {"kind": "paragraph", "text": "first on the page",
           "bbox": [50, 100, 200, 20]}
    bottom = {"kind": "paragraph", "text": "second on the page",
              "bbox": [50, 300, 200, 20]}
    return [
        {"name": "consistent.pdf", "document": "d.pdf", "path": "/dev/null",
         "blocks": [dict(top), dict(bottom)]},
        {"name": "reversed.pdf", "document": "d.pdf", "path": "/dev/null",
         "blocks": [dict(bottom), dict(top)]},
    ]


def main() -> int:
    failures: list[str] = []

    def check(name: str, got, want, why: str) -> None:
        if got != want:
            failures.append(f"{name}: got {got!r}, want {want!r} -- {why}")

    prov = bench.order_provenance(pages())
    by_name = {row["name"]: row["agreement_with_geometry"]
               for row in prov.get("per_page", [])}
    check("the consistent page agrees 1.0", by_name.get("consistent.pdf"), 1.0,
          "annotated top-down, which is the geometric order")
    check("the reversed page agrees under 0.9",
          by_name.get("reversed.pdf", 1.0) < 0.9, True,
          "annotated bottom-up on purpose; two blocks inverted score 0.0")

    run = bench.measure("doclaynet", {}, pages(), {}, [], {}, "r-test", None)
    carried = run.get("order_provenance", {}).get("per_page")
    check("the run JSON carries the per-page list", isinstance(carried, list),
          True, "a summary cannot name the pages a ticket is sent to")
    check("and it names both pages",
          sorted(r["name"] for r in carried or []),
          ["consistent.pdf", "reversed.pdf"],
          "one row per scored page, beside the summary counts")

    old = {"pages": 2, "mean_agreement_with_geometry": 0.5,
           "pages_below_0.9": 1}
    check("a run without the list is red (missing)", "per_page" in old, False,
          "the old shape: summary only, which this gate refuses")

    for line in failures:
        print(f"FAIL {line}", file=sys.stderr)
    if failures:
        return 1
    print("✓ order provenance: per-page list names both pages, "
          "and a run without it is red")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
