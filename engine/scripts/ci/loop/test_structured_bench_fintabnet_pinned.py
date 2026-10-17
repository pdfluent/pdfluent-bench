#!/usr/bin/env python3
# Copyright (c) 2026 Innovation Trigger B.V.
#
# PDFluent is available under two licences, at your option: the GNU AGPLv3, or
# the PDFluent Commercial Licence. See the LICENSE file in this repository --
# that file travels with the copy you received, which a URL does not.
"""FinTabNet draws a pinned page list; a failed read fails the run (#574, T3).

Two rounds drew DIFFERENT pages -- 33 of 120 sealed pages shared -- because a
page refused for a TRANSIENT reason was skipped and the walk took the next
candidates. One bad fetch changed the kept 300, hence the caps, hence the
sealed draw.

The rule under test: structural refusals (no table above the floor, the
two caps, the alignment check on CRC-verified bytes) still skip; transient
refusals (provenance, CRC32, the page did not answer) retry with backoff and
then fail loudly naming the page, never substituting the next candidate. An
alignment failure is structural -- the same bytes fail the same way -- so it
is skipped with NO retry in walk mode. Pinned pages are never re-aligned:
the pin CRC check and `Archive.fetch`'s CRC check prove the bytes are the
pinned page's, the check ran when the pin was made, and re-running it would
test the tool's answer, not the page (post-W: a crash under the memory
ceiling read as "" and FATALed "the page differs" on identical bytes). A
pinned CRC that differs is still FATAL. A provenance failure on five pages
of one jsonl at once re-fetches that file once before failing. When
`corpus/structured/FINTABNET_DRAW.json` exists, exactly its pages are read in
its order; without one the walk runs and `fetch-kept.json` -- the would-be
pin -- is written.

Seven cases, each red without the change:

  1. a member read that fails once then succeeds keeps the same list as a
     clean fetch (retry, not substitute).
  2. a member read that always fails raises naming the page; it does not
     return a list that substitutes another page.
  3. a truncated jsonl failing every page re-fetches that file once; still
     bad, it raises.
  4. with a pin present the fetch reads exactly the pinned pages in pin
     order; a pinned CRC that differs raises.
  5. structural caps still skip, and a clean fetch keeps the walk's head with
     `fetch-kept.json` verifying as the would-be pin.
  6. a deterministic alignment failure in walk mode is skipped and counted,
     the kept list is the walk's list with the next candidate in its place,
     and the same page in a pin is kept with no alignment call at all.
  7. a transport failure inside the jsonl fetch raises TransportFailed out
     of the fetch, never counted as provenance nor re-fetched.

Off the rig and off the network: the annotations tar and the 16 GB archive
are synthetic fixtures read through the same code (`RangeReader`, `Archive`,
`JsonlCache`), with `hf_hub_download` and the round-machine guard stubbed.
No rig page is added to the repo; pins hold names and CRCs only.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import pathlib
import sys
import tarfile
import tempfile
import types
import zipfile

REPO = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "scripts" / "ci" / "loop"))
sys.path.insert(0, str(REPO / "scripts" / "extraction"))
sys.path.insert(0, str(REPO / "scripts" / "ci"))

import structured_bench as bench  # noqa: E402
import structured_holdout_rule as holdout_rule  # noqa: E402
import fintabnet_archive as archive_mod  # noqa: E402

JSONL = "FinTabNet_1.0.0_cell_val.jsonl"


def _table(source_file: str, line_index: int, table_id: int) -> dict:
    cells = []
    for row in range(2):
        for col in range(3):
            if len(cells) >= 5:
                break
            cells.append({"row_nums": [row], "column_nums": [col],
                          "is_column_header": row == 0,
                          "pdf_text_content": f"cell {row}.{col}"})
    return {
        "pdf_page_index": 0,
        "pdf_table_bbox": [0.0, 0.0, 100.0, 100.0],
        "exclude_for_structure": False,
        "fintabnet_source_file_name": source_file,
        "fintabnet_source_line_index": line_index,
        "fintabnet_source_table_id": table_id,
        "cells": cells,
    }


def _fixtures(pages: list[tuple[str, str, int]], tables_per_page: int = 1,
              ) -> tuple[bytes, bytes, dict]:
    """A synthetic annotations tar, a synthetic archive, and the jsonl lines.

    Every page is eval, every table is above the floor, every provenance line
    matches. Returns (tar_bytes, zip_bytes, lines_by_key).
    """
    lines: list[bytes] = []
    annotations: dict[tuple, list] = {}
    for key in pages:
        company, year, num = key
        tables = []
        for _ in range(tables_per_page):
            table_id = 1000 + len(lines)
            lines.append(json.dumps({
                "table_id": table_id,
                "filename": f"{company}/{year}/page_{num}.pdf",
            }).encode())
            tables.append(_table(JSONL, len(lines) - 1, table_id))
        annotations[key] = tables
    tar_blob = io.BytesIO()
    with tarfile.open(fileobj=tar_blob, mode="w:gz") as tar:
        for (company, year, num), tables in annotations.items():
            payload = json.dumps(tables).encode()
            info = tarfile.TarInfo(
                f"FinTabNet.c-PDF_Annotations/"
                f"{company}_{year}_page_{num}_tables.json")
            info.size = len(payload)
            tar.addfile(info, io.BytesIO(payload))
    zip_blob = io.BytesIO()
    with zipfile.ZipFile(zip_blob, "w", zipfile.ZIP_DEFLATED) as archive:
        for company, year, num in pages:
            archive.writestr(
                f"fintabnet/pdf/{company}/{year}/page_{num}.pdf",
                f"%PDF-1.4 fixture {company} {year} {num}".encode())
        archive.writestr(
            "fintabnet/LICENSE.txt",
            (archive_mod.LICENSE_FIRST_LINE + "\nthe rest\n").encode())
        archive.writestr(f"fintabnet/{JSONL}", b"\n".join(lines) + b"\n")
    return tar_blob.getvalue(), zip_blob.getvalue(), annotations


def _pin_sha(pages: list[dict]) -> str:
    """The pin digest, as the spec defines it (independent of the code)."""
    h = hashlib.sha256()
    for page in pages:
        h.update(f"{page['name']}\0{page['crc32']}\0{page['split']}\n".encode())
    return h.hexdigest()


class _Harness:
    """The fetch with its network and machine behind fixtures."""

    def __init__(self, tmp: pathlib.Path, tar_bytes: bytes, zip_bytes: bytes,
                 npdfs: int, materialise: int) -> None:
        self.tmp = tmp
        self.work = tmp / "work"
        self.work.mkdir(parents=True, exist_ok=True)
        (tmp / "annotations.tar.gz").write_bytes(tar_bytes)
        self.tar_path = tmp / "annotations.tar.gz"
        self.zip_bytes = zip_bytes
        self.entry = {
            "name": "FinTabNet fixture",
            "revision": "abc123",
            "annotations_revision": "def456",
            "annotations_repo_id": "fixture/annotations",
            "annotations_file": "annotations.tar.gz",
            "repo_id": "fixture/documents",
            "documents_archive": "archive.zip",
            "documents_archive_bytes": len(zip_bytes),
            "documents_pdfs": npdfs,
            "materialise_pages": materialise,
            "walk_multiplier": 3,
            "annotates": ["table"],
        }
        self.saved: dict = {}

    def __enter__(self) -> "_Harness":
        self.saved["round_machine"] = os.environ.get("PDFLUENT_ROUND_MACHINE")
        os.environ["PDFLUENT_ROUND_MACHINE"] = "1"
        fake_hf = types.ModuleType("huggingface_hub")
        tar_path = self.tar_path

        def fake_download(*_a, **_k):
            return str(tar_path)

        fake_hf.hf_hub_download = fake_download  # type: ignore[attr-defined]
        self.saved["hf"] = sys.modules.get("huggingface_hub")
        sys.modules["huggingface_hub"] = fake_hf
        zip_bytes = self.zip_bytes

        class _Reader:
            def __init__(self, _url: str, timeout: int = 120) -> None:
                self.timeout = timeout

            def read(self, start: int, end: int) -> bytes | None:
                if not 0 <= start <= end < len(zip_bytes):
                    return None
                return zip_bytes[start:end + 1]

        self.saved["reader"] = archive_mod.RangeReader
        archive_mod.RangeReader = _Reader  # type: ignore[assignment]
        self.saved["aligned"] = bench._aligned
        bench._aligned = lambda _t, _p: True  # noqa: E731
        self.saved["page_text"] = bench._page_text
        bench._page_text = lambda _p: "fixture page text"  # noqa: E731
        # `_sleep_backoff` and `FINTABNET_DRAW` exist only with the change;
        # getattr keeps this harness running on the old code so the cases
        # below fail on their assertions (red) rather than on an AttributeError.
        self.saved["backoff"] = getattr(bench, "_sleep_backoff", None)
        if self.saved["backoff"] is not None:
            bench._sleep_backoff = lambda _a: None  # noqa: E731
        self.saved["pin"] = getattr(bench, "FINTABNET_DRAW", None)
        bench.FINTABNET_DRAW = self.tmp / "NO_PIN.json"  # type: ignore[attr-defined]
        return self

    def __exit__(self, *_exc) -> None:
        if self.saved["round_machine"] is None:
            os.environ.pop("PDFLUENT_ROUND_MACHINE", None)
        else:
            os.environ["PDFLUENT_ROUND_MACHINE"] = self.saved["round_machine"]
        if self.saved["hf"] is None:
            sys.modules.pop("huggingface_hub", None)
        else:
            sys.modules["huggingface_hub"] = self.saved["hf"]
        archive_mod.RangeReader = self.saved["reader"]
        bench._aligned = self.saved["aligned"]
        bench._page_text = self.saved["page_text"]
        if self.saved["backoff"] is not None:
            bench._sleep_backoff = self.saved["backoff"]
        if self.saved["pin"] is None:
            try:
                delattr(bench, "FINTABNET_DRAW")
            except AttributeError:
                pass
        else:
            bench.FINTABNET_DRAW = self.saved["pin"]  # type: ignore[attr-defined]

    def fetch(self, limit: int | None = None) -> list[dict]:
        # A fresh pages dir per fetch: the second fetch of a comparison must
        # not read the first one's bytes.
        pages = self.work / "pages"
        if pages.is_dir():
            for child in pages.iterdir():
                child.unlink()
        return bench.fetch_fintabnet(self.entry, self.work, limit)


def _six_pages() -> list[tuple[str, str, int]]:
    return [("BA", "2020", 1), ("BA", "2020", 2), ("BA", "2021", 1),
            ("BA", "2021", 2), ("AF", "2020", 1), ("AF", "2020", 2)]


def case_retry_keeps_the_same_list() -> list[str]:
    """A read that fails once then succeeds keeps the clean list."""
    failures: list[str] = []
    pages = _six_pages()
    tar_bytes, zip_bytes, _annotations = _fixtures(pages)
    with tempfile.TemporaryDirectory(prefix="ftn-pin-") as tmp:
        harness = _Harness(pathlib.Path(tmp), tar_bytes, zip_bytes,
                           len(pages), 3)
        with harness:
            clean = [r["name"] for r in harness.fetch()]
            if len(clean) != 3:
                return [f"the clean fetch kept {len(clean)} pages, not 3 -- "
                        "the fixture cannot compare a retry against it."]
            real_fetch = archive_mod.Archive.fetch
            failed_once: list[str] = []

            def fail_once(self, name: str) -> bytes:
                if (name.startswith("fintabnet/pdf/")
                        and not failed_once):
                    failed_once.append(name)
                    raise archive_mod.MemberRefused(
                        "injected: the member read fails once")
                return real_fetch(self, name)

            archive_mod.Archive.fetch = fail_once  # type: ignore[assignment]
            try:
                retried = [r["name"] for r in harness.fetch()]
            finally:
                archive_mod.Archive.fetch = real_fetch
            if not failed_once:
                failures.append("the injected failure never fired -- the "
                                "retry was never exercised.")
            if retried != clean:
                failures.append(
                    f"a read that fails once kept {retried}, the clean fetch "
                    f"kept {clean} -- the retry substituted the next "
                    "candidate instead of re-reading the member.")
    return failures


def case_persistent_failure_names_the_page() -> list[str]:
    """A read that always fails raises with the page name, never substitutes."""
    pages = _six_pages()
    tar_bytes, zip_bytes, _annotations = _fixtures(pages)
    with tempfile.TemporaryDirectory(prefix="ftn-pin-") as tmp:
        harness = _Harness(pathlib.Path(tmp), tar_bytes, zip_bytes,
                           len(pages), 3)
        with harness:
            clean = [r["name"] for r in harness.fetch()]
            first_pdf: list[str] = []
            real_fetch = archive_mod.Archive.fetch

            def fail_first_pdf(self, name: str) -> bytes:
                if name.startswith("fintabnet/pdf/"):
                    if not first_pdf:
                        first_pdf.append(name)
                    if name == first_pdf[0]:
                        raise archive_mod.MemberRefused(
                            "injected: the member read always fails")
                return real_fetch(self, name)

            archive_mod.Archive.fetch = fail_first_pdf  # type: ignore[assignment]
            try:
                try:
                    got = [r["name"] for r in harness.fetch()]
                except SystemExit as exc:
                    message = str(exc)
                else:
                    return [f"a read that always fails returned {got} instead "
                            f"of raising -- the clean fetch kept {clean}, so "
                            "the failure was skipped and the next candidate "
                            "substituted."]
            finally:
                archive_mod.Archive.fetch = real_fetch
            if not first_pdf:
                return ["the injected failure never fired -- nothing failed."]
            # The page name is the walk name plus .pdf; the member path holds
            # the same company, year and number.
            stem = first_pdf[0].rsplit("/", 1)[-1].replace(".pdf", "")
            if stem not in message or ".pdf" not in message:
                return [f"the persistent failure raised, but not with the "
                        f"page name ({stem!r} not in {message[:200]!r})."]
            if "Failing loudly" not in message:
                return [f"the persistent failure raised {message[:200]!r}, "
                        "which does not say it fails loudly rather than "
                        "substituting."]
    return []


def case_truncated_jsonl_refetches_once() -> list[str]:
    """242-style provenance failures re-fetch the jsonl once, then raise."""
    # Two tables per page: three attempts count six failures against the one
    # file, which crosses the threshold of five inside the first page.
    pages = _six_pages()
    tar_bytes, zip_bytes, _annotations = _fixtures(pages, tables_per_page=2)
    with tempfile.TemporaryDirectory(prefix="ftn-pin-") as tmp:
        harness = _Harness(pathlib.Path(tmp), tar_bytes, zip_bytes,
                           len(pages), 3)
        with harness:
            jsonl_fetches: list[str] = []
            real_fetch = archive_mod.Archive.fetch

            def truncate_jsonl(self, name: str) -> bytes:
                if name == f"fintabnet/{JSONL}":
                    jsonl_fetches.append(name)
                    return b'{"table_id": 999999, "filename": "WRONG"}\n'
                return real_fetch(self, name)

            archive_mod.Archive.fetch = truncate_jsonl  # type: ignore[assignment]
            try:
                try:
                    got = [r["name"] for r in harness.fetch()]
                except SystemExit as exc:
                    message = str(exc)
                else:
                    return [f"a truncated jsonl returned {got} instead of "
                            "raising -- 242 provenance mismatches were once "
                            "skipped and the walk substituted."]
            finally:
                archive_mod.Archive.fetch = real_fetch
            if len(jsonl_fetches) != 2:
                detail = ("never re-fetched" if len(jsonl_fetches) < 2
                          else "fetched more than once")
                return [f"a truncated jsonl was fetched "
                        f"{len(jsonl_fetches)} time(s), not twice (once plus "
                        "the single re-fetch) -- the file was "
                        f"{detail}."]
            if ".pdf" not in message:
                return [f"the truncated jsonl raised, but not with a page "
                        f"name: {message[:200]!r}."]
    return []


def case_pin_reads_exactly_pinned() -> list[str]:
    """With a pin, exactly the pinned pages in pin order; a bad CRC raises."""
    failures: list[str] = []
    pages = _six_pages()
    tar_bytes, zip_bytes, _annotations = _fixtures(pages)
    with tempfile.TemporaryDirectory(prefix="ftn-pin-") as tmp:
        tmpdir = pathlib.Path(tmp)
        harness = _Harness(tmpdir, tar_bytes, zip_bytes, len(pages), 3)
        with harness:
            walked = harness.fetch()
            if len(walked) != 3:
                return [f"the walked fetch kept {len(walked)} pages, not 3."]
            seed = holdout_rule.seed_for("fintabnet")
            _tuning, holdout = holdout_rule.split(walked, seed=seed)
            held = {p["name"] for p in holdout}

            def reader(start: int, end: int) -> bytes | None:
                return zip_bytes[start:end + 1]

            archive = archive_mod.open_archive(reader, len(zip_bytes))
            pin_pages = []
            for row in walked:
                prefix, num = row["name"][:-4].rsplit("_page_", 1)
                # The company holds the year: BA_2020 -> BA, 2020.
                company, year = prefix.rsplit("_", 1)
                member = archive_mod.zip_path_of(company, year, int(num))
                pin_pages.append({
                    "name": row["name"],
                    "crc32": f"{archive.entries[member].crc:08x}",
                    "split": "holdout" if row["name"] in held else "tuning",
                })
            # Pin order differs from walk order on purpose: a fetch that
            # walks anyway returns the walk's order, not this.
            pin_pages = list(reversed(pin_pages))
            if [p["name"] for p in pin_pages] == [r["name"] for r in walked]:
                pin_pages = [pin_pages[1], pin_pages[2], pin_pages[0]]
            pin = {"set": "fintabnet", "pages": pin_pages,
                   "sha256": _pin_sha(pin_pages)}
            pin_path = tmpdir / "FINTABNET_DRAW.json"
            pin_path.write_text(json.dumps(pin))
            bench.FINTABNET_DRAW = pin_path  # type: ignore[attr-defined]
            pinned = [r["name"] for r in harness.fetch()]
            if pinned != [p["name"] for p in pin_pages]:
                failures.append(
                    f"with a pin the fetch read {pinned}, not the pin order "
                    f"{[p['name'] for p in pin_pages]} -- it walked instead "
                    "of reading exactly the pinned pages.")
            refusals = json.loads(
                (harness.work / "fetch-refusals.json").read_text())
            if not str(refusals.get("mode", "")).startswith("pinned "):
                failures.append(
                    f"the pinned evidence records mode "
                    f"{refusals.get('mode')!r}, not 'pinned <sha>'.")
            # A pinned page whose CRC differs refuses to seal.
            bad_pages = [dict(p) for p in pin_pages]
            bad_pages[1]["crc32"] = (
                "00000000" if bad_pages[1]["crc32"] != "00000000"
                else "ffffffff")
            bad_pin = {"set": "fintabnet", "pages": bad_pages,
                       "sha256": _pin_sha(bad_pages)}
            pin_path.write_text(json.dumps(bad_pin))
            try:
                got = [r["name"] for r in harness.fetch()]
            except SystemExit as exc:
                if bad_pages[1]["name"] not in str(exc):
                    failures.append(
                        f"a pinned CRC that differs raised, but not with the "
                        f"page name: {str(exc)[:200]!r}.")
            else:
                failures.append(
                    f"a pinned CRC that differs returned {got} instead of "
                    "raising -- the fetch sealed a page that differs.")
    return failures


def case_structural_caps_still_skip() -> list[str]:
    """Caps still skip; a clean fetch keeps the walk's head as the pin."""
    failures: list[str] = []
    # Six pages of one report with room for six: the per-report cap of four
    # skips two (structural, counted) and the short fetch refuses to seal --
    # exactly as today. Without the caps all six would be kept.
    pages = [("BA", "2020", n) for n in range(1, 7)]
    tar_bytes, zip_bytes, _annotations = _fixtures(pages)
    with tempfile.TemporaryDirectory(prefix="ftn-pin-") as tmp:
        harness = _Harness(pathlib.Path(tmp), tar_bytes, zip_bytes,
                           len(pages), 6)
        with harness:
            try:
                got = [r["name"] for r in harness.fetch()]
            except SystemExit as exc:
                if "kept 4 page(s)" not in str(exc):
                    failures.append(
                        f"six pages of one report raised, but not as a short "
                        f"fetch of four: {str(exc)[:200]!r}.")
            else:
                failures.append(
                    f"six pages of one report kept all six {got} -- the "
                    "per-report cap no longer skips.")
                return failures
            refusals = json.loads(
                (harness.work / "fetch-refusals.json").read_text())
            if refusals.get("refused", {}).get("over the per-report cap") != 2:
                failures.append(
                    f"the caps refused {refusals.get('refused')}, not two "
                    "pages over the per-report cap.")
            if refusals.get("mode") != "walked, no pin":
                failures.append(
                    f"the walked evidence records mode "
                    f"{refusals.get('mode')!r}.")
    # A clean fetch with no cap pressure keeps the walk's head and writes the
    # would-be pin beside the evidence.
    pages = _six_pages()
    tar_bytes, zip_bytes, _annotations = _fixtures(pages)
    with tempfile.TemporaryDirectory(prefix="ftn-pin-") as tmp:
        harness = _Harness(pathlib.Path(tmp), tar_bytes, zip_bytes,
                           len(pages), 4)
        with harness:
            rows = harness.fetch()
            names = [r["name"] for r in rows]
            if len(names) != 4:
                return failures + [
                    f"a clean fetch kept {len(names)} pages, not 4."]
            kept_path = harness.work / "fetch-kept.json"
            if not kept_path.is_file():
                failures.append("the walked fetch wrote no fetch-kept.json "
                                "-- the next healthy run has no would-be pin.")
                return failures
            kept = json.loads(kept_path.read_text())
            if [p["name"] for p in kept.get("pages", [])] != names:
                failures.append("fetch-kept.json does not name the kept "
                                "pages in kept order.")
            if kept.get("sha256") != _pin_sha(kept.get("pages", [])):
                failures.append("fetch-kept.json's sha256 is not the digest "
                                "of its own list.")
            for page in kept.get("pages", []):
                if sorted(page) != ["crc32", "name", "split"]:
                    failures.append(
                        f"a kept page carries {sorted(page)}, not names and "
                        "CRCs only.")
                    break
    return failures


def case_alignment_failure_skips_walked_pinned_skips_realign() -> list[str]:
    """An alignment failure skips walked (counted, no retry); pinned is kept.

    The bytes are CRC-verified, so the failure is structural: a retry would
    re-read the same bytes and fail the same way. Walked, the page is counted
    in `pages_not_aligned`, its file unlinked, and the walk keeps the next
    candidate -- the kept list is today's list with the failed page replaced.
    Pinned, the same page is kept with no alignment call at all: the pin CRC
    check and `Archive.fetch`'s CRC check prove the bytes are the pinned
    page's, and the check ran when the pin was made.
    """
    failures: list[str] = []
    pages = _six_pages()
    tar_bytes, zip_bytes, _annotations = _fixtures(pages)
    with tempfile.TemporaryDirectory(prefix="ftn-pin-") as tmp:
        tmpdir = pathlib.Path(tmp)
        harness = _Harness(tmpdir, tar_bytes, zip_bytes, len(pages), 3)
        with harness:
            clean = [r["name"] for r in harness.fetch()]
            if len(clean) != 3:
                return [f"the clean fetch kept {len(clean)} pages, not 3 -- "
                        "the fixture cannot place a skipped page against it."]
            # The walk's fourth page: the next candidate the skip must take.
            harness.entry["materialise_pages"] = 4
            clean4 = [r["name"] for r in harness.fetch()]
            harness.entry["materialise_pages"] = 3
            if len(clean4) != 4 or clean4[:3] != clean:
                return [f"the walk's head moved ({clean} vs {clean4}) -- "
                        "the fixture cannot name the next candidate."]
            fail_name = clean[0]
            aligned_calls: list[str] = []
            sleeps: list[int] = []
            member_fetches: list[str] = []

            def fail_one(_tables: list, text: str) -> bool:
                aligned_calls.append(text)
                return text != fail_name

            bench._page_text = lambda p: p.name  # noqa: E731
            bench._aligned = fail_one  # type: ignore[assignment]
            bench._sleep_backoff = lambda a: sleeps.append(a)  # noqa: E731
            real_fetch = archive_mod.Archive.fetch

            def count_fetch(self, name: str) -> bytes:
                if name.startswith("fintabnet/pdf/"):
                    member_fetches.append(name)
                return real_fetch(self, name)

            archive_mod.Archive.fetch = count_fetch  # type: ignore[assignment]
            try:
                try:
                    kept = [r["name"] for r in harness.fetch()]
                except SystemExit as exc:
                    return [f"a deterministic alignment failure in walk mode "
                            f"raised {str(exc)[:160]!r} instead of skipping "
                            "-- every walked round aborts on the first such "
                            "page and never pins."]
            finally:
                archive_mod.Archive.fetch = real_fetch
            expected = [n for n in clean4 if n != fail_name][:3]
            if kept != expected:
                failures.append(
                    f"a skipped alignment failure kept {kept}, not the "
                    f"walk's list with the next candidate in its place "
                    f"{expected}.")
            refusals = json.loads(
                (harness.work / "fetch-refusals.json").read_text())
            if refusals.get("pages_not_aligned") != 1:
                failures.append(
                    f"the skipped page counted "
                    f"{refusals.get('pages_not_aligned')}, not 1, in "
                    "pages_not_aligned.")
            if (harness.work / "pages" / fail_name).exists():
                failures.append(f"the skipped page {fail_name} left its "
                                "file behind instead of unlinking it.")
            prefix, num = fail_name[:-4].rsplit("_page_", 1)
            company, year = prefix.rsplit("_", 1)
            member = archive_mod.zip_path_of(company, year, int(num))
            if member_fetches.count(member) != 1:
                failures.append(
                    f"the failed page's member was read "
                    f"{member_fetches.count(member)} time(s), not once -- "
                    "the alignment failure retried.")
            if aligned_calls.count(fail_name) != 1:
                failures.append(
                    f"the failed page was alignment-checked "
                    f"{aligned_calls.count(fail_name)} time(s), not once.")
            if sleeps:
                failures.append(f"the skipped page slept {len(sleeps)} "
                                "backoff(s) -- the skip retried.")
            # PINNED: the page is identified by its bytes, not re-aligned.
            # `_page_text` reads "" (the post-W tool failure) and the check
            # would fail it -- but neither runs for pinned pages.
            bench._aligned = lambda _t, _p: True  # noqa: E731
            bench._page_text = lambda _p: "fixture page text"  # noqa: E731
            walked = harness.fetch()
            seed = holdout_rule.seed_for("fintabnet")
            _tuning, holdout = holdout_rule.split(walked, seed=seed)
            held = {p["name"] for p in holdout}

            def reader(start: int, end: int) -> bytes | None:
                return zip_bytes[start:end + 1]

            archive = archive_mod.open_archive(reader, len(zip_bytes))
            pin_pages = []
            for row in walked:
                stem, page_num = row["name"][:-4].rsplit("_page_", 1)
                comp, yr = stem.rsplit("_", 1)
                entry = archive.entries[
                    archive_mod.zip_path_of(comp, yr, int(page_num))]
                pin_pages.append({
                    "name": row["name"],
                    "crc32": f"{entry.crc:08x}",
                    "split": "holdout" if row["name"] in held else "tuning",
                })
            assert fail_name in [p["name"] for p in pin_pages]
            pin = {"set": "fintabnet", "pages": pin_pages,
                   "sha256": _pin_sha(pin_pages)}
            pin_path = tmpdir / "FINTABNET_DRAW.json"
            pin_path.write_text(json.dumps(pin))
            bench.FINTABNET_DRAW = pin_path  # type: ignore[attr-defined]
            bench._page_text = lambda _p: ""  # noqa: E731
            pinned_calls: list[str] = []

            def refuse(_tables: list, _text: str) -> bool:
                pinned_calls.append("called")
                return False

            bench._aligned = refuse  # type: ignore[assignment]
            try:
                got = [r["name"] for r in harness.fetch()]
            except SystemExit as exc:
                failures.append(
                    f"the pinned fetch over byte-identical pages raised "
                    f"{str(exc)[:200]!r} -- the tool failure was read as a "
                    "page that differs.")
                return failures
            want = [p["name"] for p in pin_pages]
            if got != want:
                failures.append(
                    f"the pinned fetch kept {got}, not the pinned pages "
                    f"{want}.")
            if pinned_calls:
                failures.append(
                    f"the pinned fetch made {len(pinned_calls)} alignment "
                    "call(s) -- pinned pages are identified by their bytes, "
                    "not re-aligned.")
    return failures


def case_transport_failure_inside_fetch_propagates() -> list[str]:
    """A TransportFailed inside _fetch raises out, never counted nor refreshed.

    The jsonl the transport never answered is not a provenance mismatch:
    `JsonlCache._fetch` catches `MemberRefused` only, and the bench's
    provenance handler and its `refresh` re-fetch catch `ProvenanceRefused`
    only, so the failure raises out of the fetch as `TransportFailed`
    without counting provenance or re-fetching the file.
    """
    pages = _six_pages()
    tar_bytes, zip_bytes, _annotations = _fixtures(pages)
    with tempfile.TemporaryDirectory(prefix="ftn-pin-") as tmp:
        harness = _Harness(pathlib.Path(tmp), tar_bytes, zip_bytes,
                           len(pages), 3)
        with harness:
            real_fetch = archive_mod.Archive.fetch
            jsonl_attempts: list[str] = []
            refresh_calls: list[str] = []
            real_refresh = archive_mod.JsonlCache.refresh

            def fail_jsonl(self, name: str) -> bytes:
                if name == f"fintabnet/{JSONL}":
                    jsonl_attempts.append(name)
                    raise archive_mod.TransportFailed(
                        "injected: the jsonl never answered")
                return real_fetch(self, name)

            def count_refresh(self, filename: str):
                refresh_calls.append(filename)
                return real_refresh(self, filename)

            archive_mod.Archive.fetch = fail_jsonl  # type: ignore[assignment]
            archive_mod.JsonlCache.refresh = count_refresh  # type: ignore[assignment]
            try:
                try:
                    got = [r["name"] for r in harness.fetch()]
                except archive_mod.TransportFailed:
                    pass
                except SystemExit as exc:
                    return [f"a jsonl transport failure raised SystemExit "
                            f"({str(exc)[:160]!r}) instead of TransportFailed "
                            "-- the refresh path converted it."]
                else:
                    return [f"a jsonl transport failure returned {got} "
                            "instead of raising -- it was swallowed."]
            finally:
                archive_mod.Archive.fetch = real_fetch
                archive_mod.JsonlCache.refresh = real_refresh
            if not jsonl_attempts:
                return ["the injected transport failure never fired -- "
                        "the jsonl was never fetched."]
            if refresh_calls:
                return [f"a jsonl transport failure re-fetched "
                        f"{refresh_calls} -- the refresh counter moved."]
    return []


def main() -> int:
    failures: list[str] = []
    failures += case_retry_keeps_the_same_list()
    failures += case_persistent_failure_names_the_page()
    failures += case_truncated_jsonl_refetches_once()
    failures += case_pin_reads_exactly_pinned()
    failures += case_structural_caps_still_skip()
    failures += case_alignment_failure_skips_walked_pinned_skips_realign()
    failures += case_transport_failure_inside_fetch_propagates()
    for line in failures:
        print(f"FAIL {line}")
    if failures:
        print(f"\n{len(failures)} of the pinned draw's guarantees do not hold.")
        return 1
    print("✓ fintabnet pinned draw: transient retries then fails loudly "
          "naming the page, the jsonl re-fetches once, the pin reads exactly "
          "its pages, caps still skip, alignment skips walked while pinned "
          "pages are never re-aligned, and a transport failure inside the "
          "jsonl fetch propagates")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
