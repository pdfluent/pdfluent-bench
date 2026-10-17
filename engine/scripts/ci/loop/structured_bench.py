#!/usr/bin/env python3
# Copyright (c) 2026 Innovation Trigger B.V.
#
# PDFluent is available under two licences, at your option: the GNU AGPLv3, or
# the PDFluent Commercial Licence. See the LICENSE file in this repository --
# that file travels with the copy you received, which a URL does not.
"""The structured-extraction baseline: one protocol, one machine, one run id (#566).

    structured_bench.py --set doclaynet --tools ours,docling --fetch --draw --measure

This is the instrument the whole of #574 is sized by. It fetches a benchmark
set, splits it into a tuning half and a sealed holdout, runs every tool it is
allowed to run over the same pages on the same machine in the same session, and
writes one run file with a per-page row for every axis and a table per category.

WHAT IT MEASURES, AND WHY THOSE FIVE
====================================
`scripts/extraction/structured_axes.py` holds the arithmetic and says why each
axis is shaped the way it is. What matters here is that five axes are reported
separately and never averaged: a single "document understanding score" is a
number that cannot be acted on, and the milestone under it has one ticket per
axis (#567 blocks, #568 headings, #569 lists, #570 tables, #571 reading order).
A baseline whose shape does not match the work it sizes is a baseline nobody
consults twice.

EVERY TOOL AND EVERY SET IS READ OUT OF A REGISTER FIRST
========================================================
`benchmarks/extraction/comparison/EXTRACTORS.toml` says which tools may be run
and which of those may be published; `benchmarks/structured/comparison/
DATASETS.toml` says the same about the documents. Both are read here BEFORE
anything is fetched or run, and a refusal is loud:

  * a tool with `on_the_rig = false` is not run, whatever `--tools` says
  * a set with `on_the_rig = false` is not fetched, whatever `--set` says
  * a run that touched anything with `publishable = false` says so in the run
    file, in a field a published table is checked against

That order -- register, then machine -- is the one thing in this file that is
not a convenience. #566 named OmniDocBench and Marker, and both are refused:
OmniDocBench because its only statement of terms is "for research purposes only
and not for commercial use", Marker because its weights licence forbids any use
by an entity selling a competing product. Each register carries the clause, the
quote and the reading. Without the check in code, a later run would quietly put
them back.

THE HOLDOUT IS NOT MEASURED
===========================
`--measure` reads the tuning half. The sealed half needs `--holdout` AND
`--because`, and the run file records the sentence: a holdout spent without a
reason written down at the moment of spending is a holdout nobody can audit
afterwards. `benchmarks/HOLDOUTS.md` says what burns one.

TWO RUNS A DAY
==============
#574's rule: 22:00 on whatever landed, and 12:00 only if a bundle of fixes
landed since. It is enforced here rather than remembered, by counting the run
files already written for today -- because the rule exists to stop a round
measuring one fix at a time, and a rule that only lives in a ticket is a rule
the third terminal of the evening has not read.
"""
from __future__ import annotations

import argparse
import datetime as dt
import functools
import hashlib
import html
import json
import os
import pathlib
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import time
import tomllib
import urllib.error
import urllib.request

REPO = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "scripts" / "extraction"))
sys.path.insert(0, str(REPO / "scripts" / "ci"))

import pubtables_cells  # noqa: E402
import structured_axes as axes  # noqa: E402
import structured_holdout_rule as holdout_rule  # noqa: E402
import fintabnet_archive  # noqa: E402

DATASETS = REPO / "benchmarks" / "structured" / "comparison" / "DATASETS.toml"
EXTRACTORS = REPO / "benchmarks" / "extraction" / "comparison" / "EXTRACTORS.toml"
RUNS = pathlib.Path(os.environ.get("BENCH_RUNS_DIR")  # pdfluent-bench: override
                    or REPO / "benchmarks" / "runs" / "structured")
# The manifest of one set; a run asks `holdout_rule.draw_path_for` for its
# own, and this name is DocLayNet's, which is what the first rounds drew.
MANIFEST = holdout_rule.MANIFEST

PREFIX = "structured"

# A page that has not answered in this long has hung. The same five minutes the
# extractor matrix uses, and one number for every tool: a per-tool timeout is a
# handicap decided by us.
TIMEOUT_S = 300

# The name our own column goes under, everywhere a tool is keyed by name.
#
# It was spelt out at each of the three places that needed it until #582's
# geometry pass arrived with a fourth; that one was written against
# `structured_parity.py`, where the same constant already lives, and reached
# the measurement as a `NameError` that cost a paid round. One name, in the
# file that uses it.
OURS = "pdfluent"

# The balanced tier's column (#641): `pdfluent extract --quality balanced`
# per page, like ours, through `markdown_of` below. Page-at-a-time, never
# batched and never cached: it is ours, measured every round.
BALANCED = "balanced"

# The balanced tier with TATR table structure (#636, track B, P4): the same
# harness as BALANCED, plus `--table-model-dir`, from the same binary off
# the same branch, so only TATR on/off differs between the two columns.
BALANCED_TATR = "balanced_tatr"

# The ONNX Runtime linux-x64 1.23.2 archive both balanced columns unpack:
# 8,309,231 bytes off the pinned microsoft/onnxruntime release URL. One
# constant here, one hash in each column's `version_pin`, and the round
# refuses when any of the three disagrees -- a GitHub release URL answers
# 302, and without -L curl banks the empty redirect body with exit 0.
ORT_ARCHIVE_SHA256 = "1fa4dcaef22f6f7d5cd81b28c2800414350c10116f5fdd46a2160082551c5f9b"

# THE OWNER'S RULE OF 22-09-2026, which replaces the two-runs-a-day rule of
# #574 and is enforced rather than remembered: ONE RUN PER VALIDATED BUNDLE,
# one machine at a time, and the Hetzner ceiling is the hard limit.
#
# WHY THE COUNT HAD TO GO. A calendar day is not the unit the rule was ever
# about -- what #574 wanted to stop was a round per fix, and it said so ("a run
# per fix is how a round stops being a round"). Counting files per day answers
# that badly the moment more than one branch is in flight: on 22-09 a merge
# brought in another ticket's three rounds of the same day and the protocol
# refused a bundle that had taken none of them (#582). The question it asks now
# is the question it meant: has THIS head already been measured on this set,
# and is there money left.

# DocLayNet's eleven classes, mapped onto the five things this milestone builds.
# `furniture` is not an axis of its own here -- it is what #571 takes OUT of the
# body -- but it is counted, because a tool that leaves running headers in the
# text is wrong in a way the text axis reports as noise.
DOCLAYNET_CLASS = {
    1: "paragraph",   # Caption
    2: "paragraph",   # Footnote
    3: "paragraph",   # Formula
    4: "list_item",   # List-item
    5: "furniture",   # Page-footer
    6: "furniture",   # Page-header
    7: "ignore",      # Picture
    8: "heading",     # Section-header
    9: "table",       # Table
    10: "paragraph",  # Text
    11: "heading",    # Title
}

BODY = ("heading", "list_item", "table", "paragraph")

# What a set's ground truth carries, when a page does not say. DocLayNet draws
# every block on the page, which is what let the first rounds assume it; the
# table set draws tables and nothing else, and every axis that needs more says
# so rather than scoring a zero that is a statement about the corpus (#575).
EVERYTHING = ("heading", "list_item", "table", "paragraph", "furniture")


# ---------------------------------------------------------------------------
# The registers, and the refusals they carry.
# ---------------------------------------------------------------------------
def load_register(path: pathlib.Path, section: str) -> tuple[dict, dict]:
    """The section, AND the meta beside it.

    The meta was dropped here once, and with it `verdict_valid_days` -- so the
    two registers each declared that a verdict older than 180 days is absent
    permission, and nothing read it. A register whose expiry is not enforced is
    a register that says the terms were read once, which is the claim it exists
    to stop anybody making. (codex, #1922)
    """
    if not path.is_file():
        raise SystemExit(f"[{PREFIX}] FATAL: {path.relative_to(REPO)} is not there, "
                         "and nothing may be measured without it.")
    loaded = tomllib.loads(path.read_text())
    return loaded.get(section, {}), loaded.get("meta", {})


def fresh_enough(entry: dict, meta: dict, what: str) -> None:
    """Refuse an entry whose verdict has expired, by its own register's rule."""
    days = meta.get("verdict_valid_days")
    read_on = entry.get("read_on")
    if not days:
        return
    if not read_on:
        raise SystemExit(f"[{PREFIX}] REFUSED: `{what}` carries no `read_on`, so "
                         "its verdict cannot be shown to be current.")
    try:
        read = dt.date.fromisoformat(str(read_on))
    except ValueError:
        raise SystemExit(f"[{PREFIX}] REFUSED: `{what}` has an unreadable "
                         f"`read_on` ({read_on!r}).") from None
    age = (dt.date.today() - read).days
    if age > days:
        raise SystemExit(
            f"[{PREFIX}] REFUSED: the verdict for `{what}` was read on {read_on}, "
            f"{age} days ago, and this register treats anything older than "
            f"{days} days as absent rather than as permission. Read the terms "
            "again and write the date, then run this.")


def dataset_entry(name: str) -> dict:
    register, meta = load_register(DATASETS, "datasets")
    if name not in register:
        raise SystemExit(
            f"[{PREFIX}] FATAL: `{name}` has no row in "
            f"{DATASETS.relative_to(REPO)}. A set is read before it is fetched: "
            "write the row, with the clause and the quote, and then run this.")
    entry = register[name]
    if not entry.get("on_the_rig"):
        raise SystemExit(
            f"[{PREFIX}] REFUSED: `{entry.get('name', name)}` is not on the rig.\n"
            f"  why: {entry.get('why_not', 'no reason recorded')}\n"
            f"  clause: {entry.get('clause', '?')}\n"
            f"  read on: {entry.get('read_on', '?')}\n"
            f"Read the verdict in {DATASETS.relative_to(REPO)} before arguing "
            "with this: it is a reading of the terms, and the way to change it "
            "is to change the terms or the reading, in that file, on a ticket.")
    fresh_enough(entry, meta, entry.get("name", name))
    return entry


# WHAT A VERDICT IS, for the purpose of running a tool (#620): the licence, the
# clause that was read, the day it was read and the reading. A row missing any
# of them has not been read, whatever its flags say, and is refused -- the
# order the register prescribes is "read, write the verdict, then run".
VERDICT_FIELDS = ("licence", "clause", "read_on", "verdict")


def tools_allowed(asked: list[str], register: dict | None = None,
                  meta: dict | None = None) -> tuple[dict, list[dict]]:
    """The tools that may be run, and the refusals, both spelt out.

    Returns the allowed entries and a list of refusals -- which go in the run
    file rather than being dropped. A table of two columns where a reader
    expected three has to say which one is missing and why, on the same page as
    the numbers; a refusal recorded only in a register nobody opens is a
    footnote that will be lost the first time the table is copied.
    """
    if register is None:
        register, meta = load_register(EXTRACTORS, "extractors")
    allowed: dict = {}
    refused: list[dict] = []
    for name in asked:
        key = {"ours": OURS}.get(name, name)
        entry = register.get(key)
        if entry is None:
            refused.append({"tool": name, "why": "no row in the extractor register",
                            "clause": None})
            continue
        unread = [f for f in VERDICT_FIELDS if not str(entry.get(f) or "").strip()]
        if unread:
            refused.append({"tool": entry.get("name", name),
                            "why": "no licence verdict: " + ", ".join(unread) + " missing",
                            "clause": entry.get("clause")})
            continue
        # THE STRUCTURED RIG'S OWN FLAG (#620). `on_the_rig` is the plain-text
        # matrix of #390, which refuses a tool on its rig without a run of it;
        # the tools of #619 are measured here and nowhere else.
        if not entry.get("on_the_structured_rig"):
            refused.append({"tool": entry.get("name", name),
                            "why": entry.get("structured_why_not") or entry.get("why_not")
                            or "not on the structured rig",
                            "clause": entry.get("clause"),
                            "read_on": entry.get("read_on")})
            continue
        fresh_enough(entry, meta or {}, entry.get("name", name))
        allowed[key] = entry
    return allowed, refused


# ---------------------------------------------------------------------------
# The set: fetched to the machine, never to the Mac.
# ---------------------------------------------------------------------------
def fetch(entry: dict, work: pathlib.Path, limit: int | None) -> list[dict]:
    """Materialise the set as one PDF and one ground-truth file per page.

    The parquet rows carry the original PDF beside the annotation, which is the
    property that made this set the one (DATASETS.toml). Pages are written out
    rather than held in memory because the tools are separate processes: a
    benchmark that fed one tool from memory and another from disk would be
    measuring the difference.
    """
    # pdfluent-bench: the engine rig-data guard is not ported (PORTING.md).
    try:
        import pyarrow.parquet as pq
        from huggingface_hub import hf_hub_download
    except ImportError as exc:  # pragma: no cover - the machine has them
        raise SystemExit(
            f"[{PREFIX}] FATAL: {exc}. The rig installs these: "
            "`bash scripts/extraction/rig_bootstrap.sh`.")

    pdfs = work / "pages"
    pdfs.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    # THE REVISION, NOT THE BRANCH. `main` moves, and a seal over pages fetched
    # from "whatever main was that morning" seals a set nobody can get back: the
    # manifest would still say DocLayNet v1.2 while naming different pages. So
    # the register pins a commit and the fetch asks for it. (codex, #1922)
    revision = entry.get("revision")
    if not revision:
        raise SystemExit(
            f"[{PREFIX}] FATAL: `{entry.get('name')}` has no `revision` in "
            f"{DATASETS.relative_to(REPO)}. A set fetched from a moving branch "
            "cannot be sealed: pin the commit, then run this.")
    for part in range(entry.get("parts", 6)):
        name = f"data/test-{part:05d}-of-{entry.get('parts', 6):05d}.parquet"
        print(f"[{PREFIX}] fetching {name} at {revision[:12]}", flush=True)
        path = hf_hub_download(repo_id=entry["repo_id"], filename=name,
                               repo_type="dataset", revision=revision,
                               cache_dir=str(work / "hf"))
        table = pq.read_table(path)
        for batch in table.to_batches(max_chunksize=64):  # noqa: B007
            for record in batch.to_pylist():
                row = _page_of(record, pdfs)
                if row is not None:
                    rows.append(row)
                if limit is not None and len(rows) >= limit:
                    return rows
    return rows


# ---------------------------------------------------------------------------
# The table set: annotations from one archive, documents from another (#575).
# ---------------------------------------------------------------------------
# How far a cell's text has to be found on the page before the annotation and
# the document are believed to be talking about the same page. The pages are
# CUT OUT of an article by an index the annotation gives, and an off-by-one
# there would grade every tool against the wrong page while every digest in the
# manifest stayed honest. So it is checked with poppler -- a reader that is
# neither ours nor the reference's -- and the pages that fail are counted.
ALIGNED_SHARE = 0.6
# Below this many characters a cell's text says nothing about which page it is
# on: "12" is on every page of a table of numbers.
ALIGNED_MIN_CHARS = 4


def _get(url: str, timeout: int = 60) -> bytes | None:
    request = urllib.request.Request(url, headers={
        # Who is asking, because an archive that has to rate-limit somebody
        # should be able to see who. The PMC Cloud Service is the only route
        # its terms allow for automated retrieval, and this is that route.
        "User-Agent": "pdfluent-structured-bench/1.0 (benchmark fetch; "
                      "https://pdfluent.com)"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as answer:
            return answer.read()
    except (urllib.error.URLError, OSError, ValueError):
        return None


def article_record(bucket: str, pmcid: str) -> dict | None:
    """The newest version of one article in the Cloud Service, and its licence.

    The bucket keys an article as `PMC1234567.<version>/`, and a listing is how
    a caller learns which versions exist. The metadata beside the PDF carries
    `license_code`, which is the per-article licence statement the subset's
    terms point at -- read BEFORE the PDF, so that a document whose terms
    refuse us is never fetched at all.
    """
    listing = _get(f"{bucket}/?list-type=2&delimiter=/&prefix={pmcid}.")
    if listing is None:
        return None
    versions = re.findall(rf"<Prefix>({re.escape(pmcid)}\.(\d+))/</Prefix>",
                          listing.decode("utf-8", "replace"))
    if not versions:
        return None
    key = max(versions, key=lambda v: int(v[1]))[0]
    meta = _get(f"{bucket}/{key}/{key}.json")
    if meta is None:
        return None
    try:
        record = json.loads(meta)
    except json.JSONDecodeError:
        return None
    return {"key": key, "licence": record.get("license_code"),
            "pdf": f"{bucket}/{key}/{key}.pdf",
            "retracted": bool(record.get("is_retracted"))}


def may_fetch(record: dict | None, allowed: set[str]) -> str | None:
    """Why this article's PDF may not be fetched, or None if it may.

    The decision is here, out of the loop, because it is the licence rule of
    this set and a rule that can only be exercised by buying a machine and a
    network is a rule nobody can test. "Terms vary per article" is the subset's
    own sentence, so an article with no code at all is refused rather than
    treated as permission -- silence is not a licence, which is the same
    reading `icdar2019-ctdar` was refused under.
    """
    if record is None:
        return "the archive did not answer"
    code = record.get("licence") or "no code"
    if code not in allowed:
        return f"licence {code}"
    if record.get("retracted"):
        return "retracted"
    return None


# Said once per run by `_page_text`, for the record: whether pdftotext runs
# under `systemd-run`, and the first page's outcome with it. A round whose
# pages read "" has to show which path ran -- the tool's answer and the
# ceiling's kill look identical downstream. Only when STRUCTURED_MEMORY_MAX
# is set: without a ceiling there is no second path to tell apart.
_PAGE_TEXT_PATH_LOGGED = False


def _log_page_text_path_once(probe: dict) -> None:
    """Log, once per run, which path pdftotext runs on. See above."""
    global _PAGE_TEXT_PATH_LOGGED
    ceiling = os.environ.get("STRUCTURED_MEMORY_MAX", "").strip()
    if _PAGE_TEXT_PATH_LOGGED or not ceiling:
        return
    _PAGE_TEXT_PATH_LOGGED = True
    if shutil.which("systemd-run") is not None:
        print(f"[{PREFIX}] pdftotext runs under systemd-run "
              f"(STRUCTURED_MEMORY_MAX={ceiling}); first page: outcome "
              f"{probe.get('outcome')}, systemd-run exit status "
              f"{probe.get('returncode')}", flush=True)
    else:
        print(f"[{PREFIX}] STRUCTURED_MEMORY_MAX={ceiling} is set but "
              "systemd-run is not on PATH, so pdftotext runs unwrapped; "
              f"first-page outcome {probe.get('outcome')}", flush=True)


def _page_text(path: pathlib.Path) -> str:
    # A MISSING TOOL IS AN ERROR, NOT EMPTY TEXT. run_tool reports a
    # binary that is not on PATH as a crash with no output, so without
    # this check a machine without pdftotext reads every page as "" and
    # the alignment check refuses every page as unaligned -- the T5
    # train's attempt 9 (04-10), which kept 0 of 264 dev pages that way.
    # A page that reads empty WITH the tool present is still a normal
    # refusal further down this file, not an error here.
    if shutil.which("pdftotext") is None:
        raise SystemExit(
            "REFUSED: pdftotext is not installed (poppler-utils): the "
            "alignment check cannot read the page's own text, and an "
            "empty read would refuse every page as unaligned")
    # A TOOL FAILURE IS NAMED, NEVER READ AS TEXT. `run_tool` reports a
    # crash or a timeout with no output, and this used to read that as "":
    # post-W, a pdftotext killed under the round's memory ceiling FATALed
    # a PINNED page as "the page differs" -- the same bytes, the same
    # poppler, a different tool outcome. An empty read with outcome ok
    # stays what it is further down this file: an unaligned page, skipped.
    probe: dict = {}
    out, _ms = run_tool(["pdftotext", "-q", str(path), "-"], timeout=120,
                        probe=probe)
    _log_page_text_path_once(probe)
    outcome = probe.get("outcome")
    if outcome in ("crash", "timeout"):
        raise SystemExit(
            f"REFUSED: pdftotext {outcome} on {path.name}: the tool did not "
            "answer, so there is no page text to check -- and reading its "
            "silence as \"\" would refuse the page as unaligned.")
    return axes.normalise(out or "").lower()


def _aligned(tables: list[dict], page_text: str) -> bool:
    """Is this the page the annotation says it is?"""
    wanted = [axes.normalise(t).lower()
              for table in tables
              for t in html.unescape(
                  " \0".join(re.findall(r">([^<]+)<", table["html"]))).split("\0")
              if len(axes.normalise(t)) >= ALIGNED_MIN_CHARS]
    if not wanted:
        return False
    found = sum(1 for t in wanted if t in page_text)
    return found >= ALIGNED_SHARE * len(wanted)


def fetch_pubtables(entry: dict, work: pathlib.Path, limit: int | None) -> list[dict]:
    """The table set: cells from PubTables-1M, pages from the PMC Cloud Service.

    THE ORDER OF THE TWO FETCHES IS THE LICENCE RULE. The annotations are one
    archive under one permissive agreement and may be taken whole; the
    documents are one article at a time under terms that VARY, so each one's
    own licence code is read before its bytes are asked for, and a code the
    register does not allow means the article is never fetched. The refusals
    are counted and land in the manifest, so the corpus reads back as what was
    allowed rather than as what was taken.

    A PAGE IS CUT OUT OF ITS ARTICLE, because the annotation is per page and
    every axis here is per page. The cut is checked against the page's own text
    with poppler before the page is kept -- see ALIGNED_SHARE.
    """
    # pdfluent-bench: the engine rig-data guard is not ported (PORTING.md).
    try:
        from huggingface_hub import hf_hub_download
    except ImportError as exc:  # pragma: no cover - the machine has it
        raise SystemExit(
            f"[{PREFIX}] FATAL: {exc}. The rig installs it: "
            "`bash scripts/extraction/rig_bootstrap.sh`.")

    revision = entry.get("revision")
    if not revision:
        raise SystemExit(f"[{PREFIX}] FATAL: `{entry.get('name')}` has no "
                         "`revision`; a set fetched from a moving branch "
                         "cannot be sealed.")
    allowed = set(entry.get("documents_licence_codes") or [])
    if not allowed:
        raise SystemExit(
            f"[{PREFIX}] FATAL: `{entry.get('name')}` names no allowed licence "
            "code. This set's documents are licensed per article and a fetch "
            "with no list to check against would take whatever it found.")
    bucket = entry["documents_bucket"].rstrip("/")

    print(f"[{PREFIX}] fetching {entry['annotations_file']} at {revision[:12]}",
          flush=True)
    archive = hf_hub_download(repo_id=entry["repo_id"],
                              filename=entry["annotations_file"],
                              repo_type="dataset", revision=revision,
                              cache_dir=str(work / "hf"))

    pdfs = work / "pages"
    pdfs.mkdir(parents=True, exist_ok=True)
    articles = work / "articles"
    articles.mkdir(parents=True, exist_ok=True)
    seed = holdout_rule.seed_for("pubtables-1m")
    source = "pubtables-1m/pmc-open-access"

    # HOW MANY PAGES THIS SET IS, and it is in the register rather than in a
    # flag. The archive holds 401,733 articles and the documents are fetched
    # one at a time from a second archive: materialising all of them is not a
    # round, it is a mirror of somebody else's corpus. So the set IS the first
    # `materialise_pages` pages of the seeded walk that the licence allows and
    # the alignment check keeps -- a published rule, reproducible by anybody
    # with this repository, and the manifest names every page of it.
    ceiling = int(entry.get("materialise_pages") or 0)
    if not ceiling:
        raise SystemExit(
            f"[{PREFIX}] FATAL: `{entry.get('name')}` has no "
            "`materialise_pages`. This set's documents are fetched one at a "
            "time from a public archive, and a fetch with no ceiling would "
            "walk 401,733 of them.")
    if limit is not None:
        ceiling = min(ceiling, limit)
    # How far down the seeded walk the fetch is allowed to look for those
    # pages. Most articles give one or two, some give none, and some are
    # refused -- so the walk is longer than the set, and it is bounded too: a
    # round that cannot fill the set says so rather than running until the
    # machine is deleted underneath it.
    candidates = ceiling * int(entry.get("walk_multiplier") or 8)

    rows: list[dict] = []
    refused: dict[str, int] = {}
    unaligned = 0
    # TWO STREAMING PASSES, NOT ONE PASS AND RANDOM ACCESS. A `.tar.gz` is a
    # stream: seeking back to a member that has already gone past means
    # decompressing from the beginning again, so reading 400 members in seeded
    # order out of 401,733 is 400 decompressions of a 3.7 GB archive. The first
    # pass reads the NAMES, which is all the seeded order needs; the second
    # extracts the few thousand the order asks for, in the order the archive
    # has them.
    with tarfile.open(archive, "r|gz") as tar:
        names: dict[str, str] = {}
        for member in tar:
            if not member.isfile():
                continue
            pmcid = pubtables_cells.pmcid_of(pathlib.PurePath(member.name).name)
            if pmcid:
                names[pmcid] = member.name
    print(f"[{PREFIX}] {len(names)} annotated article(s) in the archive",
          flush=True)
    walk = sorted(names, key=lambda a: holdout_rule.rank(source, a, seed))[:candidates]
    wanted_members = {names[pmcid] for pmcid in walk}
    annotations: dict[str, list] = {}
    with tarfile.open(archive, "r|gz") as tar:
        for member in tar:
            if member.name not in wanted_members:
                continue
            handle = tar.extractfile(member)
            if handle is None:
                continue
            pmcid = pubtables_cells.pmcid_of(pathlib.PurePath(member.name).name)
            try:
                annotations[pmcid] = json.loads(handle.read())
            except json.JSONDecodeError:
                refused["unreadable annotation"] = refused.get(
                    "unreadable annotation", 0) + 1
    print(f"[{PREFIX}] {len(annotations)} annotation(s) read of the first "
          f"{len(walk)} in the seeded walk", flush=True)

    for pmcid in walk:
        if len(rows) >= ceiling:
            break
        annotation = annotations.get(pmcid)
        if annotation is None:
            continue
        by_page = pubtables_cells.tables_by_page(annotation)
        if not by_page:
            refused["no table above the floor"] = refused.get(
                "no table above the floor", 0) + 1
            continue

        record = article_record(bucket, pmcid)
        why_not = may_fetch(record, allowed)
        if why_not:
            refused[why_not] = refused.get(why_not, 0) + 1
            continue
        code = record["licence"]
        blob = _get(record["pdf"], timeout=180)
        if not blob:
            refused["the PDF did not answer"] = refused.get(
                "the PDF did not answer", 0) + 1
            continue
        article = articles / f"{pmcid}.pdf"
        article.write_bytes(blob)

        kept = 0
        for index in sorted(by_page):
            if len(rows) >= ceiling:
                break
            page = pdfs / f"{pmcid}-p{index:04d}.pdf"
            # `mutool merge` with one page number cuts that page out. The
            # index the annotation gives is zero-based and mutool counts
            # from one.
            if not merge_document([f"{article}", str(index + 1)], page):
                continue
            if not _aligned(by_page[index], _page_text(page)):
                page.unlink(missing_ok=True)
                unaligned += 1
                continue
            blocks = [{"kind": "table", "text": pubtables_cells.text_of(t["html"]),
                       "html": t["html"], "order": i, "bbox": t["bbox"]}
                      for i, t in enumerate(by_page[index])]
            rows.append({
                "source": source,
                "name": page.name,
                "document": pmcid,
                "page_no": index,
                "category": "scientific articles",
                "licence": code,
                "sha256": hashlib.sha256(page.read_bytes()).hexdigest(),
                "path": str(page),
                # WHAT THIS SET DRAWS, from the register rather than from here:
                # it is a property of the corpus, and an axis a set does not
                # annotate is reported as unscored with the reason rather than
                # as a zero that is a statement about the corpus. Keeping it on
                # the page is what lets a rescore read it back. (codex, #1930)
                "annotates": list(entry.get("annotates") or ["table"]),
                "blocks": blocks,
            })
            kept += 1
        article.unlink(missing_ok=True)
        if not kept:
            refused["no page survived the alignment check"] = refused.get(
                "no page survived the alignment check", 0) + 1
    print(f"[{PREFIX}] kept {len(rows)} page(s); "
          f"{unaligned} page(s) failed the alignment check; refusals: "
          f"{json.dumps(refused, sort_keys=True)}", flush=True)
    (work / "fetch-refusals.json").write_text(json.dumps(
        {"refused": refused, "pages_not_aligned": unaligned,
         "allowed_licence_codes": sorted(allowed)}, indent=1))
    # A SHORT FETCH IS A DIFFERENT SET, AND IT MUST NOT PASS AS THIS ONE. The
    # register defines this corpus as the first `materialise_pages` pages the
    # licence allows and the alignment keeps; a walk cut short by a transient
    # failure at the archive would otherwise write a smaller `pages.json`, the
    # next `--draw` would seal it under the same name, and a round would report
    # a number about a corpus the register does not describe. Widen
    # `walk_multiplier` on a ticket, or find out why the archive stopped
    # answering -- but do not quietly measure something else. (codex, #1930)
    if len(rows) != ceiling:
        raise SystemExit(
            f"[{PREFIX}] FATAL: the walk kept {len(rows)} page(s) and the "
            f"register asks for {ceiling}. It looked at {len(walk)} article(s) "
            f"({entry.get('walk_multiplier')} x the set) and was refused "
            f"{sum(refused.values())} time(s): "
            f"{json.dumps(refused, sort_keys=True)}; {unaligned} page(s) did "
            "not survive the alignment check. A short fetch is a different "
            "corpus, and sealing it under this name would publish a figure "
            "about a set the register does not describe.")
    return rows


# ---------------------------------------------------------------------------
# FinTabNet draws a PINNED page list; a failed read fails the run (#574, T3).
# ---------------------------------------------------------------------------
# WHY. Two rounds drew DIFFERENT pages -- 33 of 120 sealed pages shared -- so
# the summary showed a false TEDS drop where the paired comparison on the
# shared pages was flat. The seeded walk is deterministic, but any page refused
# for a TRANSIENT reason was skipped and the walk took the next candidates:
# one bad fetch changed the kept 300, hence the caps, hence the sealed draw.
#
# TRANSIENT vs STRUCTURAL. Structural is a property of the page and
# deterministic: no table above the floor, over the per-report or per-company
# cap, retracted -- and, in the walk, the alignment check. `archive.fetch`
# already checks the member CRC32, so the bytes it returns are the full read;
# an alignment failure on them is SKIPPED -- counted, the page unlinked, the
# walk moves on -- with NO retry, because a retry would re-read the same
# bytes. (Not re-run the same tool run: poppler on the same bytes can still
# answer differently -- a crash, a timeout -- and that is a tool failure,
# named loudly by `_page_text`, never read as text.) Transient is a property
# of this fetch: provenance mismatch, CRC32 mismatch, the page did not answer.
#
# A transient refusal is RETRIED up to FINTABNET_RETRIES times with backoff.
# If it still fails, the fetch FAILS LOUDLY -- SystemExit naming the page and
# the reason -- and NEVER moves on to the next candidate. A short fetch that
# substituted other pages would be a different set under this name. PINNED
# pages are NOT re-aligned: the pin CRC check and `Archive.fetch`'s CRC check
# prove the bytes are the pinned page's, the alignment check ran when the pin
# was made, and re-running it would test the tool's answer, not the page
# (post-W: a crash under the memory ceiling read as "" and FATALed "the page
# differs" on identical bytes).
# A TRANSPORT failure is FATAL at once, with NO retry here: the reader raised
# it only after its own five tries, so this loop would ask five times four.
# The loop stays for provenance and CRC only.
#
# THE PIN. `corpus/structured/FINTABNET_DRAW.json` is the ordered list of kept
# page names with each page's member CRC32 and the sealed split it goes to,
# plus a sha256 of the list. When the pin exists, the fetch reads EXACTLY
# those pages (no walk), verifies each, and refuses to seal if any differs.
# With no pin, it walks as before (with retries) and writes `fetch-kept.json`
# -- the would-be pin -- into the work dir and the evidence, so the next
# healthy rig run produces the pin to commit.
FINTABNET_DRAW = REPO / "corpus" / "structured" / "FINTABNET_DRAW.json"
# Retries after the first attempt, so 3 means up to 4 attempts per page.
FINTABNET_RETRIES = 3
FINTABNET_BACKOFF_S = (1.0, 2.0, 4.0)
# Provenance failures against one jsonl file at once that mean the file
# itself was short-read: re-fetch it once and verify against the register
# before failing.
FINTABNET_PROVENANCE_REFETCH_AT = 5
# THE ARCHIVE'S SOURCE: our GCS mirror when one is given, else the HF CDN.
# Range reads of the release through the HuggingFace CDN fail under load
# (likely 429), and a throttled round once lost 19,100 of 20,000 tables to
# what read as data refusals -- so the rig reads the server-side copy
# through a signed URL the COS signs per round, the same way the teacher
# weights come in: the env var first, then the file placed by whoever
# starts the round. The URL is NEVER printed -- errors name the source as
# "the GCS mirror", and the evidence records only the label below.
FINTABNET_ARCHIVE_URL_ENV = "FINTABNET_ARCHIVE_URL"
FINTABNET_ARCHIVE_URL_FILE = pathlib.Path("/opt/ex/fintabnet/archive.url")

_PIN_NAME = re.compile(r"^(.+)_(\d{4})_page_(\d+)\.pdf$")


def _fintabnet_pin_sha(pages: list[dict]) -> str:
    """The sha256 of a pin's page list, in the order it is pinned in.

    Over `name`, `crc32` and `split` per page, NUL-separated, one page per
    line: the same shape as `holdout_rule.digest_of`, so a reordered list or
    a moved page changes the digest.
    """
    h = hashlib.sha256()
    for page in pages:
        h.update(f"{page['name']}\0{page['crc32']}\0{page['split']}\n".encode())
    return h.hexdigest()


def _read_fintabnet_pin() -> dict | None:
    """The committed pin, verified, or None when no pin is committed yet.

    Refuses -- SystemExit -- a pin whose sha is not the digest of its own
    list: a pin that does not verify is not a pin.
    """
    if not FINTABNET_DRAW.is_file():
        return None
    try:
        pin = json.loads(FINTABNET_DRAW.read_text())
    except (OSError, ValueError) as exc:
        raise SystemExit(
            f"[{PREFIX}] FATAL: {FINTABNET_DRAW.relative_to(REPO)} is not "
            f"readable JSON: {exc}.") from None
    pages = pin.get("pages")
    if not isinstance(pages, list) or not pages:
        raise SystemExit(
            f"[{PREFIX}] FATAL: {FINTABNET_DRAW.relative_to(REPO)} names no "
            "pages, so it pins nothing.")
    if pin.get("sha256") != _fintabnet_pin_sha(pages):
        raise SystemExit(
            f"[{PREFIX}] FATAL: {FINTABNET_DRAW.relative_to(REPO)} has sha256 "
            f"{str(pin.get('sha256'))[:16]}... but its list digests to "
            f"{_fintabnet_pin_sha(pages)[:16]}.... A pin that does not verify "
            "is not a pin.")
    return pin


def _fintabnet_key_of_pinned(name: str) -> tuple[str, str, int]:
    """`(company, year, num)` for a pinned page name, or a loud refusal."""
    found = _PIN_NAME.match(name or "")
    if not found:
        raise SystemExit(
            f"[{PREFIX}] FATAL: pinned page {name!r} does not parse as "
            "`<COMPANY>_<YEAR>_page_<N>.pdf`.")
    return found.group(1), found.group(2), int(found.group(3))


def _sleep_backoff(attempt: int) -> None:
    """Sleep before retry `attempt` (0-based): 1s, 2s, 4s."""
    if attempt < len(FINTABNET_BACKOFF_S):
        time.sleep(FINTABNET_BACKOFF_S[attempt])
    else:
        time.sleep(FINTABNET_BACKOFF_S[-1])


def _fintabnet_archive_source(entry: dict, revision: str,
                              ) -> tuple[str, str]:
    """Where the archive is read from: the mirror, or the HF CDN as today.

    The env var first, then the file, then the pinned HF URL as today. An
    empty var or an empty or missing file falls through to the next -- a
    round that meant to use the mirror says so with a URL in it, not with a
    file that exists. The returned URL is never printed anywhere: callers
    name the label, never the address.
    """
    url = (os.environ.get(FINTABNET_ARCHIVE_URL_ENV) or "").strip()
    if not url:
        try:
            url = FINTABNET_ARCHIVE_URL_FILE.read_text().strip()
        except OSError:
            url = ""
    if url:
        return "gcs-mirror", url
    return ("hf-cdn", fintabnet_archive.documents_url(
        entry["repo_id"], revision, entry["documents_archive"]))


def _write_fintabnet_kept(work: pathlib.Path, rows: list[dict],
                          archive, seed: str, archive_source: str,
                          transport_retries: int) -> dict:
    """The kept pages as the pin the next healthy run commits.

    Ordered as kept, with each page's member CRC32 from the central directory
    and the split the seal will put it in, plus the sha256 of the list. Names
    and CRCs only: no page bytes, no annotations. The source that served the
    bytes rides along -- never the URL -- so the committed pin says which
    mirror drew it.
    """
    _tuning, holdout = holdout_rule.split(rows, seed=seed)
    held = {p["name"] for p in holdout}
    pages = []
    for row in rows:
        company, year, num = _fintabnet_key_of_pinned(row["name"])
        member = fintabnet_archive.zip_path_of(company, year, num)
        member_entry = archive.entries.get(member)
        if member_entry is None:
            raise SystemExit(
                f"[{PREFIX}] FATAL: kept page {row['name']} is not in the "
                "archive directory, so no pin can name its CRC32.")
        pages.append({
            "name": row["name"],
            "crc32": f"{member_entry.crc:08x}",
            "split": "holdout" if row["name"] in held else "tuning",
        })
    kept = {"set": "fintabnet", "mode": "walked, no pin",
            "source": archive_source,
            "transport_retries": transport_retries, "pages": pages,
            "sha256": _fintabnet_pin_sha(pages)}
    (work / "fetch-kept.json").write_text(json.dumps(kept, indent=1) + "\n")
    return kept


def fetch_fintabnet(entry: dict, work: pathlib.Path, limit: int | None) -> list[dict]:
    """Borderless financial tables, end to end from the PDF (#574).

    THE SECOND SET THAT IS TWO SOURCES: the cells from FinTabNet.c
    (Microsoft, CDLA-Permissive-2.0), one file per page, and the pages from
    IBM's FinTabNet 1.0.0 through the pinned mirror. The annotations are one
    archive under one agreement and may be taken whole; the documents are
    read by HTTP Range out of a 16 GB zip and never downloaded whole -- the
    directory once, then per page the local header plus the compressed data.

    THE LANE IS THE EVAL SIDE ONLY, company-disjoint: a page of an eval
    company in the training dump would be tuning on the rig. The rule is one
    function (`fintabnet_split.side`), used here and -- later -- there.

    A page is kept when its annotation names at least one table above the
    floor, its report and its company still have room, every table proves
    itself against IBM's own jsonl line, the bytes match the directory's
    CRC32, and -- in the walk -- poppler finds the tables' text on the page.
    A pinned page is identified by its bytes, not re-aligned by a tool.

    TRANSIENT FAILS LOUDLY, STRUCTURAL SKIPS. No table above the floor,
    the two caps and (in the walk) the alignment check are structural -- a
    property of the page, on CRC-verified bytes -- and are counted and left
    out as before, with NO retry. Provenance, CRC32 and a page that did not
    answer are transient -- a property of this fetch -- and are retried with
    backoff; if they still fail, the fetch raises naming the page and NEVER
    substitutes the next candidate, because one bad fetch once changed the
    kept 300 and with it the sealed draw. A TRANSPORT failure is FATAL at
    once, with NO retry here: the reader raised it only after its own
    retries. A short fetch refuses to seal, exactly as the PubTables lane
    does. PINNED pages skip the alignment check: the pin CRC check and
    `Archive.fetch`'s CRC check prove the bytes are the pinned page's, the
    alignment check ran when the pin was made, and a tool failure now --
    a crash, a timeout -- is named loudly by `_page_text`, never read as
    a page that differs.

    PINNED OR WALKED. When `corpus/structured/FINTABNET_DRAW.json` exists,
    exactly its pages are read in its order, each verified against its CRC32
    and its sealed split, and any difference refuses to seal. With no pin,
    the seeded walk runs and `fetch-kept.json` -- the would-be pin -- is
    written beside the evidence.
    """
    # pdfluent-bench: the engine rig-data guard is not ported (PORTING.md).

    # THE PINNING, FIRST: both sources pinned or no fetch. Before the split
    # refusal so that the refusal is testable where it runs -- a pinning
    # check behind the round-machine gate could only be exercised by buying a
    # machine, which is how a check stops being checked. No bytes move before
    # either of them, so the protection is the same in both orders.
    revision = entry.get("revision")
    if not revision:
        raise SystemExit(f"[{PREFIX}] FATAL: `{entry.get('name')}` has no "
                         "`revision` for its documents; a set fetched from a "
                         "moving branch cannot be sealed.")
    arevision = entry.get("annotations_revision")
    if not arevision:
        raise SystemExit(f"[{PREFIX}] FATAL: `{entry.get('name')}` has no "
                         "`annotations_revision`; a set fetched from a moving "
                         "branch cannot be sealed.")
    try:
        from huggingface_hub import hf_hub_download
    except ImportError as exc:  # pragma: no cover - the machine has it
        raise SystemExit(
            f"[{PREFIX}] FATAL: {exc}. The rig installs it: "
            "`bash scripts/extraction/rig_bootstrap.sh`.")

    ceiling = int(entry.get("materialise_pages") or 0)
    if not ceiling:
        raise SystemExit(
            f"[{PREFIX}] FATAL: `{entry.get('name')}` has no "
            "`materialise_pages`. This set's documents are read one at a "
            "time out of a 16 GB archive, and a fetch with no ceiling would "
            "walk 89,646 of them.")
    if limit is not None:
        ceiling = min(ceiling, limit)
    candidates = ceiling * int(entry.get("walk_multiplier") or 3)

    print(f"[{PREFIX}] fetching {entry['annotations_file']} at "
          f"{arevision[:12]}", flush=True)
    annotations_tar = hf_hub_download(
        repo_id=entry["annotations_repo_id"], filename=entry["annotations_file"],
        repo_type="dataset", revision=arevision, cache_dir=str(work / "hf"))

    pdfs = work / "pages"
    pdfs.mkdir(parents=True, exist_ok=True)
    seed = holdout_rule.seed_for("fintabnet")
    source = fintabnet_archive.SOURCE

    # TWO STREAMING PASSES, for the reason the PubTables lane gives: a
    # `.tar.gz` is a stream, and reading the wanted members in seeded order
    # out of 89,646 means decompressing from the start either never or
    # always. The first pass reads the names; the second extracts the few the
    # walk asks for.
    with tarfile.open(annotations_tar, "r|gz") as tar:
        names = fintabnet_archive.annotation_names(tar)
    print(f"[{PREFIX}] {len(names)} annotated page(s) in the archive",
          flush=True)
    lane = fintabnet_archive.eval_names(names)
    print(f"[{PREFIX}] {len(lane)} of them on the eval side", flush=True)
    # PINNED OR WALKED. A verified pin names exactly the pages to read, in
    # order; without one the seeded walk runs and the kept list is written
    # back as the would-be pin. Read here -- after the annotations are in
    # hand, before anything is chosen -- so both modes share the passes.
    pin = _read_fintabnet_pin()
    pinned_by_name: dict[str, dict] | None = None
    if pin is not None:
        pinned_by_name = {p["name"]: p for p in pin["pages"]}
        plan = [_fintabnet_key_of_pinned(p["name"]) for p in pin["pages"]]
        if limit is not None:
            # A smoke run reads the head of the pin; the split check below
            # is skipped for a head, since a head seals differently.
            plan = plan[:ceiling]
        missing = [fintabnet_archive.walk_name(*k) + ".pdf" for k in plan
                   if k not in lane]
        if missing:
            raise SystemExit(
                f"[{PREFIX}] FATAL: pinned page(s) {', '.join(missing[:3])} "
                "have no annotation in the archive. The pin names pages the "
                "annotations no longer hold; refusing to seal.")
        wanted = {lane[key] for key in plan}
        print(f"[{PREFIX}] pinned draw {pin['sha256'][:12]}: "
              f"{len(plan)} page(s) in pin order, no walk", flush=True)
        walk: list = list(plan)
    else:
        walk = sorted(lane, key=lambda k: holdout_rule.rank(
            source, fintabnet_archive.walk_name(*k), seed))[:candidates]
        wanted = {lane[key] for key in walk}
        plan = list(walk)
    with tarfile.open(annotations_tar, "r|gz") as tar:
        annotations, unreadable = fintabnet_archive.read_annotations(
            tar, wanted)
    refused: dict[str, int] = {}
    if unreadable:
        refused["unreadable annotation"] = unreadable
    print(f"[{PREFIX}] {len(annotations)} annotation(s) read of "
          f"{len(plan)} {'pinned' if pin is not None else 'in the seeded walk'}",
          flush=True)

    archive_source, archive_url = _fintabnet_archive_source(
        entry, revision)
    where = ("the GCS mirror" if archive_source == "gcs-mirror"
             else "the HF CDN")
    print(f"[{PREFIX}] reading the central directory of "
          f"{entry['documents_archive']} by range from {where}", flush=True)
    reader = fintabnet_archive.RangeReader(archive_url)
    if archive_source == "gcs-mirror":
        # IDENTITY BEFORE TRUST: the mirror's own size -- the Content-Range
        # total of one ranged byte, not a 16 GB download -- must be the
        # register's, before the directory is even read. The member CRC32s,
        # and with them the pin (FINTABNET_DRAW.json), are identical
        # whichever source served the bytes: the mirror is a copy of the
        # release, and every member is still checked against the directory.
        want_bytes = int(entry["documents_archive_bytes"])
        mirror_bytes = reader.size()
        if mirror_bytes is None:
            raise SystemExit(
                f"[{PREFIX}] REFUSED: the GCS mirror did not answer its "
                f"size, so it cannot be the register's {want_bytes} bytes.")
        if mirror_bytes != want_bytes:
            raise SystemExit(
                f"[{PREFIX}] REFUSED: the GCS mirror holds {mirror_bytes} "
                f"bytes and the register pins {want_bytes}.")
    read = reader.read
    archive = fintabnet_archive.open_archive(
        read, int(entry["documents_archive_bytes"]))
    print(f"[{PREFIX}] {len(archive.entries)} member(s) in the directory",
          flush=True)
    # Refuses the whole set -- SystemExit -- unless the LICENSE reads as the
    # verdict did and the PDF count is the register's. A transport failure
    # here is fatal too: a round that cannot read the archive says so rather
    # than measuring something else.
    try:
        fintabnet_archive.check_set(archive, int(entry["documents_pdfs"]))
    except fintabnet_archive.TransportFailed as exc:
        raise SystemExit(
            f"[{PREFIX}] FATAL: the archive did not answer: {exc}.") from None
    expected_jsonl = {row.get("file"): row
                      for row in (entry.get("jsonl_files") or [])
                      if row.get("file")}
    jsonls = fintabnet_archive.JsonlCache(archive, expected=expected_jsonl)
    caps = fintabnet_archive.Caps()

    rows: list[dict] = []
    unaligned = 0
    max_attempts = FINTABNET_RETRIES + 1
    # Provenance failures per jsonl file, across the pages so far: many at
    # once mean the file itself was short-read, and it is re-fetched once.
    provenance_counts: dict[str, int] = {}
    provenance_refreshed: set[str] = set()
    for company, year, num in plan:
        if len(rows) >= ceiling:
            break
        name = fintabnet_archive.walk_name(company, year, num) + ".pdf"
        annotation = annotations.get((company, year, num))
        if annotation is None:
            if pinned_by_name is not None:
                raise SystemExit(
                    f"[{PREFIX}] FATAL: pinned page {name}: its annotation "
                    "was not read from the archive. Refusing to seal a page "
                    "that differs.")
            continue
        kept = fintabnet_archive.kept_tables(annotation)
        tables = pubtables_cells.tables_by_page(kept).get(0, [])
        if not tables:
            if pinned_by_name is not None:
                raise SystemExit(
                    f"[{PREFIX}] FATAL: pinned page {name} names no table "
                    "above the floor. The annotation differs from the pin; "
                    "refusing to seal.")
            refused["no table above the floor"] = refused.get(
                "no table above the floor", 0) + 1
            continue
        document = fintabnet_archive.document_of(company, year)
        # BEFORE ANY BYTE OF THE PAGE: the jsonl is already here and the PDF
        # is one range read away, so a page its report cannot take costs
        # nothing. `take` at the end cannot then fail -- the walk is single
        # threaded and nothing else takes -- and the assert says so.
        spent = caps.full(company, document)
        if spent == "report":
            if pinned_by_name is not None:
                raise SystemExit(
                    f"[{PREFIX}] FATAL: pinned page {name} is over the "
                    "per-report cap in pin order. The pin is inconsistent "
                    "with the caps; refusing to seal.")
            refused["over the per-report cap"] = refused.get(
                "over the per-report cap", 0) + 1
            continue
        if spent == "company":
            if pinned_by_name is not None:
                raise SystemExit(
                    f"[{PREFIX}] FATAL: pinned page {name} is over the "
                    "per-company cap in pin order. The pin is inconsistent "
                    "with the caps; refusing to seal.")
            refused["over the per-company cap"] = refused.get(
                "over the per-company cap", 0) + 1
            continue
        if pinned_by_name is not None:
            member = fintabnet_archive.zip_path_of(company, year, num)
            member_entry = archive.entries.get(member)
            if member_entry is None:
                raise SystemExit(
                    f"[{PREFIX}] FATAL: pinned page {name} is not in the "
                    "archive directory. Refusing to seal a page that differs.")
            want_crc = str(pinned_by_name[name].get("crc32") or "")
            try:
                want_int = int(want_crc, 16)
            except ValueError:
                raise SystemExit(
                    f"[{PREFIX}] FATAL: pinned page {name} pins crc32 "
                    f"{want_crc!r}, which is not hex. Refusing to seal.") from None
            if member_entry.crc != want_int:
                raise SystemExit(
                    f"[{PREFIX}] FATAL: pinned page {name} has CRC32 "
                    f"{member_entry.crc:08x}, the pin says {want_crc}. "
                    "Refusing to seal a page that differs.")
        # TRANSIENT, RETRIED, THEN LOUD. Provenance and the member bytes
        # are a property of this fetch: up to `max_attempts` attempts with
        # backoff, and on the last failure the run ends naming the page. It
        # NEVER takes the next candidate instead -- a fetch that substituted
        # would be a different set under this name. The alignment check
        # below is NOT retried -- the bytes are CRC-verified, so a failure
        # is structural -- and runs in walk mode only: pinned pages skip it
        # (their bytes are the proof; see below).
        blob: bytes | None = None
        attempt = 0
        skipped_alignment = False
        while True:
            errors: list | None = []
            for table in kept:
                try:
                    fintabnet_archive.check_provenance(
                        company, year, num, table, jsonls)
                except fintabnet_archive.TransportFailed as exc:
                    # The jsonl the transport never answered is not a
                    # provenance mismatch and never triggers the refresh
                    # below: it propagates as itself -- a TransportFailed,
                    # never retried here (the reader spent its own retries)
                    # -- naming the page it failed on the way to.
                    raise fintabnet_archive.TransportFailed(
                        f"{name}: transport failed: {exc}") from None
                except fintabnet_archive.ProvenanceRefused as exc:
                    src = str(table.get("fintabnet_source_file_name") or "")
                    provenance_counts[src] = provenance_counts.get(src, 0) + 1
                    if (src and provenance_counts[src]
                            >= FINTABNET_PROVENANCE_REFETCH_AT
                            and src not in provenance_refreshed):
                        provenance_refreshed.add(src)
                        print(f"[{PREFIX}] {src}: "
                              f"{provenance_counts[src]} provenance failures "
                              "at once; re-fetching the file once", flush=True)
                        try:
                            jsonls.refresh(src)
                        except fintabnet_archive.ProvenanceRefused as re_exc:
                            raise SystemExit(
                                f"[{PREFIX}] FATAL: {name}: provenance file "
                                f"{src} re-fetched once and still {re_exc}. "
                                "Failing loudly; never substituting another "
                                "page.") from None
                        # Against the fresh file, this same attempt again.
                        errors = None
                        break
                    assert errors is not None
                    errors.append(exc)
            if errors is None:
                continue
            if errors:
                if attempt >= max_attempts - 1:
                    raise SystemExit(
                        f"[{PREFIX}] FATAL: {name}: provenance mismatch: "
                        f"{errors[0]} (after {max_attempts} attempts). Failing "
                        "loudly; never substituting another page.") from None
                print(f"[{PREFIX}] {name}: provenance mismatch "
                      f"(attempt {attempt + 1}/{max_attempts}): {errors[0]}; "
                      "retrying", flush=True)
                _sleep_backoff(attempt)
                attempt += 1
                continue
            try:
                blob = archive.fetch(
                    fintabnet_archive.zip_path_of(company, year, num))
            except fintabnet_archive.CRCRefused as exc:
                if attempt >= max_attempts - 1:
                    raise SystemExit(
                        f"[{PREFIX}] FATAL: {name}: CRC32 mismatch: {exc} "
                        f"(after {max_attempts} attempts). Failing loudly; "
                        "never substituting another page.") from None
                print(f"[{PREFIX}] {name}: CRC32 mismatch "
                      f"(attempt {attempt + 1}/{max_attempts}): {exc}; "
                      "retrying", flush=True)
                _sleep_backoff(attempt)
                attempt += 1
                continue
            except fintabnet_archive.MemberRefused as exc:
                if attempt >= max_attempts - 1:
                    raise SystemExit(
                        f"[{PREFIX}] FATAL: {name}: the page did not answer: "
                        f"{exc} (after {max_attempts} attempts). Failing "
                        "loudly; never substituting another page.") from None
                print(f"[{PREFIX}] {name}: the page did not answer "
                      f"(attempt {attempt + 1}/{max_attempts}): {exc}; "
                      "retrying", flush=True)
                _sleep_backoff(attempt)
                attempt += 1
                continue
            except fintabnet_archive.TransportFailed as exc:
                # TRANSPORT IS NOT DATA, AND NOT RETRIED HERE: the reader
                # raised this only after its own five tries, so the retry
                # loop around it would ask five times four -- and a page
                # the transport never answered is not a refusal. FATAL at
                # once, naming the page: the loop above stays for
                # provenance and CRC only.
                raise SystemExit(
                    f"[{PREFIX}] FATAL: {name}: transport failed: "
                    f"{exc}. Failing loudly; never substituting "
                    "another page.") from None
            page = pdfs / name
            assert blob is not None
            page.write_bytes(blob)
            # PINNED PAGES ARE NOT RE-ALIGNED. The pin CRC check above and
            # `Archive.fetch`'s CRC check on these same bytes just now proved
            # this IS the pinned page, by its bytes; the alignment check ran
            # when the pin was made. Re-running it here would not test the
            # page -- it would test whether pdftotext answers the same way
            # twice, and post-W it did not: a crash under the round's memory
            # ceiling read as "" and FATALed the pinned page as "the page
            # differs". The walk keeps the check exactly as it is: that is
            # where it selects pages.
            if (pinned_by_name is None
                    and not _aligned(tables, _page_text(page))):
                # STRUCTURAL, ON CRC-VERIFIED BYTES: no retry -- the same
                # bytes would fail the same way. The page is counted and
                # left out; the walk moves on.
                page.unlink(missing_ok=True)
                unaligned += 1
                print(f"[{PREFIX}] {name}: the alignment check failed; "
                      "counted and skipped (structural, no retry)", flush=True)
                skipped_alignment = True
                break
            break
        if skipped_alignment:
            continue
        # The call counts; the assert only says what the peek above promised.
        # (In that order, so that the count stands even where asserts do not.)
        took = caps.take(company, document)
        assert took, f"{document} had room a moment ago; the walk takes alone"
        blocks = [{"kind": "table", "text": pubtables_cells.text_of(t["html"]),
                   "html": t["html"], "order": i, "bbox": t["bbox"]}
                  for i, t in enumerate(tables)]
        rows.append({
            "source": source,
            "name": name,
            "document": document,
            "page_no": 0,
            "category": fintabnet_archive.CATEGORY,
            "licence": fintabnet_archive.DOCUMENT_LICENCE,
            "sha256": hashlib.sha256(blob).hexdigest(),
            "path": str(page),
            "annotates": list(entry.get("annotates") or ["table"]),
            "blocks": blocks,
        })
    mode = f"pinned {pin['sha256']}" if pin is not None else "walked, no pin"
    # Every retry the reader took on the way here: a throttled round says
    # so about itself. A reader that never retries -- the tests' -- counts
    # none.
    transport_retries = int(getattr(reader, "transport_retries", 0) or 0)
    print(f"[{PREFIX}] kept {len(rows)} page(s) ({mode}) from {where}; "
          f"{unaligned} page(s) failed the alignment check; refusals: "
          f"{json.dumps(refused, sort_keys=True)}", flush=True)
    (work / "fetch-refusals.json").write_text(json.dumps(
        {"mode": mode, "source": archive_source,
         "transport_retries": transport_retries,
         "refused": refused, "pages_not_aligned": unaligned,
         "licence": fintabnet_archive.DOCUMENT_LICENCE,
         "caps": {"per_document": caps.per_document,
                  "per_company": caps.per_company},
         "jsonl_observed": jsonls.observed()}, indent=1))
    if pin is None:
        kept = _write_fintabnet_kept(work, rows, archive, seed,
                                     archive_source, transport_retries)
        print(f"[{PREFIX}] wrote fetch-kept.json: {len(kept['pages'])} "
              f"page(s), sha256 {kept['sha256'][:12]} -- the would-be pin",
              flush=True)
    elif pin is not None and len(rows) == len(pin["pages"]):
        # The full pin was read: the sealed split must be the pin's, or the
        # pages moved under it. A head (a smoke run with --limit) seals
        # differently and is not checked.
        _tuning, holdout = holdout_rule.split(rows, seed=seed)
        held = {p["name"] for p in holdout}
        for row in rows:
            want = pinned_by_name[row["name"]].get("split") if pinned_by_name else None
            got = "holdout" if row["name"] in held else "tuning"
            if want != got:
                raise SystemExit(
                    f"[{PREFIX}] FATAL: pinned page {row['name']} seals as "
                    f"{got}, the pin says {want}. Refusing to seal.")
    # A SHORT FETCH IS A DIFFERENT SET, for the reason the PubTables lane
    # gives at the same check: sealing fewer pages under this name would
    # publish a figure about a set the register does not describe.
    if len(rows) != ceiling:
        if pin is not None:
            raise SystemExit(
                f"[{PREFIX}] FATAL: the pinned fetch kept {len(rows)} page(s) "
                f"of {len(pin['pages'])} pinned and the register asks for "
                f"{ceiling}. Refusing to seal.")
        raise SystemExit(
            f"[{PREFIX}] FATAL: the walk kept {len(rows)} page(s) and the "
            f"register asks for {ceiling}. It looked at {len(walk)} page(s) "
            f"({entry.get('walk_multiplier')} x the set) and was refused "
            f"{sum(refused.values())} time(s): "
            f"{json.dumps(refused, sort_keys=True)}; {unaligned} page(s) did "
            "not survive the alignment check. A short fetch is a different "
            "corpus, and sealing it under this name would publish a figure "
            "about a set the register does not describe.")
    return rows


def census(rows: list[dict], sample_size: int = 25) -> dict:
    """Is a row's `pdf` one page or a whole document?

    The whole reading of this set rests on it. The dataset card says the column
    is "the original PDF", the annotations are per page, and every number here
    assumes the two mean the same thing -- if the blob were the whole document,
    a tool would be graded on a page's annotations against a hundred pages of
    text and the text axis would collapse for reasons nothing in the code
    explains.

    So it is checked rather than assumed, on the machine, with the reader that
    is already installed, and the answer goes in the run file. A sample, because
    the question is about the shape of the column and not about one row.
    """
    if not shutil.which("pdfinfo"):
        return {"checked": 0, "why": "pdfinfo is not on this machine"}
    counts: dict[str, int] = {}
    for row in rows[:sample_size]:
        out, _ = run_tool(["pdfinfo", row["path"]], timeout=30)
        pages = "unreadable"
        for line in (out or "").splitlines():
            if line.startswith("Pages:"):
                pages = line.split(":", 1)[1].strip()
        counts[pages] = counts.get(pages, 0) + 1
    return {"checked": min(sample_size, len(rows)), "pages_per_file": counts}


def _page_of(record: dict, pdfs: pathlib.Path) -> dict | None:
    """One parquet row as a page: the PDF on disk and the blocks beside it."""
    blob = record.get("pdf")
    meta = record.get("metadata") or {}
    if not blob:
        return None
    digest = hashlib.sha256(blob).hexdigest()
    name = f"{meta.get('page_hash') or digest[:16]}.pdf"
    path = pdfs / name
    if not path.is_file():
        path.write_bytes(blob)
    blocks = []
    cells = record.get("pdf_cells") or []
    for index, class_id in enumerate(record.get("category_id") or []):
        kind = DOCLAYNET_CLASS.get(int(class_id), "ignore")
        if kind == "ignore":
            continue
        inside = cells[index] if index < len(cells) else []
        text = " ".join((c.get("text") or "") for c in (inside or []))
        if not axes.normalise(text):
            continue
        boxes = record.get("bboxes") or []
        box = list(boxes[index]) if index < len(boxes) else []
        blocks.append({"kind": kind, "text": text, "order": index, "bbox": box})
    if not blocks:
        return None
    return {
        "source": "doclaynet-v1.2/test",
        "name": name,
        "document": meta.get("original_filename") or name,
        "page_no": meta.get("page_no"),
        "category": meta.get("doc_category"),
        "sha256": digest,
        "path": str(path),
        "blocks": blocks,
    }


# ---------------------------------------------------------------------------
# The draw.
# ---------------------------------------------------------------------------
def _drawn_on(set_name: str, seal: str) -> str:
    """The date the committed manifest carries, if it seals the same pages."""
    today = dt.date.today().isoformat()
    path = holdout_rule.draw_path_for(set_name)
    if not path.is_file():
        return today
    try:
        before = json.loads(path.read_text())
    except json.JSONDecodeError:
        return today
    if (before.get("seal") or {}).get("holdout") == seal and before.get("drawn"):
        return before["drawn"]
    return today


def draw(set_name: str, entry: dict, pages: list[dict],
         fetch_source: str | None = None,
         transport_retries: int | None = None) -> dict:
    """Split the set, seal both halves, and write the manifest.

    Written once, before anything is measured. The digest of the holdout is
    published on the ticket the same day -- that is the whole of what a seal
    buys, and it buys nothing if the numbers came first.
    """
    seed = holdout_rule.seed_for(set_name)
    tuning, sealed = holdout_rule.split(pages, seed=seed)
    held = {p["name"] for p in sealed}
    entries = []
    for p in pages:
        row = {
            "source": p["source"], "name": p["name"], "document": p["document"],
            "page_no": p["page_no"], "category": p["category"],
            "sha256": p["sha256"],
            "split": "holdout" if p["name"] in held else "tuning",
        }
        # THE DOCUMENT'S OWN LICENCE, where the set has one per document. It is
        # in the manifest rather than in a log because that is what makes the
        # corpus readable afterwards as what was ALLOWED: a reader can check
        # every row against the list in the register without refetching
        # anything. (#575)
        if p.get("licence"):
            row["licence"] = p["licence"]
        entries.append(row)
    manifest = {
        "set": set_name,
        "version": entry.get("version", ""),
        "revision": entry.get("revision", ""),
        "source": entry.get("source", ""),
        "licence": entry.get("licence", ""),
        # THE DAY IT WAS DRAWN, WHICH IS NOT THE DAY IT WAS REDRAWN. A round
        # redraws the split from the pages it materialised -- that is what
        # makes the seal checkable -- and writing today's date each time would
        # re-date a set that was sealed days ago, against a ledger that says
        # when it was drawn. So the date is kept as long as the seal is: a
        # manifest whose pages moved is a new draw and says so. (#575)
        "drawn": _drawn_on(set_name, holdout_rule.digest_of(sealed)),
        "seed": seed,
        "rule": "scripts/ci/structured_holdout_rule.py",
        "share": holdout_rule.HOLDOUT_SHARE,
        "licence_codes": sorted({e["licence"] for e in entries
                                 if e.get("licence")}) or None,
        "counts": {
            "pages": len(entries),
            "tuning": len(tuning),
            "holdout": len(sealed),
            "documents": len(holdout_rule.documents_of(entries)),
        },
        "seal": {
            "tuning": holdout_rule.digest_of(tuning),
            "holdout": holdout_rule.digest_of(sealed),
        },
        "pages": entries,
    }
    if set_name == "fintabnet":
        # WHICH FETCH DREW THIS: the pin's sha, or the walk. The fetch writes
        # the same string into fetch-refusals.json; the manifest carries it
        # so a seal can be read back as pinned or walked without the work
        # dir. A corrupt pin refuses the seal rather than drawing under it.
        pin = _read_fintabnet_pin()
        manifest["fetch_mode"] = (
            f"pinned {pin['sha256']}" if pin is not None else "walked, no pin")
        # WHICH MIRROR SERVED IT, and how often the transport queued: the
        # fetch's own evidence, read back by main, so the seal says whether
        # its bytes came from the GCS mirror or the HF CDN. Absent when the
        # draw never saw a fetch -- a synthetic set seals without one.
        if fetch_source is not None:
            manifest["fetch_source"] = fetch_source
        if transport_retries is not None:
            manifest["transport_retries"] = transport_retries
    path = holdout_rule.draw_path_for(set_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=1, sort_keys=False) + "\n")
    return manifest


# ---------------------------------------------------------------------------
# The tools.
# ---------------------------------------------------------------------------
def geometric(blocks: list[dict]) -> list[dict]:
    """The same blocks in a plain top-down, left-to-right order.

    WHY THIS EXISTS. The order axis is scored against the order the ANNOTATION
    gives, and the dataset card does not say that order is a reading order. If
    it were merely the order the boxes were drawn in, every order figure in this
    run would be noise -- and worse than noise, because the tool that was
    trained on this dataset would score well on it for a reason that is not
    reading.

    So the annotation order is compared with the order geometry alone would
    give, and the comparison is reported (`order_provenance`). Read it as
    follows:

      near 1.0    the annotation order IS the geometric order. It is a reading
                  order for a single column and says nothing more.
      well under  the annotation order carries something geometry does not --
                  which on a page of two columns is exactly what a reading order
                  is, and is the evidence this axis needs.

    Top-down and left-to-right, with the y coordinate banded so that two blocks
    on one line sort by x. Deliberately the naive rule: its job is to be the
    thing the annotation is compared WITH, and a clever one would hide the
    difference this statistic exists to show.
    """
    def key(block: dict) -> tuple:
        box = block.get("bbox") or [0, 0]
        # THE PAGE FIRST, where the blocks carry one (#584). Over a document the
        # geometry of two pages is the same geometry -- the top of page four is
        # above the foot of page three by every number in the box -- so a sort
        # that reads only the box interleaves the pages and answers a question
        # about nothing. Run structured-doclaynet-20260922T144220Z is what
        # that looks like: the geometric order fell to 0.112 for us and 0.077
        # for Docling in the in-document reading, which is both columns saying
        # the instrument is wrong rather than either of them reading badly.
        return (block.get("page", 0), round(float(box[1]) / 10.0), float(box[0]))
    return sorted(blocks, key=key)


def order_provenance(pages: list[dict]) -> dict:
    """How far the annotation order is from the order geometry would give.

    Only on a set that drew the whole page. On one that annotates a single
    class of block, "the annotation order" is the order of the tables and the
    statistic would answer a question nobody asked -- and it would answer it
    with a number, which reads as an answer. (#575)
    """
    drawn = {kind for page in pages for kind in (page.get("annotates") or EVERYTHING)}
    if "paragraph" not in drawn:
        return {"pages": 0,
                "why": "this set does not annotate the running text of a page, "
                       "so it has no reading order to compare with geometry",
                "per_page": []}
    scores = []
    per_page = []
    for page in pages:
        body = [b for b in page["blocks"] if b["kind"] in BODY]
        if not all(b.get("bbox") for b in body):
            continue
        row = axes.reading_order_score([b["text"] for b in body],
                                       [b["text"] for b in geometric(body)])
        if row.get("scored"):
            scores.append(row["score"])
            per_page.append({"name": page.get("name", ""),
                             "agreement_with_geometry": row["score"]})
    if not scores:
        return {"pages": 0, "why": "no page carried a box for every block",
                "per_page": []}
    return {"pages": len(scores),
            "mean_agreement_with_geometry": round(sum(scores) / len(scores), 6),
            "pages_below_0.9": sum(1 for s in scores if s < 0.9),
            "per_page": per_page}


def sample(pages: list[dict], size: int, seed: str = holdout_rule.SEED) -> list[dict]:
    """A seeded sample of one half, by whole documents, of about `size` pages.

    WHY A SAMPLE AT ALL. Docling reads a page in seconds on a CPU, and a
    thousand of them is most of a machine-day. A round that measured the whole
    tuning half would cost more than the fixes it sizes, and #574 buys one
    machine per run.

    WHY SEEDED AND BY DOCUMENT. The same two reasons the split has: a sample
    somebody chose is a sample somebody can be argued with about, and pages of
    one document are not independent draws. The walk is the split's own
    `rank`, so the sample is a consequence of a published sentence and the same
    size asks for the same pages every time.

    The size is approximate for the reason the split's is: the document that
    would cross the line is left out rather than taken, so a run is never
    larger than it asked for.
    """
    grouped = holdout_rule.documents_of(pages)
    held: set[str] = set()
    count = 0
    for document in sorted(grouped, key=lambda d: holdout_rule.rank(
            grouped[d][0]["source"], d, seed)):
        if count + len(grouped[document]) > size:
            continue
        held.add(document)
        count += len(grouped[document])
    # Returned in the order the caller had them, which is the manifest's, so
    # that a run file reads like the manifest rather than like the seed.
    return [p for p in pages if p["document"] in held]


# THE CLOCK AND THE HIGH-WATER MARK, TAKEN BY A LAUNCHER (#621). A process's
# peak RSS is only readable by its parent, after it is reaped, and only as
# RUSAGE_CHILDREN -- the maximum over every child that parent ever had. So each
# tool runs under a launcher of its own whose only child is the tool: its
# RUSAGE_CHILDREN is that tool's peak, whole process tree included, and it is
# the launcher's clock around the child that is reported, so the launcher's own
# start-up is not charged to the tool. The launcher also owns the timeout, so a
# tool that runs over is killed, not orphaned.
LAUNCHER = r"""
import json, resource, subprocess, sys, time
report, timeout, command = sys.argv[1], float(sys.argv[2]), sys.argv[3:]
started = time.monotonic()
outcome = "ok"
try:
    code = subprocess.run(command, timeout=timeout).returncode
    outcome = "ok" if code == 0 else "crash"
except subprocess.TimeoutExpired:
    code, outcome = 124, "timeout"
except OSError:
    code, outcome = 127, "crash"
ms = int((time.monotonic() - started) * 1000)
rss = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
if sys.platform == "darwin":
    rss //= 1024
with open(report, "w") as out:
    json.dump({"outcome": outcome, "ms": ms, "peak_rss_kb": rss}, out)
sys.exit(code)
"""


def run_tool(command: list[str], timeout: int = TIMEOUT_S,
             probe: dict | None = None) -> tuple[str | None, int]:
    """Run one tool process; `probe`, when given, receives what #621 measures:
    `outcome` (ok | crash | timeout), `ms`, `peak_rss_kb`, and `returncode`
    (the launcher's exit status -- under the memory ceiling, systemd-run's
    own, which `_page_text` logs for the record).
    """
    import tempfile

    # A MEMORY CEILING PER TOOL (#623). On 25-09-2026 the round lost its machine
    # three hours in, still inside DocLayNet, with the heavy CPU columns on 16
    # GB: one tool that swells takes every column and the evidence with it. So
    # where the round asks for one, each process runs in its own scope with
    # `MemoryMax`, and a tool that does not fit is killed alone -- a crash in
    # its column, and the machine goes on. `--scope` execs the command, so the
    # launcher's high-water mark is still the tool's.
    ceiling = os.environ.get("STRUCTURED_MEMORY_MAX", "").strip()
    if ceiling and shutil.which("systemd-run"):
        command = ["systemd-run", "--scope", "--quiet", "-p", f"MemoryMax={ceiling}",
                   "-p", "MemorySwapMax=0", "--", *command]
    with tempfile.NamedTemporaryFile("r", suffix=".json") as report:
        started = dt.datetime.now()
        try:
            done = subprocess.run([sys.executable, "-c", LAUNCHER, report.name, str(timeout),
                                   *command],
                                  capture_output=True, timeout=timeout + 60, check=False)
        except (subprocess.TimeoutExpired, OSError):
            if probe is not None:
                probe.update(outcome="timeout", ms=timeout * 1000,
                             peak_rss_kb=None, returncode=None)
            return None, timeout * 1000
        try:
            measured = json.loads(pathlib.Path(report.name).read_text() or "{}")
        except (OSError, ValueError):
            measured = {}
    ms = measured.get("ms", int((dt.datetime.now() - started).total_seconds() * 1000))
    outcome = measured.get("outcome", "crash")
    if probe is not None:
        probe.update(outcome=outcome, ms=ms,
                     peak_rss_kb=measured.get("peak_rss_kb"),
                     returncode=done.returncode)
    if outcome == "timeout":
        return None, timeout * 1000
    if done.returncode != 0:
        return None, ms
    return done.stdout.decode("utf-8", "replace"), ms


# How many characters of a block's text stand in for it in the type table.
# Enough to find the block again in the markdown beside it, and not so much
# that the run file carries every page twice.
SET_IN_KEY_CHARS = 48

# How many cased letters a block needs before "it is all capitals" is a
# statement about the block rather than about one word. The same floor
# `crate::headings` reads, because this table exists to be compared with what
# that classifier did.
MIN_CAPS_LETTERS = 4


def is_all_caps(text: str) -> bool:
    """Whether every cased letter of a block is a capital, and there are enough."""
    cased = 0
    for ch in text:
        if ch.islower():
            return False
        if ch.isupper():
            cased += 1
    return cased >= MIN_CAPS_LETTERS


def set_in(payload: str | None) -> list[dict] | None:
    """What each of OUR blocks was set in, beside what it says (#578).

    WHY THE ROUND CARRIES THIS. The evidence a round keeps is every column's
    markdown and the annotations it was graded against, and neither of them
    holds a single fact about the TYPE. So the dominant cluster of the heading
    axis -- 56 headings we emitted as a paragraph with the right text, 45 of
    them set within five per cent of the body's own line -- could be counted
    from a finished round and not diagnosed: the size says nothing about them
    and only the weight, the capitals or the face can, and none of the three
    was written down anywhere. A rule built on that would have been a guess
    about a page the rig cannot show. This is the answer to that, and it is
    deliberately OURS alone: what another tool's document model was set in is
    its business, and this table exists to be read against `crate::headings`,
    which reads exactly these four things.

    Taken from `to_json` (#572), which already carries every span's size, face
    and weight, so nothing in the engine changes to produce it -- and asked for
    in a SECOND call that is never the timed one, because the round grades the
    markdown and a clock that included this would not be the clock a caller
    pays.

    `None` where the tool could not answer or did not write JSON: a page that
    says nothing about its type is a page this table leaves out, rather than
    one it describes with zeroes.
    """
    if not payload:
        return None
    try:
        blocks = json.loads(payload).get("blocks")
    except (ValueError, AttributeError):
        return None
    if not isinstance(blocks, list):
        return None
    out: list[dict] = []
    for block in blocks:
        spans = block.get("spans") or []
        weights: dict[float, int] = {}
        faces: dict[str, int] = {}
        bold = italic = total = 0
        for span in spans:
            chars = len("".join((span.get("text") or "").split()))
            if not chars:
                continue
            total += chars
            size = span.get("fontSize")
            if isinstance(size, (int, float)):
                weights[round(float(size), 2)] = weights.get(round(float(size), 2), 0) + chars
            face = span.get("fontName")
            if face:
                faces[face] = faces.get(face, 0) + chars
            if span.get("isBold"):
                bold += chars
            if span.get("isItalic"):
                italic += chars
        text = axes.normalise(block.get("text") or "")
        out.append({
            "kind": block.get("kind"),
            "text": text[:SET_IN_KEY_CHARS],
            # The size the MIDDLE character of the block is set in, and the
            # face most of its characters are in. The median rather than the
            # commonest size, because that is what `blocks::dominant_font_size`
            # hands the classifier -- it sorts one size per glyph and takes the
            # middle one -- and a table that says 8 point where the classifier
            # clusters on 10 is a table that cannot diagnose the classifier.
            # (codex, #578)
            "size": weighted_median(weights),
            "face": max(faces.items(), key=lambda kv: kv[1])[0] if faces else None,
            "bold": total > 0 and bold * 2 > total,
            "italic": total > 0 and italic * 2 > total,
            "caps": is_all_caps(text),
        })
    return out


def weighted_median(weights: dict) -> float | None:
    """The size the middle character of a block is set in.

    `weights` is characters per size, which is the same population
    `blocks::dominant_font_size` sorts: one size per glyph, middle one wins.
    """
    if not weights:
        return None
    total = sum(weights.values())
    seen = 0
    for size in sorted(weights):
        seen += weights[size]
        if seen * 2 > total:
            return size
    return max(weights)


def json_of(pdf: pathlib.Path, binaries: dict, timeout: int = TIMEOUT_S) -> str | None:
    """Our own block model as JSON, for [`set_in`]. Never timed."""
    binary = binaries.get("pdfluent")
    if not binary:
        return None
    payload, _ms = run_tool([binary, "--json", str(pdf)], timeout=timeout)
    return payload


def markdown_of(tool: str, entry: dict, pdf: pathlib.Path, binaries: dict,
                timeout: int = TIMEOUT_S, probe: dict | None = None) -> tuple[str | None, int]:
    """Every tool, through one door: a page in, markdown out.

    ONE FORMAT FOR EVERY COLUMN, and it is the format the tools that have
    structure already emit. Docling's own export is markdown; ours was plain
    text for the baseline round (#566), which IS markdown -- with no headings,
    no lists and no tables in it, which is precisely the gap this milestone
    exists to close.

    OUR COLUMN IS NOW THE BLOCK MODEL'S OWN MARKDOWN (#567, #568), through the
    `structured_markdown` example rather than through `extract-text`: the
    command line's surface is #573, and until it lands the example is where the
    serializer can be read from. `extract-text` is still exactly what the
    extraction rig next door measures, and that measurement is unchanged.

    What this must not do is be generous on our behalf. A reader that inferred a
    heading from a short line would report a capability we do not ship, and the
    round after would measure the reader's inference rather than the code's --
    so what is asked for here is the serializer's bytes and nothing else.
    """
    if tool == OURS:
        binary = binaries.get(OURS)
        if not binary:
            return None, 0
        return run_tool([binary, str(pdf)], timeout=timeout, probe=probe)
    if tool == BALANCED:
        # THE BALANCED COLUMN (#641): the CLI's balanced tier per page, like
        # ours -- one process per page, markdown on stdout, 4 intra threads
        # (the shared layer's default; the round sets no other number). The
        # model dir is the remote script's layout
        # (`<dir>/layout-heron-ep03/heron_ep03.onnx`) via BALANCED_MODEL_DIR;
        # ORT_DYLIB_PATH rides the environment the round set.
        binary = binaries.get(BALANCED)
        if not binary:
            return None, 0
        command = [binary, "extract", "--quality", "balanced", "--format", "md"]
        model_dir = os.environ.get("BALANCED_MODEL_DIR") or os.environ.get("PDFLUENT_MODEL_DIR")
        if model_dir:
            command += ["--model-dir", model_dir]
        command.append(str(pdf))
        return run_tool(command, timeout=timeout, probe=probe)
    if tool == BALANCED_TATR:
        # THE BALANCED+TATR COLUMN (#636, track B): the same tier per page,
        # like BALANCED -- one process per page, markdown on stdout, 4 intra
        # threads (the shared layer's default for the layout session and for
        # TATR alike; the round sets no other number) -- plus
        # `--table-model-dir` at the TATR package
        # (`<dir>/tatr-v1.1-all/tatr-v1.1-all.onnx`) via TATR_MODEL_DIR. The
        # round lays TATR beside the layout model in the one model cache, so
        # the fallback is the same dir BALANCED reads; ORT_DYLIB_PATH rides
        # the environment the round set.
        binary = binaries.get(BALANCED_TATR)
        if not binary:
            return None, 0
        command = [binary, "extract", "--quality", "balanced", "--format", "md"]
        model_dir = os.environ.get("BALANCED_MODEL_DIR") or os.environ.get("PDFLUENT_MODEL_DIR")
        if model_dir:
            command += ["--model-dir", model_dir]
        tatr_dir = os.environ.get("TATR_MODEL_DIR") or model_dir
        if tatr_dir:
            command += ["--table-model-dir", tatr_dir]
        command.append(str(pdf))
        return run_tool(command, timeout=timeout, probe=probe)
    if tool in ADAPTED:
        python = binaries.get(tool)
        if not python:
            return None, 0
        adapter = REPO / "scripts" / "extraction" / "adapters" / "extract_structured.py"
        return run_tool([python, str(adapter), "--tool", tool, str(pdf)],
                        timeout=timeout, probe=probe)
    return None, 0


# The tools that run through `extract_structured.py`, each in its own venv at
# /opt/ex/<tool> (`scripts/extraction/rig_bootstrap.sh`), overridable per tool
# with <TOOL>_PYTHON. The register decides which of them may run; this only
# says how.
ADAPTED = ("docling", "liteparse", "pymupdf4llm", "unstructured", "mineru", "pdfplumber", "teacher",
           "teacher_ep03", "teacher_ens",
           # pdfluent-bench: the bench columns (bench/adapters, PORTING.md)
           "docling_minimal", "pdfluent_fast", "pdfluent_balanced", "pdfluent_balanced_tatr")


# A comment is not text. Docling writes `<!-- image -->` where a figure was, and
# counting that as a paragraph would charge it for characters the page does not
# hold and hand it a block the annotator never drew. Stripped from every column,
# so the rule is the same for all of them rather than a favour to one.
COMMENT = re.compile(r"<!--.*?-->", re.S)
# NOR IS AN IMAGE (#623). MinerU writes every figure inline as a Markdown image
# with a base64 `data:` URI -- 8.8 MB gzipped over 200 DocLayNet pages -- and read
# as a paragraph that payload charged it tens of thousands of characters the page
# does not hold. An image reference is a figure, which no axis here scores, so
# it is stripped from every column the way a comment is.
# NOR IS A CODE FENCE'S MARKER LINE (#623). LiteParse wraps a page it found no
# text on in an empty ```text fence, and the marker read as a paragraph "```text"
# -- a character count on a page that holds none, which also put every scan in
# the readable subset. The fence's content is kept; its two marker lines are
# syntax, for every column alike.
FENCE = re.compile(r"^[ \t]*```[\w+-]*[ \t]*$", re.M)
# NOR IS INLINE EMPHASIS (#628). Weight, slant and a raised baseline are how a
# run was SET; the annotation holds the characters. `**net**`, `*days*`,
# `H<sub>2</sub>O` and `~~gone~~` are read as `net`, `days`, `H2O` and `gone`
# for every column alike. LiteParse, MinerU and pymupdf4llm wrote them before
# this and were charged the asterisks; Docling writes none, so no pinned
# reference moves. Underscore emphasis is left alone: `_` is a letter inside
# identifiers and file names far more often than a mark on a page.
INLINE_TAG = re.compile(r"</?(?:sub|sup|b|i|em|strong|s|del|u)>", re.I)
INLINE_STRONG = re.compile(r"\*\*\*(\S(?:.*?\S)?)\*\*\*|\*\*(\S(?:.*?\S)?)\*\*")
INLINE_EM = re.compile(r"(?<![\w*])\*(\S(?:[^*\n]*?\S)?)\*(?![\w*])")
INLINE_STRIKE = re.compile(r"~~(\S(?:.*?\S)?)~~")


def unmarked(line: str) -> str:
    """A line's text without inline emphasis, for every column (#628)."""
    line = INLINE_TAG.sub("", line)
    line = INLINE_STRONG.sub(lambda m: m.group(1) or m.group(2), line)
    line = INLINE_EM.sub(r"\1", line)
    return INLINE_STRIKE.sub(r"\1", line)


IMAGE = re.compile(r"!\[[^\]\n]*\]\([^)\s]*(?:\s+\"[^\"]*\")?\)")

HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.*)$")
# A NESTED ITEM IS INDENTED, and by more than three spaces. CommonMark nests a
# list inside an item from the parent's content column, which is four for a
# `10. ` parent and where our serializer writes every level (#569); Docling
# indents its nested items too. Three spaces was therefore a ceiling that
# counted only the outermost level of every tool's lists and read the rest as
# paragraphs -- against a ground truth (DocLayNet class 4) that has no nesting
# and calls all of them items. Widened for every column at once, which is the
# only way a change to the instrument is not a favour to one of them.
BULLET = re.compile(r"^[ \t]*(?:[-*+]|\d{1,3}[.)])\s+(.*)$")
TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")
TABLE_RULE = re.compile(r"^\s*\|[\s:|-]+\|\s*$")


def blocks_of(markdown: str) -> list[dict]:
    """A tool's markdown as the same kind of blocks the ground truth has.

    Deliberately literal. A heading is a line that says it is one; a list item
    is a line that starts with a bullet or a number; a table is a run of pipe
    rows or an HTML table. Everything else is a paragraph, and blank lines
    separate them. Nothing here guesses, for the reason in `markdown_of`.
    """
    blocks: list[dict] = []
    paragraph: list[str] = []
    table: list[str] = []
    # HOW MANY OF THOSE ROWS ARE THE HEADER. A markdown pipe table says so with
    # its delimiter row, and dropping that row without remembering it turned
    # every header cell into a `<td>` -- so a table read PERFECTLY out of
    # markdown scored 0.714 against a ground truth whose header is `<th>`,
    # while the same table emitted as HTML scored 1.0. That is a bias according
    # to which format an adapter happens to emit, which is the one thing a
    # comparison must not have. (codex, #1930)
    header_rows: list[int] = []

    def flush_paragraph() -> None:
        if paragraph:
            text = " ".join(paragraph).strip()
            if text:
                blocks.append({"kind": "paragraph", "text": text})
            paragraph.clear()

    def flush_table() -> None:
        if table:
            # THE TEXT IS THE CELLS, NOT THE SYNTAX. Joining the raw rows keeps
            # every `|`, and those delimiters then go into the page's text axis
            # and into the matching against a ground truth that reads `a b` --
            # so a tool that emitted the table CORRECTLY was charged for its
            # serialisation and could fall under MATCH_FLOOR for it. The rows
            # are kept for the HTML, which is what TEDS reads. (codex, #1922)
            blocks.append({"kind": "table", "text": _pipe_to_text(table),
                           "html": _pipe_to_html(table, header_rows)})
            table.clear()
            header_rows.clear()

    inside_html = []
    for line in FENCE.sub("", IMAGE.sub(" ", COMMENT.sub(" ", markdown or ""))).splitlines():
        line = unmarked(line)
        if inside_html or "<table" in line.lower():
            inside_html.append(line)
            if "</table>" in line.lower():
                html = "\n".join(inside_html)
                blocks.append({"kind": "table",
                               "text": " ".join(re.sub(r"<[^>]+>", " ", html).split()),
                               "html": html})
                inside_html = []
            continue
        if TABLE_ROW.match(line):
            flush_paragraph()
            if TABLE_RULE.match(line):
                # Everything before the delimiter is the header. CommonMark's
                # tables allow exactly one such row, and a delimiter that
                # arrives anywhere else is a table this reader cannot parse --
                # so the first one wins and a second is ignored.
                if not header_rows:
                    header_rows.append(len(table))
            else:
                table.append(line)
            continue
        flush_table()
        heading = HEADING.match(line)
        if heading:
            flush_paragraph()
            blocks.append({"kind": "heading", "text": heading.group(2).strip()})
            continue
        bullet = BULLET.match(line)
        if bullet:
            flush_paragraph()
            blocks.append({"kind": "list_item", "text": bullet.group(1).strip()})
            continue
        if not line.strip():
            flush_paragraph()
            continue
        paragraph.append(line.strip())
    flush_paragraph()
    flush_table()
    return blocks


def _pipe_to_text(rows: list[str]) -> str:
    """A markdown pipe table as the words it holds, in reading order."""
    cells = []
    for row in rows:
        cells.extend(c.strip() for c in row.strip().strip("|").split("|"))
    return " ".join(c for c in cells if c)


def _pipe_to_html(rows: list[str], header_rows: list[int] | None = None) -> str:
    """A markdown pipe table as the HTML that TEDS reads.

    The rows above the delimiter are the header and come out as `<th>`, which
    is what the tag half of a TEDS cell comparison is about. Without that, a
    table read correctly out of markdown was charged for every header cell
    against a ground truth that marks them -- a penalty for the tool's choice
    of serialisation rather than for its reading. (codex, #1930)
    """
    header = header_rows[0] if header_rows else 0
    out = ["<table>"]
    for index, row in enumerate(rows):
        cells = [c.strip() for c in row.strip().strip("|").split("|")]
        tag = "th" if index < header else "td"
        out.append("<tr>" + "".join(f"<{tag}>{c}</{tag}>" for c in cells) + "</tr>")
    out.append("</table>")
    return "".join(out)


# ---------------------------------------------------------------------------
# The measurement.
# ---------------------------------------------------------------------------
def _unscored(why: str) -> dict:
    return {"scored": False, "why": why}


@functools.lru_cache(maxsize=1)
def words_excluded() -> dict[str, str]:
    """The documents whose text cannot be a reference for the words axis, by name.

    Read from the register (`words_excluded` under a set in DATASETS.toml), so
    the exclusion and its reason are where the set's verdict is, and not only
    here. (#1973)
    """
    loaded = tomllib.loads(DATASETS.read_text())
    out: dict[str, str] = {}
    for entry in (loaded.get("datasets") or {}).values():
        for row in entry.get("words_excluded") or []:
            out[row["document"]] = row["why"]
    return out


def words_row(page: dict, truth: list[dict], body: list[dict], produced: list[dict],
              annotates: set, whole_page: bool, norm=axes.normalise) -> dict:
    """The order-free words axis for one page or one document (#1973).

    Where the set drew the whole page, its body blocks are the words and its
    furniture is counted apart. Where it drew tables only, precision is asked
    of the tool's table blocks -- do they hold anything the tables do not --
    and recall of everything the tool wrote, because a table's words read into
    a paragraph were still read, and that they left the table is the tables
    axis's to say.
    """
    why = words_excluded().get(page.get("document"))
    if why:
        return _unscored(f"not a reference for words: {why} (DATASETS.toml `words_excluded`)")
    # A document read whole carries each block's page (`document_of`), and the
    # overlap rule stays inside a page (codex, #1976).
    if whole_page:
        return axes.bag_of_words_score(
            [b["text"] for b in body], [b["text"] for b in produced],
            furniture=[b["text"] for b in truth if b["kind"] == "furniture"],
            truth_pages=[b.get("page", 0) for b in body], norm=norm)
    if "table" in annotates:
        tables = [b for b in truth if b["kind"] == "table"]
        return axes.bag_of_words_score(
            [b["text"] for b in tables],
            [b["text"] for b in produced if b["kind"] == "table"],
            recall_over=[b["text"] for b in produced],
            truth_pages=[b.get("page", 0) for b in tables], norm=norm)
    return _unscored("this set annotates neither a page's body nor its tables")


def score_page(page: dict, produced: list[dict], norm=axes.normalise) -> dict:
    """Every axis, for one tool on one page.

    AN AXIS A SET DOES NOT ANNOTATE IS NOT SCORED, and saying so is the whole
    of what `annotates` is for. A table corpus draws tables and nothing else:
    scoring its pages for running text would compare a page of prose with the
    words of one table and report a text axis of near zero that is a statement
    about the corpus. The first set (DocLayNet) draws every block, which is why
    nothing needed this until a second one arrived (#575).

    `norm` prepares the four joined axes (text, words, headings, lists); it
    defaults to today's `normalise`, so the default call is v1 bit for bit.
    The v2 column is the same call with the v2 normaliser -- and every call
    also carries the v2 cells beside the v1 ones (below), so a round scored by
    this harness has both.
    """
    truth = page["blocks"]
    annotates = set(page.get("annotates") or EVERYTHING)
    body = [b for b in truth if b["kind"] in BODY]
    row: dict = {"name": page["name"], "document": page["document"],
                 "category": page.get("category"),
                 # What the tool read off the page at all, for the readable
                 # subset (`readable_pages`): a page no tool gets a character
                 # out of is a scanned page, and it scores 0.000 on every axis
                 # for every column alike.
                 "produced_chars": sum(len("".join(b["text"].split())) for b in produced)}
    # The body axes -- running text and reading order -- need the set to have
    # drawn the whole page, not one class of block on it.
    whole_page = "paragraph" in annotates
    not_drawn = ("this set does not annotate the running text of a page, so "
                 "there is nothing to compare a page of it with")

    row["text"] = (axes.text_score(" ".join(b["text"] for b in body),
                                   " ".join(b["text"] for b in produced), norm=norm)
                   if whole_page else _unscored(not_drawn))
    for kind, key in (("heading", "headings"), ("list_item", "lists")):
        row[key] = (axes.category_score(
            [b["text"] for b in truth if b["kind"] == kind],
            [b["text"] for b in produced if b["kind"] == kind], norm=norm)
            if kind in annotates else
            _unscored(f"this set does not annotate {key}"))
    # Tables, twice over. `tables` is "did the tool find the table", which is
    # all a set that marks table REGIONS can answer; `teds` is the structure,
    # and it is scored only where the ground truth carries the cells. Neither
    # stands in for the other: a found-it figure in a TEDS column is the one
    # misreading this pair exists to stop.
    row["tables"] = (axes.category_score(
        [b["text"] for b in truth if b["kind"] == "table"],
        [b["text"] for b in produced if b["kind"] == "table"])
        if "table" in annotates else _unscored("this set does not annotate tables"))
    cells = [b["html"] for b in truth if b["kind"] == "table" and b.get("html")]
    if not cells:
        row["teds"] = _unscored(
            "the set annotates table regions, not cell structure "
            "(benchmarks/structured/comparison/DATASETS.toml)"
            if "table" in annotates else "this set does not annotate tables")
    else:
        row["teds"] = axes.table_structure_score(
            cells, [b.get("html", "") for b in produced if b["kind"] == "table"])
    # THE SAME AXIS AGAINST THE SAME BLOCKS IN THE ORDER GEOMETRY PUTS THEM,
    # for the reason `order_geometric` stands beside `order` (#579). The text
    # axis is an edit distance over a string, and a string has an order: the
    # truth above is the blocks in the ANNOTATION's order, which on DocLayNet
    # agrees with geometry on 0.61 of pages. A reader cannot tell from one
    # number how much of a column's distance is the characters and how much is
    # which order the truth was assembled in, and the difference is not small:
    # in run structured-doclaynet-20260921T000905Z our column reads 0.613
    # against Docling's 0.630 in the annotation's order and 0.716 against 0.719
    # in geometry's. Printed for every column at once, so it is a property of
    # the instrument rather than a discount for one tool, and the axis the
    # reference pins is still the first one.
    row["text_geometric"] = (axes.text_score(
        " ".join(b["text"] for b in geometric(body)),
        " ".join(b["text"] for b in produced))
        if whole_page else _unscored(not_drawn))
    row["words"] = words_row(page, truth, body, produced, annotates, whole_page, norm=norm)
    row["order"] = (axes.reading_order_score([b["text"] for b in body],
                                             [b["text"] for b in produced])
                    if whole_page else _unscored(not_drawn))
    # The same axis against the other reading of "the right order", so that a
    # reader can see whether the figure depends on which one is believed. One
    # of the two is the dataset's annotation and the other is geometry; neither
    # is beyond argument, and a number that is the same under both is.
    row["order_geometric"] = (axes.reading_order_score(
        [b["text"] for b in geometric(body)], [b["text"] for b in produced])
        if whole_page else _unscored(not_drawn))
    # Furniture that leaked into the body: what #571 takes out. Counted rather
    # than scored, because zero is the only interesting value and a share of a
    # handful of blocks is noise.
    furniture = [b["text"] for b in truth if b["kind"] == "furniture"]
    leaked = axes.match_items([axes.normalise(f) for f in furniture],
                              [axes.normalise(b["text"]) for b in produced])
    row["furniture"] = {"annotated": len(furniture), "in_the_body": len(leaked)}
    # THE V2 CELLS, beside the v1 ones (#574). The same four axes read through
    # the joining rule; matching, table cells and order are unchanged. The
    # English flag is decided from the TRUTH page, so it -- and with it the
    # rule -- is the same for every tool on the page. A non-English page falls
    # back to v1 rules inside its v2 cells and says so in `v2_applied`.
    lexicon = _v2_lexicon()
    english = axes.page_is_english([b["text"] for b in truth], lexicon)
    v2 = functools.partial(axes.normalise_v2, lexicon=lexicon, english=english)
    row["v2_applied"] = english
    row["text_v2"] = (axes.text_score(" ".join(b["text"] for b in body),
                                      " ".join(b["text"] for b in produced), norm=v2)
                      if whole_page else _unscored(not_drawn))
    for kind, key in (("heading", "headings"), ("list_item", "lists")):
        row[f"{key}_v2"] = (axes.category_score(
            [b["text"] for b in truth if b["kind"] == kind],
            [b["text"] for b in produced if b["kind"] == kind], norm=v2)
            if kind in annotates else
            _unscored(f"this set does not annotate {key}"))
    row["words_v2"] = words_row(page, truth, body, produced, annotates, whole_page, norm=v2)
    return row


@functools.lru_cache(maxsize=1)
def _v2_lexicon() -> frozenset:
    """The v2 dictionary, loaded and sha-verified once per process."""
    return axes.load_lexicon()


# The tools that convert a whole batch in one process, and are MEASURED that
# way. Docling spends eight to ten seconds of every page loading its layout and
# table models, and a harness that pays that per page measures Python's import
# system for two of every three hours. Nobody runs it like that; a pipeline
# loads the models once and converts a directory.
#
# The cost of the choice is that one wall clock in this table is amortised and
# the other is not, so the run file says which per column and the table prints
# it. An unlabelled clock across columns with different answers to that question
# is not a comparison.
BATCHED = ADAPTED


def measure_batch(tool: str, pages: list[dict], binaries: dict,
                  text_dir: pathlib.Path | None,
                  probe: dict | None = None) -> tuple[dict, int]:
    """One process for the whole batch; returns page name -> markdown and the ms.

    `probe`, when given, receives the batch's outcome and peak RSS and, under
    `laps`, the adapter's per-page clock: page name -> (ms, outcome).
    """
    python = binaries.get(tool)
    adapter = REPO / "scripts" / "extraction" / "adapters" / "extract_structured.py"
    scratch = (text_dir or pathlib.Path("/tmp")) / f"{tool}-batch"
    scratch.mkdir(parents=True, exist_ok=True)
    listing = scratch / "pages.txt"
    listing.write_text("\n".join(p["path"] for p in pages) + "\n")
    # EMPTY, EVERY TIME. The work directory is reused on purpose -- refetching
    # two and a half gigabytes is five minutes of a paid hour -- so a page that
    # failed on this attempt would otherwise be scored from the markdown of the
    # last one, and counted as answered. (codex, #1922)
    out = scratch / "out"
    if out.exists():
        shutil.rmtree(out)
    started = dt.datetime.now()
    run_tool([python, str(adapter), "--tool", tool, "--batch", str(listing),
              "--out-dir", str(out)], timeout=TIMEOUT_S * max(1, len(pages)), probe=probe)
    ms = int((dt.datetime.now() - started).total_seconds() * 1000)
    if probe is not None:
        by_file = {pathlib.Path(p["path"]).name: p["name"] for p in pages}
        laps: dict = {}
        timings = out / "timings.tsv"
        if timings.is_file():
            for line in timings.read_text().splitlines():
                parts = line.split("\t")
                if len(parts) == 3 and parts[0] in by_file and parts[1].isdigit():
                    laps[by_file[parts[0]]] = (int(parts[1]), parts[2])
        probe["laps"] = laps
    produced: dict = {}
    for page in pages:
        written = out / f"{pathlib.Path(page['path']).name}.md"
        if written.is_file():
            produced[page["name"]] = written.read_text()
    return produced, ms


def _by_document(pages: list[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for page in pages:
        grouped.setdefault(page["document"], []).append(page)
    return grouped


def merge_document(paths: list[str], out: pathlib.Path) -> bool:
    """The pages of one document back into one PDF, in page order.

    `mutool merge`, which the rig already installs for the plain-text oracle,
    and no fallback: a document silently assembled by something else is a
    document whose page order nobody can check. A machine without it is an
    error, not a skipped page: returning False here let the T5 dev fetch
    drop pages with no refusal counted at all. The furniture measurements
    keep their own `why` -- they check for mutool before they ever call
    this, so their semantics are unchanged.
    """
    if not shutil.which("mutool"):
        raise SystemExit(
            "REFUSED: mutool is not installed (mupdf-tools): pages "
            "cannot be cut out of their articles, and a page that is "
            "never cut must not read as a page that failed its check")
    out.parent.mkdir(parents=True, exist_ok=True)
    done, _ = run_tool(["mutool", "merge", "-o", str(out), *paths],
                       timeout=TIMEOUT_S)
    return done is not None and out.is_file()


def furniture_in_documents(tool: str, tool_entry: dict, pages: list[dict],
                           binaries: dict, work: pathlib.Path) -> dict:
    """The furniture count with the pages back in the documents they came from.

    WHY THE SINGLE-PAGE COUNT IS NOT THE RULE'S COUNT (#571, #575). What #571
    built is a rule about a DOCUMENT: a line has to repeat across three pages
    before it is believed to be a running head. DocLayNet ships one page per
    file, so on this set the rule never sees a second page and every header is
    body text -- which is why our column leaked 197 of 320 in run
    ...20260919T140007Z while Docling, which decides per page, leaked 27. That
    figure is a statement about the shape of the corpus.

    The parquet carries `metadata.page_no` and the document name, and the split
    and the sample are both drawn BY DOCUMENT, so every page of a sampled
    document is in the sample and no page of a sealed one can be: putting them
    back together is a regrouping of what is already here rather than a second
    fetch. Both counts are reported, because the one that is comparable with an
    earlier round and the one that measures the rule are not the same number.

    A document of ONE page is left out. Merging it would produce the same file
    under another name and count the same leak twice, and the rule cannot fire
    on it either -- so it is neither evidence for nor against, and the counts
    say how many pages the figure is over.
    """
    # A SET THAT DRAWS NO FURNITURE HAS NO FURNITURE COUNT, and it must not
    # report one. Assembling its documents anyway would cost a second pass of
    # every tool over every page and write `annotated: 0, in_the_body: 0` into
    # the run file -- which reads as "this column leaks nothing" and means
    # "nobody looked". (#575)
    drawn = {kind for page in pages for kind in (page.get("annotates") or EVERYTHING)}
    if "furniture" not in drawn:
        return {"annotated_by_the_set": False,
                "why": "this set does not annotate running headers and footers, "
                       "so there is no furniture to count in or out of the body"}
    grouped = _by_document(pages)
    multi = {name: sorted(rows, key=lambda r: (r.get("page_no") is None,
                                               r.get("page_no"), r["name"]))
             for name, rows in grouped.items() if len(rows) > 1}
    if not multi:
        return {"why": "every document in this sample is one page long, so "
                       "there is nothing to put back together"}
    if not shutil.which("mutool"):
        return {"why": "mutool is not on this machine, and a document assembled "
                       "by something unnamed is a document nobody can check"}

    place = work / "documents"
    annotated = leaked = 0
    measured_documents = measured_pages = 0
    failed: list[str] = []
    for name, rows in multi.items():
        merged = place / f"{tool}-{hashlib.sha256(name.encode()).hexdigest()[:16]}.pdf"
        if not merge_document([r["path"] for r in rows], merged):
            failed.append(name)
            continue
        markdown, _ms = markdown_of(tool, tool_entry, merged, binaries,
                                    timeout=TIMEOUT_S * len(rows))
        merged.unlink(missing_ok=True)
        if markdown is None:
            failed.append(name)
            continue
        produced = blocks_of(markdown)
        furniture = [axes.normalise(b["text"]) for r in rows
                     for b in r["blocks"] if b["kind"] == "furniture"]
        furniture = [f for f in furniture if f]
        annotated += len(furniture)
        leaked += len(axes.match_items(
            furniture, [axes.normalise(b["text"]) for b in produced]))
        measured_documents += 1
        measured_pages += len(rows)
    if not measured_documents:
        return {"why": "no document could be assembled or read: "
                       f"{len(failed)} of {len(multi)} failed"}
    return {"documents": measured_documents, "pages": measured_pages,
            "annotated": annotated, "in_the_body": leaked,
            "documents_that_did_not_answer": failed,
            "of_documents": len(multi),
            # The names, so that the caller can count the SAME pages the
            # single-page way. Two figures over two populations are not a
            # comparison, and this is the one place the two can be tied.
            "covered": sorted(r["name"] for name, rows in multi.items()
                              if name not in failed for r in rows)}


def document_of(name: str, rows: list[dict]) -> dict:
    """One document as the thing an axis is scored on (#584).

    Its blocks are its pages' blocks in page order, which is what a reader of
    the document sees and what a tool fed the whole document has to produce.
    `annotates` is the union, because a set draws the same classes on every
    page of one document and a page that drew none of a class must not narrow
    what the document is scored on.
    """
    annotates: list[str] = []
    for row in rows:
        for kind in row.get("annotates") or EVERYTHING:
            if kind not in annotates:
                annotates.append(kind)
    return {
        "name": name,
        "document": name,
        "category": rows[0].get("category"),
        "page_count": len(rows),
        "annotates": annotates,
        # Each block keeps the page it was drawn on, because over a document
        # the geometry of two pages is the same geometry: see `geometric`.
        "blocks": [dict(block, page=index)
                   for index, row in enumerate(rows) for block in row["blocks"]],
    }


def in_documents(tool: str, tool_entry: dict, pages: list[dict], binaries: dict,
                 work: pathlib.Path | None, text_dir: pathlib.Path | None,
                 per_page: dict[str, str] | None = None) -> dict:
    """Every axis, with the pages back in the documents they came from (#584).

    WHY THIS IS THE READING THE GATE HOLDS. Half of what this benchmark charges
    us on the heading axis is a rule about a DOCUMENT being measured on a page:
    a running head is a heading to anything that sees one page, and it is
    repetition that tells it from a section head (#571). #583 built two rules
    that refuse one on a single page and measured both: on the annotation they
    separate cleanly, on our own output they refuse as many true headings as
    false ones and take the axis DOWN. The discriminator is not on the page.
    So the page stops being the unit: a document is, the reading a caller
    actually gets, and the single-page reading stays beside it as the number
    that is comparable with the rounds before this one.
    AND BOTH COLUMNS ARE READ THE WAY THEIR CALLER READS THEM. Ours is fed the
    document, because that is what our extractor is given and what its rules
    are about. Docling's per-page markdown is strung together in page order,
    because that is what its pipeline produces for a document -- its layout
    model decides per page either way, so re-running it over an assembled PDF
    would measure the same decisions at the price of an hour of the paid
    machine. The run file says which of the two each column got.
    """
    grouped = _by_document(pages)
    multi = {name: sorted(rows, key=lambda r: (r.get("page_no") is None,
                                               r.get("page_no"), r["name"]))
             for name, rows in grouped.items() if len(rows) > 1}
    if not multi:
        return {"why": "every document in this sample is one page long, so "
                       "there is nothing to put back together"}
    concatenated = per_page is not None
    if not concatenated and not shutil.which("mutool"):
        return {"why": "mutool is not on this machine, and a document assembled "
                       "by something unnamed is a document nobody can check"}

    rows: list[dict] = []
    failed: list[str] = []
    for name, page_rows in multi.items():
        if concatenated:
            parts = [per_page.get(row["name"]) for row in page_rows]
            if any(part is None for part in parts):
                failed.append(name)
                continue
            markdown = "\n\n".join(parts)
        else:
            place = (work or pathlib.Path(".")) / "documents"
            merged = place / f"{tool}-{hashlib.sha256(name.encode()).hexdigest()[:16]}.pdf"
            if not merge_document([row["path"] for row in page_rows], merged):
                failed.append(name)
                continue
            markdown, _ms = markdown_of(tool, tool_entry, merged, binaries,
                                        timeout=TIMEOUT_S * len(page_rows))
            merged.unlink(missing_ok=True)
            if markdown is None:
                failed.append(name)
                continue
        if text_dir is not None:
            out = text_dir / "documents" / tool
            out.mkdir(parents=True, exist_ok=True)
            (out / f"{hashlib.sha256(name.encode()).hexdigest()[:16]}.md").write_text(markdown)
        rows.append(score_page(document_of(name, page_rows), blocks_of(markdown)))
    if not rows:
        return {"why": "no document could be read: "
                       f"{len(failed)} of {len(multi)} failed",
                "documents_that_did_not_answer": failed}
    return {
        "documents": len(rows),
        "pages": sum(row["page_count"] for row in
                     (document_of(name, page_rows) for name, page_rows in multi.items()
                      if name not in failed)),
        "of_documents": len(multi),
        "read_as": "the document, fed to the tool" if not concatenated
                   else "the tool's pages, strung together in page order",
        "documents_that_did_not_answer": failed,
        "summary": summarise_tool(rows),
        "tail": tails(rows),
        "documents_measured": rows,
    }


# ---------------------------------------------------------------------------
# The dimensions beyond quality (#621): what a buyer weighs besides the axes.
# ---------------------------------------------------------------------------
# ONE TOKENIZER FOR EVERY COLUMN, pinned: tokens per page is a cost a RAG
# pipeline pays per call, and two tokenizers would make it two units. o200k_base
# is the encoding of the models most pipelines call today; the version is pinned
# in the bench venv (`scripts/extraction/rig_bootstrap.sh`) and the run file
# records both.
TOKENIZER = ("tiktoken", "o200k_base")

# The dimensions every tool in a table must have, and where each comes from.
# `measured` ones come from the round; `declared` ones from the tool's register
# row, which is read before anything runs (#620) and says what the tool's own
# output carries. The table refuses a column missing any of them.
DIMENSIONS = (
    ("ms_p50", "ms/page p50", "measured"),
    ("ms_p95", "ms/page p95", "measured"),
    ("cold_start_ms", "cold start ms", "measured"),
    ("peak_rss_mb", "peak RSS MB", "measured"),
    ("install_mb", "install MB", "measured"),
    ("tokens_per_page", "tokens/page", "measured"),
    ("crash_share", "crashes", "measured"),
    ("timeout_share", "timeouts", "measured"),
    ("empty_share", "empty", "measured"),
    ("grounding", "grounding", "declared"),
    ("confidence", "confidence", "declared"),
    ("licence", "licence", "declared"),
    ("offline", "offline", "declared"),
    # Whether the column read pixels. OCR is off on every column the tool lets
    # us switch it off on; Unstructured's hi_res runs Tesseract inside its own
    # pipeline whatever it is passed, so its column says so rather than
    # pretending (#623: the rig had no tesseract, and the warm-up caught it).
    ("ocr", "OCR", "declared"),
    ("cost_per_1000_pages", "cost/1000 pages", "declared"),
)


def count_tokens(text: str) -> int | None:
    """Tokens of `text` under the pinned tokenizer; None where it is not installed."""
    try:
        import tiktoken
    except ImportError:
        return None
    try:
        return len(tiktoken.get_encoding(TOKENIZER[1]).encode(text, disallowed_special=()))
    except Exception:  # noqa: BLE001 -- an encoding file that cannot load is "not measured"
        return None


def tokenizer_version() -> str | None:
    try:
        from importlib.metadata import version

        return f"{TOKENIZER[0]} {version(TOKENIZER[0])} {TOKENIZER[1]}"
    except Exception:  # noqa: BLE001
        return None


def tree_bytes(path: pathlib.Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += (pathlib.Path(root) / name).lstat().st_size
            except OSError:
                continue
    return total


def install_mb(tool: str, entry: dict, binaries: dict) -> float | None:
    """What installing the tool puts on disk: its venv (or our binary) plus the
    model caches its register row names. None when nothing is there to weigh,
    which the table reads as missing rather than as zero."""
    where = binaries.get(tool)
    if not where or not pathlib.Path(where).exists():
        return None
    target = pathlib.Path(where)
    # Ours and balanced are single binaries, not venvs: weighing
    # `parent.parent` would weigh `target/` for the CLI. The model and the ORT
    # library ride `model_cache` below. Balanced+TATR is the same binary.
    size = target.stat().st_size if tool in (OURS, BALANCED, BALANCED_TATR) else tree_bytes(target.parent.parent)
    for cache in entry.get("model_cache") or []:
        folder = pathlib.Path(os.path.expanduser(cache))
        if folder.exists():
            size += tree_bytes(folder)
    # AND THE SYSTEM PACKAGES THE COLUMN NEEDS (#623, codex on #1975):
    # Unstructured's hi_res cannot run without Tesseract, which lives under
    # /usr and not in its venv. Weighed from the package's own file list, so a
    # column is not lighter for having a dependency outside its directory.
    size += system_package_bytes(entry.get("system_packages") or [])
    return round(size / 1e6, 1)


def system_package_bytes(packages: list[str]) -> int:
    """The bytes of the regular files the named Debian packages installed."""
    total = 0
    for package in packages:
        listed, _ = run_tool(["dpkg-query", "-L", package], timeout=30)
        for line in (listed or "").splitlines():
            path = pathlib.Path(line.strip())
            if path.is_file() and not path.is_symlink():
                total += path.stat().st_size
    return total


def cold_start(tool: str, entry: dict, pages: list[dict], binaries: dict) -> int | None:
    """One fresh process on the first page: what a caller waits for the first
    answer, model load included. A batch amortises it and the per-page CLI pays
    it every time; this is the same reading for both."""
    if not pages:
        return None
    probe: dict = {}
    markdown_of(tool, entry, pathlib.Path(pages[0]["path"]), binaries, probe=probe)
    return probe.get("ms") if probe.get("outcome") == "ok" else None


def dimensions_of(tool: str, entry: dict, binaries: dict, milliseconds: list[int],
                  outcomes: list[str], peaks: list, markdowns: list[str],
                  cold: int | None) -> dict:
    """The #621 dimensions of one column. A value that could not be measured is
    None, and `missing_dimensions` refuses the table over it."""
    pages = max(1, len(outcomes))
    peaks = [p for p in peaks if isinstance(p, (int, float))]
    tokens = [count_tokens(m) for m in markdowns]
    paid = entry.get("licence_kind") == "commercial-paid"
    return {
        "ms_p50": axes.percentile(milliseconds, 0.5) if milliseconds else None,
        "ms_p95": axes.percentile(milliseconds, 0.95) if milliseconds else None,
        "cold_start_ms": cold,
        "peak_rss_mb": round(max(peaks) / 1024, 1) if peaks else None,
        "install_mb": install_mb(tool, entry, binaries),
        "tokens_per_page": (axes.percentile([t for t in tokens if t is not None], 0.5)
                            if tokens and all(t is not None for t in tokens) else None),
        "tokenizer": tokenizer_version(),
        "crash_share": round(outcomes.count("crash") / pages, 4),
        "timeout_share": round(outcomes.count("timeout") / pages, 4),
        "empty_share": round(sum(1 for m in markdowns if not m.strip()) / pages, 4),
        "grounding": entry.get("grounding"),
        "confidence": entry.get("confidence"),
        "licence": entry.get("licence"),
        "offline": entry.get("offline"),
        "ocr": entry.get("ocr"),
        "cost_per_1000_pages": entry.get("cost_per_1000_pages") if paid else "free",
    }


def missing_dimensions(run: dict) -> list[str]:
    """Every (tool, dimension) the table would have to leave empty.

    A run from before #621 carries no `dimensions` at all and is not judged;
    one that carries them is judged on every column. And a competitive round
    (`--tools all`) is judged on the register too: a structured-rig tool that
    is neither a column nor a refusal was silently left out.
    """
    if "dimensions_schema" not in run:
        return []
    missing = []
    for key, tool in run["tools"].items():
        dims = tool.get("dimensions") or {}
        for name, _label, _source in DIMENSIONS:
            if dims.get(name) is None:
                missing.append(f"{tool.get('name', key)}: {name}")
    for name in run.get("register_expected") or []:
        present = {t.get("name") for t in run["tools"].values()} | {
            r.get("tool") for r in run.get("refused") or []}
        if name not in present:
            missing.append(f"{name}: on the register, absent from the table")
    return missing


def dimension_lines(run: dict) -> list[str]:
    """The dimensions table. Refuses -- raises -- when anything is missing."""
    if "dimensions_schema" not in run:
        return ["", "This run predates the dimensions of #621."]
    missing = missing_dimensions(run)
    if missing:
        raise SystemExit(f"[{PREFIX}] REFUSED: the parity table is missing "
                         f"{len(missing)} dimension(s): " + "; ".join(missing))

    def cell(value) -> str:
        if isinstance(value, bool):
            return "yes" if value else "no"
        if isinstance(value, float) and 0 <= value <= 1 and not value.is_integer():
            return f"{value:.1%}"
        return str(value)

    lines = ["", "Beyond quality (#621). Tokens are "
                 f"{next(iter(run['tools'].values()))['dimensions']['tokenizer']}; RSS is the "
                 "process tree's high-water mark; cold start is one fresh process on the first "
                 "page:", "",
             "| tool | " + " | ".join(label for _n, label, _s in DIMENSIONS) + " |",
             "|---|" + "---|" * len(DIMENSIONS)]
    for tool in run["tools"].values():
        lines.append(f"| {tool['name']} | "
                     + " | ".join(cell(tool["dimensions"][n]) for n, _l, _s in DIMENSIONS) + " |")
    lines += batch_clock_lines(run)
    return lines


def batch_clock_lines(run: dict) -> list[str]:
    """Our column's clock the way the competitors' is taken: one batch (#623).

    The table above times ours one process per page, start-up included, and the
    other columns inside one batch. This is ours inside one batch as well --
    serially, comparable with theirs, and with the pages shared over the
    machine's threads, which is what a pipeline would do and theirs did not.
    """
    ours = (run.get("tools") or {}).get(OURS) or {}
    clock = ours.get("batch_clock")
    if not clock:
        return []
    lines = ["", "Our clock the way theirs is taken -- one batch, start-up amortised (#623):", "",
             "| run | threads | ms/page (whole process ÷ pages) | ms/page p50 | ms/page p95 | same markdown as one process per page |",
             "|---|---|---|---|---|---|"]
    for label in ("serial", "parallel"):
        one = clock.get(label)
        if not one:
            continue
        lines.append(f"| {label} | {one['jobs']} | {one['ms_per_page']} | {one.get('ms_p50', '—')} | "
                     f"{one.get('ms_p95', '—')} | {one['same_markdown']}/{one['pages']} |")
    return lines


def batch_clock(pages: list[dict], binaries: dict, per_page: dict[str, str],
                scratch: pathlib.Path) -> dict | None:
    """Time our binary's `--batch` over the pages, serially and over every CPU.

    `per_page` is the markdown the one-process-per-page pass produced, which the
    batch must reproduce page for page: a faster clock over different output
    would be a clock of something else.
    """
    binary = binaries.get(OURS)
    if not binary or not pathlib.Path(binary).exists():
        return None
    scratch.mkdir(parents=True, exist_ok=True)
    listing = scratch / "pages.txt"
    listing.write_text("\n".join(p["path"] for p in pages) + "\n")
    by_file = {pathlib.Path(p["path"]).name: p["name"] for p in pages}
    out: dict = {}
    for label, jobs in (("serial", 1), ("parallel", max(1, os.cpu_count() or 1))):
        target = scratch / f"out-{label}"
        if target.exists():
            shutil.rmtree(target)
        # THE PARENT'S CLOCK, around the whole process (codex, #1997): the
        # competitors' batch is timed that way by `measure_batch`, start-up,
        # list parsing and the writing of every file included. The child's own
        # `wall_ms` starts after all that and is kept only beside it.
        _out, process_ms = run_tool(
            [binary, "--batch", str(listing), "--out-dir", str(target), "--jobs", str(jobs)],
            timeout=TIMEOUT_S * max(1, len(pages)))
        try:
            summary = json.loads((target / "batch.json").read_text())
        except (OSError, ValueError):
            out[label] = None
            continue
        laps = []
        same = 0
        for line in (target / "timings.tsv").read_text().splitlines():
            parts = line.split("\t")
            if len(parts) == 3 and parts[2] == "ok" and parts[1].isdigit():
                laps.append(int(parts[1]))
        for file, name in by_file.items():
            written = target / f"{file}.md"
            if written.is_file() and written.read_text() == per_page.get(name):
                same += 1
        out[label] = {
            "jobs": summary.get("jobs", jobs), "pages": summary.get("pages", len(pages)),
            "process_ms": process_ms, "wall_ms_inside": summary.get("wall_ms"),
            "ms_per_page": round(process_ms / max(1, summary.get("pages") or 1), 1),
            "ms_p50": axes.percentile(laps, 0.5) if laps else None,
            "ms_p95": axes.percentile(laps, 0.95) if laps else None,
            "same_markdown": same,
        }
    return out


# ---------------------------------------------------------------------------
# COMPETITORS ARE MEASURED ONCE (owner, 24-09-2026).
# ---------------------------------------------------------------------------
# A competitor's Markdown for a page does not change until its version, the
# adapter that drives it or the pages do, and running Docling, MinerU and
# Unstructured again every round is most of a round's machine-hours for numbers
# that cannot have moved -- and on 25-09 it was what took the machine down. So
# the first run of a competitor on a set stores its output here, keyed by
# exactly those three things, and every later round scores that output with
# today's scorer and runs only our engine. The clock comes from the stored run
# too: it was measured on the rig, and a round has no newer figure for it.
#
# What is stored is what the licences allow to be stored: the per-page Markdown
# of the two open sets (CDLA-Permissive), in the repository, gzipped, one file
# per tool and set, each file at most 1 MB and the whole cache at most 20 MB
# (COS, 24-09). ParseBench's verdict forbids keeping any page's output, so its
# cache is the scores alone (parsebench_round.py).
COMPETITORS = pathlib.Path(os.environ.get("BENCH_COMPETITOR_CACHE")  # pdfluent-bench: opt-in
                           or REPO / "benchmarks" / "structured" / "competitors")
CACHE_FILE_LIMIT = 1_000_000
DATA_URI = re.compile(r"\(data:[^)]*\)")
CACHE_TOTAL_LIMIT = 20_000_000
ADAPTER = REPO / "scripts" / "extraction" / "adapters" / "extract_structured.py"


def _shown(path: pathlib.Path) -> str:
    """A path as the repository names it, or as it is when it lies outside."""
    try:
        return str(path.relative_to(REPO))
    except ValueError:
        return str(path)


def adapter_digest() -> str:
    """The adapter file's sha256: a changed adapter is a changed configuration."""
    return hashlib.sha256(ADAPTER.read_bytes()).hexdigest()


def cache_key(set_name: str, tool: str, tool_entry: dict, pages: list[dict]) -> dict:
    # The pages BY CONTENT as well as by name (codex, #1982): a revision that
    # changes a PDF under the same name would otherwise score old Markdown
    # against new truth. The ground truth travels in the same digest, so a
    # changed annotation is a miss too.
    content = hashlib.sha256()
    for page in pages:
        content.update(f"{page['name']}\0{page.get('sha256', '')}\0".encode())
        content.update(json.dumps(page.get("blocks"), sort_keys=True).encode())
    return {"set": set_name, "tool": tool,
            "version_pin": str(tool_entry.get("version_pin") or ""),
            "pages_digest": pages_digest(pages),
            "content_digest": content.hexdigest(),
            "adapter": adapter_digest()}


def declared_now(tool: str, dimensions: dict, tool_entry: dict | None = None) -> dict:
    """A column's dimensions with the DECLARED ones read from today's register.

    The measured ones are the run's; what the register says about a tool -- its
    licence, its OCR, its grounding -- is a reading of the tool, and a correction
    to the reading must reach every table, the cached columns included (#623:
    MinerU's `ocr` said off while it read the scanned pages).
    """
    if tool_entry is None:
        register, _meta = load_register(EXTRACTORS, "extractors")
        tool_entry = register.get(tool) or {}
    out = dict(dimensions or {})
    paid = tool_entry.get("licence_kind") == "commercial-paid"
    for name, _label, source in DIMENSIONS:
        if source != "declared":
            continue
        if name == "cost_per_1000_pages":
            out[name] = tool_entry.get(name) if paid else "free"
        else:
            out[name] = tool_entry.get(name)
    return out


def cache_path(key: dict) -> pathlib.Path:
    tag = hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()[:16]
    return COMPETITORS / key["set"] / f"{key['tool']}-{tag}.json.gz"


def cache_load(key: dict) -> dict | None:
    """The stored run of this competitor on these pages, or None."""
    import gzip

    path = cache_path(key)
    if not path.is_file():
        return None
    stored = json.loads(gzip.decompress(path.read_bytes()))
    return stored if stored.get("key") == key else None


def cache_store(key: dict, record: dict) -> pathlib.Path:
    """Write one competitor's run; refuses a file over the per-file limit."""
    import gzip

    path = cache_path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    # An inline image's payload is stored as a marker: `blocks_of` strips the
    # image either way, so no score reads it, and it is what took MinerU's
    # entry to 8.8 MB. The dimensions were measured on the full output.
    if "markdown" in record:
        record = dict(record, markdown={
            name: DATA_URI.sub("(data:stripped)", text)
            for name, text in record["markdown"].items()})
    body = gzip.compress(json.dumps({"key": key, **record}, sort_keys=True).encode(), mtime=0)
    if len(body) > CACHE_FILE_LIMIT:
        raise SystemExit(f"[{PREFIX}] REFUSED: the cache entry for {key['tool']} on "
                         f"{key['set']} is {len(body)} bytes, over {CACHE_FILE_LIMIT}; "
                         "store the scores and aligned blocks instead of the Markdown "
                         "(COS, 24-09).")
    path.write_bytes(body)
    return path


def cache_within_limits(root: pathlib.Path = COMPETITORS) -> list[str]:
    """Every file at most 1 MB and the whole cache at most 20 MB."""
    if not root.is_dir():
        return []
    files = [f for f in root.rglob("*") if f.is_file()]
    problems = [f"{f.relative_to(root)} is {f.stat().st_size} bytes"
                for f in files if f.stat().st_size > CACHE_FILE_LIMIT]
    total = sum(f.stat().st_size for f in files)
    if total > CACHE_TOTAL_LIMIT:
        problems.append(f"the cache is {total} bytes, over {CACHE_TOTAL_LIMIT}")
    return problems


def measure(set_name: str, entry: dict, pages: list[dict], allowed: dict,
            refused: list[dict], binaries: dict, run_id: str,
            note: str | None, text_dir: pathlib.Path | None = None,
            geometry: str = "tail") -> dict:
    tools: dict = {}
    # What a batched column produced per page, kept for the in-document pass:
    # its reading of a document is its pages strung together, and running it
    # again over an assembled PDF would measure the same per-page decisions at
    # the price of an hour of the paid machine (#584).
    batched: dict[str, dict[str, str]] = {}
    # Competitor runs to store in the cache once their document pass is in.
    pending: dict[str, tuple[dict, dict]] = {}
    furniture_stored: dict[str, dict] = {}
    for tool, tool_entry in allowed.items():
        rows: list[dict] = []
        failed = 0
        milliseconds: list[int] = []
        # Ours and balanced are measured every round and never cached: the
        # cache is for competitors whose output cannot have moved.
        # Balanced+TATR is ours too, under the same rule.
        key = cache_key(set_name, tool, tool_entry, pages) if tool not in (OURS, BALANCED, BALANCED_TATR) else None
        stored = (cache_load(key) if key and os.environ.get("BENCH_COMPETITOR_CACHE")
                  else None)  # pdfluent-bench: cache is opt-in
        if stored is not None:
            # FROM THE CACHE: no competitor process runs, and today's scorer
            # reads what it said then (owner, 24-09-2026).
            produced = stored["markdown"]
            batched[tool] = produced
            if "furniture_in_the_document" in stored:
                furniture_stored[tool] = stored["furniture_in_the_document"]
            for page in pages:
                markdown = produced.get(page["name"])
                if markdown is None:
                    failed += 1
                    continue
                if text_dir is not None:
                    place = text_dir / tool
                    place.mkdir(parents=True, exist_ok=True)
                    (place / f"{page['name']}.md").write_text(markdown)
                rows.append(score_page(page, blocks_of(markdown)))
            tools[tool] = {
                "name": tool_entry.get("name", tool),
                "version": stored["version"],
                "publishable": bool(tool_entry.get("publishable")),
                "pages": len(rows), "did_not_answer": failed,
                "ms_per_page": stored["ms_per_page"],
                "start_up_amortised": stored["start_up_amortised"],
                "dimensions": declared_now(tool, stored["dimensions"], tool_entry),
                "from_cache": {"file": _shown(cache_path(key)),
                               "measured": stored.get("measured")},
                "summary": summarise_tool(rows), "summary_v2": summarise_tool_v2(rows),
                "tail": tails(rows), "documents": rows,
            }
            continue
        cold = cold_start(tool, tool_entry, pages, binaries)
        if tool in BATCHED:
            probe: dict = {}
            produced, ms = measure_batch(tool, pages, binaries, text_dir, probe=probe)
            batched[tool] = produced
            laps = probe.get("laps") or {}
            outcomes = []
            for page in pages:
                lap = laps.get(page["name"])
                # A page the adapter never reached ran out with the batch: a
                # timeout if the batch timed out, else the batch died on it.
                outcomes.append(lap[1] if lap else "ok" if page["name"] in produced else
                                ("timeout" if probe.get("outcome") == "timeout" else "crash"))
                if lap and lap[1] == "ok":
                    milliseconds.append(lap[0])
            markdowns = [produced[p["name"]] for p in pages if p["name"] in produced]
            for page in pages:
                markdown = produced.get(page["name"])
                if markdown is None:
                    failed += 1
                    continue
                if text_dir is not None:
                    place = text_dir / tool
                    place.mkdir(parents=True, exist_ok=True)
                    (place / f"{page['name']}.md").write_text(markdown)
                rows.append(score_page(page, blocks_of(markdown)))
            tools[tool] = {
                "name": tool_entry.get("name", tool),
                "version": tool_version(tool, tool_entry, binaries),
                "publishable": bool(tool_entry.get("publishable")),
                "pages": len(rows),
                "did_not_answer": failed,
                "ms_per_page": round(ms / max(1, len(pages)), 1),
                "start_up_amortised": True,
                "dimensions": dimensions_of(tool, tool_entry, binaries, milliseconds,
                                            outcomes, [probe.get("peak_rss_kb")],
                                            markdowns, cold),
                "summary": summarise_tool(rows),
                "summary_v2": summarise_tool_v2(rows),
                "tail": tails(rows),
                "documents": rows,
            }
            # Only a batch that ran to its end is the tool's answer: a batch
            # the machine killed (a timeout, the memory ceiling) is ours, and
            # caching it would make an infrastructure fault permanent.
            if (key is not None and probe.get("outcome") == "ok"
                    and os.environ.get("BENCH_COMPETITOR_CACHE")):  # pdfluent-bench: opt-in
                # Written after the document pass below, which it carries too.
                pending[tool] = (key, {
                    "version": tools[tool]["version"],
                    "ms_per_page": tools[tool]["ms_per_page"],
                    "start_up_amortised": True,
                    "dimensions": tools[tool]["dimensions"],
                    "measured": {"run_id": run_id, "machine": machine()},
                    "markdown": produced,
                })
            continue
        outcomes = []
        peaks = []
        markdowns = []
        per_page_markdown: dict[str, str] = {}
        for page in pages:
            probe = {}
            markdown, ms = markdown_of(tool, tool_entry, pathlib.Path(page["path"]), binaries,
                                       probe=probe)
            milliseconds.append(ms)
            peaks.append(probe.get("peak_rss_kb"))
            outcomes.append(probe.get("outcome", "crash" if markdown is None else "ok"))
            if markdown is None:
                failed += 1
                continue
            markdowns.append(markdown)
            per_page_markdown[page["name"]] = markdown
            # WHAT EVERY COLUMN ACTUALLY SAID, kept beside the scores. The
            # instance is deleted after the round, so a later change to an axis
            # is otherwise answerable only by buying the machine again -- which
            # is what #428 learnt on the plain-text rig. The texts are evidence
            # and not a register: they leave with the evidence tarball and
            # nothing commits them.
            if text_dir is not None:
                out = text_dir / tool
                out.mkdir(parents=True, exist_ok=True)
                (out / f"{page['name']}.md").write_text(markdown)
            row = score_page(page, blocks_of(markdown))
            # WHAT OUR OWN BLOCKS WERE SET IN, and only ours: see `set_in`. It
            # is asked for here, in the page-at-a-time loop, because that is the
            # loop our column takes -- `BATCHED` is the adapted tools' path and what
            # their document models were set in is their business. A page that cannot
            # answer leaves the key out.
            if tool == "pdfluent":
                typography = set_in(json_of(pathlib.Path(page["path"]), binaries))
                if typography is not None:
                    row["set_in"] = typography
            rows.append(row)
        tools[tool] = {
            "name": tool_entry.get("name", tool),
            "version": tool_version(tool, tool_entry, binaries),
            "publishable": bool(tool_entry.get("publishable")),
            "pages": len(rows),
            "did_not_answer": failed,
            "ms_per_page": axes.percentile(milliseconds, 0.5) if milliseconds else None,
            # One process per page: the CLI is the only entry point on the rig
            # and its start-up is part of what a caller pays for a page.
            "start_up_amortised": False,
            # AND THE SAME COLUMN IN ONE BATCH (#623), beside it and never
            # instead of it: the table's clock stays the one it has always been.
            **({"batch_clock": batch_clock(
                pages, binaries, per_page_markdown,
                (text_dir.parent if text_dir is not None else pathlib.Path("/tmp")) / "ours-batch")}
               if tool == OURS else {}),
            "dimensions": dimensions_of(tool, tool_entry, binaries, milliseconds,
                                        outcomes, peaks, markdowns, cold),
            "summary": summarise_tool(rows),
            "summary_v2": summarise_tool_v2(rows),
            "tail": tails(rows),
            "documents": rows,
        }
    # THE SAME PAGES, PUT BACK IN THEIR DOCUMENTS (#575). One extra pass per
    # column, after every per-page number is in, so that a failure here cannot
    # cost the round its axes.
    for tool, tool_entry in allowed.items():
        if tool in furniture_stored:
            # The document pass runs the tool over assembled documents; from the
            # cache, what it counted then is what it counts.
            tools[tool]["furniture_in_the_document"] = dict(furniture_stored[tool])
            continue
        try:
            in_document = furniture_in_documents(
                tool, tool_entry, pages, binaries,
                (text_dir.parent if text_dir is not None else pathlib.Path("/tmp")))
        except OSError as exc:
            # AND THE SENTENCE ABOVE IS TRUE BECAUSE OF THIS. Everything else
            # in this pass answers with a falsy value rather than raising, but
            # a full disk does raise -- and a round that lost two hundred pages
            # of axes to a second count of running headers would be a round
            # bought twice.
            in_document = {"why": f"the pass did not finish: {exc}"}
        covered = set(in_document.pop("covered", []))
        if covered:
            in_document["single_page_in_the_body"] = sum(
                row["furniture"]["in_the_body"] for row in tools[tool]["documents"]
                if row["name"] in covered)
        tools[tool]["furniture_in_the_document"] = in_document
    for tool, (key, record) in pending.items():
        record["furniture_in_the_document"] = tools[tool]["furniture_in_the_document"]
        tools[tool]["cached_to"] = _shown(cache_store(key, record))

    # OUR OWN GEOMETRY FOR THE PAGES A TICKET WILL BE SENT TO. The tail of every
    # axis, or every page where the caller asked for it and the tar can carry it.
    # After the axes are in, for the reason the furniture pass gives: a failure
    # here may not cost the round its numbers.
    if text_dir is not None and OURS in tools:
        wanted = (
            [page["name"] for page in pages]
            if geometry == "all"
            else pages_in_any_tail(tools[OURS]["tail"])
        )
        if geometry == "none":
            wanted = []
        try:
            kept = keep_geometry(pages, wanted, binaries.get(OURS),
                                 text_dir / "geometry")
        except OSError as exc:
            kept = {"pages": 0, "why": f"the pass did not finish: {exc}"}
        kept["asked_for"] = geometry
        tools[OURS]["geometry_kept"] = kept

    # AND EVERY AXIS THE SAME WAY (#584). The reading the gate holds, for the
    # reason `in_documents` gives: half of what the heading axis charges is a
    # rule about a document being measured on a page. One more pass per column
    # and, like the furniture count, after every per-page number is in.
    for tool, tool_entry in allowed.items():
        try:
            rescored = in_documents(
                tool, tool_entry, pages, binaries,
                (text_dir.parent if text_dir is not None else pathlib.Path("/tmp")),
                text_dir,
                per_page=batched.get(tool))
        except OSError as exc:
            rescored = {"why": f"the pass did not finish: {exc}"}
        tools[tool]["in_documents"] = rescored

    return {
        "run_id": run_id,
        # Version 1 of the #621 dimensions: every column carries `dimensions`,
        # and `dimension_lines` refuses the table if any is missing.
        "dimensions_schema": 1,
        "when": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "set": set_name,
        "set_version": entry.get("version"),
        "set_licence": entry.get("licence"),
        "publishable": bool(entry.get("publishable"))
        and all(t["publishable"] for t in tools.values()),
        "pages_measured": len(pages),
        "half": note or "tuning",
        "machine": machine(),
        # WHICH HARNESS. The tool version says what was measured and this says
        # what measured it: two runs of one tool on one set can differ because
        # an axis changed, and a run file that cannot say so is a run file that
        # will be compared with the wrong thing.
        "harness": harness_revision(),
        # WHICH LINE, so that two branches' rounds are not read as one
        # sequence: see `line_measured_on`.
        "line": line_measured_on(),
        "refused": refused,
        "one_page_per_file": census(pages),
        # THE REGROUPING IS OF THIS HALF AND NO OTHER. The split and the sample
        # are both by whole document, so a sampled document's pages are all in
        # the sample and none of a sealed document's can be. Recorded as a
        # count rather than asserted in prose: a reader can check it against
        # the manifest.
        "regrouped": {
            "documents": len({p["document"] for p in pages}),
            "pages": len(pages),
            "multi_page_documents": len([
                d for d, rows in _by_document(pages).items() if len(rows) > 1]),
            "half": note or "tuning",
        },
        "order_provenance": order_provenance(pages),
        "tools": tools,
    }


# Every axis, with the key its per-page row keeps the number under and the
# name a table prints. `teds` is here rather than beside `tables` because the
# two answer different questions and a set answers one, the other or neither.
AXES = (("text", "similarity", "text (1-NED)"),
        ("text_geometric", "similarity", "text (geometric)"),
        ("words", "f1", "text (words, order-free)"),
        ("headings", "f1", "headings F1"),
        ("lists", "f1", "lists F1"),
        ("tables", "f1", "tables F1"),
        ("teds", "teds", "tables TEDS"),
        ("order", "score", "order (annotated)"),
        ("order_geometric", "score", "order (geometric)"))
# The references every axis is read against, in this order (owner via COS,
# 28-09-2026): run key and the label its column carries.
REFERENCES = (("pymupdf4llm", "pymupdf4llm"), ("docling", "Docling"))


def summarise_tool(rows: list[dict]) -> dict:
    out: dict = {}
    for axis, key, _label in AXES:
        values = [{"v": r[axis][key]} for r in rows if r[axis].get("scored")]
        out[axis] = axes.summarise(values, "v")
        out[axis]["unscored"] = len(rows) - len(values)
    leaked = [r["furniture"]["in_the_body"] for r in rows]
    out["furniture_in_the_body"] = sum(leaked)
    # THE HOLE IN AN F1 OVER ONLY THE PAGES THAT HAVE ONE. A page with no
    # headings annotated is not scored for headings -- there is no recall to
    # compute -- so a tool may mark every line on it a heading for free. That is
    # not hypothetical: it is the cheapest way to raise a heading score.
    #
    # Counted rather than folded into the F1, because a page with no ground
    # truth for a category cannot say whether a block on it IS one; what it can
    # say is how often a tool claimed a category the annotator saw nowhere on
    # the page, and a tool whose count here is large has a precision problem the
    # F1 cannot see.
    for axis in ("headings", "lists", "tables"):
        out[f"{axis}_where_none_annotated"] = sum(
            r[axis].get("produced", 0) for r in rows if not r[axis].get("scored"))
    return out


# The four axes matching v2 re-reads, with the key each v2 cell keeps its
# number under. Tables, order and matching have no v2: the rule joins in-word
# breaks, and those axes do not read words.
V2_AXES = (("text", "similarity", "text (1-NED)"),
           ("words", "f1", "text (words, order-free)"),
           ("headings", "f1", "headings F1"),
           ("lists", "f1", "lists F1"))


def summarise_tool_v2(rows: list[dict]) -> dict:
    """`summarise_tool` for the v2 cells: means, counts and the applied share.

    Reads defensively (`.get`), so rows scored by an older harness -- which
    have no v2 cells -- summarise to unscored rather than raising. v1 stays
    the reference; this is the extra column beside it.
    """
    out: dict = {}
    for axis, key, _label in V2_AXES:
        values = [{"v": r[f"{axis}_v2"][key]} for r in rows
                  if (r.get(f"{axis}_v2") or {}).get("scored")]
        out[f"{axis}_v2"] = axes.summarise(values, "v")
        out[f"{axis}_v2"]["unscored"] = len(rows) - len(values)
    out["v2_applied"] = sum(1 for r in rows if r.get("v2_applied"))
    out["pages"] = len(rows)
    return out


def tails(rows: list[dict], share: float = 0.05) -> dict:
    """The worst five percent per axis, by name. What the baseline is for."""
    out: dict = {}
    for axis, key, _label in AXES:
        scored = [{"v": r[axis][key], "name": r["name"], "document": r["document"]}
                  for r in rows if r[axis].get("scored")]
        out[axis] = axes.tail(scored, "v", share)
    return out


def pages_in_any_tail(tail: dict) -> list[str]:
    """Every page that is in the worst five percent of ANY axis, once.

    ANY axis, and that is the whole of the rule. A round is read afterwards by
    whichever ticket the numbers sent somebody to, and the page that cost the
    heading axis is rarely the page that cost the tables. Keeping the geometry of
    one axis's tail would answer one ticket and send the next one back to the
    machine, which is the cost this exists to remove (#580).

    In the order the axes are declared and the tail is sorted, so two rounds of
    one tree keep the same pages in the same order rather than whatever a set
    iterated to.
    """
    seen: list[str] = []
    for axis, _key, _label in AXES:
        for row in tail.get(axis) or []:
            name = row.get("name")
            if name and name not in seen:
                seen.append(name)
    return seen


def keep_geometry(pages: list[dict], names: list[str], binary: str | None,
                  where: pathlib.Path) -> dict:
    """Our own blocks, with their boxes, for the pages named.

    WHY THE BOXES AND NOT ONLY THE MARKDOWN. The markdown says what we read and
    the annotations say what was there, which is enough to COUNT a cluster and
    not enough to say why: which rail of the paragraph merge refused which pair
    is a question about baseline steps, left edges and the tabular mark, and none
    of that survives in a serializer's output. #580 counted twelve pages that
    come out one block per line and could not name the cause, because the pages
    are DocLayNet and the set is never on the machine that asks. #578 hit the
    same wall for the same reason: the evidence carries no type size, so the
    heading axis cannot be diagnosed offline either.

    Our column only. The reference's internals are its own business and its
    markdown is what the comparison is made of; ours is the one we have to
    debug.
    """
    if not binary or not names:
        return {"pages": 0, "why": "no page was asked for, or we have no binary"}
    where.mkdir(parents=True, exist_ok=True)
    by_name = {page["name"]: page for page in pages}
    written = 0
    failed: list[str] = []
    for name in names:
        page = by_name.get(name)
        if page is None or not page.get("path"):
            failed.append(name)
            continue
        out, _ms = run_tool([binary, "--json", str(page["path"])])
        if not out:
            failed.append(name)
            continue
        (where / f"{name}.json").write_text(out)
        written += 1
    kept = {"pages": written, "of": len(names)}
    if failed:
        kept["did_not_answer"] = failed
    return kept


def tool_version(tool: str, entry: dict, binaries: dict) -> str:
    command = entry.get("version_command") or ""
    if not command:
        return "unknown"
    if tool == "pdfluent":
        return os.environ.get("PDFLUENT_COMMIT", "source build")
    out, _ = run_tool(["bash", "-lc", command], timeout=60)
    return (out or "unknown").strip().splitlines()[0] if out else "unknown"


def harness_revision() -> str:
    out, _ = run_tool(["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"], timeout=30)
    return (out or "unknown").strip() or "unknown"


def line_measured_on() -> dict:
    """WHICH LINE OF CODE this round measured, so that two branches' rounds are
    not read as one sequence (#593).

    The ratchet asks whether a committed round is worse than the round before
    it, and `before` is a question about the CODE and not about the clock. Two
    terminals working the same evening measure two rounds an hour apart on two
    branches, and the later one is not the successor of the earlier: it is its
    sibling, measured on a tree that never held the other's fix. Read as a
    sequence, the stronger sibling makes the weaker one a regression that
    nobody can repair, because there is nothing wrong.

    The line is the BRANCH, not the merge-base: both branches of the evening
    that made this rule necessary had the same merge-base `7c52d04c3`, so a
    base cannot tell them apart. A branch name survives a rebase, which the
    tree's own sha does not -- a rebase rewrites exactly the commits an
    ancestry test would ask about.

    A round measured on master says so, and a round whose branch cannot be read
    leaves the key out: what lands on master IS master's line, which is how
    every round committed before this field is read.

    AND THE BRANCH THIS ASKS ABOUT IS THE CALLER'S, NOT THE CHECKOUT'S. A round
    runs on a throwaway instance, and `remote_gate.sh` puts the shipped tree on a
    scratch branch of its own making -- `git checkout -f -B gate-run` -- so on
    every gate instance this function used to read `gate-run`. That is not a line
    of development, it is the name of a hook the tree was hung on, and
    `structured_parity` correctly refuses to read a branch round as master's:
    the daily regression round of 24-09-2026, taken on master 1847078bd, was
    stepped past for exactly that reason and judged nothing (#574). The round
    had cost a paid hour and moved no ratchet.
    #
    So the gate forwards the branch it was called FROM as `GATE_LINE_BRANCH`,
    and that wins here. `GATE_LINE_BRANCH=` empty means the caller was on a
    detached HEAD, which is what a round on a pinned sha of master is, and an
    empty line is master's.
    #
    SCRATCH_BRANCHES is refused even when nothing was forwarded, because the
    alternative is to record a name that can only be wrong. A round whose line
    cannot be read says nothing, and nothing reads as master -- which for a gate
    round is true far more often than `gate-run` ever was, since what a gate
    measures is what somebody is about to push or what already landed.
    """
    forwarded = os.environ.get("GATE_LINE_BRANCH")
    if forwarded is not None:
        branch = forwarded.strip()
    else:
        branch, _ = run_tool(["git", "-C", str(REPO), "rev-parse", "--abbrev-ref", "HEAD"],
                             timeout=30)
        branch = (branch or "").strip()
    if not branch or branch == "HEAD" or branch in SCRATCH_BRANCHES:
        branch = ""
    base, _ = run_tool(["git", "-C", str(REPO), "merge-base", "HEAD", "github/master"],
                       timeout=30)
    out = {"branch": branch} if branch else {}
    base = (base or "").strip()
    if base:
        out["merge_base"] = base[:9]
    return out


# Branch names no round may record as its line: they are checkouts a tool made,
# not lines anybody develops on. `gate-run` is `remote_gate.sh`'s; the landing
# lane's is here for the same reason, before it costs a second round. (#593)
SCRATCH_BRANCHES = frozenset({"gate-run", "landing-run"})


def machine() -> dict:
    load = os.getloadavg()[0] if hasattr(os, "getloadavg") else None
    return {"platform": platform.platform(), "machine": platform.machine(),
            "cpus": os.cpu_count(), "load_at_start": load,
            "instance": os.environ.get("GATE_INSTANCE", "")}


# ---------------------------------------------------------------------------
# The run file, the table, and the rule about how often this may run.
# ---------------------------------------------------------------------------
def rescore_furniture_in_documents(evidence: pathlib.Path, tool: str,
                                   truth: list[dict], before: dict) -> dict:
    """The furniture count with the pages back in their documents, again (#584).

    The same markdown the axes are rescored from, and the same rule as the
    original pass: the document's own annotated furniture matched against what
    the column emitted for that document.
    """
    place = evidence / "documents" / tool
    if not place.is_dir():
        return {"why": "a rescore reads the markdown a round kept, and this "
                       "round kept none per document"}
    grouped = _by_document(truth)
    multi = {name: rows for name, rows in grouped.items() if len(rows) > 1}
    annotated = leaked = documents = pages = 0
    missing: list[str] = []
    for name, rows in multi.items():
        written = place / f"{hashlib.sha256(name.encode()).hexdigest()[:16]}.md"
        if not written.is_file():
            missing.append(name)
            continue
        produced = blocks_of(written.read_text())
        furniture = [axes.normalise(b["text"]) for row in rows
                     for b in row["blocks"] if b["kind"] == "furniture"]
        furniture = [f for f in furniture if f]
        annotated += len(furniture)
        leaked += len(axes.match_items(
            furniture, [axes.normalise(b["text"]) for b in produced]))
        documents += 1
        pages += len(rows)
    if not documents:
        return {"why": f"none of the {len(multi)} document(s) left markdown to "
                       "count again"}
    return {"documents": documents, "pages": pages, "annotated": annotated,
            "in_the_body": leaked, "of_documents": len(multi),
            "documents_that_did_not_answer": missing,
            "single_page_in_the_body":
                (before.get("furniture_in_the_document") or {}).get("single_page_in_the_body")}


def rescore_in_documents(evidence: pathlib.Path, tool: str, truth: list[dict],
                         before: dict) -> dict:
    """The in-document reading again, from the markdown the round kept (#584).

    What it may not do is invent the reading where the round did not take it:
    a round from before this pass existed kept no per-document markdown, and a
    rescore that quietly reported the page reading under the document one would
    be the mistake the `harness` field exists to make visible.
    """
    place = evidence / "documents" / tool
    if not place.is_dir():
        return {"why": "this round kept no per-document markdown, so it has no "
                       "in-document reading to score again"}
    grouped = _by_document(truth)
    multi = {name: sorted(rows, key=lambda r: (r.get("page_no") is None,
                                               r.get("page_no"), r["name"]))
             for name, rows in grouped.items() if len(rows) > 1}
    rows = []
    missing: list[str] = []
    for name, pages in multi.items():
        written = place / f"{hashlib.sha256(name.encode()).hexdigest()[:16]}.md"
        if not written.is_file():
            missing.append(name)
            continue
        rows.append(score_page(document_of(name, pages), blocks_of(written.read_text())))
    if not rows:
        return {"why": f"none of the {len(multi)} document(s) of this round left "
                       "markdown to score again"}
    return {
        "documents": len(rows),
        "of_documents": len(multi),
        # Carried over, because it is a property of how the round was TAKEN and
        # not of how it is scored.
        "read_as": (before.get("in_documents") or {}).get("read_as"),
        "documents_that_did_not_answer": missing,
        "summary": summarise_tool(rows),
        "tail": tails(rows),
        "documents_measured": rows,
    }


def rescore(evidence: pathlib.Path, previous: dict, set_name: str,
            entry: dict) -> dict:
    """Score a finished round again, from what it kept, without a machine.

    The instance is gone; what is left is every column's markdown and the
    annotations it was graded against. That is enough to answer "what would this
    round have said with the axis as it is now" -- which is the question a
    review of the scoring raises, and the one that otherwise costs another paid
    hour. #428 learnt the same thing on the plain-text rig.

    The run id says what this is: the original's, with `-rescored-<harness>`
    after it, and `rescored_from` in the file. It is not a new measurement and
    must not be read as one -- no tool ran, no clock was taken, and the timing
    of the original is carried over unchanged.
    """
    truth = json.loads((evidence / "ground-truth.json").read_text())
    # Evidence written before `annotates` was kept says nothing about what its
    # set drew, and the register does -- so an old round can still be rescored
    # without reporting axes its corpus never annotated. (codex, #1930)
    drawn = entry.get("annotates")
    if drawn:
        for page in truth:
            page.setdefault("annotates", list(drawn))
    tools: dict = {}
    for tool, before in previous["tools"].items():
        rows = []
        missing = 0
        for page in truth:
            written = evidence / tool / f"{page['name']}.md"
            if not written.is_file():
                missing += 1
                continue
            rows.append(score_page(page, blocks_of(written.read_text())))
        # RECOMPUTED, NOT ADDED. A page the original round could not read has no
        # markdown to re-read either, so it is already inside `missing`: adding
        # the old count on top made a 200-page round report 199 answered and 2
        # unanswered. The evidence is the whole population here. (codex, #1922)
        tools[tool] = dict(
            before, pages=len(rows), did_not_answer=missing,
            summary=summarise_tool(rows), summary_v2=summarise_tool_v2(rows),
            tail=tails(rows), documents=rows,
            **({"dimensions": declared_now(tool, before["dimensions"])}
               if before.get("dimensions") else {}),
            # NOT CARRIED OVER. The evidence is one markdown file per page, and
            # the document-level furniture count needs the tool to read a whole
            # document -- which needs the machine that is already gone. Keeping
            # the original's number here would put a figure taken by another
            # harness under this one's run id. (#575)
            # RESCORABLE SINCE #584, for the same reason the axes are: a round
            # now keeps what each column made of each assembled document, and
            # the count is a match over that markdown. A round that kept none
            # still says why rather than carrying another harness's figure.
            furniture_in_the_document=rescore_furniture_in_documents(
                evidence, tool, truth, before),
            # THE IN-DOCUMENT READING IS RESCORABLE, and it is the one the gate
            # holds (#584). A round keeps what each column made of each
            # assembled document beside what it made of each page, so the
            # question "what would the document reading say with the axis as it
            # is now" costs nothing either -- which is the whole reason the
            # geometric order of run ...20260922T144220Z could be corrected
            # without buying the machine again.
            in_documents=rescore_in_documents(evidence, tool, truth, before))
    return dict(previous,
                pages_digest=pages_digest(truth),
                run_id=f"{previous['run_id']}-rescored-{harness_revision()}",
                when=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                harness=harness_revision(),
                rescored_from=previous["run_id"],
                rescored_why="no tool ran; the columns are the markdown the "
                             "original round kept, scored by this harness",
                order_provenance=order_provenance(truth),
                tools=tools)


def may_i_run(tool: str) -> int:
    """Answer, for a shell caller, whether one tool may be run at all.

    Exists because a wrapper cannot import this file. The remote round drives a
    preflight before the harness starts, and a preflight that asks no register
    is a way to run software whose terms no longer permit it -- which is exactly
    what both registers are for. Prints one line and answers with its exit
    status. (codex, #1922)
    """
    allowed, refused = tools_allowed([tool])
    if allowed:
        print(f"[{PREFIX}] {tool}: allowed")
        return 0
    why = refused[0]["why"] if refused else "unknown"
    print(f"[{PREFIX}] {tool}: NOT allowed -- {why}", file=sys.stderr)
    return 1


def pages_digest(pages: list[dict]) -> str:
    """One digest over the pages a run measured, in the order it measured them.

    Over the source and the name, which is what the manifest's own seal is
    over, so that the two can be compared by anybody holding both.
    """
    h = hashlib.sha256()
    for page in pages:
        h.update(f"{page.get('source', '')}\0{page['name']}\n".encode())
    return h.hexdigest()


def run_id_of(set_name: str) -> str:
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{PREFIX}-{set_name}-{stamp}"


def runs_of_this_head(set_name: str, harness: str) -> list[pathlib.Path]:
    """The committed runs of `set_name` that were measured on this tree.

    A run file says which revision scored it, and that is the whole of the
    question: measuring the same head on the same set twice is the round per
    fix the rule exists to stop, and measuring a head nobody has measured yet
    is the round it exists to allow.
    """
    if not RUNS.is_dir() or not harness or harness == "unknown":
        return []
    found = []
    for path in sorted(RUNS.glob(f"{PREFIX}-{set_name}-*.json")):
        try:
            run = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if run.get("harness") == harness and not run.get("rescored_from"):
            found.append(path)
    return found


def money_left() -> tuple[float, float] | None:
    """What the ledger says is left of the ceiling, or None where it cannot say.

    THE LEDGER IS THE HARD LIMIT and the only one the owner's rule names, so a
    ledger that cannot be read does not refuse a round -- "I could not look" is
    not "there is no money", which is the reading `hetzner_ledger.py` itself
    insists on for a price it could not fetch.
    """
    ledger = REPO / "scripts" / "cos" / "hetzner_ledger.py"
    if not ledger.is_file():
        return None
    out, _ = run_tool([sys.executable, str(ledger), "total"], timeout=60)
    try:
        spent, ceiling, _warn, left = (float(x) for x in (out or "").split()[:4])
    except (ValueError, IndexError):
        return None
    return spent, left if left == left else ceiling - spent


def check_budget(force: bool, set_name: str = "") -> None:
    """The owner's rule of 22-09-2026, and the two things it refuses.

    One run per validated bundle: a head already measured on this set has had
    its round, and the ceiling is the hard limit. Nothing about the calendar --
    see the note beside `RUNS_PER_DAY`'s replacement above for what counting
    days did on 22-09.

    `--anyway` stays, because a rule with no way past it is a rule somebody
    routes around instead of arguing with. It is for a reason written on a
    ticket, and the round file carries the reason where a reader will find it.

    IT GOES PAST THE DUPLICATE RULE AND NOT PAST THE CEILING, which is why the
    ceiling is asked FIRST. The refusal below says the hard limit takes a
    decision by the owner and not a flag; an early return above it would have
    made that sentence false, and a flag that quietly buys a machine after the
    ledger says nothing is left is the failure the ceiling exists to stop.
    """
    return  # pdfluent-bench: cost guard lives in infra/gcp (BUDGET_EUR); PORTING.md
    spent = money_left()
    if spent is not None and spent[1] <= 0.0:
        raise SystemExit(
            f"[{PREFIX}] REFUSED: the Hetzner ledger says EUR {spent[0]:.2f} is "
            "spent and nothing is left of the ceiling. That is the hard limit "
            "of the owner's rule of 22-09-2026; it takes a decision by the "
            "owner and not a flag -- --anyway does not pass it.")
    if force or not set_name:
        return
    already = runs_of_this_head(set_name, harness_revision())
    if not already:
        return
    raise SystemExit(
        f"[{PREFIX}] REFUSED: this head has already been measured on "
        f"`{set_name}` ({', '.join(p.stem for p in already)}).\n"
        "The owner's rule of 22-09-2026 is one run per validated bundle: "
        "change something and measure that, or pass --anyway with the reason "
        "on the ticket.")


def table_of(run: dict) -> str:
    """The run as the table that goes on the ticket."""
    lines = [f"### {run['run_id']}", "",
             f"Set `{run['set']}` ({run['set_version']}, {run['set_licence']}), "
             f"{run['pages_measured']} pages of the {run['half']} half, "
             f"{run['machine']['cpus']} CPU, {run['machine']['platform']}."]
    # THE HEADLINE IS PER DOCUMENT (COS, 29-09-2026), and the page mean stands
    # right under it, as visible: one document of the DocLayNet set is 38 per
    # cent of its heading pages and 42 per cent of its table pages, so the page
    # mean moved with that one document between rounds 1 and 2 while the
    # per-document reading moved the other way.
    lines += macro_lines(run)
    lines += ["", "Per page -- every page weighing the same, beside the headline above:", "",
             "| tool | " + " | ".join(label for _a, _k, label in AXES)
             + " | ms/page |",
             "|---|" + "---|" * (len(AXES) + 1)]
    for tool in run["tools"].values():
        cells = []
        for axis, key, _label in AXES:
            summary = tool["summary"][axis]
            # THE COUNT TRAVELS WITH THE MEAN, and it is not decoration. Run
            # structured-doclaynet-20260917T192519Z scored our reading order on
            # 17 pages and Docling's on 157 of the same 200 -- the axis needs two
            # blocks that MATCH, and a column that emits no blocks has few. The
            # two means are honest and comparing them is not, so a reader has to
            # be able to see the denominator without opening the run file.
            cells.append(f"{summary['mean']:.3f} ⟨{summary['pages']}⟩"
                         if summary.get("mean") is not None else "—")
        clock = f"{tool['ms_per_page'] or '—'}"
        if tool.get("start_up_amortised"):
            clock += " (batch)"
        lines.append(f"| {tool['name']} {tool['version']} | " + " | ".join(cells)
                     + f" | {clock} |")

    names = {key: tool["name"] for key, tool in run["tools"].items()}
    axis_rows = [(axis, label) for axis, _key, label in AXES]
    lines += reference_lines("Against the references, page mean:", axis_rows,
                             reference_scores(run, lambda t: t.get("summary")), names)
    lines += v2_lines(run)

    # THE READING THE GATE HOLDS, AS ITS OWN TABLE (#584). Both are printed and
    # the document one is first, because it is the one a verdict is about and
    # the page one is what the rounds before this are comparable with. A reader
    # who sees one table cannot tell which question it answers.
    regrouped = [(tool, tool.get("in_documents") or {}) for tool in run["tools"].values()]
    if any(entry.get("summary") for _tool, entry in regrouped):
        lines += ["", "With the pages back in their documents, which is the "
                      "reading the gate holds (#584):", "",
                  "| tool | read as | " + " | ".join(label for _a, _k, label in AXES) + " |",
                  "|---|---|" + "---|" * len(AXES)]
        for tool, entry in regrouped:
            summary = entry.get("summary")
            if not summary:
                lines.append(f"| {tool['name']} | {entry.get('why', '—')} | "
                             + " | ".join("—" for _a, _k, _l in AXES) + " |")
                continue
            cells = [f"{summary[axis]['mean']:.3f} ⟨{summary[axis]['pages']}⟩"
                     if summary[axis].get("mean") is not None else "—"
                     for axis, _key, _label in AXES]
            lines.append(f"| {tool['name']} | {entry.get('read_as', '—')} | "
                         + " | ".join(cells) + " |")
        lines += ["", "⟨n⟩ is documents here, not pages. The two tables answer "
                      "two questions: a running head is a heading to anything "
                      "that sees one page, and repetition is what tells it from "
                      "a section head -- so the page reading charges a rule "
                      "about a document for something it cannot see, and the "
                      "document reading is what a caller gets."]
        lines += reference_lines(
            "Against the references, pages back in their documents:", axis_rows,
            reference_scores(run, lambda t: (t.get("in_documents") or {}).get("summary")), names)

    lines += readable_lines(run)
    lines += ["", "⟨n⟩ is the number of pages the axis could be scored on. A page "
                  "is scored for a category only where the ground truth has one, "
                  "and reading order only where at least two blocks matched, so "
                  "two means in one column may be over different pages -- which "
                  "is a reason to read the counts rather than to average them."]
    lines += dimension_lines(run)
    lines += furniture_lines(run)
    lines += tail_lines(run)
    if run["refused"]:
        lines += ["", "Not measured:"]
        for entry in run["refused"]:
            lines.append(f"- **{entry['tool']}** — {entry['why']}")
    return "\n".join(lines) + "\n"


def reference_lines(caption: str, rows: list[tuple[str, str]],
                    scores: dict[str, dict[str, float | None]],
                    names: dict[str, str]) -> list[str]:
    """Ours per axis beside the two references, then the best other column.

    WHY THESE TWO (owner via COS, 28-09-2026). pymupdf4llm and Docling are the
    references a reader compares us with, so each axis puts us against them
    first and the best of the rest after; a best-other column alone names a
    different tool on every axis and answers no single question. A difference is
    ours minus theirs, so a positive one is an axis we lead.
    """
    ours = scores.get(OURS)
    if not ours or not any(key in scores for key, _label in REFERENCES):
        return []

    def against(mine: float | None, theirs: float | None) -> str:
        if mine is None or theirs is None:
            return "—"
        return f"{mine - theirs:+.3f} ({theirs:.3f})"

    lines = ["", caption, "",
             "| axis | ours | " + " | ".join(f"vs {label}" for _k, label in REFERENCES)
             + " | best other |",
             "|---|---|" + "---|" * (len(REFERENCES) + 1)]
    for axis, label in rows:
        mine = ours.get(axis)
        cells = [against(mine, (scores.get(key) or {}).get(axis)) for key, _l in REFERENCES]
        others = sorted(((value, key) for key, row in scores.items() if key != OURS
                         for value in [row.get(axis)] if value is not None),
                        key=lambda pair: (-pair[0], pair[1]))
        if others:
            value, key = others[0]
            best = f"{names.get(key, key)} {value:.3f}" + (
                f" ({mine - value:+.3f})" if mine is not None else "")
        else:
            best = "—"
        lines.append(f"| {label} | " + ("—" if mine is None else f"{mine:.3f}")
                     + " | " + " | ".join(cells) + f" | {best} |")
    lines += ["", "A difference is ours minus theirs, their score in brackets; positive "
                  "is an axis we lead."]
    return lines


def v2_lines(run: dict) -> list[str]:
    """The v2 extra column: v1 and v2 per tool on the four joined axes, v1 first.

    A run scored by an older harness has no `summary_v2` and reads as dashes:
    old JSONs still load, and v2 shows "—" where it was never measured. v1 is
    the reference; this table asks how much of each column's v1 is in-word
    breaks the truth kept and the tool did not.
    """
    try:
        release = axes.lexicon_meta()["release"]
    except (OSError, ValueError, KeyError):
        release = "?"
    head = ["tool"]
    for _axis, _key, label in V2_AXES:
        head += [f"{label} v1", f"{label} v2"]
    head.append("v2 pages")
    lines = ["", "Matching v2 -- dictionary-gated joining of in-word breaks, "
                 "per page, beside the v1 table above:", "",
             "| " + " | ".join(head) + " |",
             "|---|" + "---|" * (len(head) - 1)]
    for tool in run["tools"].values():
        v1 = tool.get("summary") or {}
        v2 = tool.get("summary_v2") or {}
        cells = []
        for axis, _key, _label in V2_AXES:
            first = (v1.get(axis) or {}).get("mean")
            second = (v2.get(f"{axis}_v2") or {}).get("mean")
            cells.append(f"{first:.3f}" if first is not None else "—")
            cells.append(f"{second:.3f}" if second is not None else "—")
        applied, pages = v2.get("v2_applied"), v2.get("pages")
        cells.append(f"{applied}/{pages}" if applied is not None and pages else "—")
        name = tool["name"] if not tool.get("version") else f"{tool['name']} {tool['version']}"
        lines.append(f"| {name} | " + " | ".join(cells) + " |")
    lines += ["", f"v2 = dictionary-gated joining of in-word breaks (English pages only, "
                  f"SCOWL {release}); the same rule for all tools; v1 is the reference."]
    return lines


def reference_scores(run: dict, summary_of) -> dict[str, dict[str, float | None]]:
    """Each tool's mean per axis, as `summary_of(tool)` reads it; tools it cannot read are left out."""
    out: dict[str, dict[str, float | None]] = {}
    for key, tool in run["tools"].items():
        summary = summary_of(tool)
        if summary:
            out[key] = {axis: (summary.get(axis) or {}).get("mean") for axis, _k, _l in AXES}
    return out


def macro_by_document(rows: list[dict]) -> dict:
    """Each axis as the mean over DOCUMENTS of each document's page mean."""
    out: dict = {}
    for axis, key, _label in AXES:
        per_doc: dict[str, list[float]] = {}
        for r in rows:
            cell = r.get(axis) or {}
            if cell.get("scored") and isinstance(cell.get(key), (int, float)):
                per_doc.setdefault(r.get("document") or r["name"], []).append(cell[key])
        means = [sum(v) / len(v) for v in per_doc.values()]
        out[axis] = {"mean": round(sum(means) / len(means), 6) if means else None,
                     "documents": len(means)}
    return out


def macro_lines(run: dict) -> list[str]:
    """The same axes averaged per document, for every tool: the headline since 29-09.

    WHY (COS, 27-09-2026). 121 of the 200 DocLayNet pages of the round come from
    two annual reports, so the page mean the gate holds is mostly a statement
    about those two documents. This table does not replace it; it shows how much
    two documents weigh, beside it.
    """
    tools = [t for t in run["tools"].values() if t.get("documents")]
    if not tools:
        return []
    docs = {r.get("document") for t in tools for r in t["documents"]}
    lines = ["", f"Per document -- the headline (COS, 29-09-2026): each axis the mean of the "
                 f"{len(docs)} documents' own page means, every document weighing the same. "
                 "⟨n⟩ here counts documents:", "",
             "| tool | " + " | ".join(label for _a, _k, label in AXES) + " |",
             "|---|" + "---|" * len(AXES)]
    for tool in tools:
        macro = macro_by_document(tool["documents"])
        lines.append(f"| {tool['name']} | " + " | ".join(
            f"{macro[a]['mean']:.3f} ⟨{macro[a]['documents']}⟩" if macro[a]["mean"] is not None
            else "—" for a, _k, _l in AXES) + " |")
    lines += ["", "⟨n⟩ here is the number of documents the axis could be scored on, not pages."]
    lines += reference_lines(
        "Against the references, per document:",
        [(axis, label) for axis, _key, label in AXES],
        reference_scores(run, lambda t: macro_by_document(t["documents"]) if t.get("documents") else None),
        {key: tool["name"] for key, tool in run["tools"].items()})
    return lines


def readable_pages(run: dict) -> set[str] | None:
    """The pages at least one column got text out of; None when it cannot say.

    WHY A SECOND TABLE (COS, 24-09-2026). Of the 200 DocLayNet pages of the day
    round, 25 give no text in any tool -- 23 of them one image-only patent,
    CN100339781C. That is a fact about the set (it holds scans, and OCR is off on
    every column), not about any tool, and it is the TEXT axis it moves: ours
    0.616 over every page against 0.695 over the readable ones, with Docling at
    0.630 on the whole set (T2). On tables it is noise -- one of the 64 scored
    pages is unreadable, +0.002 -- so the subset is kept on every axis and argued
    on the text one. So the readable subset
    sits BESIDE the whole set, never instead of it: the comparison with Docling
    stays on the whole set, and these are exactly the pages #624's
    `needs_vision` is meant to mark.

    None for a run whose rows predate `produced_chars`; `--rescore` with the
    round's evidence recomputes them.
    """
    # OCR-OFF COLUMNS ONLY (#623). The question is whether the page has a text
    # layer, and a column that reads pixels answers a different one: with
    # Unstructured's Tesseract in the round, every scan "gave text" and the
    # subset of 26-09 was all 200 pages. A column with no `ocr` dimension is an
    # older run, and every column then had OCR off.
    rows_by_tool = [tool.get("documents") or [] for tool in run["tools"].values()
                    if not str((tool.get("dimensions") or {}).get("ocr") or "off")
                    .strip().upper().startswith("ON")]
    if not rows_by_tool or any(not rows or any("produced_chars" not in r for r in rows)
                               for rows in rows_by_tool):
        return None
    return {r["name"] for rows in rows_by_tool for r in rows if r["produced_chars"] > 0}


def readable_lines(run: dict) -> list[str]:
    """The same axes over the readable subset, marked as that and beside the whole set."""
    readable = readable_pages(run)
    if readable is None:
        return ["", "Readable subset: not computable for this run (its rows predate "
                    "`produced_chars`; rescore it from its evidence)."]
    total = run.get("pages_measured") or len(next(iter(run["tools"].values()))["documents"])
    lines = ["", f"Readable subset -- the {len(readable)} of {total} pages at least one "
                 f"OCR-off column got text out of; {total - len(readable)} give no text to "
                 "any of them (no text layer). Beside the whole set, not instead of it: the "
                 "comparison stays on the table above.", "",
             "| tool | " + " | ".join(label for _a, _k, label in AXES) + " |",
             "|---|" + "---|" * len(AXES)]
    for tool in run["tools"].values():
        summary = summarise_tool([r for r in tool["documents"] if r["name"] in readable])
        cells = [f"{summary[axis]['mean']:.3f} ⟨{summary[axis]['pages']}⟩"
                 if summary[axis].get("mean") is not None else "—"
                 for axis, _key, _label in AXES]
        lines.append(f"| {tool['name']} | " + " | ".join(cells) + " |")
    return lines


def furniture_lines(run: dict) -> list[str]:
    """The two furniture counts, side by side.

    The single-page figure is what a set of loose pages can say, and on this
    rig that is not the rule #571 built: it asks three pages to repeat a line
    before it calls it a running head, and a page on its own never offers a
    second. So the pages are put back in their documents and the same count is
    taken again (#575), and the table carries both -- because the one that is
    comparable with an earlier round and the one that means something are not
    the same number.
    """
    rows = []
    for tool in run["tools"].values():
        single = tool["summary"].get("furniture_in_the_body")
        document = tool.get("furniture_in_the_document") or {}
        if document.get("annotated_by_the_set") is False:
            # AND NOT "0 on single pages", which is the same misleading zero
            # this pair of counts exists to avoid: it would read as "this
            # column leaks nothing" and mean "the set drew nothing to leak".
            rows.append(f"- **{tool['name']}** — not measured: {document['why']}")
            continue
        if document.get("in_the_body") is not None:
            rows.append(f"- **{tool['name']}** — {single} on single pages; "
                        f"{document['in_the_body']} of {document['annotated']} "
                        f"with the pages back in their documents "
                        f"({document['documents']} document(s), "
                        f"{document['pages']} page(s))")
        else:
            rows.append(f"- **{tool['name']}** — {single} on single pages; "
                        f"not measured in document "
                        f"({document.get('why', 'not attempted')})")
    if not rows:
        return []
    return ["", "Furniture the tool put in the body (lower is better):"] + rows


def _short(name: str) -> str:
    """A page name a reader can match against the run file.

    DocLayNet names a page by a 64-character digest, and a table of those is
    unreadable; a name that is already short is printed whole rather than
    ellipsised into something that is not the name.
    """
    stem = pathlib.PurePath(name).stem
    return stem if len(stem) <= 16 else f"{stem[:16]}…"


def tail_lines(run: dict, how_many: int = 5) -> list[str]:
    """The worst pages per axis, by name, where the next ticket can read them.

    They are in the run file already; a table nobody opens the JSON for is
    where #576-#579 will actually look, and a tail that lives only in a 300 kB
    file is a tail that gets re-derived by hand every round (#575).
    """
    lines: list[str] = []
    for tool in run["tools"].values():
        tails_of = tool.get("tail") or {}
        rows = []
        for axis, _key, label in AXES:
            worst = tails_of.get(axis) or []
            if not worst:
                continue
            named = ", ".join(f"`{w['document']}` {_short(w['name'])} {w['v']:.3f}"
                              for w in worst[:how_many])
            rows.append(f"| {label} | {len(worst)} | {named} |")
        if not rows:
            continue
        lines += ["", f"The worst five percent, {tool['name']} "
                      f"(the whole list is in the run file):", "",
                  "| axis | pages in the tail | the worst of them |",
                  "|---|---|---|"] + rows
    return lines


def write_run(run: dict) -> pathlib.Path:
    RUNS.mkdir(parents=True, exist_ok=True)
    path = RUNS / f"{run['run_id']}.json"
    path.write_text(json.dumps(run, indent=1) + "\n")
    (RUNS / f"{run['run_id']}.md").write_text(table_of(run))
    return path


# ---------------------------------------------------------------------------
# The self-test: the arithmetic of the protocol, on fixtures, in a second.
# ---------------------------------------------------------------------------
def self_test() -> int:
    """`_self_test` with the competitor cache in a temporary directory: a fixture
    column must never land in the committed cache and be read by a real round."""
    import tempfile as _tf

    real = globals()["COMPETITORS"]
    with _tf.TemporaryDirectory() as scratch:
        globals()["COMPETITORS"] = pathlib.Path(scratch)
        try:
            return _self_test(real)
        finally:
            globals()["COMPETITORS"] = real


# The v1 pin: two pages and two readings as the pre-v2 harness scored
# them. Generated once from the unmodified tree; edited never.
_V1PIN_EXPECTED = '''{"rows": [{"category": "financial_reports", "document": "v1pin.pdf", "furniture": {"annotated": 1, "in_the_body": 0}, "headings": {"f1": 1.0, "matched": 1, "precision": 1.0, "produced": 1, "recall": 1.0, "scored": true, "text_of_matched": 1.0, "truth": 1}, "lists": {"f1": 1.0, "matched": 2, "precision": 1.0, "produced": 2, "recall": 1.0, "scored": true, "text_of_matched": 1.0, "truth": 2}, "name": "v1pin-a.pdf", "order": {"discordant": 0, "matched": 4, "pairs": 6, "produced": 4, "score": 1.0, "scored": true, "truth": 4}, "order_geometric": {"discordant": 0, "matched": 4, "pairs": 6, "produced": 4, "score": 1.0, "scored": true, "truth": 4}, "produced_chars": 117, "tables": {"produced": 0, "scored": false, "truth": 0, "why": "fewer ground-truth items than the floor"}, "teds": {"scored": false, "why": "the set annotates table regions, not cell structure (benchmarks/structured/comparison/DATASETS.toml)"}, "text": {"chars": 141, "edit_distance": 0.014184, "scored": true, "similarity": 0.985816}, "text_geometric": {"chars": 141, "edit_distance": 0.014184, "scored": true, "similarity": 0.985816}, "words": {"duplication": 0.0, "f1": 0.875, "furniture": 0.0, "invented": 0.086957, "matched": 21, "missing": 0.16, "precision": 0.913043, "produced": 23, "recall": 0.84, "scored": true, "truth": 25}}, {"category": "financial_reports", "document": "v1pin.pdf", "furniture": {"annotated": 0, "in_the_body": 0}, "headings": {"f1": 1.0, "matched": 1, "precision": 1.0, "produced": 1, "recall": 1.0, "scored": true, "text_of_matched": 1.0, "truth": 1}, "lists": {"produced": 0, "scored": false, "truth": 0, "why": "fewer ground-truth items than the floor"}, "name": "v1pin-b.pdf", "order": {"discordant": 0, "matched": 3, "pairs": 3, "produced": 3, "score": 1.0, "scored": true, "truth": 3}, "order_geometric": {"discordant": 0, "matched": 3, "pairs": 3, "produced": 3, "score": 1.0, "scored": true, "truth": 3}, "produced_chars": 88, "tables": {"f1": 1.0, "matched": 1, "precision": 1.0, "produced": 1, "recall": 1.0, "scored": true, "text_of_matched": 1.0, "truth": 1}, "teds": {"matched": 1, "per_table": [1.0], "produced": 1, "scored": true, "teds": 1.0, "teds_struct": 1.0, "truth": 1}, "text": {"chars": 113, "edit_distance": 0.132743, "scored": true, "similarity": 0.867257}, "text_geometric": {"chars": 113, "edit_distance": 0.132743, "scored": true, "similarity": 0.867257}, "words": {"duplication": 0.0, "f1": 0.8, "furniture": 0.0, "invented": 0.052632, "matched": 18, "missing": 0.307692, "precision": 0.947368, "produced": 19, "recall": 0.692308, "scored": true, "truth": 26}}], "summary": {"furniture_in_the_body": 0, "headings": {"mean": 1.0, "median": 1.0, "p5": 1.0, "p95": 1.0, "pages": 2, "unscored": 0, "worst": 1.0}, "headings_where_none_annotated": 0, "lists": {"mean": 1.0, "median": 1.0, "p5": 1.0, "p95": 1.0, "pages": 1, "unscored": 1, "worst": 1.0}, "lists_where_none_annotated": 0, "order": {"mean": 1.0, "median": 1.0, "p5": 1.0, "p95": 1.0, "pages": 2, "unscored": 0, "worst": 1.0}, "order_geometric": {"mean": 1.0, "median": 1.0, "p5": 1.0, "p95": 1.0, "pages": 2, "unscored": 0, "worst": 1.0}, "tables": {"mean": 1.0, "median": 1.0, "p5": 1.0, "p95": 1.0, "pages": 1, "unscored": 1, "worst": 1.0}, "tables_where_none_annotated": 0, "teds": {"mean": 1.0, "median": 1.0, "p5": 1.0, "p95": 1.0, "pages": 1, "unscored": 1, "worst": 1.0}, "text": {"mean": 0.926536, "median": 0.926536, "p5": 0.873185, "p95": 0.979888, "pages": 2, "unscored": 0, "worst": 0.867257}, "text_geometric": {"mean": 0.926536, "median": 0.926536, "p5": 0.873185, "p95": 0.979888, "pages": 2, "unscored": 0, "worst": 0.867257}, "words": {"mean": 0.8375, "median": 0.8375, "p5": 0.80375, "p95": 0.87125, "pages": 2, "unscored": 0, "worst": 0.8}}}'''


def _self_test(committed_cache: pathlib.Path) -> int:
    failures: list[str] = []

    def check(name: str, got, want, why: str) -> None:
        if got != want:
            failures.append(f"{name}: got {got!r}, want {want!r} -- {why}")

    # --- a column names its version -------------------------------------
    # A tool on the structured rig with no version command is recorded as
    # "unknown" in every run file, so its column cannot be attributed to the
    # release the rig pinned -- pdfplumber went onto the rig that way. (codex, #1968)
    extractors, _extractors_meta = load_register(EXTRACTORS, "extractors")
    check("every structured-rig tool names its version",
          sorted(k for k, row in extractors.items()
                 if row.get("on_the_structured_rig") and not str(row.get("version_command") or "").strip()),
          [], "tool_version() records 'unknown' for a row without one")
    # A column whose OCR runs in a system package answers for that package: its
    # version joins the column's and its bytes the install size (codex, #1975).
    for key, row in extractors.items():
        # A system OCR engine (Tesseract); MinerU's OCR models are in its own
        # model cache, which install MB already weighs.
        if row.get("on_the_structured_rig") and str(row.get("ocr") or "").upper().startswith("ON") \
                and "tesseract" in str(row.get("ocr")).lower():
            check(f"{key} reads pixels, so its OCR engine is weighed and versioned",
                  bool(row.get("system_packages"))
                  and "tesseract" in str(row.get("version_command")), True,
                  "two runs on two Tesseract releases would otherwise be one column")

    # --- the registers refuse, and say why -------------------------------
    register, register_meta = load_register(DATASETS, "datasets")
    check("the register knows OmniDocBench", "omnidocbench" in register, True,
          "a set that was refused has to stay in the register, or the refusal is "
          "lost the next time somebody asks for it")
    check("and refuses it", register["omnidocbench"].get("on_the_rig"), False,
          "its only statement of terms is research-only, non-commercial")
    check("with a reason", bool(register["omnidocbench"].get("why_not")), True,
          "a refusal without a reason is an opinion")
    try:
        dataset_entry("omnidocbench")
        failures.append("dataset_entry('omnidocbench') returned instead of refusing "
                        "-- the register is read but not obeyed, which is worse "
                        "than not reading it")
    except SystemExit:
        pass
    try:
        dataset_entry("doclaynet")
    except SystemExit as exc:
        failures.append(f"dataset_entry('doclaynet') refused the set on the rig: {exc}")

    allowed, refused = tools_allowed(["ours", "docling", "marker"])
    check("ours and Docling are allowed", sorted(allowed), ["docling", "pdfluent"],
          "both carry a permissive verdict in the extractor register")
    check("Marker is refused", [r["tool"] for r in refused], ["Marker"],
          "its weights licence forbids use by a competitor, and #566 asked for it")
    check("and the refusal carries the clause", bool(refused[0]["clause"]), True,
          "a table missing a column has to say which clause took it out")
    # THE COMPETITIVE REGISTER OF #620: five tools read and cleared, two paid
    # APIs held for the owner's decision on cost.
    competitors = ["liteparse", "pymupdf4llm", "unstructured", "mineru", "pdfplumber"]
    allowed, refused = tools_allowed(competitors + ["llamaparse", "gemini"])
    check("the five cleared tools of #620 are allowed", sorted(allowed), sorted(competitors),
          "each carries a verdict read on 2026-09-23 and a place on the structured rig")
    check("the paid APIs are refused for cost",
          sorted(r["why"].split(" --")[0] for r in refused),
          ["owner decision: cost", "owner decision: cost"],
          "#620: nothing paid runs without the owner's approval")
    # THE #641 TEACHER: our own instrument, allowed on the structured rig and never publishable.
    allowed, refused = tools_allowed(["teacher", "teacher_ep03", "teacher_ens"])
    check("the #641 teachers are allowed on the structured rig", sorted(allowed),
          ["teacher", "teacher_ens", "teacher_ep03"],
          "a teacher column is how its gate is read on the rig")
    check("and it is not publishable", allowed.get("teacher", {}).get("publishable"), False,
          "its weights are private: a number for it describes nothing a customer can obtain")
    check("and neither is the ensemble", allowed.get("teacher_ens", {}).get("publishable"), False,
          "its weights are private twice over")
    check("and the ensemble pins two weights files",
          len(str(allowed.get("teacher_ens", {}).get("version_pin") or "").split()), 2,
          "one sha256 per tgz, so the competitor cache can never serve a half-old ensemble")
    check("and the ensemble runs through the adapter", "teacher_ens" in ADAPTED, True,
          "without it the column is asked for and never answers")
    # THE #641 BALANCED COLUMN: our own ONNX tier, allowed and never publishable.
    allowed, _refused = tools_allowed([BALANCED])
    check("the #641 balanced column is allowed on the structured rig", sorted(allowed),
          [BALANCED],
          "a balanced column is how its gate is read on the rig")
    check("and it is not publishable", allowed.get(BALANCED, {}).get("publishable"), False,
          "its weights are private: a number for it describes nothing a customer can obtain")
    balanced_pin = str(allowed.get(BALANCED, {}).get("version_pin") or "").split()
    check("and it pins the tree, the model, the runtime and its bytes",
          len(balanced_pin), 4,
          "the tree's sha, the model sha256, the ONNX Runtime version and the archive sha256, so the round verifies what it ran")
    check("and the model pin is the ep03 export",
          balanced_pin[1] if len(balanced_pin) == 4 else "", "b7cc5d31f3223c74cc477a1386b894793618aeb398ca59cf5eb7264ec61d8afa",
          "the layout-heron-ep03 weights the tier loads")
    check("and the runtime pin is the shared ORT",
          balanced_pin[2] if len(balanced_pin) == 4 else "", "1.23.2",
          "the one ONNX Runtime every opt-in tier runs against")
    check("and the archive pin is the verified linux-x64 bytes",
          balanced_pin[3] if len(balanced_pin) == 4 else "", ORT_ARCHIVE_SHA256,
          "the sha256 the round refuses the download on, before tar")
    check("and it runs page-at-a-time like ours, not through the adapter",
          (BALANCED in ADAPTED, BALANCED in BATCHED), (False, False),
          "one CLI process per page, markdown on stdout; a batch would amortise the load the per-page clock must carry")
    # THE #636 BALANCED+TATR COLUMN: the same tier with TATR, allowed and
    # never publishable, like BALANCED above.
    allowed, _refused = tools_allowed([BALANCED_TATR])
    check("the #636 balanced+tatr column is allowed on the structured rig", sorted(allowed),
          [BALANCED_TATR],
          "a balanced+tatr column is how its gate is read on the rig")
    check("and it is not publishable", allowed.get(BALANCED_TATR, {}).get("publishable"), False,
          "its weights are private: a number for it describes nothing a customer can obtain")
    tatr_pin = str(allowed.get(BALANCED_TATR, {}).get("version_pin") or "").split()
    check("and it pins the tree, both models, the runtime and its bytes",
          len(tatr_pin), 5,
          "the tree's sha, the layout sha256, the TATR sha256, the ONNX Runtime version and the archive sha256, so the round verifies what it ran")
    check("and the layout pin is the same ep03 export balanced runs",
          tatr_pin[1] if len(tatr_pin) == 5 else "", "b7cc5d31f3223c74cc477a1386b894793618aeb398ca59cf5eb7264ec61d8afa",
          "one binary, one layout model: only TATR on/off differs between the columns")
    check("and the TATR pin is the v1.1-all export",
          tatr_pin[2] if len(tatr_pin) == 5 else "", "8c28418690b896a845a53633736a62c22ef8c9a515a23c06e9976e61360f1fcf",
          "the tatr-v1.1-all weights the tier loads")
    check("and the runtime pin is the shared ORT",
          tatr_pin[3] if len(tatr_pin) == 5 else "", "1.23.2",
          "the one ONNX Runtime every opt-in tier runs against")
    check("and the archive pin is the one archive both columns pin",
          (balanced_pin[3] if len(balanced_pin) == 4 else "",
           tatr_pin[4] if len(tatr_pin) == 5 else ""),
          (ORT_ARCHIVE_SHA256, ORT_ARCHIVE_SHA256),
          "one ORT archive serves both columns; the round refuses when they disagree")
    check("and it names the TATR weights it runs",
          ("7587a7ef" in str(allowed.get(BALANCED_TATR, {}).get("weights") or ""),
           "MIT" in str(allowed.get(BALANCED_TATR, {}).get("weights") or "")), (True, True),
          "the source revision and the licence, so the row says what the round fetched")
    check("and it runs page-at-a-time like ours, not through the adapter",
          (BALANCED_TATR in ADAPTED, BALANCED_TATR in BATCHED), (False, False),
          "one CLI process per page, markdown on stdout; a batch would amortise the load the per-page clock must carry")
    # NO VERDICT, NO RUN (#620), driven with a fixture register rather than read
    # off the real one: a row flagged for the rig whose terms were never read is
    # exactly the mistake the order "read, write, run" exists to stop.
    unread_row = {"name": "Unread", "on_the_structured_rig": True, "licence": "MIT",
                  "clause": "", "read_on": "2026-09-23", "verdict": ""}
    allowed, refused = tools_allowed(["unread"], {"unread": unread_row},
                                     {"verdict_valid_days": 180})
    check("a tool with no verdict is refused, whatever its flag says", sorted(allowed), [],
          "the register's order is read, write the verdict, then run")
    check("and the refusal names what is missing",
          bool(refused) and "clause" in refused[0]["why"] and "verdict" in refused[0]["why"],
          True, "a refusal that does not say what is missing cannot be fixed")
    read_row = dict(unread_row, clause="MIT, the grant", verdict="read",
                    read_on=dt.date.today().isoformat())
    allowed, _ = tools_allowed(["read"], {"read": read_row}, {"verdict_valid_days": 180})
    check("the same row with its verdict written is allowed", sorted(allowed), ["read"],
          "the refusal must be about the verdict and nothing else")
    # THE EXPIRY IS ENFORCED. Both registers say a verdict older than 180 days
    # is absent permission rather than permission; until #1922 nothing read it.
    check("the register sets an expiry", bool(register_meta.get("verdict_valid_days")),
          True, "a verdict with no window cannot go stale, which is not the same "
                "as never going stale")
    try:
        fresh_enough({"read_on": "2020-01-01"}, register_meta, "a stale fixture")
        failures.append("a verdict read in 2020 was accepted -- the window in the "
                        "register is decoration")
    except SystemExit:
        pass
    try:
        fresh_enough({}, register_meta, "a fixture with no date")
        failures.append("an entry with no `read_on` was accepted")
    except SystemExit:
        pass
    # And every entry ON the rig is current today, which is the check that goes
    # red on the morning a verdict expires rather than on the round after it.
    for name, entry in register.items():
        if entry.get("on_the_rig"):
            try:
                fresh_enough(entry, register_meta, name)
            except SystemExit as exc:
                failures.append(f"the set `{name}` is on the rig with a stale "
                                f"verdict: {exc}")

    # --- markdown in, blocks out -----------------------------------------
    blocks = blocks_of("# Title\n\nA paragraph of text.\n\n- one\n- two\n\n"
                       "| a | b |\n|---|---|\n| c | d |\n")
    check("a heading is read as one", blocks[0]["kind"], "heading",
          "a line that says it is a heading is one")
    check("a paragraph is read as one", blocks[1]["kind"], "paragraph", "")
    check("list items are read one by one", [b["kind"] for b in blocks[2:4]],
          ["list_item", "list_item"], "a list of two is two items, not one block")
    check("a pipe table is a table", blocks[4]["kind"], "table", "")
    check("and its text is the cells, not the pipes", blocks[4]["text"], "a b c d",
          "a ground truth reads `a b`; charging a tool for its own serialisation "
          "penalises the table it got right (codex, #1922)")
    check("and it becomes HTML TEDS can read",
          axes.teds(blocks[4]["html"], blocks[4]["html"]), 1.0, "")
    commented = blocks_of("# Title\n\n<!-- image -->\n\nA paragraph.\n")
    check("a comment is not a block", [b["kind"] for b in commented],
          ["heading", "paragraph"],
          "Docling writes `<!-- image -->` where a figure was; counting it "
          "charges the tool for characters the page does not hold")

    # THE ONE THE BASELINE STANDS ON. Plain text -- what we emit today -- must
    # read as paragraphs and nothing else. A reader that inferred a heading from
    # a short line would report a capability we do not ship.
    plain = blocks_of("Introduction\n\nThe first paragraph.\n\nThe second one.\n")
    check("plain text yields no headings",
          [b["kind"] for b in plain], ["paragraph", "paragraph", "paragraph"],
          "a reader that guesses headings measures itself, not the extractor")

    # --- the split and the seal ------------------------------------------
    pages = [{"source": "s", "name": f"p{i}.pdf", "document": f"d{i // 5}",
              "sha256": hashlib.sha256(str(i).encode()).hexdigest()}
             for i in range(100)]
    tuning, sealed = holdout_rule.split(pages)
    check("the split is twenty percent", len(sealed), 20,
          "#574's share, and a share that drifts is a holdout nobody can size")
    check("and the halves are the whole set", len(tuning) + len(sealed), 100, "")
    check("a document is never in both halves",
          {p["document"] for p in tuning} & {p["document"] for p in sealed}, set(),
          "pages of one document share a template; splitting them tunes on the "
          "holdout and nothing reports it")
    again, _ = holdout_rule.split(list(reversed(pages)))
    check("the draw does not depend on the order of the list",
          {p["name"] for p in again}, {p["name"] for p in tuning},
          "a seeded draw that reads the input order is not seeded")
    check("the seal is over what the manifest names, not the file",
          holdout_rule.digest_of(sealed),
          holdout_rule.digest_of([dict(p, extra="prose") for p in sealed]),
          "a prose edit to the header must not move the seal")
    check("and it moves when a page does",
          holdout_rule.digest_of(sealed) == holdout_rule.digest_of(sealed[:-1]), False,
          "a seal that survives a changed set seals nothing")

    boxed = [{"kind": "paragraph", "text": "second on the page", "bbox": [50, 300, 200, 20]},
             {"kind": "paragraph", "text": "first on the page", "bbox": [50, 100, 200, 20]}]
    check("geometry sorts a page top down",
          [b["text"] for b in geometric(boxed)],
          ["first on the page", "second on the page"],
          "a provenance check that reads the annotation order proves nothing")
    check("and the provenance says the two disagree",
          order_provenance([{"blocks": boxed}])["mean_agreement_with_geometry"] < 1.0,
          True, "this fixture is annotated bottom-up on purpose")
    check("a page with no boxes is not counted",
          order_provenance([{"blocks": [{"kind": "paragraph", "text": "x"}]}])["pages"],
          0, "a statistic computed over pages it could not read is a statistic "
             "about nothing")

    sampled = sample(pages, 40)
    check("a sample is about the size it asked for", len(sampled) <= 40, True,
          "the document that would cross the line is left out, never taken")
    check("and it is the same sample every time",
          {p["name"] for p in sample(list(reversed(pages)), 40)},
          {p["name"] for p in sampled},
          "a seeded sample that reads the input order is not seeded")
    check("and it is whole documents",
          len({p["document"] for p in sampled}) * 5, len(sampled),
          "half a document is a page that shares a template with a page nobody "
          "measured")

    # --- scoring one page end to end -------------------------------------
    page = {"name": "x.pdf", "document": "d", "category": "manuals", "blocks": [
        {"kind": "heading", "text": "Installing the pump"},
        {"kind": "paragraph", "text": "Read this section before you begin work."},
        {"kind": "list_item", "text": "Close the main valve"},
        {"kind": "list_item", "text": "Drain the header tank"},
        {"kind": "furniture", "text": "Page 4 of 12"},
    ]}
    perfect = score_page(page, blocks_of(
        "# Installing the pump\n\nRead this section before you begin work.\n\n"
        "- Close the main valve\n- Drain the header tank\n"))
    check("a perfect reading scores one on text", perfect["text"]["similarity"], 1.0, "")
    check("and one on headings", perfect["headings"]["f1"], 1.0, "")
    check("and one on lists", perfect["lists"]["f1"], 1.0, "")
    check("and one on reading order", perfect["order"]["score"], 1.0, "")
    check("and leaks no furniture", perfect["furniture"]["in_the_body"], 0, "")
    # What we ship today: the same text, with no structure in it. The text axis
    # must stay high and the two structure axes must be zero -- if this ever
    # reports otherwise, the baseline is flattering us and every ticket sized
    # against it is sized wrongly.
    flat = score_page(page, blocks_of(
        "Installing the pump\n\nRead this section before you begin work.\n\n"
        "Close the main valve\n\nDrain the header tank\n"))
    check("plain text still scores on text", flat["text"]["similarity"] > 0.95, True,
          "the characters are all there")
    check("but nothing on headings", flat["headings"]["f1"], 0.0,
          "a heading we did not mark is a heading we did not produce")
    check("and nothing on lists", flat["lists"]["f1"], 0.0, "")
    # And the counter that closes the hole in an F1 over only the pages that
    # have one: a tool that marks headings on a page the annotator gave none is
    # charged nothing by precision or recall, because neither is computed there.
    # Furniture in the body: what #571 takes out.
    leaky = score_page(page, blocks_of(
        "# Installing the pump\n\nPage 4 of 12\n\n"
        "Read this section before you begin work.\n\n"
        "- Close the main valve\n- Drain the header tank\n"))
    check("a running header in the body is counted",
          leaky["furniture"]["in_the_body"], 1,
          "#571 takes it out, and a baseline that cannot see it cannot size that")

    # --- v1 stays bit-identical (T3 review, #574) ---------------------------
    # Two pages and two readings, scored by the harness BEFORE the v2 column
    # existed; the expected values below are that run's, pinned. If a change
    # to the instrument moves any v1 number by a digit, this is red -- which
    # is the point: v1 is the reference and v2 stands beside it, never
    # instead of it.
    v1pin_pages = [
        {"name": "v1pin-a.pdf", "document": "v1pin.pdf", "category": "financial_reports",
         "blocks": [
             {"kind": "heading", "text": "PRINCIPAL ACTIVITIES", "bbox": [50, 80, 400, 100]},
             {"kind": "paragraph",
              "text": "The indi vidual proce dures of the company are set out below for all readers.",
              "bbox": [50, 120, 550, 200]},
             {"kind": "list_item", "text": "Close the main valve", "bbox": [70, 210, 500, 225]},
             {"kind": "list_item", "text": "Drain the header tank", "bbox": [70, 230, 500, 245]},
             {"kind": "furniture", "text": "Annual report 2003, page 4",
              "bbox": [50, 780, 550, 795]},
         ]},
        {"name": "v1pin-b.pdf", "document": "v1pin.pdf", "category": "financial_reports",
         "blocks": [
             {"kind": "heading", "text": "Thin king together", "bbox": [50, 80, 400, 100]},
             {"kind": "paragraph",
              "text": "An O V E R V I E W of next page results follows in this short section here.",
              "bbox": [50, 120, 550, 200]},
             {"kind": "table", "text": "Year Total 2024 12",
              "html": "<table><tr><th>Year</th><th>Total</th></tr>"
                      "<tr><td>2024</td><td>12</td></tr></table>",
              "bbox": [50, 210, 550, 320]},
         ]},
    ]
    v1pin_markdown = [
        "# PRINCIPAL ACTIVITIES\n\nThe individual procedures of the company are set out "
        "below for all readers.\n\n- Close the main valve\n- Drain the header tank\n",
        "## Thin king together\n\nAn overview of next page results follows in this short "
        "section here.\n\n| Year | Total |\n| --- | --- |\n| 2024 | 12 |\n",
    ]
    v1pin_want = json.loads(_V1PIN_EXPECTED)
    v1pin_rows = [score_page(p, blocks_of(m)) for p, m in zip(v1pin_pages, v1pin_markdown)]
    v1pin_keys = list(v1pin_want["rows"][0])
    check("every v1 cell of the pinned pages",
          [{k: r[k] for k in v1pin_keys} for r in v1pin_rows], v1pin_want["rows"],
          "the v2 column is extra cells; the v1 cells are the same bytes")
    check("and their v1 summary", summarise_tool(v1pin_rows), v1pin_want["summary"],
          "a mean that moved a digit is a reference that moved")

    # --- the v2 column -------------------------------------------------------
    lexicon = _v2_lexicon()
    english_v2 = functools.partial(axes.normalise_v2, lexicon=lexicon, english=True)
    broken_page = {"name": "v2.pdf", "document": "d", "blocks": [
        {"kind": "heading", "text": "PRINCIPAL A CTIVITIES"},
        {"kind": "paragraph",
         "text": "The indi vidual proce dures of the company are set out below for all readers."},
        {"kind": "list_item", "text": "Close the main valve"},
    ]}
    whole_md = ("# PRINCIPAL ACTIVITIES\n\nThe individual procedures of the company "
                "are set out below for all readers.\n\n- Close the main valve\n")
    broken_row = score_page(broken_page, blocks_of(whole_md))
    check("an English page applies v2", broken_row["v2_applied"], True,
          "nearly every token of its truth is a lexicon word")
    check("v1 still sees the breaks", broken_row["text"]["similarity"] < 1.0, True,
          "the reference does not join")
    check("v2 converges the broken text", broken_row["text_v2"]["similarity"], 1.0,
          "both sides read through the joining rule")
    check("and the broken words", broken_row["words_v2"]["f1"], 1.0, "")
    check("and reads the broken heading whole",
          broken_row["headings_v2"]["text_of_matched"], 1.0,
          "v1 matches it (the floor is low) but records the misreading")
    check("while v1 records the misreading",
          broken_row["headings"]["text_of_matched"] < 1.0, True, "")
    via_hook = score_page(broken_page, blocks_of(whole_md), norm=english_v2)
    check("score_page with the v2 normaliser IS the v2 column",
          [via_hook[a] for a in ("text", "words", "headings", "lists")],
          [broken_row[f"{a}_v2"] for a in ("text", "words", "headings", "lists")],
          "the cells beside v1 are the same call under the joining rule")
    check("and its tables and order stay v1",
          [via_hook[a] for a in ("tables", "teds", "order", "text_geometric")],
          [broken_row[a] for a in ("tables", "teds", "order", "text_geometric")],
          "matching, table cells and order have no v2")
    german_page = {"name": "de.pdf", "document": "d", "blocks": [
        {"kind": "heading", "text": "Zusammenfassung des Berichts"},
        {"kind": "paragraph",
         "text": "Der Hund bellt laut im Garten hinter dem Haus und die Katze "
                 "schlaeft tief und fest"},
    ]}
    german_md = ("# Zusammenfassung des Berichts\n\nDer Hund bellt laut im Garten hinter "
                 "dem Haus und die Katze schlaeft tief und fest\n")
    german_row = score_page(german_page, blocks_of(german_md))
    check("a German page does not apply v2", german_row["v2_applied"], False,
          "fewer than three fifths of its tokens are English words")
    check("and its v2 cells are its v1 cells",
          [german_row[f"{a}_v2"] for a in ("text", "words", "headings", "lists")],
          [german_row[a] for a in ("text", "words", "headings", "lists")],
          "a non-English page falls back to v1 rules inside the v2 column")
    check("the v2 summary counts the applied pages",
          summarise_tool_v2([broken_row, german_row])["v2_applied"], 1,
          "one of the two pages read through the joining rule")

    # --- the proxy compare prints v1 and v2 ----------------------------------
    import rig_like_proxy as proxy
    proxy_means = proxy.means({"v2.pdf": broken_row, "de.pdf": german_row})
    check("the proxy means carry v2 beside v1",
          proxy_means["text"][0] is not None and proxy_means["text_v2"][0] is not None,
          True, "v1 first, then the extra column, on the same rows")
    check("and the share of pages v2 applied to", proxy_means["v2_applied"], (0.5, 2),
          "one of the two pages read through the joining rule")
    check("and the note line under the table",
          "v1 is the reference" in proxy.summary({"v2.pdf": broken_row}), True, "")
    old_rows = {"v2.pdf": {k: v for k, v in broken_row.items()
                           if not k.endswith("_v2") and k != "v2_applied"}}
    check("a score file from before v2 still means",
          proxy.means(old_rows)["text_v2"], (None, 0),
          "old score files load, and v2 shows a dash where it was never measured")

    # The hole an F1 over only the annotated pages leaves open: a page with no
    # headings cannot report a precision, so marking every line one is free
    # there unless it is counted.
    bare = {"name": "y.pdf", "document": "d", "category": "manuals", "blocks": [
        {"kind": "paragraph", "text": "A page of running text with no heading "
                                      "anywhere on it at all."}]}
    invented = summarise_tool([score_page(bare, blocks_of(
        "# One\n\n# Two\n\nA page of running text with no heading anywhere on "
        "it at all.\n"))])
    check("a heading invented where none is annotated is counted",
          invented["headings_where_none_annotated"], 2,
          "an F1 computed only where there is ground truth is free to be bought "
          "on the pages where there is none")

    # THE QUESTION A SHELL CALLER ASKS. The remote round's preflight ran Docling
    # before the harness had read a verdict; it asks this now, so a tool off the
    # rig is not run once per round by the wrapper. (codex, #1922)
    check("a refused tool answers no", may_i_run("marker"), 1,
          "a check the harness performs and its wrapper walks past is not a check")
    check("and an allowed one answers yes", may_i_run("docling"), 0, "")

    # A rescore counts the pages it could not read, rather than adding them to a
    # count that already holds them: 199 answered and 2 unanswered of 200 is not
    # a possible run. (codex, #1922)
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        place = pathlib.Path(tmp)
        (place / "ground-truth.json").write_text(json.dumps([
            dict(page, name=f"p{i}.pdf") for i in range(3)]))
        (place / "pdfluent").mkdir()
        (place / "pdfluent" / "p0.pdf.md").write_text("# Installing the pump\n")
        again = rescore(place, {"run_id": "r", "set": "doclaynet", "tools": {
            "pdfluent": {"name": "PDFluent", "did_not_answer": 1}}},
            "doclaynet", {})
        counted = again["tools"]["pdfluent"]
        check("a rescore's pages and failures add up to the set",
              counted["pages"] + counted["did_not_answer"], 3,
              "the evidence is the whole population; adding the old count "
              "double-counts every page that already failed")
        check("a rescore carries the v2 column",
              counted.get("summary_v2", {}).get("v2_applied"), 1,
              "one page of evidence re-read, and it is English")

    # --- a markdown header row IS a header row ------------------------------
    pipe = blocks_of("| Year | Total |\n| --- | --- |\n| 2024 | 12 |\n")[0]
    truth_html = ("<table><tr><th>Year</th><th>Total</th></tr>"
                  "<tr><td>2024</td><td>12</td></tr></table>")
    check("a correct markdown table scores one against its grid",
          axes.teds(truth_html, pipe["html"]), 1.0,
          "dropping the delimiter row without remembering it made every "
          "header cell a td, so a table read perfectly out of markdown scored "
          "0.714 and the same table emitted as HTML scored 1.0 -- a bias "
          "according to which format an adapter happens to emit")
    check("and a table with no delimiter has no header",
          "<th>" in blocks_of("| a | b |\n| c | d |\n")[0]["html"], False,
          "the delimiter is what says which rows are the header; inventing "
          "one would be the same bias the other way round")
    check("the delimiter is not text either",
          _pipe_to_text(["| Year | Total |", "| 2024 | 12 |"]),
          "Year Total 2024 12", "")

    # --- the table set's two licence gates ---------------------------------
    # The documents of that set are licensed PER ARTICLE, so the rule is not
    # "is the set allowed" but "is this document allowed", and it runs before
    # the bytes are asked for.
    allowed_codes = set(
        dataset_entry("pubtables-1m").get("documents_licence_codes") or [])
    check("the register allows CC0 and CC BY and no more",
          sorted(allowed_codes), ["CC BY", "CC0"],
          "share-alike and no-derivatives raise a question this set does not "
          "need us to answer, and the rule here errs against us")
    check("an allowed article may be fetched",
          may_fetch({"licence": "CC BY", "retracted": False}, allowed_codes),
          None, "")
    check("a non-commercial article may not",
          may_fetch({"licence": "CC BY-NC", "retracted": False}, allowed_codes),
          "licence CC BY-NC",
          "we sell the engine this measurement sizes, which is the commercial "
          "use the clause names")
    check("nor a share-alike one",
          may_fetch({"licence": "CC BY-SA", "retracted": False}, allowed_codes),
          "licence CC BY-SA", "")
    check("an article with no code at all is refused, not assumed",
          may_fetch({"licence": None, "retracted": False}, allowed_codes),
          "licence no code",
          "silence is not permission, which is the reading every refusal in "
          "the register was decided under")
    check("a retracted article is refused",
          may_fetch({"licence": "CC0", "retracted": True}, allowed_codes),
          "retracted",
          "a withdrawn paper is not a document to publish a number about")
    check("and an archive that did not answer is not a pass",
          may_fetch(None, allowed_codes), "the archive did not answer", "")

    # --- the table set is a published size, not a flag ---------------------
    tables_entry = dataset_entry("pubtables-1m")
    check("the set's size is in the register", tables_entry["materialise_pages"],
          400,
          "the archive annotates 401,733 articles and the documents come one "
          "at a time from a second archive; a fetch with no ceiling is a "
          "mirror of somebody else's corpus rather than a round")
    check("and the walk that fills it is bounded too",
          tables_entry["walk_multiplier"] >= 2, True,
          "most articles give one or two pages and some are refused, so the "
          "walk is longer than the set -- and a round that cannot fill it has "
          "to say so rather than run until the machine is deleted under it")

    # --- the FinTabNet lane is pinned twice, and refuses when it is not ------
    # Two sources, two pins: the documents' `revision` and the annotations'
    # `annotations_revision`. Either one missing is a set fetched from a
    # moving branch, refused the way the PubTables lane refuses its one.
    # THE UNPROVEN FETCH FIRST (T6, #574): the round's shell loop runs under
    # `set -e`, so the set whose fetch has not yet run against its real source
    # goes first, where a refusal costs minutes rather than the round.
    remote = (REPO / "scripts/extraction/structured_bench_remote.sh").read_text()
    default_sets = re.search(r'^SETS="\$\{STRUCTURED_SETS:-([^}]*)\}"', remote, re.M)
    check("the round fetches FinTabNet before the sets it would otherwise throw away",
          (default_sets.group(1).split() or [""])[0] if default_sets else None, "fintabnet",
          "a FinTabNet refusal after DocLayNet and PubTables ends a round that "
          "has already spent two hours, and takes ParseBench with it")
    fintabnet_entry = dataset_entry("fintabnet")
    check("the FinTabNet set's size is in the register",
          fintabnet_entry["materialise_pages"], 300,
          "the archive holds 89,646 pages and the documents are read one at "
          "a time out of it; a fetch with no ceiling is a mirror of "
          "somebody else's corpus rather than a round")
    check("and the walk that fills it is bounded too",
          fintabnet_entry["walk_multiplier"] >= 2, True,
          "some pages hold no table above the floor and some are refused, "
          "so the walk is longer than the set -- and a round that cannot "
          "fill it has to say so")
    for without, pin in (({}, "`revision`"),
                         ({"revision": "abc"}, "`annotations_revision`")):
        try:
            fetch_fintabnet(dict(without, name="FinTabNet fixture"),
                            pathlib.Path("/tmp/fintabnet-pinning"), None)
            failures.append(f"fetch_fintabnet without {pin} fetched -- a set "
                            "from a moving branch cannot be sealed, and a "
                            "refusal nobody runs is not a refusal")
        except SystemExit as exc:
            if pin not in str(exc):
                failures.append(f"fetch_fintabnet without {pin} refused, but "
                                f"not about the pin: {exc}")
    # And a pinned entry still refuses off the round machine: the pinning
    # stands before the split refusal so that it can be tested here, but the
    # refusal still stands behind it. Judged as the Mac on every platform,
    # the way `test_rig_data_stays_on_the_rig.py` judges it.
    was_as_mac = os.environ.get("RIG_GUARD_AS_MAC")
    os.environ["RIG_GUARD_AS_MAC"] = "1"
    try:
        try:
            fetch_fintabnet({"name": "FinTabNet fixture", "revision": "abc",
                             "annotations_revision": "def"},
                            pathlib.Path("/tmp/fintabnet-pinning"), None)
            failures.append("fetch_fintabnet with both pins ran off the "
                            "round machine -- the rig's pages are fetched on "
                            "the round machine only")
        except SystemExit as exc:
            if "round machine" not in str(exc):
                failures.append("fetch_fintabnet with both pins refused, but "
                                f"not about the round machine: {exc}")
    finally:
        if was_as_mac is None:
            os.environ.pop("RIG_GUARD_AS_MAC", None)
        else:
            os.environ["RIG_GUARD_AS_MAC"] = was_as_mac

    # --- the lane can seal: --draw on a synthetic FinTabNet set (T6, #574) ---
    # Round 5 fetched 300 pages and died at --draw: `manifest_for("fintabnet")`
    # knew no manifest, and nothing off the rig had ever drawn one. The checks
    # above cover the pins and the round-machine refusal but never the seal,
    # so the lane went red on a paid machine instead of here. The draw below
    # writes to a scratch directory, never to the repository: a self-test that
    # sealed the real path would hand the guard a manifest with no ledger row.
    lane_pages = [{"source": fintabnet_archive.SOURCE,
                   "name": f"BA_202{i}_page_1.pdf", "document": f"BA_202{i}",
                   "page_no": 0, "category": fintabnet_archive.CATEGORY,
                   "licence": fintabnet_archive.DOCUMENT_LICENCE,
                   "sha256": hashlib.sha256(f"lane-page{i}".encode()).hexdigest()}
                  for i in range(10)]
    with tempfile.TemporaryDirectory(prefix="structured-first-draw-") as scratch:
        first = dict(holdout_rule.FIRST_DRAW)
        holdout_rule.FIRST_DRAW["fintabnet"] = (pathlib.Path(scratch)
                                                / "FINTABNET.json")
        # The draw below must land in the scratch directory. If the routing
        # regressed to MANIFESTS -- a set in both tables -- drawing here
        # would seal the repository instead of the fixture.
        routed = holdout_rule.draw_path_for("fintabnet")
        try:
            if routed.parent != pathlib.Path(scratch):
                failures.append(
                    f"draw_path_for(fintabnet) is {routed}, outside the "
                    "scratch directory -- refusing to draw into the "
                    "repository")
                lane_manifest = None
            else:
                try:
                    lane_manifest = draw("fintabnet", fintabnet_entry,
                                         lane_pages)
                except SystemExit as exc:
                    failures.append(
                        "draw(\"fintabnet\", ...) refused the synthetic set: "
                        f"{exc} -- a lane that cannot seal must go red here, "
                        "before a round buys a machine")
                    lane_manifest = None
            if lane_manifest is not None:
                lane_holdout = [p for p in lane_manifest["pages"]
                                if p["split"] == "holdout"]
                check("the FinTabNet lane seals under its own seed",
                      lane_manifest["seed"],
                      holdout_rule.seed_for("fintabnet"),
                      "a draw under another set's seed ties this split's order "
                      "to that set's")
                check("and the seal is the digest of the sealed pages",
                      lane_manifest["seal"]["holdout"],
                      holdout_rule.digest_of(lane_holdout),
                      "a seal over anything else seals nothing")
                check("and the sealed fifth is neither empty nor the whole set",
                      0 < len(lane_holdout) < len(lane_pages), True,
                      "an empty holdout would pass every check below for the "
                      "wrong reason")
                check("and a redraw of the same pages keeps the draw date",
                      _drawn_on("fintabnet",
                               lane_manifest["seal"]["holdout"]),
                      lane_manifest["drawn"],
                      "the day it was drawn is not the day it was redrawn")
        finally:
            holdout_rule.FIRST_DRAW.clear()
            holdout_rule.FIRST_DRAW.update(first)

    # --- the page an annotation says it is on IS that page ------------------
    grid = ("<table><tr><td>Prevalence</td><td>Incidence</td></tr>"
            "<tr><td>0.44</td><td>0.19</td></tr></table>")
    check("a page whose text holds the cells is aligned",
          _aligned([{"html": grid}],
                   axes.normalise("Table 2 Prevalence Incidence 0.44 0.19").lower()),
          True, "")
    check("and the next page over is not",
          _aligned([{"html": grid}],
                   axes.normalise("Discussion. The results above suggest").lower()),
          False,
          "an off-by-one in the page index would grade every tool against the "
          "wrong page while every digest in the manifest stayed honest")
    check("a table of numbers alone cannot align a page",
          _aligned([{"html": "<table><tr><td>12</td><td>7</td></tr></table>"}], "12 7"),
          False,
          "a two-character cell is on every page of a table of numbers, so it "
          "is no evidence about which one")

    # --- a set that annotates one class of block, and no more --------------
    # DocLayNet draws every block on a page; a table corpus draws tables. An
    # axis a set does not annotate must say so rather than score zero, or the
    # zero is a statement about the corpus (#575).
    # A header row, because that is what `is_column_header` gives and what a
    # markdown delimiter says. A ground truth with no header against markdown
    # that declares one is two different tables, and rightly scores as such.
    table_html = ("<table><tr><th>a</th><th>b</th></tr>"
                  "<tr><td>c</td><td>d</td></tr></table>")
    tabled = {"name": "t.pdf", "document": "PMC1.pdf", "category": "articles",
              "annotates": ["table"], "blocks": [
                  {"kind": "table", "text": "a b c d", "html": table_html}]}
    scored = score_page(tabled, blocks_of("| a | b |\n| --- | --- |\n| c | d |\n"))
    check("a table set scores TEDS", scored["teds"]["teds"], 1.0,
          "the ground truth carries the cells, which is the whole reason a "
          "second set was added")
    check("and does not score the running text",
          scored["text"]["scored"], False,
          "comparing a page of prose with the words of one table reports a "
          "text axis that is a statement about the corpus")
    check("and does not score headings", scored["headings"]["scored"], False, "")
    check("and does not score reading order", scored["order"]["scored"], False,
          "an order over one class of block is not the page's order")
    check("a set without cells still refuses TEDS",
          score_page(page, blocks_of("| a |\n| --- |\n| b |\n"))["teds"]["scored"],
          False,
          "a found-it figure in a TEDS column is the misreading the pair of "
          "axes exists to stop")

    # --- the pages, back in their documents ---------------------------------
    two_pages = [
        {"name": "a.pdf", "document": "d.pdf", "page_no": 0, "path": "/nowhere/a.pdf",
         "blocks": [{"kind": "furniture", "text": "Annual report 2024"}]},
        {"name": "b.pdf", "document": "d.pdf", "page_no": 1, "path": "/nowhere/b.pdf",
         "blocks": [{"kind": "furniture", "text": "Annual report 2024"}]},
    ]
    check("the regrouping sees one document of two pages",
          [(d, len(rows)) for d, rows in _by_document(two_pages).items()],
          [("d.pdf", 2)],
          "the split and the sample are both by document, so a sampled "
          "document's pages are all here and a sealed one's cannot be")
    lonely = furniture_in_documents("pdfluent", {}, [two_pages[0]], {},
                                    pathlib.Path("/tmp"))
    check("a document of one page is not merged", "why" in lonely, True,
          "merging it would be the same file under another name, and #571's "
          "rule cannot fire on a single page either")

    # --- the run file says both counts, and the tail, where a reader looks ---
    # --- the table ---------------------------------------------------------
    run = {"run_id": "structured-doclaynet-x", "set": "doclaynet",
           "set_version": "v", "set_licence": "CDLA-Permissive-1.0",
           "pages_measured": 1, "half": "tuning",
           "machine": {"cpus": 8, "platform": "linux"},
           "refused": [{"tool": "Marker", "why": "weights licence"}],
           "tools": {"pdfluent": {
               "name": "PDFluent", "version": "abc", "ms_per_page": 12,
               "summary": dict({a: {"mean": 0.5, "pages": 7}
                                for a, _k, _l in AXES},
                               furniture_in_the_body=197),
               "furniture_in_the_document": {
                   "documents": 13, "pages": 200, "annotated": 320,
                   "in_the_body": 4, "single_page_in_the_body": 197},
               "tail": {"headings": [{"v": 0.0, "name": "abcdef1234567890.pdf",
                                      "document": "ASX_MRM_2000.pdf"}]}}}}
    rendered = table_of(run)

    # --- the readable subset, beside the whole set (COS, 24-09-2026) --------
    def page(name: str) -> dict:
        return {"name": name, "document": "d.pdf",
                "blocks": [{"kind": "paragraph", "text": PROSE}]}
    PROSE = ("The pump is installed first, then the pipes are connected and the valves are checked for leaks before the system is filled with water and tested at the working pressure.")
    read = [{"kind": "paragraph", "text": PROSE}]
    scan_run = {"run_id": "structured-doclaynet-y", "set": "doclaynet", "set_version": "v",
                "set_licence": "CDLA-Permissive-1.0", "pages_measured": 2, "half": "tuning",
                "machine": {"cpus": 8, "platform": "linux"}, "refused": [],
                "tools": {t: {"name": t, "version": "1", "ms_per_page": 1,
                              "documents": [score_page(page("text.pdf"), read),
                                            score_page(page("scan.pdf"), [])]}
                          for t in ("ours", "theirs")}}
    for t in scan_run["tools"].values():
        t["summary"] = summarise_tool(t["documents"])
    ocr_run = json.loads(json.dumps(scan_run))
    ocr_run["tools"]["ocr"] = {"name": "ocr", "version": "1", "ms_per_page": 1,
                               "dimensions": {"ocr": "ON, inside hi_res"},
                               "documents": [score_page(page("text.pdf"), read),
                                             score_page(page("scan.pdf"), read)]}
    check("a column that reads pixels does not make a scan readable",
          readable_pages(ocr_run), {"text.pdf"},
          "with Unstructured's Tesseract in the round every scan 'gave text' (26-09)")
    two_docs = [dict(score_page(page(f"a{i}.pdf"), read), document="big.pdf") for i in range(3)]
    two_docs.append(dict(score_page(page("b.pdf"), []), document="small.pdf"))
    macro = macro_by_document(two_docs)["text"]
    check("the per-document mean weighs each document once",
          (round(macro["mean"], 3), macro["documents"]), (0.5, 2),
          "121 of 200 pages from two reports: the page mean is mostly theirs (COS, 27-09)")
    check("and the table shows it for every tool",
          "Per document" in "\n".join(macro_lines({"tools": {"t": {"name": "T", "documents": two_docs}}})),
          True, "a figure the run holds and the table leaves out is one nobody reads")
    refs = reference_lines("cap", [("text", "text")],
                           {OURS: {"text": 0.6}, "pymupdf4llm": {"text": 0.5},
                            "docling": {"text": 0.7}, "mineru": {"text": 0.8}},
                           {"mineru": "MinerU"})
    check("each axis puts us against pymupdf4llm, then Docling, then the best other",
          refs[-3], "| text | 0.600 | +0.100 (0.500) | -0.100 (0.700) | MinerU 0.800 (-0.200) |",
          "the owner's references come first, beside our score (COS, 28-09)")
    check("and a run without either reference prints no such table",
          reference_lines("cap", [("text", "text")], {OURS: {"text": 0.6}, "mineru": {"text": 0.8}}, {}),
          [], "a table of dashes answers nothing")
    headline = table_of(dict(scan_run, tools={"t": {**scan_run["tools"][next(iter(scan_run["tools"]))],
                                                    "documents": two_docs}}))
    check("the per-document reading is the headline, and the page mean stands under it",
          ("Per document -- the headline" in headline and "Per page -- every page" in headline
           and headline.index("Per document -- the headline") < headline.index("Per page -- every page")),
          True, "one document is 38 per cent of the rig's heading pages (COS, 29-09)")
    check("a page no tool reads is out of the readable subset",
          readable_pages(scan_run), {"text.pdf"},
          "a scan with OCR off is 0.000 for every column, a fact about the set")
    scan_table = table_of(scan_run)
    check("the table reports the readable subset beside the whole set",
          "Readable subset -- the 1 of 2 pages" in scan_table
          and scan_table.index("Readable subset") > scan_table.index("| tool |"), True,
          "leaving it out hides that a mean over the whole set carries the scans")
    check("and scores it on the readable pages only",
          "| ours | 1.000 ⟨1⟩" in scan_table, True,
          "the subset's text axis is the readable page's, not the average with the scan")

    check("the table carries both furniture counts",
          "197 on single pages" in rendered and "4 of 320" in rendered, True,
          "the figure that is comparable with an earlier round and the figure "
          "that measures #571's rule are not the same number")
    check("a set that draws no furniture reports no furniture count",
          "not measured" in "\n".join(furniture_lines({"tools": {"x": {
              "name": "PDFluent",
              "summary": {"furniture_in_the_body": 0},
              "furniture_in_the_document": {
                  "annotated_by_the_set": False,
                  "why": "this set does not annotate running headers"}}}})),
          True,
          "a zero there reads as `this column leaks nothing` and means `the "
          "set drew nothing to leak`")
    check("and names the worst pages of an axis",
          "ASX_MRM_2000.pdf" in rendered, True,
          "#576-#579 read the table, and a tail that lives only in a 300 kB "
          "JSON is a tail that gets re-derived by hand every round")
    check("the table names the refusal", "Marker" in rendered, True,
          "a column a reader expected has to say why it is not there")
    check("and the licence of the set", "CDLA-Permissive-1.0" in rendered, True,
          "a number's licence travels with it or it does not travel")
    check("and every mean carries its denominator", "0.500 \u27e87\u27e9" in rendered,
          True, "two means in one column may be over different pages; the run "
                "that found 17 against 157 is why the count is printed")

    # --- the table prints v1 and v2, and old runs still load -----------------
    # `run` above has no v2: it is shaped like every run file committed before
    # this column existed.
    check("a run without v2 still renders its v2 table as dashes",
          "Matching v2" in rendered and "| PDFluent abc | 0.500 | \u2014" in rendered, True,
          "old JSONs load, and v2 shows it was never measured with a dash")
    check("and the table says what v2 is",
          "v2 = dictionary-gated joining of in-word breaks" in rendered
          and "v1 is the reference" in rendered, True,
          "the note line under the table, on every report")
    run_v2 = json.loads(json.dumps(run))
    run_v2["tools"]["pdfluent"]["summary_v2"] = {
        a: {"mean": 0.6, "pages": 7} for a in
        ("text_v2", "words_v2", "headings_v2", "lists_v2")}
    run_v2["tools"]["pdfluent"]["summary_v2"].update(v2_applied=6, pages=7)
    rendered_v2 = table_of(run_v2)
    check("a run with v2 prints v1 first and v2 beside it",
          "| PDFluent abc | 0.500 | 0.600" in rendered_v2, True,
          "v1 first in every axis pair, then the extra column")
    check("and the share of pages v2 applied to", "6/7" in rendered_v2, True,
          "the flag is per page; the table carries the count")

    # --- every page the measure was handed is a row of it --------------------
    #
    # Written after a `set_in` that went into the BATCH branch by mistake and
    # left `rows.append` outside its loop: the batch then scored one page of
    # two hundred and the round's own table said 1 where it should say 200.
    # Nothing looked, because the two loops differ by four lines.
    import tempfile as _tempfile

    two_pages = [
        {"name": "p0.pdf", "document": "d.pdf", "category": "manuals", "path": "/dev/null",
         "blocks": [{"kind": "heading", "text": "Installing the pump"},
                    {"kind": "paragraph", "text": "Turn the valve clockwise until it stops."}]},
        {"name": "p1.pdf", "document": "d.pdf", "category": "manuals", "path": "/dev/null",
         "blocks": [{"kind": "heading", "text": "Removing the pump"},
                    {"kind": "paragraph", "text": "Turn the valve the other way until it stops."}]},
    ]
    batched = globals()["measure_batch"]
    globals()["measure_batch"] = lambda tool, pages, binaries, text_dir, probe=None: (
        {page["name"]: "# Installing the pump\n\nTurn the valve clockwise until it stops.\n"
         for page in pages}, 400)
    try:
        measured = measure("doclaynet", {}, two_pages,
                           {"docling": {"name": "Docling"}}, [], {}, "r", None)
    finally:
        globals()["measure_batch"] = batched
    check("the batch scores every page it was handed",
          measured["tools"]["docling"]["pages"], 2,
          "a batch that appends outside its loop reports one page of two "
          "hundred, and the mean it publishes is that one page's")
    check("and a batched column says nothing about type",
          any("set_in" in row for row in measured["tools"]["docling"]["documents"]),
          False, "what another tool's document model was set in is its business")

    # --- competitors are measured once (owner, 24-09-2026) -----------------
    # The first run stores the competitor's output; a second run with the same
    # version, adapter and pages scores the stored output and runs NOTHING of
    # the competitor -- red if a round runs one while its cache exists.
    real_cache = globals()["COMPETITORS"]
    with _tempfile.TemporaryDirectory() as cache_tmp:
        globals()["COMPETITORS"] = pathlib.Path(cache_tmp)

        def ok_batch(tool, pages, binaries, text_dir, probe=None):
            if probe is not None:
                probe["outcome"] = "ok"
            return ({page["name"]: "# Installing the pump\n\nTurn the valve clockwise until it stops.\n"
                     for page in pages}, 400)

        def no_batch(*_a, **_k):
            raise AssertionError("a competitor ran although its cache exists")

        docling_row = {"name": "Docling", "version_pin": "2.126.0"}
        try:
            globals()["measure_batch"] = ok_batch
            first = measure("doclaynet", {}, two_pages, {"docling": docling_row}, [], {}, "r1", None)
            globals()["measure_batch"] = no_batch
            try:
                second = measure("doclaynet", {}, two_pages, {"docling": docling_row}, [], {}, "r2", None)
            except AssertionError as exc:
                second = {"tools": {"docling": {"error": str(exc)}}}
            globals()["measure_batch"] = ok_batch
            moved = measure("doclaynet", {}, two_pages,
                            {"docling": dict(docling_row, version_pin="2.127.0")}, [], {}, "r3", None)
        finally:
            globals()["measure_batch"] = batched
            globals()["COMPETITORS"] = real_cache
    check("a competitor's first run is stored in the cache",
          bool(first["tools"]["docling"].get("cached_to")), True,
          "a competitor measured once is only once if its output is kept")
    check("a round with that cache runs no competitor process",
          second["tools"]["docling"].get("error"), None,
          "the owner's rule of 24-09: a competitor runs again only when its version or the "
          "pages change")
    check("and scores the stored output as a fresh run would",
          second["tools"]["docling"].get("summary"), first["tools"]["docling"]["summary"],
          "the scorer is today's; only the tool's output is from the cache")
    check("and says which cache file it read",
          bool((second["tools"]["docling"].get("from_cache") or {}).get("file")), True,
          "a column from the cache has to say so beside its numbers")
    # --- our clock in one batch (#623) -------------------------------------
    with _tempfile.TemporaryDirectory() as bt:
        btp = pathlib.Path(bt)
        fake = btp / "ours"
        fake.write_text(
            "#!/usr/bin/env python3\n"
            "import sys, json, pathlib\n"
            "a = sys.argv[1:]\n"
            "lst, out, jobs = a[a.index('--batch')+1], pathlib.Path(a[a.index('--out-dir')+1]), int(a[a.index('--jobs')+1])\n"
            "out.mkdir(parents=True, exist_ok=True)\n"
            "paths = [l for l in open(lst).read().split() if l]\n"
            "rows = []\n"
            "for p in paths:\n"
            "    n = pathlib.Path(p).name\n"
            "    (out / (n + '.md')).write_text('# ' + n + '\\n')\n"
            "    rows.append(n + '\\t3\\tok')\n"
            "(out / 'timings.tsv').write_text('\\n'.join(rows) + '\\n')\n"
            "(out / 'batch.json').write_text(json.dumps({'pages': len(paths), 'wall_ms': 6 * len(paths), 'jobs': jobs}))\n")
        fake.chmod(0o755)
        bpages = [{"name": f"n{i}.pdf", "path": str(btp / f"f{i}.pdf")} for i in range(2)]
        clock = batch_clock(bpages, {OURS: str(fake)},
                            {"n0.pdf": "# f0.pdf\n", "n1.pdf": "something else"}, btp / "s")
    check("our column is also timed in one batch, serially and in parallel",
          sorted((clock or {}).keys()), ["parallel", "serial"],
          "on 26-09 our clock was per process and theirs per batch: two measurements in one table")
    check("the batch's clock is the whole process, not the child's own timer",
          (clock or {}).get("serial", {}).get("wall_ms_inside") == 12
          and (clock or {}).get("serial", {}).get("ms_per_page")
          == round((clock or {}).get("serial", {}).get("process_ms", -1) / 2, 1), True,
          "the child's timer starts after start-up and stops before the files are written")
    check("and the batch says how much of its output matches the per-page pass",
          (clock or {}).get("serial", {}).get("same_markdown"), 1,
          "a faster clock over different output is a clock of something else")
    check("and the table shows it beside the per-process clock",
          "one batch, start-up amortised" in "\n".join(
              batch_clock_lines({"tools": {OURS: {"batch_clock": clock}}})), True,
          "a figure the run file holds and the table leaves out is one nobody reads")
    check("inline emphasis is not text, for any column",
          [b["text"] for b in blocks_of(
              "Pay the **net amount** within *thirty days*; H<sub>2</sub>O, m<sup>2</sup>, "
              "***both***, ~~gone~~ and a*b*c and 2 * 3 * 4 stay.")],
          ["Pay the net amount within thirty days; H2O, m2, both, gone and a*b*c and "
           "2 * 3 * 4 stay."],
          "a run's weight is how it was set, not what it says (#628)")
    check("a code fence's marker lines are not text, for any column",
          [b["text"] for b in blocks_of("```text\n\n```")], [],
          "LiteParse's empty fence on a scan read as text and put the scan in the readable subset")
    check("a cached column reads its declared dimensions from today's register",
          declared_now("mineru", {"ocr": "off: stale", "ms_p50": 5}).get("ocr")
          == load_register(EXTRACTORS, "extractors")[0]["mineru"].get("ocr")
          and declared_now("mineru", {"ms_p50": 5})["ms_p50"] == 5, True,
          "a correction to the register must reach the cached columns too")
    check("an inline image is not text, for any column",
          [b["text"] for b in blocks_of("Intro\n\n![](data:image/jpeg;base64,/9j/AAAA)\n\nEnd")],
          ["Intro", "End"],
          "MinerU's base64 figures read as paragraphs charged it characters the page does not hold")
    with _tempfile.TemporaryDirectory() as strip_tmp:
        globals()["COMPETITORS"] = pathlib.Path(strip_tmp)
        try:
            stored_path = cache_store({"set": "s", "tool": "t"},
                                      {"markdown": {"p.pdf": "A\n\n![](data:image/png;base64," + "Q" * 5000 + ")"}})
            import gzip as _gzip
            kept = json.loads(_gzip.decompress(stored_path.read_bytes()))["markdown"]["p.pdf"]
        finally:
            globals()["COMPETITORS"] = real_cache
    check("the cache keeps an image as a marker, not its payload",
          kept, "A\n\n![](data:stripped)", "the payload is what took MinerU's entry to 8.8 MB")
    check("a new version pin is a cache miss, measured again",
          bool(moved["tools"]["docling"].get("cached_to")), True,
          "two versions are two columns")
    remote = (REPO / "scripts" / "extraction" / "structured_bench_remote.sh").read_text()
    check("the rig measures under the interpreter that holds the tokenizer",
          sorted({line.strip().split()[0] for line in remote.splitlines()
                  if "scripts/ci/loop/structured_bench.py --set \"$SET\" --work" in line}),
          ['"$BENCH_PY"'],
          "under the system python every column's tokens/page was null (codex, #1994)")
    check("the round keeps our column's feature dump beside its markdown",
          '|| echo "!!! dump_our_features rc=$? (informational, round continues)"'
          '\n  emit_evidence "$SET" "$SET"' in remote
          and "--bin feature_dump" in remote, True,
          "a learned lane diagnoses a model from a round's evidence, not from a second machine")
    check("the round fetches the ensemble's second weights by its own signed URL",
          "TEACHER_EGRET_WEIGHTS_URL" in remote and "model-egret" in remote, True,
          "the Egret tgz arrives like the Heron ones: a URL that is never printed, into its own directory")
    check("and refuses either ensemble file on the wrong sha256",
          "teacher_ens $t_part weights sha256 is $t_got" in remote
          and "its version_pin is not two hashes" in remote, True,
          "an ensemble from half-right weights is a number about a model nobody trained")
    check("and preflights the ensemble on one page",
          "for t_tool in teacher teacher_ep03 teacher_ens" in remote, True,
          "a column that never answers must say so on page one, not after the batch")
    best_reqs = (REPO / "scripts" / "extraction" / "adapters" / "requirements-best.txt").read_text()
    best_tf = [line.split("==", 1)[1].strip() for line in best_reqs.splitlines()
               if line.strip().startswith("transformers==")]
    check("and pins transformers for Egret's D-FINE",
          remote.count("pip -q install -r scripts/extraction/adapters/requirements-best.txt") == 3
          and len(best_tf) == 1 and tuple(int(x) for x in best_tf[0].split(".")[:2]) >= (4, 52),
          True,
          "DFineForObjectDetection needs >= 4.52; both venv installs and the reinstall take "
          "requirements-best.txt, which pins it (#641, R-2)")
    check("and reinstalls transformers when a reused venv cannot import D-FINE",
          "from transformers import DFineForObjectDetection" in remote, True,
          "a venv from an older round must heal itself, not fail the round on import")
    check("the round kit finds the balanced tool by name",
          "balanced" in remote and "*,balanced,*" in remote, True,
          "the kit greps for the tool name; the gate runs only when STRUCTURED_TOOLS names it")
    check("and the header documents the secret it needs",
          "BALANCED_MODEL_URL" in "\n".join(remote.splitlines()[:80]), True,
          "a round that needs a signed URL says so where the COS looks before starting it")
    check("and it builds the CLI with the layout tier",
          "cargo build --release -p pdfluent-cli --features ort-layout" in remote, True,
          "balanced runs through `pdfluent extract --quality balanced`, built from this tree")
    check("and it fetches the model by its signed URL and refuses the wrong sha256",
          "BALANCED_MODEL_URL" in remote
          and "balanced model sha256 is $b_got" in remote
          and "its version_pin is not four pins" in remote, True,
          "the weights arrive like the teachers': a URL that is never printed, verified before use")
    check("and it lays out --model-dir as the tier reads it",
          "layout-heron-ep03/heron_ep03.onnx" in remote and "ORT_DYLIB_PATH" in remote, True,
          "the model file where `resolve_model_file` looks, the library where `ort` loads it")
    check("and it follows redirects on the ORT fetch",
          'curl -fsSL --retry 3 --retry-delay 5 "$b_ort_url"' in remote, True,
          "the GitHub release URL answers 302: without -L curl banks the empty redirect body with exit 0")
    ort_refuse_at = remote.find("balanced ORT archive sha256 is $b_ort_got")
    ort_untar_at = remote.find("tar xzf /tmp/balanced-ort.tgz")
    check("and it refuses the ORT archive on the wrong sha256, before tar",
          ort_refuse_at != -1 and ort_untar_at != -1 and ort_refuse_at < ort_untar_at
          and '"$b_ort_got" != "$b_want_ort_sha"' in remote
          and "balanced-ort-sha256" in remote, True,
          "the official linux-x64 archive at the pinned URL, verified against the register's pin before it is unpacked")
    check("and it preflights the balanced column on one page",
          "does the #641 balanced answer at all" in remote, True,
          "a column that never answers must say so on page one, not after the batch")
    check("and it writes the balanced timing beside the evidence",
          "balanced-timings.json" in remote and "intra_threads" in remote, True,
          "per-page wall time at 4 intra threads plus the 50-page batch median and p95, load reported separately")
    check("the round kit finds the balanced_tatr tool by name",
          "balanced_tatr" in remote and "*,balanced_tatr,*" in remote, True,
          "the kit greps for the tool name; the gate runs only when STRUCTURED_TOOLS names it")
    check("and the header documents the secret it needs",
          "TATR_MODEL_URL" in "\n".join(remote.splitlines()[:80]), True,
          "a round that needs a signed URL says so where the COS looks before starting it")
    check("and it builds the CLI once, with both tiers, for both columns",
          "cargo build --release -p pdfluent-cli --features ort-layout,ort-tables" in remote, True,
          "one binary off one branch, so only TATR on/off differs between the columns")
    check("and both columns run from that one binary",
          "BALANCED_TATR_BIN=\"$BALANCED_BIN\"" in remote
          and "cmp -s \"$BALANCED_TATR_BIN\" \"$BALANCED_BIN\"" in remote, True,
          "two builds would measure two binaries; the round asserts the one file")
    check("and it fetches TATR by its signed URL and refuses the wrong sha256",
          "TATR_MODEL_URL" in remote
          and "tatr model sha256 is $t_got" in remote
          and "its version_pin is not five pins" in remote, True,
          "the weights arrive like the layout model's: a URL that is never printed, verified before use")
    check("and it lays out --table-model-dir as the tier reads it",
          "tatr-v1.1-all/tatr-v1.1-all.onnx" in remote and "TATR_MODEL_DIR" in remote, True,
          "the model file where the TATR loader looks, beside the layout model in the one cache")
    check("and the meta says whether balanced_tatr is on, and why",
          "balanced_tatr: ON" in remote and "balanced_tatr: OFF" in remote, True,
          "a round without TATR_MODEL_URL runs the balanced column alone, and says so")
    check("and it preflights the balanced_tatr column on one page",
          "does the #636 balanced_tatr answer at all" in remote, True,
          "a column that never answers must say so on page one, not after the batch")
    check("and the timing covers the shared binary for either column",
          "*,balanced,*|*,balanced_tatr,*)" in remote, True,
          "one binary, one timing: the per-page clock and the batch run whichever column asked")
    # THE POST-AC ROUND's PREFLIGHTS FAILED FROM THE SECOND SET (08-10-2026):
    # the CLI refuses to overwrite `-o` without `--force`, so a fixed
    # /tmp/balanced-preflight.md answers BAD_USAGE on every set after the
    # first -- and the same refusal made 49 of the timing's 50 laps crashes.
    check("the balanced preflight writes a per-set path, not one file for the round",
          ("balanced-preflight-$SET.md" in remote
           and "-o /tmp/balanced-preflight.md" not in remote), True,
          "a fixed path fails from the second set with BAD_USAGE")
    check("and the balanced_tatr preflight too",
          ("balanced-tatr-preflight-$SET.md" in remote
           and "-o /tmp/balanced-tatr-preflight.md" not in remote), True,
          "both columns preflight every set, so both paths carry the set")
    check("and no balanced `-o` in the round can refuse on an existing file",
          remote.count("--force") >= 4, True,
          "both preflights plus the timing's per-page and batch loops overwrite")
    # THE ROUND LOST ITS INSTANCE AT THE PUBTABLES MEASUREMENT (08-10-2026):
    # 40 polls failed with the heavy columns on a 16 GB box. Each set now
    # budgets memory from MemAvailable before it measures anything.
    check("each set budgets memory from MemAvailable before it measures",
          ("memory_budget" in remote and "MemAvailable" in remote), True,
          "n x peak over half of MemAvailable, at the start of each set")
    check("and the budget logs the chosen n, with a floor of 1",
          ("memory budget for $set" in remote
           and "|| n_balanced=1" in remote and "|| n_tatr=1" in remote), True,
          "a reader of the log must see what n the set ran under, and 1 is the floor")
    loop_start = remote.find("for SET in $SETS; do")
    loop_end = remote.find('if [ "$PARSEBENCH_PAGES" -gt 0 ]', loop_start)
    loop = remote[loop_start:loop_end] if loop_start != -1 and loop_end != -1 else ""
    check("and the budget runs at the start of each set",
          (loop.find('memory_budget "$SET"') != -1
           and loop.find('memory_budget "$SET"') < loop.find("=== the measurement")), True,
          "the budget decides the set's parallelism, so it runs before the measurement")
    check("each set's evidence leaves before the next set starts",
          'emit_evidence "$SET" "$SET"' in loop, True,
          "the instance is deleted with its disk; what a set measured is out "
          "through the log before the next set starts")
    check("and the OOM evidence leaves after each set",
          ("oom_evidence" in remote and "free -m" in remote
           and "killed process|out of memory" in remote
           and "journalctl -k --since '-2h'" in remote), True,
          "free, the kernel's killed-process lines and the journal: what the "
          "kernel saw, for the round that loses its instance")
    check("and in the EXIT trap",
          "trap 'oom_evidence || true' EXIT" in remote, True,
          "a round that dies mid-set still says what the kernel saw")
    check("and the OOM evidence step is guarded, never fatal",
          'oom_evidence \\\n      || echo "!!! oom_evidence rc=$?' in remote
          and "return 0" in remote, True,
          "an informational step that fails must print its rc and let the round go on")
    # THE POST-AC ROUND DIED OF SIGPIPE (08-10-2026, exit 141): `balanced_timing`
    # listed its pages through a find-sort-head pipe under `set -euo pipefail`,
    # `head` closed the pipe, `sort` died, and pipefail plus `set -e` ended the
    # round 52 minutes in. The fix reads the whole stream, and the checks below
    # hold the shape: no pipe into a reader that closes early, anywhere.
    prone = [(n, line.strip()) for n, line in enumerate(remote.splitlines(), 1)
             if not line.strip().startswith("#")
             and re.search(r"\|\s*head\b|\|\s*grep\s+(-[a-zA-Z]*q|-m\b)", line)]
    check("the remote round never pipes into a reader that closes early",
          prone, [],
          "a pipe into `head` (or `grep -q`, or `grep -m`) kills its writer "
          "with SIGPIPE, and under pipefail that ends the round -- `sed` and "
          "`awk` read the whole stream instead")
    for guarded, name in (
            ('|| echo "!!! structured_parity --check rc=$?', "parity --check"),
            ('|| echo "!!! structured_parity --release rc=$?', "parity --release"),
            ('|| echo "!!! balanced_timing rc=$?', "balanced_timing"),
            ('|| echo "!!! dump_our_features rc=$?', "dump_our_features")):
        check(f"the informational {name} step cannot stop the round",
              guarded in remote, True,
              "each prints its rc and the round goes on: a bare call is one "
              "SIGPIPE away from ending it")
    check("and the add-on's failure prints its rc too",
          "the tables-b-pb add-on FAILED rc=$addon_rc (informational, round continues)"
          in remote, True,
          "an informational step that fails silently cannot be diagnosed")
    check("the evidence leaves base64 through one line on GNU and BSD",
          'base64 < "/tmp/structured-evidence-$label.tar.gz" | tr -d' in remote
          and "base64 -w" not in remote, True,
          "BSD base64 has neither `-w` nor a positional file; stdin plus `tr` "
          "is the same bytes on both, and a reader that never closes early "
          "cannot SIGPIPE")
    # THE LOCAL DRY RUN (08-10): the whole remote script on a Mac before every
    # launch, with every heavy step stubbed. The self-test holds its cheap
    # form -- the script is there, it runs the real script under the same
    # options, and both scripts parse -- while the round kit runs the full
    # dry run and refuses to launch on red.
    dryrun = REPO / "scripts" / "ci" / "loop" / "round_dryrun.sh"
    check("the round kit dry-runs the whole remote script before a launch",
          dryrun.is_file(), True,
          "three rounds in a row died of a shell fault a dry run catches")
    if dryrun.is_file():
        dry = dryrun.read_text()
        check("and the dry run runs the real script, not a copy",
              "structured_bench_remote.sh" in dry, True,
              "a copy drifts from what launches, and then proves nothing")
        check("and under the same shell options",
              "set -euo pipefail" in dry, True,
              "without pipefail the SIGPIPE the round died of does not happen")
        check("and it ends in one verdict line",
              "DRY-RUN GREEN" in dry and "DRY-RUN RED" in dry, True,
              "the kit launches on green and refuses on red")
        for script in (REPO / "scripts" / "extraction"
                       / "structured_bench_remote.sh", dryrun):
            parsed = subprocess.run(["bash", "-n", str(script)],
                                    capture_output=True, text=True)
            check(f"and {script.name} parses",
                  parsed.returncode, 0,
                  parsed.stderr.strip() or "a script that does not parse "
                  "fails the round that runs it")
    # The dry run's overrides are test-only: dead without dry-run mode, and
    # dry-run mode is refused on a machine with the rig -- so a round can
    # never run stubbed, whatever the environment holds.
    check("the dry-run overrides are dead without dry-run mode",
          'ROUND_DRYRUN="${STRUCTURED_DRYRUN:-0}"' in remote
          and 'if [ "$ROUND_DRYRUN" = 1 ]; then' in remote, True,
          "a round that did not come from the harness reads the production "
          "paths, unconditionally")
    check("and dry-run mode is refused on a machine with the rig",
          "dry-run mode is refused where a round runs" in remote, True,
          "the one place the mode must never take effect is the round machine")
    launching = [str(p.relative_to(REPO)) for base in
                 ("scripts/cos", ".github/workflows", "docs")
                 for p in (REPO / base).rglob("*")
                 if p.is_file() and p.suffix in (".sh", ".py", ".yml", ".md")
                 and "STRUCTURED_DRYRUN" in p.read_text(errors="replace")]
    check("and nothing that documents or launches a round sets dry-run mode",
          launching, [],
          "the harness exports it for its own child; anywhere else it would "
          "stub a real round")
    check("every entry in the committed cache carries its tokens",
          [p.name for p in committed_cache.rglob("*.json.gz")
           if json.loads(__import__("gzip").decompress(p.read_bytes()))
           .get("dimensions", {}).get("tokens_per_page") is None],
          [], "a cached column without tokens/page is a hole in every table")
    check("the committed cache stays within 1 MB a file and 20 MB in all",
          cache_within_limits(committed_cache), [],
          "the COS's limit of 24-09: past it, store scores, not Markdown")

    # --- the dimensions of #621 --------------------------------------------
    dims = measured["tools"]["docling"].get("dimensions") or {}
    check("every column carries every dimension key",
          sorted(dims), sorted([n for n, _l, _s in DIMENSIONS] + ["tokenizer"]),
          "a key that is not there cannot be refused as missing")
    check("the batch's pages all answered, so nothing crashed",
          dims.get("crash_share"), 0.0, "a page with markdown and no lap is an answer")
    complete = {n: 1 for n, _l, _s in DIMENSIONS}
    complete.update(tokenizer="tiktoken x o200k_base", grounding="bbox+page",
                    confidence="none", licence="MIT", offline=True,
                    cost_per_1000_pages="free")
    dim_run = {"run_id": "d", "set": "doclaynet", "set_version": "v",
               "set_licence": "l", "pages_measured": 1, "half": "tuning",
               "machine": {"cpus": 8, "platform": "linux"}, "refused": [],
               "dimensions_schema": 1,
               "tools": {"x": {"name": "X", "version": "1", "ms_per_page": 1,
                               "summary": {a: {"mean": 0.5, "pages": 1} for a, _k, _l in AXES},
                               "dimensions": dict(complete)}}}
    try:
        check("a complete table renders its dimensions",
              "Beyond quality (#621)" in table_of(dim_run), True, "")
    except SystemExit as exc:
        failures.append(f"a complete dimensions table was refused: {exc}")
    # THE TABLE FAILS WHEN A DIMENSION IS MISSING (#621), for every dimension:
    # a check that holds for one and forgets the rest is a list nobody reads.
    for name, _label, _source in DIMENSIONS:
        holed = json.loads(json.dumps(dim_run))
        holed["tools"]["x"]["dimensions"][name] = None
        try:
            table_of(holed)
            failures.append(f"the table rendered with `{name}` missing")
        except SystemExit:
            pass
    # And a competitive round is held to the register: a tool read for the
    # rig that is neither a column nor a refusal was silently dropped.
    dropped = json.loads(json.dumps(dim_run))
    dropped["register_expected"] = ["X", "LiteParse"]
    try:
        table_of(dropped)
        failures.append("a competitive table rendered without a tool on the register")
    except SystemExit:
        pass
    dropped["refused"] = [{"tool": "LiteParse", "why": "not installed"}]
    try:
        table_of(dropped)
    except SystemExit as exc:
        failures.append(f"a refusal did not count as the tool being accounted for: {exc}")
    older = json.loads(json.dumps(dim_run))
    del older["dimensions_schema"]
    check("a run from before #621 still renders, and says so",
          "predates the dimensions" in table_of(older), True,
          "the committed rounds are read by --rescore; they must not start failing")

    with _tempfile.TemporaryDirectory() as tmp:
        fake = pathlib.Path(tmp) / "structured_markdown"
        fake.write_text(
            "#!/usr/bin/env python3\n"
            "import sys\n"
            "if '--json' in sys.argv:\n"
            "    print('{\"blocks\": [{\"kind\": \"heading\", \"text\": \"Installing the pump\","
            " \"spans\": [{\"text\": \"Installing the pump\", \"fontSize\": 12.0,"
            " \"fontName\": \"F\", \"isBold\": true, \"isItalic\": false}]}]}')\n"
            "else:\n"
            "    print('# Installing the pump')\n")
        fake.chmod(0o755)
        ours = measure("doclaynet", {}, two_pages,
                       {"pdfluent": {"name": "PDFluent"}}, [],
                       {"pdfluent": str(fake)}, "r", None)
    rows = ours["tools"]["pdfluent"]["documents"]
    check("our column scores every page too", len(rows), 2,
          "the page-at-a-time loop is the one our column takes")
    check("and every row of it says what the blocks were set in",
          [row.get("set_in", [{}])[0].get("face") for row in rows], ["F", "F"],
          "the table the next heading round starts from is per page, and a "
          "round that carries it for one page carries it for none")

    with _tempfile.TemporaryDirectory() as tmp:
        fake = pathlib.Path(tmp) / "pdfluent"
        fake.write_text(
            "#!/usr/bin/env python3\n"
            "import sys\n"
            "args = sys.argv[1:]\n"
            "assert args[:4] == ['extract', '--quality', 'balanced', '--format'], args\n"
            "assert args[4] == 'md', args\n"
            "print('# Installing the pump')\n")
        fake.chmod(0o755)
        balanced = measure("doclaynet", {}, two_pages,
                           {BALANCED: {"name": "PDFluent balanced"}}, [],
                           {BALANCED: str(fake)}, "r", None)
    rows = balanced["tools"][BALANCED]["documents"]
    check("the balanced column answers through the CLI", len(rows), 2,
          "one `pdfluent extract --quality balanced --format md` per page, like ours")
    check("and it is page-at-a-time, start-up included",
          balanced["tools"][BALANCED].get("start_up_amortised"), False,
          "the per-page clock carries the load; the batch timing beside the evidence amortises it")

    with _tempfile.TemporaryDirectory() as tmp:
        fake = pathlib.Path(tmp) / "pdfluent"
        fake.write_text(
            "#!/usr/bin/env python3\n"
            "import sys\n"
            "args = sys.argv[1:]\n"
            "assert args[:4] == ['extract', '--quality', 'balanced', '--format'], args\n"
            "assert args[4] == 'md', args\n"
            "assert '--table-model-dir' in args, args\n"
            "assert args[args.index('--table-model-dir') + 1] == '/models', args\n"
            "print('# Installing the pump')\n")
        fake.chmod(0o755)
        tatr_env, tatr_had = os.environ.get("TATR_MODEL_DIR"), "TATR_MODEL_DIR" in os.environ
        os.environ["TATR_MODEL_DIR"] = "/models"
        try:
            tatr = measure("doclaynet", {}, two_pages,
                           {BALANCED_TATR: {"name": "PDFluent balanced + table structure"}}, [],
                           {BALANCED_TATR: str(fake)}, "r", None)
        finally:
            if tatr_had:
                os.environ["TATR_MODEL_DIR"] = tatr_env
            else:
                os.environ.pop("TATR_MODEL_DIR", None)
    rows = tatr["tools"][BALANCED_TATR]["documents"]
    check("the balanced_tatr column answers through the CLI with its flag", len(rows), 2,
          "one `pdfluent extract --quality balanced --table-model-dir ... --format md` per page, like balanced")
    check("and it is page-at-a-time, start-up included",
          tatr["tools"][BALANCED_TATR].get("start_up_amortised"), False,
          "the per-page clock carries the load; the batch timing beside the evidence amortises it")

    # --- every axis with the pages back in their documents -------------------
    #
    # The reading the gate holds (#584), and the case that says why: a page
    # with no heading annotated is not scored for headings at all, so a column
    # may claim one there for free. Put the two pages back in their document
    # and the same claim is a precision loss, which is what a caller who
    # extracts the document actually gets.
    doc_pages = [
        {"name": "p0.pdf", "document": "report.pdf", "category": "manuals", "page_no": 1,
         "blocks": [{"kind": "furniture", "text": "Annual report 2024"},
                    {"kind": "heading", "text": "Scope of this report"},
                    {"kind": "paragraph", "text": "The works were completed within the estimate."}]},
        {"name": "p1.pdf", "document": "report.pdf", "category": "manuals", "page_no": 2,
         "blocks": [{"kind": "furniture", "text": "Annual report 2024"},
                    {"kind": "paragraph", "text": "The retention will be released in the spring."}]},
    ]
    per_page = {
        "p0.pdf": "# Scope of this report\n\nThe works were completed within the estimate.\n",
        "p1.pdf": "# Annual report 2024\n\nThe retention will be released in the spring.\n",
    }
    single = [score_page(page, blocks_of(per_page[page["name"]])) for page in doc_pages]
    check("a page with no heading annotated scores none",
          [row["headings"].get("scored", False) for row in single], [True, False],
          "an axis is scored for a category only where the ground truth has one")
    check("so the single-page mean sees no fault",
          summarise_tool(single)["headings"]["mean"], 1.0,
          "the claim on the second page is free, which is the hole this reading "
          "has and the reason the gate stopped reading it")
    regrouped = in_documents("docling", {}, doc_pages, {}, None, None, per_page=per_page)
    check("the document is one row", regrouped.get("documents"), 1,
          "two pages of one document are one reading")
    check("and its blocks are its pages' blocks in page order",
          [b["text"] for b in document_of("report.pdf", doc_pages)["blocks"]][:2],
          ["Annual report 2024", "Scope of this report"],
          "a document is read in the order its pages are")
    check("and the claim is a fault there",
          round(regrouped["summary"]["headings"]["mean"], 3), 0.667,
          "one heading found of one, against two produced: the running head on "
          "the second page is what the in-document reading charges for")
    check("the reading says which one it is",
          regrouped.get("read_as"), "the tool's pages, strung together in page order",
          "a column fed the document and a column strung together are two "
          "readings, and a table that does not say which is which is a table "
          "nobody can check")
    check("a sample of one-page documents has nothing to regroup",
          "why" in in_documents("docling", {}, doc_pages[:1], {}, None, None,
                                per_page=per_page),
          True, "a document of one page is the page, and merging it would count "
                "the same reading twice")

    # --- what our own blocks were set in ------------------------------------
    #
    # The table the next heading round starts from (#578). Three blocks of one
    # page, each carrying the shape a heading classifier reads: a caps line in
    # the body size and a bold face, a paragraph in the body, and a line whose
    # first span is a superscript that must not speak for the line it opens.
    payload = json.dumps({"blocks": [
        {"kind": "paragraph", "text": "I. INTRODUCTION", "spans": [
            {"text": "I. INTRODUCTION", "fontSize": 10.0,
             "fontName": "NimbusRomNo9L-Medi", "isBold": True, "isItalic": False}]},
        {"kind": "paragraph", "text": "The nonvanishing neutrino masses", "spans": [
            {"text": "The nonvanishing neutrino masses", "fontSize": 10.0,
             "fontName": "NimbusRomNo9L-Regu", "isBold": False, "isItalic": False}]},
        {"kind": "paragraph", "text": "A.", "spans": [
            {"text": "A.", "fontSize": 10.0, "fontName": "NimbusRomNo9L-Regu",
             "isBold": False, "isItalic": False}]},
        {"kind": "list_item", "text": "Y. K. Kato, Science 306, 1910 (2004).", "spans": [
            {"text": "12", "fontSize": 7.0, "fontName": "NimbusRomNo9L-Regu",
             "isBold": False, "isItalic": False},
            {"text": "Y. K. Kato, Science 306, 1910 (2004).", "fontSize": 10.0,
             "fontName": "NimbusRomNo9L-Regu", "isBold": False, "isItalic": False}]},
    ]})
    typography = set_in(payload)
    check("every block says what it was set in", [t["kind"] for t in typography],
          ["paragraph", "paragraph", "paragraph", "list_item"],
          "the table is per block, in the order the blocks are in")
    check("the caps line is capitals", typography[0]["caps"], True,
          "capitals are the one signal a document with a single size has left, "
          "and 45 of the 56 headings this exists for are in the body size")
    check("and its weight", typography[0]["bold"], True,
          "weight is what `crate::headings` ranks by where the size is equal")
    check("and its face", typography[0]["face"], "NimbusRomNo9L-Medi",
          "a document that sets its headings in another cut of the same family "
          "says so in the face and nowhere else")
    check("the body line is not capitals", typography[1]["caps"], False,
          "a promoter that fired on the body would promote the whole page")
    check("two cased letters are not a statement about a block",
          typography[2]["caps"], False,
          "`A.` is an initial, a clause reference or a column head, and calling "
          "it capitals would make every one of them a heading candidate")
    check("a superscript does not speak for its line",
          (typography[3]["size"], typography[3]["bold"]), (10.0, False),
          "the size is the one the MIDDLE character is in, which is how the "
          "classifier next door clusters a document")
    # THREE SIZES, AND THE COMMONEST IS NOT THE MIDDLE ONE. `dominant_font_size`
    # sorts one size per glyph and takes the middle; a table that took the
    # commonest instead would say 8 point about a block the classifier clusters
    # at 10, and then the table cannot diagnose the classifier. (codex, #578)
    three = json.dumps({"blocks": [{"kind": "paragraph", "text": "mixed", "spans": [
        {"text": "x" * 40, "fontSize": 8.0, "fontName": "F", "isBold": False,
         "isItalic": False},
        {"text": "y" * 35, "fontSize": 10.0, "fontName": "F", "isBold": False,
         "isItalic": False},
        {"text": "z" * 25, "fontSize": 12.0, "fontName": "F", "isBold": False,
         "isItalic": False}]}]})
    check("the size is the middle character's, not the commonest",
          set_in(three)[0]["size"], 10.0,
          "40 characters at 8 point, 35 at 10 and 25 at 12: the commonest is 8 "
          "and the middle one is 10, which is the one the engine hands the "
          "classifier")
    check("and the text is the key back into the markdown",
          typography[1]["text"], "The nonvanishing neutrino masses",
          "the table is read beside the evidence, so a block has to be findable "
          "in it")
    check("a tool that did not answer is left out", set_in(None), None,
          "a page that says nothing about its type is not a page described "
          "with zeroes")
    check("and so is one that wrote something else", set_in("not json"), None,
          "a parser that guessed here would put a shape in the run file that "
          "nothing produced")
    # --- the geometry a round keeps for the pages a ticket is sent to -----
    # EVERY AXIS, not the one whose number sent somebody looking. The page that
    # cost the heading axis is rarely the page that cost the tables, and keeping
    # one axis's tail answers one ticket and sends the next back to the machine
    # (#580).
    tail = {
        "text": [{"name": "text-worst.pdf", "v": 0.1}],
        "headings": [{"name": "headings-worst.pdf", "v": 0.0}],
        "lists": [],
        "tables": [{"name": "text-worst.pdf", "v": 0.0},
                   {"name": "tables-worst.pdf", "v": 0.0}],
        "teds": [],
        "order": [{"name": "order-worst.pdf", "v": 0.2}],
        "order_geometric": [{"name": "order-worst.pdf", "v": 0.3}],
    }
    kept = pages_in_any_tail(tail)
    check("the tail of every axis is kept", sorted(kept),
          ["headings-worst.pdf", "order-worst.pdf", "tables-worst.pdf",
           "text-worst.pdf"],
          "a round read afterwards by another ticket needs the pages that cost "
          "THAT axis, not the ones that cost the first")
    check("and each page once", len(kept), len(set(kept)),
          "a page in two tails is one page of geometry, not two")
    check("and in the order the axes are declared", kept[0], "text-worst.pdf",
          "two rounds of one tree keep the same pages in the same order rather "
          "than whatever a set iterated to")
    check("a page with no geometry to write is not an error",
          keep_geometry([], [], None, pathlib.Path("/tmp"))["pages"], 0,
          "a round that asked for nothing has nothing to report, and `none` is "
          "a choice a caller is allowed to make")

    # EVERY GLOBAL NAME THIS FILE USES EXISTS IN IT. A name that is only
    # wrong on a path the tests do not walk is a paid round: #582's geometry
    # pass used `OURS`, which lives in `structured_parity.py` and not here, and
    # the measurement fell over with a `NameError` twenty-six minutes into an
    # hour somebody had bought. `symtable` answers it for the whole file in
    # milliseconds, which is the difference between a defect and an incident.
    import builtins
    import symtable

    def undefined(table, seen: set) -> None:
        for name in table.get_symbols():
            if name.is_global() and not name.is_assigned():
                if (name.get_name() not in globals()
                        and not hasattr(builtins, name.get_name())):
                    seen.add(f"{table.get_name()}: {name.get_name()}")
        for child in table.get_children():
            undefined(child, seen)

    loose: set[str] = set()
    undefined(symtable.symtable(pathlib.Path(__file__).read_text(), __file__,
                                "exec"), loose)
    check("every global name this file uses is defined in it", sorted(loose), [],
          "a name that is only wrong on a path no test walks is a paid round: "
          "#582's geometry pass reached the measurement as a NameError")

    # THE OWNER'S RULE OF 22-09-2026, and the day it replaced. `runs_of_this
    # _head` is what the refusal reads, so the test is about which runs it
    # counts as this head's: the same revision on the same set, and neither
    # another branch's round of the same day nor a rescore of one.
    with tempfile.TemporaryDirectory() as shed:
        shelf = pathlib.Path(shed)
        mine = {"harness": "abc1234", "set": "doclaynet"}
        (shelf / f"{PREFIX}-doclaynet-20260922T000000Z.json").write_text(
            json.dumps(mine))
        (shelf / f"{PREFIX}-doclaynet-20260922T010000Z.json").write_text(
            json.dumps({"harness": "def5678", "set": "doclaynet"}))
        (shelf / f"{PREFIX}-doclaynet-20260922T020000Z.json").write_text(
            json.dumps(dict(mine, rescored_from="x")))
        (shelf / f"{PREFIX}-pubtables-1m-20260922T030000Z.json").write_text(
            json.dumps(dict(mine, set="pubtables-1m")))
        was = globals()["RUNS"]
        globals()["RUNS"] = shelf
        try:
            check("a head already measured on this set is refused",
                  [p.stem for p in runs_of_this_head("doclaynet", "abc1234")],
                  [f"{PREFIX}-doclaynet-20260922T000000Z"],
                  "one run per validated bundle is the rule; a second round of "
                  "the same head on the same set is the round per fix #574 "
                  "wanted stopped")
            check("another branch's round of the same day is not this head's",
                  runs_of_this_head("doclaynet", "def5678")[0].stem,
                  f"{PREFIX}-doclaynet-20260922T010000Z",
                  "counting files per day refused a bundle that had taken none "
                  "of the day's rounds, which is what took the count out (#582)")
            check("a rescore is not a round", 
                  len(runs_of_this_head("doclaynet", "abc1234")), 1,
                  "no tool ran and no machine was bought, so it cannot be the "
                  "round this head is allowed")
            check("and a set this head has not been measured on is free",
                  runs_of_this_head("pubtables-1m", "def5678"), [],
                  "a round measures each set once; the sets are asked "
                  "separately because they are bought separately")

            # --- --anyway passes the duplicate rule, not the ceiling -------
            purse = globals()["money_left"]
            globals()["money_left"] = lambda: (40.0, 0.0)
            try:
                for anyway in (False, True):
                    try:
                        check_budget(anyway, "pubtables-1m")
                        failures.append(
                            "check_budget bought a machine with nothing left of "
                            f"the ceiling (--anyway={anyway}) -- the refusal says "
                            "the hard limit takes a decision by the owner and not "
                            "a flag, so a flag must not be able to pass it")
                    except SystemExit:
                        pass
                globals()["money_left"] = lambda: (10.0, 30.0)
                try:
                    check_budget(True, "doclaynet")
                except SystemExit as exc:
                    failures.append(
                        "--anyway did not pass the duplicate rule while money was "
                        f"left: {exc} -- that is the one thing the flag is for")
            finally:
                globals()["money_left"] = purse
        finally:
            globals()["RUNS"] = was

    for line in failures:
        print(f"FAIL {line}", file=sys.stderr)
    # --- no round records a checkout somebody's tool made ----------------
    # A gate instance holds the tree on `gate-run`, so a round that asks git
    # which branch it is on gets a name no lane develops on -- and
    # `structured_parity` then reads the round as a branch round and steps past
    # it. That happened to the daily round of master 1847078bd on 24-09-2026
    # (#574, #593): a paid hour that moved no ratchet. These four cases are the
    # whole rule, and each is red under the version that shipped that round.
    was = os.environ.get("GATE_LINE_BRANCH")
    try:
        for forwarded, want, why in (
            ("gate-run", "",
             "a round may never record the gate's own scratch branch as its line"),
            ("landing-run", "",
             "nor the landing lane's, for the same reason and before it costs a round"),
            ("", "",
             "a caller on a detached HEAD is on no branch, and no branch reads as master"),
            ("t2/tables-fragmentation", "t2/tables-fragmentation",
             "a real lane branch is the whole point of the field and must survive"),
        ):
            os.environ["GATE_LINE_BRANCH"] = forwarded
            check(f"the line of a round forwarded {forwarded!r}",
                  line_measured_on().get("branch", ""), want, why)
    finally:
        if was is None:
            os.environ.pop("GATE_LINE_BRANCH", None)
        else:
            os.environ["GATE_LINE_BRANCH"] = was

    if failures:
        print(f"\n{len(failures)} problem(s) in the structured baseline protocol.",
              file=sys.stderr)
        return 1
    print("✓ structured bench: registers refuse what they say they refuse, "
          "plain text yields no structure, the draw is seeded and by document, "
          "and every axis scores what it claims")
    return 0


# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--set", default="doclaynet", help="which set (DATASETS.toml)")
    parser.add_argument("--tools", default="ours,docling",
                        help="comma-separated, or `all` for every tool the register has read "
                             "for this rig (#619); refused tools are reported, not run")
    parser.add_argument("--work", default=os.environ.get("STRUCTURED_WORK", "/opt/structured"),
                        help="where the set is materialised, on the machine")
    parser.add_argument("--fetch", action="store_true", help="materialise the set")
    parser.add_argument("--draw", action="store_true", help="draw and seal the split")
    parser.add_argument("--measure", action="store_true", help="run the tools")
    parser.add_argument("--limit", type=int, default=None,
                        help="stop after this many pages (a smoke run, recorded as one)")
    parser.add_argument("--pages", type=int, default=None,
                        help="measure a seeded sample of this many pages of the "
                             "half, by the same rule the split uses")
    parser.add_argument("--holdout", action="store_true",
                        help="measure the SEALED half; needs --because")
    parser.add_argument("--because", default="",
                        help="why the holdout is being spent; goes in the run file")
    parser.add_argument("--anyway", action="store_true",
                        help="run although today's two runs are spent")
    parser.add_argument("--rescore", metavar="RUN_FILE",
                        help="score a finished round again from the markdown and "
                             "the annotations it kept; buys no machine")
    parser.add_argument("--evidence", metavar="DIR",
                        help="where that round's `text/` directory was unpacked")
    parser.add_argument("--may-i-run", metavar="TOOL",
                        help="ask the register whether this tool may be run, for "
                             "a caller that cannot import this file; exit 0 if so")
    parser.add_argument(
        "--geometry", choices=("tail", "all", "none"), default="tail",
        help=(
            "whose boxes to keep beside the markdown: the pages in the worst "
            "five percent of any axis (the default), every page, or none. "
            "Measured on 22-09-2026: our blocks with their boxes and spans are "
            "90 to 125 KiB a page before compression -- a page of a financial "
            "report is the upper end -- so a tail of ten to thirty pages is one "
            "to four megabytes raw and `all` over two hundred pages is about 20 "
            "MiB, which the evidence travels base64 through a log. Hence asked "
            "for rather than assumed"))
    parser.add_argument("--fill-cache", action="store_true",
                        help="measure the listed competitors into the competitor cache "
                             "and write no run file: one tool per run, so a failure "
                             "costs that tool and not the night (#623)")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        return self_test()
    # Before any fetch or build (codex, #1982): a cache fill measures named
    # competitors, and the default `ours,docling` would be refused only after
    # the expensive part.
    if args.fill_cache:
        named = [t.strip() for t in args.tools.split(",") if t.strip()]
        if {"ours", OURS} & set(named) or parser.get_default("tools") == args.tools:
            raise SystemExit(f"[{PREFIX}] REFUSED: --fill-cache needs --tools naming the "
                             "competitors to measure (never ours, which every round runs).")
    if args.may_i_run:
        return may_i_run(args.may_i_run)
    if args.rescore:
        if not args.evidence:
            raise SystemExit(f"[{PREFIX}] --rescore needs --evidence <dir>")
        previous = json.loads(pathlib.Path(args.rescore).read_text())
        again = rescore(pathlib.Path(args.evidence), previous,
                        previous["set"], dataset_entry(previous["set"]))
        path = write_run(again)
        print(table_of(again))
        print(f"[{PREFIX}] rescored {previous['run_id']} -> {path.relative_to(REPO)}")
        return 0
    if not (args.fetch or args.draw or args.measure):
        parser.print_help()
        return 2

    entry = dataset_entry(args.set)
    work = pathlib.Path(args.work)
    pages_path = work / "pages.json"

    if args.fetch:
        if args.set == "pubtables-1m":
            pages = fetch_pubtables(entry, work, args.limit)
        elif args.set == "fintabnet":
            pages = fetch_fintabnet(entry, work, args.limit)
        else:
            pages = fetch(entry, work, args.limit)
        work.mkdir(parents=True, exist_ok=True)
        pages_path.write_text(json.dumps(pages))
        print(f"[{PREFIX}] {len(pages)} page(s) materialised in {work}")
    elif args.draw or args.measure:
        if not pages_path.is_file():
            raise SystemExit(f"[{PREFIX}] FATAL: {pages_path} is not there. "
                             "Run with --fetch first; the set is fetched to the "
                             "machine and never to a laptop.")
        pages = json.loads(pages_path.read_text())

    if args.draw:
        fetch_source: str | None = None
        transport_retries: int | None = None
        if args.set == "fintabnet":
            # --draw runs after --fetch in this work dir, or in its own run
            # against the same dir: either way the fetch's evidence is here
            # to be carried into the seal. Unreadable or absent, the seal
            # draws without it rather than refusing a set for its meta.
            refusals_path = work / "fetch-refusals.json"
            if refusals_path.is_file():
                try:
                    refusals = json.loads(refusals_path.read_text())
                except (OSError, ValueError):
                    refusals = {}
                if isinstance(refusals.get("source"), str):
                    fetch_source = refusals["source"]
                if isinstance(refusals.get("transport_retries"), int):
                    transport_retries = refusals["transport_retries"]
        manifest = draw(args.set, entry, pages, fetch_source=fetch_source,
                        transport_retries=transport_retries)
        print(f"[{PREFIX}] drawn {manifest['counts']['holdout']} sealed page(s) of "
              f"{manifest['counts']['pages']} in "
              f"{manifest['counts']['documents']} document(s)")
        print(f"[{PREFIX}] seal (holdout) {manifest['seal']['holdout']}")
        print(f"[{PREFIX}] seal (tuning)  {manifest['seal']['tuning']}")

    if not args.measure:
        return 0

    if args.holdout and not args.because:
        raise SystemExit(
            f"[{PREFIX}] REFUSED: --holdout without --because. A holdout spent "
            "without a reason written at the moment of spending is a holdout "
            "nobody can audit afterwards (benchmarks/HOLDOUTS.md).")
    if not args.fill_cache:
        check_budget(args.anyway, args.set)

    manifest_path = holdout_rule.draw_path_for(args.set)
    if not manifest_path.is_file():
        raise SystemExit(f"[{PREFIX}] FATAL: no manifest at "
                         f"{manifest_path.relative_to(REPO)}; draw the split "
                         "first.")
    manifest = json.loads(manifest_path.read_text())
    half = "holdout" if args.holdout else "tuning"
    wanted = {p["name"] for p in manifest["pages"] if p["split"] == half}
    chosen = [p for p in pages if p["name"] in wanted]
    if args.pages:
        chosen = sample(chosen, args.pages, holdout_rule.seed_for(args.set))
    if args.limit:
        chosen = chosen[:args.limit]
    if not chosen:
        raise SystemExit(f"[{PREFIX}] FATAL: none of the materialised pages are in "
                         f"the {half} half of the committed manifest.")

    asked = [t.strip() for t in args.tools.split(",") if t.strip()]
    # `--tools all` IS THE COMPETITIVE ROUND (#619): every tool the register
    # has read for the structured rig, run or refused, and the table is then
    # held to all of them (`missing_dimensions`).
    expected: list[str] = []
    if asked == ["all"]:
        register, _meta = load_register(EXTRACTORS, "extractors")
        asked = [key for key, row in register.items() if "on_the_structured_rig" in row]
        expected = [register[key].get("name", key) for key in asked]
    allowed, refused = tools_allowed(asked)
    for entry_refused in refused:
        print(f"[{PREFIX}] NOT RUN: {entry_refused['tool']} -- {entry_refused['why']}",
              file=sys.stderr)
    if not allowed:
        raise SystemExit(f"[{PREFIX}] FATAL: every tool asked for is refused.")

    # No column phones home during a round: Unstructured pings Scarf on import
    # unless told not to (EXTRACTORS.toml), and the children inherit this.
    os.environ.setdefault("DO_NOT_TRACK", "1")
    os.environ.setdefault("SCARF_NO_ANALYTICS", "true")
    binaries = {
        # The block model's markdown, from the tree this round was built from.
        # NOT `pdfluent-cli` on the PATH: a released binary beside a source
        # build is the one difference a round must never have in it.
        "pdfluent": os.environ.get("PDFLUENT_STRUCTURED_BIN")
        or str(REPO / "target" / "release" / "examples" / "structured_markdown"),
        # The balanced tier's CLI, built from this tree with `ort-layout` by
        # the remote script. Overridable for a local round.
        BALANCED: os.environ.get("BALANCED_BIN")
        or str(REPO / "target" / "release" / "pdfluent"),
        # The same binary: the round builds once with both tiers and points
        # both columns at it. A local round with only BALANCED_BIN set runs
        # balanced_tatr from it too.
        BALANCED_TATR: os.environ.get("BALANCED_TATR_BIN")
        or os.environ.get("BALANCED_BIN")
        or str(REPO / "target" / "release" / "pdfluent"),
        **{tool: os.environ.get(f"{tool.upper()}_PYTHON", f"/opt/ex/{tool}/bin/python")
           for tool in ADAPTED},
    }
    if args.fill_cache:
        if OURS in allowed or BALANCED in allowed or BALANCED_TATR in allowed:
            raise SystemExit(f"[{PREFIX}] REFUSED: --fill-cache measures competitors; "
                             "our engine is measured by every round and never cached.")
        filled = measure(args.set, entry, chosen, allowed, refused, binaries,
                         run_id_of(args.set), "fill the competitor cache",
                         text_dir=work / "text", geometry="none")
        holes = 0
        for name, row in filled["tools"].items():
            where = row.get("cached_to") or (row.get("from_cache") or {}).get("file")
            print(f"[{PREFIX}] {name}: {'cached ' + where if where else 'NOT cached (the batch did not finish)'}")
            # A cached column without a dimension is a hole in every table that
            # reads it: the fill of 25/26-09 ran without the tokenizer and every
            # entry had to be counted offline (codex, #1994). Said, and failed.
            missing = [n for n, _l, _s in DIMENSIONS
                       if (row.get("dimensions") or {}).get(n) is None]
            if missing:
                holes += 1
                print(f"[{PREFIX}] FAIL {name}: no {', '.join(missing)} in what was cached",
                      file=sys.stderr)
        stored = all(r.get("cached_to") or r.get("from_cache") for r in filled["tools"].values())
        return 0 if stored and not holes else 1
    run = measure(args.set, entry, chosen, allowed, refused, binaries,
                  run_id_of(args.set), args.because or half,
                  text_dir=work / "text", geometry=args.geometry)
    if expected:
        run["register_expected"] = expected
    # THE GROUND TRUTH OF WHAT WAS MEASURED, beside what every column said.
    # Keeping only the texts was half the lesson of #428: a change to an axis
    # can be answered by recomputing, but only if the annotations the axis reads
    # are still there. They live on the instance, which is deleted minutes
    # later, and the first round could not be re-scored after review found two
    # defects in the reader -- that cost a second machine. (codex, #1922)
    truth = work / "text" / "ground-truth.json"
    truth.parent.mkdir(parents=True, exist_ok=True)
    truth.write_text(json.dumps([
        # `annotates` TRAVELS WITH THE ANNOTATIONS. Without it a rescore --
        # which is the whole point of keeping them -- reads the pages as if
        # every set drew every class of block, and reports a text axis and a
        # reading order for a corpus of tables. (codex, #1930)
        {k: page[k] for k in ("source", "name", "document", "page_no",
                              "category", "sha256", "annotates", "blocks")
         if k in page}
        for page in chosen], indent=1))
    run["sample"] = {
        "asked": args.pages, "measured": len(chosen),
        "of_the_half": len(wanted),
        "seed": holdout_rule.seed_for(args.set),
        "rule": "whole documents in the split's own seeded order "
                "(structured_bench.sample)",
    } if args.pages else None
    # WHICH PAGES, AND NOT MERELY HOW MANY. Two rounds can both say `doclaynet,
    # tuning, 200 pages` and be over different two hundred pages -- a moved
    # revision, a redrawn manifest, a changed sampling rule -- and a ratchet
    # comparing their means would then be reporting the corpus. So a run says
    # what it measured, by digest over the names, and the gate refuses to
    # ratchet two runs whose digests differ. (codex, #1930)
    run["pages_digest"] = pages_digest(chosen)
    path = write_run(run)
    print(table_of(run))
    print(f"[{PREFIX}] run file {path.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
