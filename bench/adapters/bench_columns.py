# Copyright (c) 2026 Innovation Trigger B.V.
#
# PDFluent is available under two licences, at your option: the GNU AGPLv3, or
# the PDFluent Commercial Licence. See the LICENSE file in this repository --
# that file travels with the copy you received, which a URL does not.
"""The benchmark's own columns, served through the engine's adapter.

`engine/scripts/extraction/adapters/extract_structured.py` is the ported
adapter; it imports this module and adds `TOOLS` below to its own table, so
every column, ported or new, runs through one command line, one batch protocol
(`--batch LIST --out-dir DIR`, one `<page>.md` per page plus `timings.tsv`) and
one markdown reader (`blocks_of` in the ported harness).

Columns:

  docling_minimal         Docling with TableFormer off and OCR off. Docling has
                          no PDF pipeline without its layout model, so the
                          layout model still runs; this column measures what
                          is left when the table model is switched off.
  pdfluent_fast           the public `pdfluent` package, `quality="fast"`
  pdfluent_balanced       `quality="balanced"`: layout model from
                          PDFLUENT_MODEL_DIR (or the package's per-user dir)
  pdfluent_balanced_tatr  balanced plus the TATR table model from
                          PDFLUENT_TABLE_MODEL_DIR (required): the model root
                          that holds tatr-v1.1-all/tatr-v1.1-all.onnx

OCR is off on every column (`ocr="never"`), as on every ported column.
"""
from __future__ import annotations

import os
import pathlib

PDFLUENT_MIN = (1, 1, 0)


def _version_tuple(text: str) -> tuple[int, ...]:
    parts = []
    for piece in text.split(".")[:3]:
        digits = "".join(ch for ch in piece if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def pdfluent_available() -> str | None:
    """None when the installed `pdfluent` can serve these columns, else why not."""
    try:
        from importlib.metadata import version

        installed = version("pdfluent")
    except Exception:  # noqa: BLE001 -- not installed at all
        return "the `pdfluent` package is not installed"
    if _version_tuple(installed) < PDFLUENT_MIN:
        return (f"`pdfluent` {installed} is installed; these columns need "
                f">= {'.'.join(map(str, PDFLUENT_MIN))} (Document.extract_markdown with quality=)")
    return None


def _pdfluent(path: str, quality: str, tables: bool) -> str:
    why = pdfluent_available()
    if why:
        raise RuntimeError(why)
    import pdfluent

    kwargs: dict = {"quality": quality, "ocr": "never"}
    if quality == "balanced":
        model_dir = os.environ.get("PDFLUENT_MODEL_DIR")
        if model_dir:
            kwargs["model_dir"] = model_dir
    if tables:
        table_dir = os.environ.get("PDFLUENT_TABLE_MODEL_DIR")
        if not table_dir:
            raise RuntimeError("pdfluent_balanced_tatr needs PDFLUENT_TABLE_MODEL_DIR "
                               "(the model root holding tatr-v1.1-all/tatr-v1.1-all.onnx)")
        kwargs["table_model_dir"] = table_dir
    document = pdfluent.open_pdf(path)
    return document.extract_markdown(**kwargs)


def with_pdfluent_fast(path: str, ocr: bool) -> str:
    return _pdfluent(path, "fast", False)


def with_pdfluent_balanced(path: str, ocr: bool) -> str:
    return _pdfluent(path, "balanced", False)


def with_pdfluent_balanced_tatr(path: str, ocr: bool) -> str:
    return _pdfluent(path, "balanced", True)


def _docling_minimal_converter(ocr: bool):
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    options = PdfPipelineOptions()
    options.do_ocr = ocr
    options.do_table_structure = False
    return DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)})


_CONVERTER: dict = {}


def with_docling_minimal(path: str, ocr: bool) -> str:
    if ocr not in _CONVERTER:
        _CONVERTER[ocr] = _docling_minimal_converter(ocr)
    return _CONVERTER[ocr].convert(path).document.export_to_markdown()


def batch_docling_minimal(paths: list[str], out_dir: str, ocr: bool, clock_class) -> int:
    """`batch_docling` of the ported adapter, with TableFormer off."""
    converter = _docling_minimal_converter(ocr)
    out = pathlib.Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    written = 0
    clock = clock_class(out)
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


TOOLS = {
    "docling_minimal": with_docling_minimal,
    "pdfluent_fast": with_pdfluent_fast,
    "pdfluent_balanced": with_pdfluent_balanced,
    "pdfluent_balanced_tatr": with_pdfluent_balanced_tatr,
}
