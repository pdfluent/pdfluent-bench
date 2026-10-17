#!/usr/bin/env python3
# Copyright (c) 2026 Innovation Trigger B.V.
#
# PDFluent is available under two licences, at your option: the GNU AGPLv3, or
# the PDFluent Commercial Licence. See the LICENSE file in this repository --
# that file travels with the copy you received, which a URL does not.
"""Every column of our register through ParseBench's own scorer (#623).

ParseBench (Apache-2.0, `parse-bench` on PyPI) ships a provider per tool, and
they are not one measurement: its pymupdf4llm pipeline runs OCR, its
Unstructured pipeline calls a hosted API, its MinerU pipelines need a GPU. A
table in which each column was configured by somebody else, differently, is a
table of configurations.

So this registers ONE provider, `pdfluent_harness`, and one pipeline per tool
on the structured rig, and every one of them does the same thing: hand
ParseBench the Markdown that tool already wrote for the page, in the batch our
DocLayNet and PubTables rounds run (`structured_bench.measure_batch`, OCR off;
our own column one process per page, as there). The scoring is ParseBench's,
untouched, which is what makes the numbers ParseBench numbers; the
configuration is ours and the same for every column, which is what makes them a
comparison.

WHY A REPLAY AND NOT A COMMAND PER PAGE. ParseBench calls its provider once per
page, and a fresh interpreter per page is Docling's model load, and MinerU's,
paid again on every one of them -- the #566 lesson, where a page-at-a-time
Docling spent two thirds of its clock importing. So the tools run as they do in
the other two sets, and the provider reads what they wrote: `<markdown
dir>/docs/<ParseBench's page id>.pdf.md`, the page's own path plus `.md`. A page with no file is a page
the tool did not answer, and ParseBench records it as a failure, which is the
verdict it deserves.

TABLES ARE HANDED OVER AS HTML. ParseBench's table metrics read `<table>`
blocks only; its own LiteParse provider converts pipe tables at this boundary
with markdown2, and so does this, for every column alike -- the same function,
imported, not a copy.

Usage (in the venv that has parse-bench):
    parsebench_harness.py --tool liteparse --markdown-dir MD/liteparse \\
        --input-dir CASES --output-dir OUT [--group table]
    parsebench_harness.py --self-test
"""
from __future__ import annotations

import argparse
import os
import pathlib
import sys
from datetime import datetime

PROVIDER = "pdfluent_harness"


def markdown_path(markdown_dir: str, example_id: str) -> pathlib.Path:
    """Where the batch left the page's Markdown: `docs/<example id>.pdf.md`.

    ParseBench names a page `<category dir>/<stem>` (`text/text_ocr__p4013` is
    `docs/text/text_ocr__p4013.pdf`), and that id is the key and not the file it
    hands the provider: `randomize_external_filenames` copies the page to a
    temporary name first, which failed every page of the first replay while a
    command-per-page smoke run had passed. By id and not by file name for a
    second reason: the same page sits in two categories under one name (a chart
    page is often a layout page too), and a replay keyed on the name alone would
    hand one category the other's answer.
    """
    if not example_id or ".." in example_id.split("/"):
        raise ValueError(f"not a ParseBench page id: {example_id!r}")
    return pathlib.Path(markdown_dir) / "docs" / f"{example_id}.pdf.md"


def register(tool: str, markdown_dir: str) -> str:
    """Register the provider (once) and this tool's pipeline; returns its name."""
    from parse_bench.inference.pipelines import get_pipeline, register_pipeline
    from parse_bench.inference.providers.base import Provider, ProviderPermanentError
    from parse_bench.inference.providers.parse.liteparse import LiteParseProvider
    from parse_bench.inference.providers.registry import register_provider
    from parse_bench.schemas.parse_output import PageIR, ParseOutput
    from parse_bench.schemas.pipeline import PipelineSpec
    from parse_bench.schemas.pipeline_io import InferenceResult, RawInferenceResult
    from parse_bench.schemas.product import ProductType

    if not getattr(register, "_done", False):
        @register_provider(PROVIDER)
        class HarnessProvider(Provider):
            """The Markdown the tool's batch wrote for this page, read back."""

            def run_inference(self, pipeline, request):
                started = datetime.now()
                written = markdown_path(pipeline.config["markdown_dir"], request.example_id)
                if not written.is_file():
                    raise ProviderPermanentError(f"the tool wrote nothing for {written.name}")
                markdown = written.read_text(encoding="utf-8", errors="replace")
                completed = datetime.now()
                return RawInferenceResult(
                    request=request, pipeline=pipeline, pipeline_name=pipeline.pipeline_name,
                    product_type=request.product_type,
                    raw_output={"markdown": markdown},
                    started_at=started, completed_at=completed,
                    latency_in_ms=int((completed - started).total_seconds() * 1000))

            def normalize(self, raw_result):
                markdown = LiteParseProvider._convert_md_tables_to_html(
                    raw_result.raw_output.get("markdown", ""))
                output = ParseOutput(
                    task_type="parse", example_id=raw_result.request.example_id,
                    pipeline_name=raw_result.pipeline_name,
                    pages=[PageIR(page_index=0, markdown=markdown)], markdown=markdown)
                return InferenceResult(
                    request=raw_result.request, pipeline_name=raw_result.pipeline_name,
                    product_type=raw_result.product_type, raw_output=raw_result.raw_output,
                    output=output, started_at=raw_result.started_at,
                    completed_at=raw_result.completed_at, latency_in_ms=raw_result.latency_in_ms)

        register._done = True
    name = f"harness_{tool}"
    try:
        get_pipeline(name)
    except ValueError:
        register_pipeline(PipelineSpec(pipeline_name=name, provider_name=PROVIDER,
                                       product_type=ProductType.PARSE,
                                       config={"markdown_dir": str(markdown_dir)}))
    return name


def self_test() -> int:
    """The registration and the round trip, on a fake tool, where parse-bench is installed."""
    try:
        import parse_bench  # noqa: F401
    except ImportError:
        print("SKIPPED (not a pass): parse-bench is not installed here; the round installs it.")
        return 0
    import tempfile

    from parse_bench.inference.pipelines import get_pipeline
    from parse_bench.inference.providers.registry import create_provider
    from parse_bench.schemas.pipeline_io import InferenceRequest

    failures = []
    with tempfile.TemporaryDirectory() as tmp:
        cases = pathlib.Path(tmp) / "cases"
        md = pathlib.Path(tmp) / "md"
        fetched = pathlib.Path(tmp) / "data"
        fetched.mkdir()
        (fetched / "p1.pdf").write_bytes(b"%PDF-1.4\n")
        for category in ("table", "chart"):
            (cases / "docs" / category).mkdir(parents=True)
            os.symlink(fetched / "p1.pdf", cases / "docs" / category / "p1.pdf")
            (md / "docs" / category).mkdir(parents=True)
        (md / "docs" / "table" / "p1.pdf.md").write_text("# T\n\n| a | b |\n|---|---|\n| 1 | 2 |\n")
        (md / "docs" / "chart" / "p1.pdf.md").write_text("the chart page\n")
        name = register("echo", str(md))
        spec = get_pipeline(name)
        if spec.provider_name != PROVIDER:
            failures.append("the pipeline is not on the harness provider")
        provider = create_provider(spec)
        try:
            table = provider.normalize(provider.run_inference(spec, InferenceRequest(
                example_id="table/p1", source_file_path=str(fetched / "p1.pdf"),
                product_type=spec.product_type)))
            if "<table>" not in table.output.markdown.lower():
                failures.append("a pipe table was not handed over as HTML")
            chart = provider.run_inference(spec, InferenceRequest(
                example_id="chart/p1", source_file_path=str(fetched / "p1.pdf"),
                product_type=spec.product_type))
            if "chart page" not in chart.raw_output["markdown"]:
                failures.append("one name in two categories read the other category's answer")
        except Exception as exc:  # noqa: BLE001
            failures.append(f"round trip failed: {type(exc).__name__}: {exc}")
        (md / "docs" / "table" / "p1.pdf.md").unlink()
        try:
            provider.run_inference(spec, InferenceRequest(
                example_id="table/p1", source_file_path=str(fetched / "p1.pdf"),
                product_type=spec.product_type))
            failures.append("a page the tool did not answer was handed over as an answer")
        except Exception:  # noqa: BLE001 -- the refusal is the pass
            pass
    for problem in failures:
        print(f"FAIL {problem}")
    if failures:
        return 1
    print("OK self-test: one provider, one pipeline per tool, the batch's Markdown by path, "
          "tables as HTML, an unanswered page a failure")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tool")
    ap.add_argument("--markdown-dir", help="where the tool's batch wrote <path under cases>.md")
    ap.add_argument("--input-dir")
    ap.add_argument("--output-dir")
    ap.add_argument("--group", default=None)
    ap.add_argument("--max-concurrent", type=int, default=4)
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        return self_test()
    if not (args.tool and args.markdown_dir and args.input_dir and args.output_dir):
        ap.error("--tool, --markdown-dir, --input-dir and --output-dir are required")
    name = register(args.tool, args.markdown_dir)
    from parse_bench.pipeline.cli import PipelineCLI

    PipelineCLI().run(name, input_dir=args.input_dir, output_dir=args.output_dir,
                      max_concurrent=args.max_concurrent, group=args.group,
                      open_report=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
