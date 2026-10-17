#!/usr/bin/env python3
# Copyright (c) 2026 Innovation Trigger B.V.
#
# PDFluent is available under two licences, at your option: the GNU AGPLv3, or
# the PDFluent Commercial Licence. See the LICENSE file in this repository --
# that file travels with the copy you received, which a URL does not.
"""One command line for every Python extractor on the rig (#390).

`mutool` and `pdftotext` print a document's text to standard output when asked;
the Python libraries want three lines of code each. This file is those three
lines, so that every column of the comparison is produced by the same shape of
command and the run file can record a command a reader can retype.

WHY EACH TOOL RUNS IN ITS OWN VIRTUALENV

The recipes in `benchmarks/extraction/comparison/EXTRACTORS.toml` name an
interpreter per tool (`/opt/ex/<tool>/bin/python`) rather than one environment
with everything installed. Docling pins versions of numpy, pillow and torch that
the light readers do not, and a single resolver would silently decide which
release of each library got measured. Four environments cost disk; one
environment costs the ability to say what ran.

WHAT THIS DOES NOT DO

It does not time anything and it does not judge anything. The harness measures
wall time around this process, identically for every column including the two
that need no adapter, so that the fixed cost of starting an interpreter lands in
the column that pays it. A tool that needs two seconds of imports before it
reads a byte HAS that cost, and hiding it inside the adapter would be measuring
a program nobody runs.

PAGE SEPARATORS. Where the tool exposes pages, the pages are joined with a form
feed (U+000C), which is what `pdftotext` and `mutool` emit. The order axis can
then be read per page as well as per document, which is the difference between
"98% of the pairs are in order" and "every page is scrambled and the pages are
in the right order" (#389).

Usage:
    extract_text.py --tool pypdf|pymupdf|pdfminer|docling [--no-ocr] FILE
    extract_text.py --self-test
"""
from __future__ import annotations

import argparse
import sys

FF = "\f"


def with_pypdf(path: str) -> str:
    from pypdf import PdfReader

    reader = PdfReader(path)
    return FF.join((page.extract_text() or "") for page in reader.pages)


def with_pymupdf(path: str) -> str:
    import pymupdf

    with pymupdf.open(path) as doc:
        return FF.join(page.get_text() for page in doc)


def with_pdfminer(path: str) -> str:
    from pdfminer.high_level import extract_text

    # pdfminer already separates pages with a form feed of its own.
    return extract_text(path)


def with_docling(path: str, ocr: bool) -> str:
    """Docling's plain-text export.

    Markdown is its natural output and the axes here read plain text, so the
    export is the one that drops the syntax rather than the one that keeps it:
    counting `##` and `|` as characters the document did not contain would
    penalise the tool for doing its job.

    OCR is off by default here and the recipe says so on the command line. With
    OCR on, Docling reads the pixels of a page whose text layer it could not
    parse, which is a different question from the one this table asks -- the
    same reason `--skip-text` is in the OCRmyPDF recipe next door. A run with
    OCR on cannot be compared with these columns.
    """
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions

    options = PdfPipelineOptions()
    options.do_ocr = ocr
    converter = DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)}
    )
    document = converter.convert(path).document
    export = getattr(document, "export_to_text", None)
    return export() if callable(export) else document.export_to_markdown()


TOOLS = {
    "pypdf": lambda path, ocr: with_pypdf(path),
    "pymupdf": lambda path, ocr: with_pymupdf(path),
    "pdfminer": lambda path, ocr: with_pdfminer(path),
    "docling": with_docling,
}


def self_test() -> int:
    """The contract this file has to keep, checkable without any of the tools.

    Nothing here imports an extractor: the point is that the argument surface
    and the failure behaviour are the same for every column, and that is what a
    run on a machine without pypdf installed can still check.
    """
    failures = []
    if set(TOOLS) != {"pypdf", "pymupdf", "pdfminer", "docling"}:
        failures.append(f"the tool table has drifted: {sorted(TOOLS)}")
    if FF != "\x0c":
        failures.append("the page separator must be a form feed")
    parser = build_parser()
    known = parser.parse_args(["--tool", "pypdf", "x.pdf"])
    if known.tool != "pypdf" or known.file != "x.pdf" or known.no_ocr:
        failures.append("the argument surface has changed")
    for problem in failures:
        print(f"FAIL {problem}")
    if failures:
        return 1
    print(f"OK self-test: {len(TOOLS)} tools, one command line, form-feed pages")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tool", choices=sorted(TOOLS))
    parser.add_argument("--no-ocr", action="store_true",
                        help="Docling only: do not read pixels, only the text layer")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("file", nargs="?")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.self_test:
        return self_test()
    if not args.tool or not args.file:
        print("usage: extract_text.py --tool TOOL [--no-ocr] FILE", file=sys.stderr)
        return 2
    # No try/except around the extraction. A tool that raises has failed to
    # parse the document, the harness records that as the `parses` axis going
    # red, and swallowing the exception here would turn every failure into an
    # empty string -- which scores as a parse that found no text, and is a
    # different defect.
    text = TOOLS[args.tool](args.file, not args.no_ocr)
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
