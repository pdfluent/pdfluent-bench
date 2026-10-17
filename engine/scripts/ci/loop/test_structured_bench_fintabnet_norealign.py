#!/usr/bin/env python3
# Copyright (c) 2026 Innovation Trigger B.V.
#
# PDFluent is available under two licences, at your option: the GNU AGPLv3, or
# the PDFluent Commercial Licence. See the LICENSE file in this repository --
# that file travels with the copy you received, which a URL does not.
"""A pinned FinTabNet page is identified by its bytes, not re-aligned (#574).

Post-W refused to seal pinned page EFX_2016_page_68.pdf: "the alignment check
failed. It passed alignment when pinned, so the page differs." Same archive,
same pin, same mirror -- and the pin CRC check plus `Archive.fetch`'s CRC
check had already PROVED the bytes identical. What differed was not the page
but the tool outcome: `_page_text` reads "" whenever pdftotext crashes or
times out (here, killed under the round's memory ceiling), and "" fails
alignment. The message "the page differs" was false.

The rules under test:

  1. PINNED MODE DOES NOT RE-ALIGN. After the two CRC checks pass, the page
     is the pinned page; the alignment check ran when the pin was made. The
     walk keeps it exactly as today.
  2. A TOOL FAILURE IS NAMED, NEVER READ AS TEXT. `_page_text` passes a
     probe to run_tool; on crash or timeout it raises REFUSED naming
     pdftotext and the page. An empty read with outcome ok stays a normal
     unaligned page, skipped.
  3. THE PATH IS ON THE RECORD: with STRUCTURED_MEMORY_MAX set, the first
     page read logs once whether pdftotext runs under systemd-run.

Four cases, each red without the change:

  1. a pinned fetch whose bytes match the pin, with `_page_text` reading ""
     (the post-W tool failure): the pages are kept, and no alignment call
     is made for pinned pages.
  2. a walk page with `run_tool` reporting outcome crash: REFUSED naming
     pdftotext and the page, never silently counted as unaligned.
  3. a walk page with outcome ok and empty text: still counted as unaligned
     and skipped (unchanged).
  4. with STRUCTURED_MEMORY_MAX set, the first page read logs the
     pdftotext path exactly once.

Off the rig and off the network: the pinned suite's own fixtures
(`_fixtures`, `_Harness`) with `run_tool` and `shutil.which` stubbed.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import pathlib
import shutil
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "scripts" / "ci" / "loop"))
sys.path.insert(0, str(REPO / "scripts" / "extraction"))
sys.path.insert(0, str(REPO / "scripts" / "ci"))

import structured_bench as bench  # noqa: E402
import structured_holdout_rule as holdout_rule  # noqa: E402
import fintabnet_archive as archive_mod  # noqa: E402
import test_structured_bench_fintabnet_pinned as pinned  # noqa: E402

# Every cell text the fixture tables carry: a page reading this is aligned.
GOOD_TEXT = "cell 0.0 cell 0.1 cell 0.2 cell 1.0 cell 1.1"


def _pin_for(harness: pinned._Harness, walked: list[dict],
             zip_bytes: bytes) -> pathlib.Path:
    """A pin over exactly `walked`, in walked order, CRCs from the archive."""
    seed = holdout_rule.seed_for("fintabnet")
    _tuning, holdout = holdout_rule.split(walked, seed=seed)
    held = {p["name"] for p in holdout}

    def reader(start: int, end: int) -> bytes | None:
        return zip_bytes[start:end + 1]

    archive = archive_mod.open_archive(reader, len(zip_bytes))
    pin_pages = []
    for row in walked:
        stem, num = row["name"][:-4].rsplit("_page_", 1)
        company, year = stem.rsplit("_", 1)
        entry = archive.entries[
            archive_mod.zip_path_of(company, year, int(num))]
        pin_pages.append({
            "name": row["name"],
            "crc32": f"{entry.crc:08x}",
            "split": "holdout" if row["name"] in held else "tuning",
        })
    pin = {"set": "fintabnet", "pages": pin_pages,
           "sha256": pinned._pin_sha(pin_pages)}
    pin_path = harness.tmp / "FINTABNET_DRAW.json"
    pin_path.write_text(json.dumps(pin))
    return pin_path


def case_pinned_page_kept_without_realignment() -> list[str]:
    """Byte-identical pinned pages are kept; no alignment call is made."""
    pages = pinned._six_pages()
    tar_bytes, zip_bytes, _annotations = pinned._fixtures(pages)
    with tempfile.TemporaryDirectory(prefix="ftn-norealign-") as tmp:
        harness = pinned._Harness(pathlib.Path(tmp), tar_bytes, zip_bytes,
                                  len(pages), 3)
        with harness:
            walked = harness.fetch()
            if len(walked) != 3:
                return [f"the walked fetch kept {len(walked)} pages, not 3."]
            bench.FINTABNET_DRAW = _pin_for(  # type: ignore[attr-defined]
                harness, walked, zip_bytes)
            # The post-W tool failure: the page reads "".
            bench._page_text = lambda _p: ""  # noqa: E731
            aligned_calls: list[str] = []

            def refuse(_tables: list, _text: str) -> bool:
                aligned_calls.append("called")
                return False

            bench._aligned = refuse  # type: ignore[assignment]
            try:
                kept = [r["name"] for r in harness.fetch()]
            except SystemExit as exc:
                return [f"a pinned fetch over byte-identical pages raised "
                        f"{str(exc)[:200]!r} -- the tool failure was read as "
                        "a page that differs."]
            want = [r["name"] for r in walked]
            if kept != want:
                return [f"the pinned fetch kept {kept}, not the pinned "
                        f"pages {want}."]
            if aligned_calls:
                return ["the pinned fetch made "
                        f"{len(aligned_calls)} alignment call(s) -- pinned "
                        "pages are identified by their bytes, not re-aligned."]
    return []


def case_tool_crash_is_refused_loudly() -> list[str]:
    """A pdftotext crash in the walk raises REFUSED with tool and page."""
    pages = pinned._six_pages()
    tar_bytes, zip_bytes, _annotations = pinned._fixtures(pages)
    with tempfile.TemporaryDirectory(prefix="ftn-norealign-") as tmp:
        harness = pinned._Harness(pathlib.Path(tmp), tar_bytes, zip_bytes,
                                  len(pages), 3)
        with harness:
            clean = [r["name"] for r in harness.fetch()]
            if len(clean) != 3:
                return [f"the clean fetch kept {len(clean)} pages, not 3."]
            bench._page_text = harness.saved["page_text"]
            real_run_tool = bench.run_tool
            real_which = shutil.which
            saved_ceiling = os.environ.pop("STRUCTURED_MEMORY_MAX", None)

            def crash(_command: list, timeout: int = 120,
                      probe: dict | None = None):
                if probe is not None:
                    probe.update(outcome="crash", ms=9, peak_rss_kb=1,
                                 returncode=137)
                return None, 9

            shutil.which = (lambda name: "/usr/bin/pdftotext"  # noqa: E731
                            if name == "pdftotext" else real_which(name))
            bench.run_tool = crash  # type: ignore[assignment]
            try:
                try:
                    got = [r["name"] for r in harness.fetch()]
                except SystemExit as exc:
                    message = str(exc)
                else:
                    return [f"a pdftotext crash returned {got} instead of "
                            "raising -- the tool failure was silently "
                            "counted as unaligned."]
            finally:
                bench.run_tool = real_run_tool
                shutil.which = real_which
                if saved_ceiling is not None:
                    os.environ["STRUCTURED_MEMORY_MAX"] = saved_ceiling
            for needle in ("REFUSED", "pdftotext", "crash", clean[0]):
                if needle not in message:
                    return [f"the crash refusal must name {needle}: "
                            f"{message[:200]!r}."]
    return []


def case_empty_read_with_outcome_ok_stays_unaligned() -> list[str]:
    """Outcome ok with empty text is still an unaligned page, skipped."""
    pages = pinned._six_pages()
    tar_bytes, zip_bytes, _annotations = pinned._fixtures(pages)
    with tempfile.TemporaryDirectory(prefix="ftn-norealign-") as tmp:
        harness = pinned._Harness(pathlib.Path(tmp), tar_bytes, zip_bytes,
                                  len(pages), 4)
        with harness:
            bench._page_text = harness.saved["page_text"]
            bench._aligned = harness.saved["aligned"]
            real_run_tool = bench.run_tool
            real_which = shutil.which
            saved_ceiling = os.environ.pop("STRUCTURED_MEMORY_MAX", None)
            calls: list[str] = []

            def first_empty(command: list, timeout: int = 120,
                            probe: dict | None = None):
                if probe is not None:
                    probe.update(outcome="ok", ms=3, peak_rss_kb=1,
                                 returncode=0)
                calls.append("read")
                if len(calls) == 1:
                    return "", 3
                return GOOD_TEXT, 3

            shutil.which = (lambda name: "/usr/bin/pdftotext"  # noqa: E731
                            if name == "pdftotext" else real_which(name))
            bench.run_tool = first_empty  # type: ignore[assignment]
            try:
                try:
                    kept = [r["name"] for r in harness.fetch()]
                except SystemExit as exc:
                    return [f"an empty read with outcome ok raised "
                            f"{str(exc)[:200]!r} instead of skipping -- the "
                            "refusal overreaches past tool failures."]
            finally:
                bench.run_tool = real_run_tool
                shutil.which = real_which
                if saved_ceiling is not None:
                    os.environ["STRUCTURED_MEMORY_MAX"] = saved_ceiling
            if len(kept) != 4:
                return [f"the walk kept {len(kept)} pages, not 4 -- the "
                        "empty read was not skipped in favour of the next "
                        "candidate."]
            refusals = json.loads(
                (harness.work / "fetch-refusals.json").read_text())
            if refusals.get("pages_not_aligned") != 1:
                return [f"the empty read counted "
                        f"{refusals.get('pages_not_aligned')}, not 1, in "
                        "pages_not_aligned."]
    return []


def case_memory_ceiling_logs_the_path_once() -> list[str]:
    """With the ceiling set, the first read logs the pdftotext path once."""
    if not hasattr(bench, "_log_page_text_path_once"):
        return ["_page_text logs no path under STRUCTURED_MEMORY_MAX -- "
                "a future round cannot show which path ran."]
    page = pathlib.Path("page.pdf")
    real_run_tool = bench.run_tool
    real_which = shutil.which
    saved_ceiling = os.environ.get("STRUCTURED_MEMORY_MAX")
    saved_flag = bench._PAGE_TEXT_PATH_LOGGED

    def ok(_command: list, timeout: int = 120, probe: dict | None = None):
        if probe is not None:
            probe.update(outcome="ok", ms=3, peak_rss_kb=1, returncode=0)
        return GOOD_TEXT, 3

    shutil.which = (lambda name: "/usr/bin/pdftotext"  # noqa: E731
                    if name == "pdftotext" else real_which(name))
    bench.run_tool = ok  # type: ignore[assignment]
    os.environ["STRUCTURED_MEMORY_MAX"] = "1g"
    bench._PAGE_TEXT_PATH_LOGGED = False
    try:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            bench._page_text(page)
            bench._page_text(page)
        lines = [line for line in out.getvalue().splitlines()
                 if "pdftotext" in line and "systemd-run" in line]
    finally:
        bench.run_tool = real_run_tool
        shutil.which = real_which
        if saved_ceiling is None:
            os.environ.pop("STRUCTURED_MEMORY_MAX", None)
        else:
            os.environ["STRUCTURED_MEMORY_MAX"] = saved_ceiling
        bench._PAGE_TEXT_PATH_LOGGED = saved_flag
    if len(lines) != 1:
        return [f"the path logged {len(lines)} time(s) over two reads, not "
                "once."]
    if "outcome ok" not in lines[0]:
        return [f"the path log names no first-page outcome: {lines[0]!r}."]
    return []


def main() -> int:
    failures: list[str] = []
    failures += case_pinned_page_kept_without_realignment()
    failures += case_tool_crash_is_refused_loudly()
    failures += case_empty_read_with_outcome_ok_stays_unaligned()
    failures += case_memory_ceiling_logs_the_path_once()
    for line in failures:
        print(f"FAIL {line}")
    if failures:
        print(f"\n{len(failures)} of the pinned-bytes guarantees do not hold.")
        return 1
    print("pinned pages are identified by their bytes, never re-aligned; "
          "a tool failure is refused loudly, an empty ok read stays "
          "unaligned, and the ceiling logs the pdftotext path once")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
