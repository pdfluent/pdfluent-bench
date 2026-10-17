#!/usr/bin/env python3
# Copyright (c) 2026 Innovation Trigger B.V.
#
# PDFluent is available under two licences, at your option: the GNU AGPLv3, or
# the PDFluent Commercial Licence. See the LICENSE file in this repository --
# that file travels with the copy you received, which a URL does not.
"""A missing tool is an error, not empty text.

`run_tool` reports a binary that is not on PATH as a crash with no
output, so `_page_text` used to read every page as "" on a machine
without pdftotext -- and the T5 train's attempt 9 (04-10) kept 0 of
264 dev pages that way, every one "not carrying its table's text".
`merge_document` likewise returned False without mutool, dropping
pages with no refusal counted at all. Both refuse now, naming the
tool and the package that carries it.

Hermetic throughout: the tools are fakes on a scratch PATH, and the
absent case is an empty PATH. With the tool present an empty read
stays what it was -- "" from `_page_text`, False from a failing
`merge_document` -- never a refusal.

And a mutation: with either `shutil.which` check taken out, the
absent case returns instead of raising, and this test fails.
"""
from __future__ import annotations

import os
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
REPO = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "scripts" / "extraction"))
sys.path.insert(0, str(REPO / "scripts" / "ci"))

import structured_bench as bench  # noqa: E402


def refused_by(call) -> str | None:
    """The REFUSED message `call` raises, or None when it returns."""
    try:
        call()
    except SystemExit as exc:
        return str(exc.code)
    return None


def main() -> int:
    failures: list[str] = []
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        empty = tmp / "empty"
        empty.mkdir()
        box = tmp / "bin"
        box.mkdir()
        (box / "pdftotext").write_text('#!/bin/sh\necho "Some TABLE text"\n')
        (box / "mutool").write_text("#!/bin/sh\nexit 1\n")
        (box / "pdftotext").chmod(0o755)
        (box / "mutool").chmod(0o755)
        page = tmp / "page.pdf"
        page.write_bytes(b"%PDF-1.4\n")
        out = tmp / "cut.pdf"

        saved = os.environ.get("PATH", "")
        try:
            # No tools on PATH: both refuse, naming tool and package.
            os.environ["PATH"] = str(empty)
            message = refused_by(lambda: bench._page_text(page))
            if message is None:
                failures.append("_page_text without pdftotext returns "
                                "instead of refusing")
            else:
                for needle in ("REFUSED", "pdftotext", "poppler-utils"):
                    if needle not in message:
                        failures.append("the _page_text refusal must name "
                                        f"{needle}: {message!r}")
            message = refused_by(
                lambda: bench.merge_document([str(page), "1"], out))
            if message is None:
                failures.append("merge_document without mutool returns "
                                "instead of refusing")
            else:
                for needle in ("REFUSED", "mutool", "mupdf-tools"):
                    if needle not in message:
                        failures.append("the merge_document refusal must "
                                        f"name {needle}: {message!r}")

            # The tools present: an empty read stays normal, never a
            # refusal -- and ordinary reads still read.
            os.environ["PATH"] = str(box)
            try:
                text = bench._page_text(page)
            except SystemExit as exc:
                failures.append("_page_text with pdftotext present must "
                                f"not refuse: {exc.code!r}")
            else:
                if "table" not in text:
                    failures.append("_page_text must still read the "
                                    f"tool's output: {text!r}")
            (box / "pdftotext").write_text("#!/bin/sh\nexit 0\n")
            (box / "pdftotext").chmod(0o755)
            try:
                text = bench._page_text(page)
            except SystemExit as exc:
                failures.append("a page that reads empty with the tool "
                                f"present must not refuse: {exc.code!r}")
            else:
                if text != "":
                    failures.append("an empty read stays an empty read, "
                                    f"not {text!r}")
            try:
                cut = bench.merge_document([str(page), "1"], out)
            except SystemExit as exc:
                failures.append("merge_document with mutool present must "
                                f"not refuse: {exc.code!r}")
            else:
                if cut is not False:
                    failures.append("a failing mutool stays False, "
                                    f"not {cut!r}")
        finally:
            os.environ["PATH"] = saved

    for line in failures:
        print(f"FAIL {line}", file=sys.stderr)
    if failures:
        return 1
    print("missing tools refuse, naming tool and package; "
          "empty reads with the tools present stay normal")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
