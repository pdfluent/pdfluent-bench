#!/usr/bin/env python3
# Copyright (c) 2026 Innovation Trigger B.V.
#
# PDFluent is available under two licences, at your option: the GNU AGPLv3, or
# the PDFluent Commercial Licence. See the LICENSE file in this repository --
# that file travels with the copy you received, which a URL does not.
"""One command line for every structured extractor on the rig (#566).

`extract_text.py` next door asks each tool for a wall of text. This one asks for
the STRUCTURE -- headings, lists, tables, in the order the tool put them -- and
the interchange format is Markdown, because that is what the tools with
structure already emit and what a RAG pipeline actually consumes.

WHY MARKDOWN AND NOT A SCHEMA OF OUR OWN

A JSON block format would be a better instrument and a worse measurement: every
tool would need an adapter that decided what its output MEANT, and those
decisions would be ours rather than the tool's. Markdown is the format Docling,
Marker and every pipeline in this field publish in, so the mapping is the
tool's own and the harness reads all columns through one reader
(`blocks_of()` in `scripts/ci/loop/structured_bench.py`).

The cost is that a tool is credited only for structure it marks up. That is the
right cost and it is the one that lands on US: our own column is plain text
today, so it scores zero on headings and lists -- which is the baseline #566 was
written to establish, and it must not be softened here.

OCR IS OFF, ON EVERY COLUMN

The same rule as next door and for the same reason: reading pixels is a
different question from reading a document, and a run with OCR on one column
cannot be compared with a column without it. The sets on the rig ship real PDFs
with a text layer (`benchmarks/structured/comparison/DATASETS.toml`), so nothing
here needs it.

Usage:
    extract_structured.py --tool {docling,liteparse,pymupdf4llm,unstructured,mineru,pdfplumber} FILE
    extract_structured.py --self-test
"""
from __future__ import annotations

import argparse
import pathlib
import sys


def batch_docling(paths: list[str], out_dir: str, ocr: bool) -> int:
    """Every page in ONE process, which is how this tool is actually used.

    WHY THIS EXISTS, MEASURED. Driven a page at a time, Docling spent about
    fifteen seconds per page on a cpx42 and the gaps between finished pages said
    where it went: roughly eight to ten seconds of that is loading the layout
    and table models, paid again on every page because every page was a new
    interpreter. Five hundred pages of that is two hours of a paid machine
    measuring Python's import system.

    Nobody runs it that way. A pipeline loads the models once and converts a
    directory, and `convert_all` is the call it uses. So the batch is the
    measurement, and the run file records for each column whether its start-up
    was amortised -- because a wall clock compared across columns with different
    answers to that is not a comparison.
    """
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions

    options = PdfPipelineOptions()
    options.do_ocr = ocr
    options.do_table_structure = True
    converter = DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)}
    )
    out = pathlib.Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    written = 0
    clock = Clock(out)
    # `raises_on_error=False`: one unreadable page must not end a batch of five
    # hundred. A page that produced nothing is a page the harness counts as
    # unanswered, which is the verdict it deserves and not a dead round.
    for result in converter.convert_all([str(p) for p in paths], raises_on_error=False):
        document = getattr(result, "document", None)
        name = pathlib.Path(str(result.input.file)).name
        if document is None:
            clock.lap(name, "crash")
            continue
        (out / f"{name}.md").write_text(document.export_to_markdown())
        clock.lap(name, "ok")
        written += 1
    return written


class Clock:
    """Per-page wall time inside a batch, for the p50/p95 of #621.

    A batch's one clock is its total; the percentiles need the pages. Each lap
    is the time since the previous one, written to `<out>/timings.tsv` as
    name, ms, outcome -- so the first lap carries the model load, which is
    what the harness's separate cold-start reading is compared against.
    """

    def __init__(self, out: pathlib.Path):
        import time

        self._now = time.monotonic
        self._last = self._now()
        self._file = (out / "timings.tsv").open("w")

    def lap(self, name: str, outcome: str) -> None:
        now = self._now()
        self._file.write(f"{name}\t{int((now - self._last) * 1000)}\t{outcome}\n")
        self._file.flush()
        self._last = now


def batch_any(tool: str, paths: list[str], out_dir: str, ocr: bool) -> int:
    """Every page in one process for the other tools, for Docling's reason.

    A page that raises is left unwritten -- counted as unanswered by the
    harness -- and named on stderr, rather than ending the batch.
    """
    out = pathlib.Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    written = 0
    clock = Clock(out)
    for path in paths:
        name = pathlib.Path(path).name
        try:
            markdown = TOOLS[tool](path, ocr)
        except Exception as exc:  # noqa: BLE001 -- one page, not the batch
            print(f"{path}: {type(exc).__name__}: {exc}", file=sys.stderr)
            clock.lap(name, "crash")
            continue
        (out / f"{name}.md").write_text(markdown)
        clock.lap(name, "ok")
        written += 1
    return written


def with_docling(path: str, ocr: bool) -> str:
    """Docling's Markdown export -- its natural output, and the one to grade.

    `export_to_text` is what the plain-text comparison of #390 takes, and taking
    it here would throw away the whole of what this benchmark measures: the
    headings and tables would arrive as paragraphs and Docling would score like
    a plain extractor. So this is the export with the syntax IN it.
    """
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions

    options = PdfPipelineOptions()
    options.do_ocr = ocr
    options.do_table_structure = True
    converter = DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)}
    )
    return converter.convert(path).document.export_to_markdown()


def with_liteparse(path: str, ocr: bool) -> str:
    """LiteParse's own Markdown (#620): `output_format="markdown"`, no images.

    Links off, as LiteParse's own published benchmark runs it (`--no-links`):
    the ground truth is plain text and a URL in brackets is characters the
    page does not hold. OCR is on by default in LiteParse, and off here for
    the reason at the top of this file.
    """
    from liteparse import LiteParse

    parser = _cached("liteparse", lambda: LiteParse(
        output_format="markdown", ocr_enabled=ocr, image_mode="off", extract_links=False))
    return parser.parse(path).text


def with_pymupdf4llm(path: str, ocr: bool) -> str:
    """pymupdf4llm's `to_markdown`, with the layout package it pins installed.

    `pymupdf_layout` is what makes pymupdf4llm a structure tool rather than a
    text dump, and 1.28.2 depends on it, so the default is the layout pipeline.
    Its default OCR mode runs Tesseract on any page it judges to need it, when
    one is installed; `OCRMode.NEVER` makes that the same rule as every other
    column. Its parser messages go to stdout -- a bare `print()` and
    `pymupdf.message` -- and would be scored as the page's first paragraph, so
    both are sent to stderr.
    """
    import contextlib

    import pymupdf
    import pymupdf4llm
    from pymupdf4llm.ocr import OCRMode

    pymupdf.set_messages(fd=2)
    with contextlib.redirect_stdout(sys.stderr):
        return pymupdf4llm.to_markdown(
            path, use_ocr=OCRMode.SELECT_KEEP_OLD if ocr else OCRMode.NEVER)


def with_unstructured(path: str, ocr: bool) -> str:
    """Unstructured's `hi_res` strategy, with tables, as Markdown.

    `auto` picks `fast` (pdfminer, no model) for any page with a text layer,
    which would measure pdfminer a second time under another name. `hi_res`
    with `infer_table_structure` is the pipeline Unstructured sells for
    structure, and the one a buyer comparing it with Docling would run.
    The Markdown is Unstructured's own `element_to_md`: the mapping from its
    element types to syntax is the tool's, not ours. Telemetry is switched
    off by the caller (`DO_NOT_TRACK=1`).
    """
    from unstructured.partition.pdf import partition_pdf
    from unstructured.staging.base import element_to_md

    elements = partition_pdf(path, strategy="hi_res", infer_table_structure=True,
                             languages=["eng"])
    # Its own `element_to_md` per element, joined by a blank line. Its
    # `elements_to_md` joins them with a single newline, which Markdown reads
    # as one paragraph -- every element Unstructured separated would be glued
    # back together and scored as one block. The mapping stays the tool's;
    # only the separator it already meant is made explicit.
    return "\n\n".join(md for md in (element_to_md(e) for e in elements) if md.strip()) + "\n"


def with_mineru(path: str, ocr: bool) -> str:
    """MinerU's `basic` tier through its own library API, Markdown out.

    `standard`, the default, adds a VLM: the GGUF it pulls is a
    personal-account conversion with no licence stated (EXTRACTORS.toml), the
    upstream model is a GPU model, and a cpx42 has no GPU. `basic` is the
    small-model pipeline (layout, table and formula models, no VLM) -- the
    MinerU a CPU buyer would run, and the table says which tier it was.
    `parse_mode="txt"` reads the text layer, which is OCR off; image analysis
    is off because it is a VLM feature. The library call, not the CLI: since
    4.x the CLI talks to a local server that has to be started first.
    """
    from mineru.parser import MinerUParser

    parser = _cached("mineru", lambda: MinerUParser(
        tier="basic", parse_mode="ocr" if ocr else "txt", image_analysis=False))
    return parser.parse(path).markdown()


def with_pdfplumber(path: str, ocr: bool) -> str:
    """The baseline (#620): pdfplumber's tables as pipe rows, its text between.

    pdfplumber has no Markdown of its own, so this is the one column whose
    mapping is ours, and it is kept as plain as a baseline must be: every
    table `find_tables` reports becomes a pipe table, first row as header,
    and the words outside the tables are `extract_text()` paragraphs split on
    blank lines. No headings and no lists, because pdfplumber does not claim
    to find them -- inferring them here would credit the adapter, not the tool.
    """
    import pdfplumber

    parts: list[str] = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            tables = page.find_tables()
            outside = page
            for table in tables:
                outside = outside.outside_bbox(table.bbox)
            text = outside.extract_text() or ""
            parts.extend(chunk.strip() for chunk in text.split("\n\n") if chunk.strip())
            for table in tables:
                rows = [[(cell or "").replace("\n", " ").replace("|", "\\|") for cell in row]
                        for row in table.extract() if row]
                if not rows:
                    continue
                width = max(len(r) for r in rows)
                rows = [r + [""] * (width - len(r)) for r in rows]
                lines = ["| " + " | ".join(rows[0]) + " |",
                         "|" + "---|" * width]
                lines += ["| " + " | ".join(r) + " |" for r in rows[1:]]
                parts.append("\n".join(lines))
    return "\n\n".join(parts) + "\n"


_CACHE: dict = {}


def _cached(name: str, build):
    """One parser per process, so a batch loads a tool once, as a pipeline does."""
    if name not in _CACHE:
        _CACHE[name] = build()
    return _CACHE[name]


def _teacher(path: str, cache: str, env: str, default: str) -> str:
    import os
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    from teacher_page import Teacher

    repo = pathlib.Path(__file__).resolve().parents[3]
    # The table grids are OUR engine's (teacher_page.grid_matcher, then --region); without the binary
    # an unmatched table is one cell.
    grids = os.environ.get("STRUCTURED_MARKDOWN", str(repo / "target" / "release" / "examples" / "structured_markdown"))
    # The detector reads OUR pixels (teacher_page R-1): the render binary beside structured_markdown,
    # from the same place (env, else the release build). No fallback to another renderer exists.
    render = os.environ.get("RENDER_PAGE_PNG", str(repo / "target" / "release" / "examples" / "render_page_png"))
    teacher = _cached(cache, lambda: Teacher(
        os.environ.get(env, default),
        os.environ.get("FEATURE_DUMP", str(repo / "target" / "release" / "feature_dump")),
        device="cpu", repo=repo, structured_markdown=grids if pathlib.Path(grids).is_file() else None,
        render_page_png=render if pathlib.Path(render).is_file() else None))
    return teacher.markdown_of(path)


def with_teacher_ep03(path: str, ocr: bool) -> str:
    """The same teacher after 3 more epochs (#641): better on tables, weaker on headings on the proxy.

    A second column so the rig can say whether headings from `teacher` plus tables from this one is
    the teacher to distil. Weights in TEACHER_EP03_MODEL (default /opt/ex/teacher/model-ep03).
    """
    return _teacher(path, "teacher_ep03", "TEACHER_EP03_MODEL", "/opt/ex/teacher/model-ep03")


def with_teacher(path: str, ocr: bool) -> str:
    """The #641 teacher: a layout detector (Heron-101 fine-tuned on DocLayNet /train) plus OUR PDF text.

    `teacher_page.py` does the work; the model loads once per batch through `_cached`. Where the weights and
    the feature dump live is the machine's: TEACHER_MODEL (default /opt/ex/teacher/model, fetched and
    sha256-checked by structured_bench_remote.sh) and FEATURE_DUMP (default this tree's release build). CPU
    only; nothing is written outside the batch's out-dir. `ocr` is ignored: the column reads the text layer.
    """
    return _teacher(path, "teacher", "TEACHER_MODEL", "/opt/ex/teacher/model")


def with_teacher_ens(path: str, ocr: bool) -> str:
    """The strongest teacher (#641): Heron-ft ep03 plus Egret-ft ep00, per-class thresholds, union.

    `teacher_page.EnsembleTeacher` does the work; both models load once per batch through `_cached`.
    Weights in TEACHER_ENS_MODEL_A (default /opt/ex/teacher/model-ep03) and TEACHER_ENS_MODEL_B
    (default /opt/ex/teacher/model-egret), fetched and sha256-checked by structured_bench_remote.sh.
    """
    import os
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    from teacher_page import EnsembleTeacher

    repo = pathlib.Path(__file__).resolve().parents[3]
    grids = os.environ.get("STRUCTURED_MARKDOWN", str(repo / "target" / "release" / "examples" / "structured_markdown"))
    render = os.environ.get("RENDER_PAGE_PNG", str(repo / "target" / "release" / "examples" / "render_page_png"))
    teacher = _cached("teacher_ens", lambda: EnsembleTeacher(
        os.environ.get("TEACHER_ENS_MODEL_A", "/opt/ex/teacher/model-ep03"),
        os.environ.get("TEACHER_ENS_MODEL_B", "/opt/ex/teacher/model-egret"),
        os.environ.get("FEATURE_DUMP", str(repo / "target" / "release" / "feature_dump")),
        device="cpu", repo=repo, structured_markdown=grids if pathlib.Path(grids).is_file() else None,
        render_page_png=render if pathlib.Path(render).is_file() else None,
        # chosen on the proxy TUNING half (m160 report: heading F1 0.8958 -> 0.9017)
        short_threshold=0.10, leftover_rows=False, furniture_band=None, furniture_duel=False,
        orphan_tables=True, engine_tags=True, merge_style_runs=True, engine_text=True))
    return teacher.markdown_of(path)


# --- pdfluent-bench: bench columns, see bench_columns.py ---
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[4] / "bench" / "adapters"))
import bench_columns  # noqa: E402


TOOLS = {
    "docling": with_docling,
    "teacher": with_teacher,
    "teacher_ep03": with_teacher_ep03,
    "teacher_ens": with_teacher_ens,
    "liteparse": with_liteparse,
    "pymupdf4llm": with_pymupdf4llm,
    "unstructured": with_unstructured,
    "mineru": with_mineru,
    "pdfplumber": with_pdfplumber,
    **bench_columns.TOOLS,  # pdfluent-bench:
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tool", choices=sorted(TOOLS))
    parser.add_argument("--ocr", action="store_true",
                        help="read pixels as well; a run with this on is not "
                             "comparable with one without it")
    parser.add_argument("--batch", metavar="LISTFILE",
                        help="a file of paths, one per line: convert them all in "
                             "ONE process and write <out-dir>/<name>.md per page")
    parser.add_argument("--out-dir", help="where a batch writes its markdown")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("file", nargs="?")
    return parser


def self_test() -> int:
    """The contract, checkable on a machine with none of the tools installed."""
    failures = []
    if set(TOOLS) != {"docling", "liteparse", "pymupdf4llm", "unstructured", "mineru",
                      "pdfplumber", "teacher", "teacher_ep03", "teacher_ens",
                      *bench_columns.TOOLS}:  # pdfluent-bench:
        failures.append(f"the tool table has drifted: {sorted(TOOLS)}")
    parser = build_parser()
    known = parser.parse_args(["--tool", "docling", "x.pdf"])
    if known.tool != "docling" or known.file != "x.pdf" or known.ocr:
        failures.append("the argument surface has changed")
    batched = parser.parse_args(["--tool", "docling", "--batch", "l.txt",
                                 "--out-dir", "/tmp/x"])
    if batched.batch != "l.txt" or batched.out_dir != "/tmp/x":
        failures.append("the batch surface has changed")
    if known.ocr:
        failures.append("OCR must be off unless it is asked for")
    # THE BEST TIER RUNS SHORT HEADINGS AT 0.10: the teacher_ens column pins its tuning kwargs
    # explicitly, so a default drift cannot silently change the rig. Faked: no weights, no torch.
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    import teacher_page
    seen: dict = {}

    class _FakeEnsemble:
        def __init__(self, *args, **kwargs):
            seen.update(kwargs)

        def markdown_of(self, path):
            return ""

    orig = teacher_page.EnsembleTeacher
    teacher_page.EnsembleTeacher = _FakeEnsemble
    _CACHE.pop("teacher_ens", None)
    try:
        with_teacher_ens("x.pdf", False)
    finally:
        teacher_page.EnsembleTeacher = orig
        _CACHE.pop("teacher_ens", None)
    want = {"short_threshold": 0.10, "leftover_rows": False,
            "furniture_band": None, "furniture_duel": False, "orphan_tables": True,
            "engine_tags": True, "merge_style_runs": True, "engine_text": True}
    got = {k: seen.get(k, "<missing>") for k in want}
    if got != want:
        failures.append(f"teacher_ens kwargs have drifted: {got}")
    # THE BEST TIER RENDERS WITH OUR ENGINE (R-1): the column passes the render binary through;
    # its value depends on the build, so only its presence is pinned here.
    if "render_page_png" not in seen:
        failures.append("teacher_ens must pass render_page_png through to EnsembleTeacher")
    for problem in failures:
        print(f"FAIL {problem}")
    if failures:
        return 1
    print(f"OK self-test: {len(TOOLS)} structured tool(s), one command line, "
          "Markdown out, OCR off")
    return 0


def main() -> int:
    args = build_parser().parse_args()
    if args.self_test:
        return self_test()
    if args.batch:
        if not args.out_dir:
            print("--batch needs --out-dir", file=sys.stderr)
            return 2
        paths = [line.strip() for line in
                 pathlib.Path(args.batch).read_text().splitlines() if line.strip()]
        written = (batch_docling(paths, args.out_dir, args.ocr) if args.tool == "docling"
                   else bench_columns.batch_docling_minimal(paths, args.out_dir, args.ocr, Clock)
                   if args.tool == "docling_minimal"  # pdfluent-bench:
                   else batch_any(args.tool, paths, args.out_dir, args.ocr))
        print(f"{written} of {len(paths)} page(s) converted", file=sys.stderr)
        return 0
    if not args.tool or not args.file:
        print("usage: extract_structured.py --tool TOOL FILE", file=sys.stderr)
        return 2
    # No try/except, for the reason extract_text.py gives: a tool that raises
    # has failed on the document, and swallowing it here would turn every
    # failure into an empty page, which scores as a page the tool read and found
    # nothing on. Those are different defects and the run file tells them apart.
    sys.stdout.write(TOOLS[args.tool](args.file, args.ocr))
    return 0


if __name__ == "__main__":
    sys.exit(main())
