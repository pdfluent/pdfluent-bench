#!/usr/bin/env python3
# Copyright (c) 2026 Innovation Trigger B.V.
#
# PDFluent is available under two licences, at your option: the GNU AGPLv3, or
# the PDFluent Commercial Licence. See the LICENSE file in this repository --
# that file travels with the copy you received, which a URL does not.
"""IBM's FinTabNet 1.0.0 archive, read by HTTP Range, never downloaded whole (#574).

The mirror (`Leon1207/FinTabNet`, `archive.zip`, 16,776,878,936 bytes) holds
89,646 one-page PDFs, the six `FinTabNet_1.0.0_*.jsonl` and IBM's
`fintabnet/LICENSE.txt`. Fetching all of it for a 300-page round would be a
mirror of somebody else's corpus, so this reads three things over the network:

  1. the zip64 end of central directory, then the central directory once
     (9.5 MB, 89,653 entries);
  2. per page, the local header plus the compressed data;
  3. each referenced jsonl whole once, for the provenance check below.

EVERY READ IS A RANGE, and a server that answers one with the whole file is
treated as a failure rather than read: a `200` where a `206` was asked for is
16 GB arriving as one response. A 429 or a 5xx, a network error, or a short
body is retried -- five tries with a jittered backoff -- because a CDN that
is rate-limiting sixty-four workers answers the next ask; anything else that
is not a 206 is returned as a failure, not read.

TRANSPORT IS NOT DATA. A read the server never answered -- the retries spent,
the body short, the connection dropped -- raises `TransportFailed`, never
`MemberRefused`: on 03-10-2026 the dump counted 750 pages as refused whose
only fault was a 429 from the CDN, and 61 more as provenance mismatches for
the same reason. A transport failure is retried by the caller and counted
apart; a refusal is what the bytes say about themselves.

EVERY REFUSAL HAS A REASON, AND A REFUSAL IS NEVER A PAGE. A CRC32 that does
not match the central directory, a provenance line that names another table,
a LICENSE that is missing or reads differently, a set whose PDF count moved:
each is refused by name. `MemberRefused` is one page or one file, counted by
the caller; `SystemExit` is the whole set.

This file holds no network of its own beyond `RangeReader`: everything takes
a `read(start, end)` callable, so the self-test drives the same code over
bytes it built itself. The seeded walk, the alignment check and the rows live
in `scripts/ci/loop/structured_bench.py`, which is the half that fetches.
"""
from __future__ import annotations

import random
import re
import struct
import sys
import time
import urllib.error
import urllib.request

import fintabnet_split

PREFIX = "fintabnet"

# What every row of the set carries, frozen here rather than in the caller so
# that the self-test holds the values the spec pins.
SOURCE = "fintabnet/ibm-1.0.0"
DOCUMENT_LICENCE = "CDLA-Permissive-1.0"
CATEGORY = "financial reports"

# The release's own terms, as `fintabnet/LICENSE.txt` prints them. The set is
# refused without them: a licence tag on a re-upload is not evidence, and the
# verdict in DATASETS.toml was read off this text.
LICENSE_PATH = "fintabnet/LICENSE.txt"
LICENSE_FIRST_LINE = "Community Data License Agreement \u2013 Permissive \u2013 Version 1.0"

# Where the pages live in the archive.
PDF_PREFIX = "fintabnet/pdf/"
PDF_SUFFIX = ".pdf"

# The only compression this archive uses, over all 89,653 entries. Anything
# else is refused with its method number rather than guessed at.
METHOD_DEFLATE = 8

# One file per page: `COMPANY_YEAR_page_N_tables.json`, under one directory.
# The company is the ticker exactly as the archive spells it.
MEMBER = re.compile(r"^(.+)_(\d{4})_page_(\d+)_tables\.json$")

# At most this many pages of one annual report, and of one company, in the
# set. Otherwise one heavy filer weighs like a third of it.
PER_DOCUMENT = 4
PER_COMPANY = 20

# A range read is answered in slices of this, so a 9.5 MB directory and an
# 84 MB jsonl each arrive with a per-slice timeout rather than on one.
CHUNK = 1 << 20


class MemberRefused(Exception):
    """One member is not what the set says it is. Counted, never a page."""


class CRCRefused(MemberRefused):
    """The bytes are not the directory's bytes for this member."""


class ProvenanceRefused(MemberRefused):
    """The annotation is not IBM's own line for this page."""


class TransportFailed(Exception):
    """The bytes never arrived: retries spent, body short, connection gone.

    Deliberately NOT a `MemberRefused`: a transport failure says nothing
    about the member, only about the road to it, and catching it as a refusal
    is how the dump wrote 750 rate-limited pages off as bad data. The caller
    retries the member and counts it apart.
    """


def documents_url(repo_id: str, revision: str, archive: str) -> str:
    """The pinned archive, as one URL. The revision is the commit, not `main`."""
    return (f"https://huggingface.co/datasets/{repo_id}/resolve/"
            f"{revision}/{archive}")


# Five tries per range read: the first ask plus four retries. The 03-10
# dump drew 64 workers against one CDN, and a 429 under that load is a
# queue, not an answer.
READ_TRIES = 5
# Between tries, `2 ** attempt` seconds up to this cap, plus up to a second
# of jitter so sixty-four backed-off workers do not ask again as one.
READ_BACKOFF_CAP = 30.0


def _retryable_status(status: int) -> bool:
    """Whether an HTTP status is worth asking again: 429, or a 5xx."""
    return status == 429 or 500 <= status <= 599


def _content_range_total(value: str | None) -> int | None:
    """The total out of a `Content-Range` header, or None when it names none.

    `bytes 0-0/16776878936` answers 16776878936; a missing header, a total of
    `*`, or anything not shaped like one answers None rather than guessing.
    """
    if not value or "/" not in value:
        return None
    total = value.rsplit("/", 1)[1].strip()
    if not total.isdigit():
        return None
    return int(total)


class RangeReader:
    """One URL, read in inclusive byte ranges, or not at all."""

    def __init__(self, url: str, timeout: int = 120, urlopen=None,
                 sleep=None) -> None:
        self.url = url
        self.timeout = timeout
        # Every retry this reader took, across every call: the round's
        # evidence counts them, so a throttled round says so about itself.
        self.transport_retries = 0
        # The seams the self-test drives: a fake transport and a fake clock,
        # so the retries are exercised without a network or a wait.
        self._urlopen = urlopen or urllib.request.urlopen
        self._sleep = sleep or time.sleep

    def _wait(self, attempt: int) -> None:
        """The pause before the next try: exponential, capped, jittered."""
        delay = min(READ_BACKOFF_CAP, 2.0 ** attempt)
        self._sleep(delay + random.uniform(0.0, 1.0))

    def _request(self, start: int, end: int):
        """One ranged ask: the slice, and who is asking for it."""
        return urllib.request.Request(self.url, headers={
            "Range": f"bytes={start}-{end}",
            # Who is asking, because an archive that has to rate-limit
            # somebody should be able to see who.
            "User-Agent": "pdfluent-structured-bench/1.0 (benchmark fetch; "
                          "https://pdfluent.com)"})

    def _again(self, attempt: int) -> bool:
        """Whether another try follows: count it, wait, and say so."""
        if attempt + 1 >= READ_TRIES:
            return False
        self.transport_retries += 1
        self._wait(attempt)
        return True

    def read(self, start: int, end: int) -> bytes | None:
        """Bytes `start` through `end`, or None when the server did not answer.

        A `200` is a failure here, not a superset of what was asked: the file
        behind this URL is 16 GB, and reading that response would be the
        download this module exists to avoid -- so a non-206 answer is never
        read, only its status looked at. A 429 or a 5xx, a network error, or
        a short body is retried, five tries in all; anything else that is not
        a 206 -- a 404, a 416 -- fails at once, because asking again cannot
        change it.
        """
        want = end - start + 1
        request = self._request(start, end)
        for attempt in range(READ_TRIES):
            try:
                with self._urlopen(request,
                                   timeout=self.timeout) as answer:
                    if answer.status != 206:
                        if not _retryable_status(answer.status):
                            return None
                    else:
                        body = answer.read()
                        if len(body) == want:
                            return body
            except urllib.error.HTTPError as exc:
                if not _retryable_status(exc.code or 0):
                    return None
            except (urllib.error.URLError, OSError, ValueError):
                pass
            self._again(attempt)
        return None

    def size(self) -> int | None:
        """The file's whole size, or None when the server did not say.

        One byte is asked for and only its `Content-Range` total is kept, so
        this is the size without the download. Retried like `read` -- a 429
        here is the same queue -- and counted there too. Anything else that
        is not a 206 with a total fails at once: a 404, or a 206 whose
        Content-Range names no total, cannot be asked into sense.
        """
        request = self._request(0, 0)
        for attempt in range(READ_TRIES):
            try:
                with self._urlopen(request,
                                   timeout=self.timeout) as answer:
                    if answer.status != 206:
                        if not _retryable_status(answer.status):
                            return None
                    else:
                        total = _content_range_total(
                            answer.getheader("Content-Range"))
                        if total is None:
                            return None
                        if len(answer.read()) == 1:
                            return total
            except urllib.error.HTTPError as exc:
                if not _retryable_status(exc.code or 0):
                    return None
            except (urllib.error.URLError, OSError, ValueError):
                pass
            self._again(attempt)
        return None


class LocalFileReader:
    """Our own copy of the archive on local disk, read by seek, never by HTTP.

    The dump VM copies the mirror once and verifies its sha256; the workers
    then read members out of the file with the same `Archive` code the range
    reader feeds. One open handle for the worker's whole run: opening per
    page would work but say nothing.
    """

    def __init__(self, path) -> None:
        self.path = str(path)
        self._handle = open(self.path, "rb")

    def read(self, start: int, end: int) -> bytes | None:
        """Bytes `start` through `end`, or None past the end or on an error."""
        want = end - start + 1
        try:
            self._handle.seek(start)
            out = bytearray()
            while len(out) < want:
                chunk = self._handle.read(want - len(out))
                if not chunk:
                    return None
                out += chunk
        except (OSError, ValueError):
            return None
        return bytes(out)

    def close(self) -> None:
        """The handle back: the worker calls this once, at its end."""
        try:
            self._handle.close()
        except OSError:
            pass


def _read_all(read, start: int, size: int) -> bytes | None:
    """`size` bytes from `start`, in slices, or None on the first failure."""
    out = bytearray()
    while len(out) < size:
        piece = read(start + len(out), min(start + size, start + len(out) + CHUNK) - 1)
        if piece is None:
            return None
        out += piece
    return bytes(out)


class CentralEntry:
    """One file of the archive, as the central directory names it."""

    def __init__(self, name: str, method: int, crc: int, csize: int,
                 usize: int, offset: int) -> None:
        self.name = name
        self.method = method
        self.crc = crc
        self.csize = csize
        self.usize = usize
        self.offset = offset

    def __repr__(self) -> str:  # pragma: no cover - debugging only
        return (f"CentralEntry({self.name!r}, method={self.method}, "
                f"csize={self.csize}, usize={self.usize})")


def _zip64_extra(extra: bytes, csize: int, usize: int, offset: int,
                 ) -> tuple[int, int, int]:
    """The true sizes and offset out of a zip64 extra field.

    The extra record carries only the fields whose central-directory value is
    `0xFFFFFFFF`, in the order uncompressed size, compressed size, local
    header offset -- so they are read conditionally, in that order, and not as
    three fixed quadwords.
    """
    pos = 0
    while pos + 4 <= len(extra):
        tag, size = struct.unpack("<HH", extra[pos:pos + 4])
        value = extra[pos + 4:pos + 4 + size]
        if tag == 0x0001:
            at = 0
            if usize == 0xFFFFFFFF:
                (usize,) = struct.unpack("<Q", value[at:at + 8])
                at += 8
            if csize == 0xFFFFFFFF:
                (csize,) = struct.unpack("<Q", value[at:at + 8])
                at += 8
            if offset == 0xFFFFFFFF:
                (offset,) = struct.unpack("<Q", value[at:at + 8])
                at += 8
        pos += 4 + size
    return csize, usize, offset


class Archive:
    """The archive's directory, with the reader that fetches out of it."""

    def __init__(self, read, entries: dict[str, CentralEntry]) -> None:
        self._read = read
        self.entries = entries

    def fetch(self, name: str) -> bytes:
        """One member's bytes: local header, compressed data, inflate, CRC32.

        Raises `MemberRefused` with the reason -- a missing member, a wrong
        method, an undecodable stream, a CRC32 that is not the directory's --
        and never returns a page that failed any of them. A read the
        transport never answered raises `TransportFailed` instead: the
        member is not refused, the road to it failed.
        """
        import zlib

        entry = self.entries.get(name)
        if entry is None:
            raise MemberRefused(f"{name} is not in the archive")
        if entry.method != METHOD_DEFLATE:
            raise MemberRefused(
                f"{name} is method {entry.method}, not deflate (8)")
        head = self._read(entry.offset, entry.offset + 29)
        if head is None:
            raise TransportFailed(
                f"{name} did not answer its local header")
        if head[:4] != b"PK\x03\x04":
            raise MemberRefused(f"{name} has no local header where the "
                                "directory says it is")
        (seen_method,) = struct.unpack("<H", head[8:10])
        if seen_method != entry.method:
            raise MemberRefused(
                f"{name} is method {seen_method} at its data and "
                f"{entry.method} in the directory")
        name_len, extra_len = struct.unpack("<HH", head[26:30])
        data_at = entry.offset + 30 + name_len + extra_len
        blob = _read_all(self._read, data_at, entry.csize)
        if blob is None:
            raise TransportFailed(
                f"{name} did not answer its {entry.csize} byte(s)")
        try:
            # Bounded by the directory's own size: a corrupt stream is refused
            # partway rather than inflated until the machine gives out.
            out = bytearray()
            worker = zlib.decompressobj(-15)
            for at in range(0, len(blob), CHUNK):
                out += worker.decompress(blob[at:at + CHUNK])
                if len(out) > entry.usize:
                    raise MemberRefused(
                        f"{name} inflates past its {entry.usize} byte(s)")
            out += worker.flush()
        except MemberRefused:
            raise
        except zlib.error as exc:
            raise MemberRefused(f"{name} does not inflate: {exc}") from None
        if (zlib.crc32(out) & 0xFFFFFFFF) != entry.crc:
            raise CRCRefused(
                f"{name} has CRC32 {zlib.crc32(out) & 0xFFFFFFFF:08x}, "
                f"the directory says {entry.crc:08x}")
        return bytes(out)


def open_archive(read, size: int) -> Archive:
    """The archive at the end of `read`, whose whole file is `size` bytes.

    The end of central directory -- the zip64 one where the archive needs it,
    which this one does -- then the directory once. Anything else is the set
    refusing itself, loudly: a set read from a directory that cannot be parsed
    is a set nobody can get back.
    """
    tail = read(size - 22, size - 1)
    if tail is None:
        raise SystemExit(
            f"[{PREFIX}] FATAL: the archive did not answer its end of "
            "central directory.")
    if tail[:4] != b"PK\x05\x06":
        raise SystemExit(
            f"[{PREFIX}] REFUSED: no end of central directory where the "
            f"register's {size} bytes say it is.")
    (_disk, _cd_disk, disk_entries, total_entries, cd_size, cd_off,
     _comment) = struct.unpack("<HHHHIIH", tail[4:])
    if (disk_entries == 0xFFFF or total_entries == 0xFFFF
            or cd_size == 0xFFFFFFFF or cd_off == 0xFFFFFFFF):
        locator = read(size - 42, size - 23)
        if locator is None or locator[:4] != b"PK\x06\x07":
            raise SystemExit(
                f"[{PREFIX}] REFUSED: the directory overflows its 32-bit "
                "fields and no zip64 locator stands before it.")
        (eocd64_at,) = struct.unpack("<Q", locator[8:16])
        head = read(eocd64_at, eocd64_at + 55)
        if head is None or head[:4] != b"PK\x06\x06":
            raise SystemExit(
                f"[{PREFIX}] REFUSED: no zip64 end of central directory at "
                f"{eocd64_at}.")
        (size64,) = struct.unpack("<Q", head[4:12])
        wide = read(eocd64_at, eocd64_at + 12 + size64 - 1)
        if wide is None:
            raise SystemExit(
                f"[{PREFIX}] FATAL: the archive did not answer its zip64 end "
                "of central directory.")
        (_v_made, _v_need, _d1, _d2, disk_entries, total_entries, cd_size,
         cd_off) = struct.unpack("<HHIIQQQQ", wide[12:56])
    if cd_off + cd_size > size:
        raise SystemExit(
            f"[{PREFIX}] REFUSED: the directory ({cd_size} byte(s) at "
            f"{cd_off}) does not fit in the register's {size} bytes.")
    raw = _read_all(read, cd_off, cd_size)
    if raw is None:
        raise SystemExit(
            f"[{PREFIX}] FATAL: the archive did not answer its central "
            "directory.")
    entries: dict[str, CentralEntry] = {}
    pos = 0
    while pos < len(raw):
        if raw[pos:pos + 4] != b"PK\x01\x02":
            raise SystemExit(
                f"[{PREFIX}] REFUSED: entry {len(entries)} of the central "
                "directory is not an entry.")
        (_made, _need, flag, method, _time, _date, crc, csize, usize,
         name_len, extra_len, _comment_len, _disk, _iattr, _eattr,
         offset) = struct.unpack("<HHHHHHIIIHHHHHII", raw[pos + 4:pos + 46])
        name_bytes = raw[pos + 46:pos + 46 + name_len]
        try:
            name = name_bytes.decode("utf-8" if flag & 0x800 else "cp437")
        except UnicodeDecodeError:
            raise SystemExit(
                f"[{PREFIX}] REFUSED: entry {len(entries)} of the central "
                "directory names un-decodable bytes.") from None
        extra = raw[pos + 46 + name_len:pos + 46 + name_len + extra_len]
        csize, usize, offset = _zip64_extra(extra, csize, usize, offset)
        if name in entries:
            raise SystemExit(
                f"[{PREFIX}] REFUSED: the directory names {name!r} twice, so "
                "no fetch out of it can say which one it read.")
        entries[name] = CentralEntry(name, method, crc, csize, usize, offset)
        pos += 46 + name_len + extra_len + _comment_len
    if len(entries) != total_entries:
        raise SystemExit(
            f"[{PREFIX}] REFUSED: the directory holds {len(entries)} "
            f"entries and promises {total_entries}.")
    return Archive(read, entries)


def check_set(archive: Archive, expected_pdfs: int) -> None:
    """Refuse the whole set unless it is IBM's release, member for member.

    The LICENSE whose text the verdict was read off, with its first line
    exact, and the PDF count the register pins. Either one moved means the
    mirror is not the release anymore, and there is no smaller thing to fall
    back to: without the terms there is no permission, and without the count
    there is no evidence the pages are the ones Microsoft annotated.
    """
    if LICENSE_PATH not in archive.entries:
        raise SystemExit(
            f"[{PREFIX}] REFUSED: the archive has no {LICENSE_PATH}, and "
            "without IBM's own terms beside the pages there is no set to "
            "measure.")
    try:
        text = archive.fetch(LICENSE_PATH)
    except MemberRefused as exc:
        raise SystemExit(f"[{PREFIX}] REFUSED: {LICENSE_PATH} {exc}.") from None
    try:
        first = text.split(b"\n")[0].decode("utf-8").rstrip("\r")
    except UnicodeDecodeError:
        raise SystemExit(
            f"[{PREFIX}] REFUSED: {LICENSE_PATH} is not decodable text.") from None
    if first != LICENSE_FIRST_LINE:
        raise SystemExit(
            f"[{PREFIX}] REFUSED: {LICENSE_PATH} starts with {first[:80]!r}, "
            f"not with {LICENSE_FIRST_LINE!r}.")
    pdfs = sum(1 for n in archive.entries
               if n.startswith(PDF_PREFIX) and n.endswith(PDF_SUFFIX))
    if pdfs != expected_pdfs:
        raise SystemExit(
            f"[{PREFIX}] REFUSED: the archive holds {pdfs} PDFs and the "
            f"register pins {expected_pdfs}.")


class JsonlCache:
    """IBM's six jsonl, each fetched whole once, for the provenance check.

    `expected`, when given, maps each filename to its uncompressed size and
    sha256 from the register (`DATASETS.toml` `jsonl_files`): a file whose
    bytes are not those is refused rather than read, because a short or
    substituted jsonl would otherwise fail every page that points into it as
    a provenance mismatch. Until the register carries them, the central
    directory's CRC32 -- checked by `Archive.fetch` -- is the only
    verification, and `observed()` reports what a healthy fetch saw so the
    register can be filled from its evidence.
    """

    def __init__(self, archive: Archive,
                 expected: dict[str, dict] | None = None) -> None:
        self._archive = archive
        self._expected: dict[str, dict] = dict(expected or {})
        self._lines: dict[str, list[bytes]] = {}
        self._observed: dict[str, dict] = {}

    def lines(self, filename: str) -> list[bytes]:
        """Every line of `fintabnet/<filename>`, fetched on first use.

        A `TransportFailed` passes through unwrapped: a provenance file the
        transport never answered is not a provenance mismatch, and wrapping
        it is how the dump counted 61 rate-limited pages as bad annotations.
        """
        if filename not in self._lines:
            self._lines[filename] = self._fetch(filename)
        return self._lines[filename]

    def refresh(self, filename: str) -> list[bytes]:
        """Fetch `filename` again, replacing what is cached.

        Called when many pages fail provenance against one file at once: the
        file itself was likely short-read. Verifies against the register when
        it names the file, and raises `ProvenanceRefused` when the re-fetch
        still does not verify -- the caller then fails the run loudly rather
        than substituting other pages.
        """
        self._lines.pop(filename, None)
        self._lines[filename] = self._fetch(filename)
        return self._lines[filename]

    def observed(self) -> dict[str, dict]:
        """Each file fetched so far, by uncompressed size and sha256."""
        return dict(self._observed)

    def _fetch(self, filename: str) -> list[bytes]:
        import hashlib

        try:
            blob = self._archive.fetch(f"fintabnet/{filename}")
        except MemberRefused as exc:
            raise ProvenanceRefused(
                f"provenance file {filename}: {exc}") from None
        want = self._expected.get(filename) or {}
        if want.get("bytes") is not None and len(blob) != want["bytes"]:
            raise ProvenanceRefused(
                f"provenance file {filename} is {len(blob)} bytes, "
                f"the register pins {want['bytes']}")
        digest = hashlib.sha256(blob).hexdigest()
        if want.get("sha256") and digest != want["sha256"]:
            raise ProvenanceRefused(
                f"provenance file {filename} has sha256 {digest[:16]}..., "
                f"the register pins {str(want['sha256'])[:16]}...")
        self._observed[filename] = {"bytes": len(blob), "sha256": digest}
        rows = blob.split(b"\n")
        del blob
        return rows


def ibm_filename(company: str, year: str, num: int) -> str:
    """The page as IBM's jsonl names it: `<COMPANY>/<YEAR>/page_<N>.pdf`."""
    return f"{company}/{year}/page_{num}.pdf"


def check_provenance(company: str, year: str, num: int, table: dict,
                     jsonls: JsonlCache) -> None:
    """The annotation's table against IBM's own line for it.

    `fintabnet_source_line_index` into `fintabnet_source_file_name` must hold
    `table_id == fintabnet_source_table_id` and IBM's filename for this page.
    A mismatch is a refusal with the reason, counted by the caller -- an
    annotation that is not IBM's line for this page grades a tool against
    somebody else's table.
    """
    import json

    source = table.get("fintabnet_source_file_name")
    if not source:
        raise ProvenanceRefused("the table names no fintabnet_source_file_name")
    lines = jsonls.lines(source)
    index = table.get("fintabnet_source_line_index")
    if not isinstance(index, int) or not 0 <= index < len(lines):
        raise ProvenanceRefused(
            f"provenance line index {index!r} is not a line of the "
            f"{len(lines)} {source} holds")
    try:
        row = json.loads(lines[index])
    except ValueError:
        raise ProvenanceRefused(
            f"provenance line {index} of {source} is not JSON") from None
    want_id = table.get("fintabnet_source_table_id")
    if row.get("table_id") != want_id:
        raise ProvenanceRefused(
            f"provenance line {index} of {source} names table_id "
            f"{row.get('table_id')!r}, the annotation says {want_id!r}")
    want_file = ibm_filename(company, year, num)
    if row.get("filename") != want_file:
        raise ProvenanceRefused(
            f"provenance line {index} of {source} names "
            f"{row.get('filename')!r}, the page is {want_file}")


def page_key_of(member_name: str) -> tuple[str, str, int] | None:
    """`(company, year, num)` for an annotation member, else None.

    A name that does not match is skipped rather than guessed at, the way
    `pubtables_cells.pmcid_of` skips a file that names no article: the key is
    the only thing the pages can be fetched by.
    """
    import pathlib

    found = MEMBER.match(pathlib.PurePath(member_name).name)
    if not found:
        return None
    return found.group(1), found.group(2), int(found.group(3))


def zip_path_of(company: str, year: str, num: int) -> str:
    """The page's path in the archive."""
    return f"{PDF_PREFIX}{company}/{year}/page_{num}.pdf"


def document_of(company: str, year: str) -> str:
    """One annual report: the unit the per-document headline weighs."""
    return f"{company}_{year}"


def walk_name(company: str, year: str, num: int) -> str:
    """The page as the seeded walk ranks it: `<COMPANY>_<YEAR>_page_<N>`."""
    return f"{company}_{year}_page_{num}"


def annotation_names(tar) -> dict[tuple[str, str, int], str]:
    """First streaming pass: every annotation member's key and name.

    `tar` is an open tarfile in stream mode (`r|gz`), because a `.tar.gz` is
    a stream: seeking back to a member that has gone past means
    decompressing from the beginning again. The second pass extracts only the
    members the seeded walk asks for, in the order the archive has them --
    the same two passes the PubTables lane takes.
    """
    names: dict[tuple[str, str, int], str] = {}
    for member in tar:
        if not member.isfile():
            continue
        key = page_key_of(member.name)
        if key is not None:
            names[key] = member.name
    return names


def eval_names(names: dict[tuple[str, str, int], str],
               ) -> dict[tuple[str, str, int], str]:
    """The lane's pages: eval companies only, through the one split rule."""
    return {key: member for key, member in names.items()
            if fintabnet_split.side(key[0]) == "eval"}


def read_annotations(tar, wanted: set[str],
                     ) -> tuple[dict[tuple[str, str, int], list], int]:
    """Second streaming pass: the wanted members, parsed, and a count.

    Returns the annotations by key and how many members could not be read at
    all, which the caller counts rather than fails on: one unreadable member
    of a 255 MB archive refuses one page, not the set.
    """
    import json

    annotations: dict[tuple[str, str, int], list] = {}
    unreadable = 0
    for member in tar:
        if member.name not in wanted:
            continue
        key = page_key_of(member.name)
        handle = tar.extractfile(member)
        if key is None or handle is None:
            unreadable += 1
            continue
        try:
            annotation = json.loads(handle.read())
        except ValueError:
            unreadable += 1
            continue
        if not isinstance(annotation, list):
            unreadable += 1
            continue
        annotations[key] = annotation
    return annotations, unreadable


def kept_tables(annotation: list[dict] | None) -> list[dict]:
    """The tables the lane may grade: `exclude_for_structure == false`.

    Only an explicit false keeps a table. Anything else -- true, missing, or
    of another type -- leaves it out: a table Microsoft excluded from
    structure is not ground truth for structure.
    """
    return [t for t in (annotation or [])
            if t.get("exclude_for_structure") is False]


class Caps:
    """At most 4 pages per annual report, 20 per company, in walk order."""

    def __init__(self, per_document: int = PER_DOCUMENT,
                 per_company: int = PER_COMPANY) -> None:
        self.per_document = per_document
        self.per_company = per_company
        self._documents: dict[str, int] = {}
        self._companies: dict[str, int] = {}

    def full(self, company: str, document: str) -> str | None:
        """Which cap is spent for one more page: `"report"`, `"company"`, or None."""
        if self._documents.get(document, 0) >= self.per_document:
            return "report"
        if self._companies.get(company, 0) >= self.per_company:
            return "company"
        return None

    def room(self, company: str, document: str) -> bool:
        """Whether one more page of this report may be taken."""
        return self.full(company, document) is None

    def take(self, company: str, document: str) -> bool:
        """Count one page taken, or False when its report or company is full."""
        if not self.room(company, document):
            return False
        self._documents[document] = self._documents.get(document, 0) + 1
        self._companies[company] = self._companies.get(company, 0) + 1
        return True


# ---------------------------------------------------------------------------
# The self-test: a synthetic archive and a synthetic annotations tar, in bytes.
# ---------------------------------------------------------------------------
def _bytes_reader(data: bytes):
    def read(start: int, end: int) -> bytes | None:
        if not 0 <= start <= end < len(data):
            return None
        return data[start:end + 1]
    return read


def _table(cells: int, source_file: str, line_index: int, table_id: int,
           excluded: bool = False) -> dict:
    return {
        "pdf_page_index": 0,
        "pdf_table_bbox": [0.0, 0.0, 100.0, 100.0],
        "exclude_for_structure": excluded,
        "fintabnet_source_file_name": source_file,
        "fintabnet_source_line_index": line_index,
        "fintabnet_source_table_id": table_id,
        "cells": [{"row_nums": [r], "column_nums": [c],
                   "is_column_header": r == 0,
                   "pdf_text_content": f"cell {r}.{c}"}
                  for r in range(2) for c in range((cells + 1) // 2)][:cells],
    }


def _zip_bytes(members: list[tuple[str, bytes]]) -> bytes:
    import io
    import zipfile

    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, blob in members:
            archive.writestr(name, blob)
    return out.getvalue()


def _to_zip64(data: bytes) -> bytes:
    """The same archive with a zip64 end of directory, like the mirror's.

    The rig's archive needs the zip64 records -- 89,653 entries, sizes past
    4 GB -- and an implementation whose zip64 path nothing exercised would
    meet it for the first time on a paid machine. So the small synthetic
    archive is rewritten: every size and offset maxed out with its true value
    in a zip64 extra field, then a zip64 end record, a locator and an end with
    sentinels. What `open_archive` makes of either spelling must be identical.
    """
    eocd_at = len(data) - 22
    assert data[eocd_at:eocd_at + 4] == b"PK\x05\x06"
    (_disk, _cd_disk, entries, _total, cd_size, cd_off,
     _comment) = struct.unpack("<HHHHIIH", data[eocd_at + 4:eocd_at + 22])
    # Every entry grows by one zip64 extra record; the directory starts where
    # it did and the ends are appended after it.
    directory = bytearray()
    pos = cd_off
    for _ in range(entries):
        assert data[pos:pos + 4] == b"PK\x01\x02"
        head = bytearray(data[pos:pos + 46])
        (_made, _need, _flag, _method, _time, _date, _crc, csize, usize,
         name_len, extra_len, comment_len, _disk_n, _iattr, _eattr,
         offset) = struct.unpack("<HHHHHHIIIHHHHHII", head[4:46])
        name = data[pos + 46:pos + 46 + name_len]
        extra = data[pos + 46 + name_len:pos + 46 + name_len + extra_len]
        comment = data[pos + 46 + name_len + extra_len:
                       pos + 46 + name_len + extra_len + comment_len]
        extra = (extra + struct.pack("<HHQQQ", 0x0001, 24, usize, csize,
                                     offset))
        struct.pack_into("<II", head, 20, 0xFFFFFFFF, 0xFFFFFFFF)
        struct.pack_into("<H", head, 30, len(extra))
        struct.pack_into("<I", head, 42, 0xFFFFFFFF)
        directory += head + name + extra + comment
        pos += 46 + name_len + extra_len + comment_len
    new_cd_size = len(directory)
    eocd64_at = cd_off + new_cd_size
    eocd64 = (b"PK\x06\x06" + struct.pack("<QHHIIQQQQ", 44, 51, 45, 0, 0,
                                          entries, entries, new_cd_size,
                                          cd_off))
    locator = (b"PK\x06\x07" + struct.pack("<IQI", 0, eocd64_at, 1))
    eocd = (b"PK\x05\x06" + struct.pack("<HHHHIIH", 0, 0, 0xFFFF, 0xFFFF,
                                        0xFFFFFFFF, 0xFFFFFFFF, 0))
    return data[:cd_off] + bytes(directory) + eocd64 + locator + eocd


def _with_far_offset(data: bytes, offset: int) -> tuple[bytes, str]:
    """The archive with its first entry's offset past 4 GB, and its name.

    Only the zip64 extra field's offset quadword is rewritten -- the file
    stays small while the directory claims far, the way the mirror's own
    entries carry offsets past 4 GB. Reading that member seeks past the end
    on either road.
    """
    out = bytearray(data)
    (eocd64_at,) = struct.unpack("<Q", out[len(out) - 34:len(out) - 26])
    (size64,) = struct.unpack("<Q", out[eocd64_at + 4:eocd64_at + 12])
    wide = out[eocd64_at:eocd64_at + 12 + size64]
    (_v_made, _v_need, _d1, _d2, _disk_entries, _total, _cd_size,
     cd_off) = struct.unpack("<HHIIQQQQ", wide[12:56])
    pos = cd_off
    assert out[pos:pos + 4] == b"PK\x01\x02"
    (_made, _need, flag, _method, _time, _date, _crc, csize, usize,
     name_len, extra_len, _comment_len, _disk, _iattr, _eattr,
     _off) = struct.unpack("<HHHHHHIIIHHHHHII", out[pos + 4:pos + 46])
    name = out[pos + 46:pos + 46 + name_len]
    extra_at = pos + 46 + name_len
    at = extra_at
    while at + 4 <= extra_at + extra_len:
        tag, size = struct.unpack("<HH", out[at:at + 4])
        if tag == 0x0001:
            # The offset quadword follows whichever sizes are present.
            skip = 0
            if usize == 0xFFFFFFFF:
                skip += 8
            if csize == 0xFFFFFFFF:
                skip += 8
            struct.pack_into("<Q", out, at + 4 + skip, offset)
            break
        at += 4 + size
    else:
        raise AssertionError("the first entry carries no zip64 extra field")
    return (bytes(out),
            name.decode("utf-8" if flag & 0x800 else "cp437"))


def self_test() -> int:
    import io
    import json
    import pathlib
    import tarfile
    import tempfile

    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    import pubtables_cells  # noqa: E402
    import fintabnet_split  # noqa: E402

    failures: list[str] = []

    def check(name: str, got, want, why: str) -> None:
        if got != want:
            failures.append(f"{name}: got {got!r}, want {want!r} -- {why}")

    def check_raises(name: str, call, text: str, why: str) -> None:
        try:
            call()
        except (MemberRefused, TransportFailed, SystemExit) as exc:
            if text not in str(exc):
                failures.append(f"{name}: refused, but not about {text!r}: "
                                f"{exc} -- {why}")
            return
        failures.append(f"{name}: stayed silent -- {why}")

    def check_raises_kind(name: str, call, kind: type, text: str,
                          why: str) -> None:
        """The refusal carries its kind, which is what the caller counts by."""
        try:
            call()
        except (MemberRefused, TransportFailed, SystemExit) as exc:
            if type(exc) is not kind:
                failures.append(
                    f"{name}: refused as {type(exc).__name__}, want "
                    f"{kind.__name__} -- {why}")
            elif text not in str(exc):
                failures.append(f"{name}: refused, but not about {text!r}: "
                                f"{exc} -- {why}")
            return
        failures.append(f"{name}: stayed silent -- {why}")

    # --- the fixture: 3 PDFs, a LICENSE.txt, one jsonl ----------------------
    # BA is eval, JPM is train (scripts/ci/test_fintabnet_split.py freezes
    # both); the=jsonl line the annotation points at names BA's page.
    provenance = (b'{"table_id": 7, "filename": "BA/2020/page_1.pdf"}\n'
                  b'{"table_id": 8, "filename": "BA/2020/page_2.pdf"}\n')
    members = [
        ("fintabnet/pdf/BA/2020/page_1.pdf", b"%PDF-1.4 one"),
        ("fintabnet/pdf/BA/2020/page_2.pdf", b"%PDF-1.4 two"),
        ("fintabnet/pdf/BA/2021/page_1.pdf", b"%PDF-1.4 three"),
        ("fintabnet/LICENSE.txt",
         (LICENSE_FIRST_LINE + "\nthe rest of the agreement\n").encode()),
        ("fintabnet/FinTabNet_1.0.0_cell_val.jsonl", provenance),
    ]
    blob = _zip_bytes(members)
    archive = open_archive(_bytes_reader(blob), len(blob))
    check("every member is in the directory", len(archive.entries), 5,
          "3 PDFs, the LICENSE and one jsonl")
    check_set(archive, 3)

    key = ("BA", "2020", 1)
    annotation = [_table(5, "FinTabNet_1.0.0_cell_val.jsonl", 0, 7)]
    kept = kept_tables(annotation)
    check("an exclude_for_structure=false table is kept", len(kept), 1, "")
    by_page = pubtables_cells.tables_by_page(kept)
    check("and it passes the floor of 4 cells", len(by_page.get(0, [])), 1,
          "PubTables-1M's floor, reused unchanged")
    page = archive.fetch(zip_path_of(*key))
    check("the page reads back", page, b"%PDF-1.4 one",
          "local header plus compressed data, inflated")
    cache = JsonlCache(archive)
    check_provenance(*key, kept[0], cache)
    caps = Caps()
    check("and the caps take it", caps.take("BA", "BA_2020"), True,
          "the first page of a report is always room")

    # --- the same archive in the zip64 spelling ------------------------------
    # What open_archive makes of either spelling must be identical, because
    # the mirror needs the zip64 records and this is the only place that path
    # is exercised before the rig.
    wide = _to_zip64(blob)
    try:
        archive64 = open_archive(_bytes_reader(wide), len(wide))
    except SystemExit as exc:
        failures.append(f"the zip64 spelling is refused: {exc} -- the rig's "
                        "archive needs the zip64 records")
        archive64 = None
    if archive64 is not None:
        check("the zip64 directory names the same members",
              sorted(archive64.entries), sorted(archive.entries), "")
        check("and the page reads back from it too",
              archive64.fetch(zip_path_of(*key)), b"%PDF-1.4 one", "")
        check_set(archive64, 3)

    # --- transport is not data -------------------------------------------
    check("TransportFailed is not a MemberRefused",
          issubclass(TransportFailed, MemberRefused), False,
          "catching it as a refusal is how the dump wrote 750 rate-limited "
          "pages off as bad data")
    check("five tries per range read", READ_TRIES, 5,
          "the first ask plus four retries")
    silent = Archive(lambda start, end: None, archive.entries)
    check_raises_kind("a member the transport never answered",
                      lambda: silent.fetch(zip_path_of(*key)),
                      TransportFailed, "local header",
                      "a transport failure is retried and counted apart, "
                      "never refused")
    check_raises_kind("a provenance file the transport never answered",
                      lambda: JsonlCache(silent).lines(
                          "FinTabNet_1.0.0_cell_val.jsonl"),
                      TransportFailed, "local header",
                      "wrapping it as a mismatch is how 61 rate-limited "
                      "pages were counted as bad annotations")

    # --- a 429 is a queue, not an answer ---------------------------------
    class _Answer:
        """A canned HTTP answer: its status, its body, and whether it was
        read -- a 200 must never be, because behind it sits 16 GB."""

        def __init__(self, status: int, body: bytes, reads: list,
                     content_range: str | None = None) -> None:
            self.status = status
            self._body = body
            self._reads = reads
            self._content_range = content_range

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self) -> bytes:
            self._reads.append(self.status)
            return self._body

        def getheader(self, name: str) -> str | None:
            if name.lower() == "content-range":
                return self._content_range
            return None

    def _scripted(script, calls: list, reads: list):
        """A urlopen that plays `script`: ("answer", status, body) or
        ("answer", status, body, content_range), or ("raise", exc), one entry
        per call."""

        pending = list(script)

        def urlopen(request, timeout=None):
            calls.append(request.get_header("Range"))
            action = pending.pop(0)
            if action[0] == "raise":
                raise action[1]
            ranged = action[3] if len(action) > 3 else None
            return _Answer(action[1], action[2], reads,
                           content_range=ranged)

        return urlopen

    def _http_error(code: int):
        return urllib.error.HTTPError("https://example.invalid/archive.zip",
                                      code, "canned", {}, None)

    calls, reads, waits = [], [], []
    reader = RangeReader(
        "https://example.invalid/archive.zip",
        urlopen=_scripted([("raise", _http_error(429)),
                           ("raise", _http_error(429)),
                           ("answer", 206, b"0123456789")], calls, reads),
        sleep=waits.append)
    check("two 429s then a 206 read back",
          reader.read(0, 9), b"0123456789",
          "a CDN that is rate-limiting sixty-four workers answers the "
          "third ask")
    check("in three tries", len(calls), 3, "")
    check("with two waits", len(waits), 2, "between the tries, not after")
    bounded = all(0.0 < wait <= 2.0 ** at + 1.0
                  for at, wait in enumerate(waits))
    check("the waits back off, jittered", bounded, True,
          f"got {waits!r}: `2 ** attempt` seconds plus up to one")

    calls, reads, waits = [], [], []
    reader = RangeReader(
        "https://example.invalid/archive.zip",
        urlopen=_scripted([("raise", _http_error(429))] * READ_TRIES,
                          calls, reads),
        sleep=waits.append)
    check("five 429s give up", reader.read(0, 9), None,
          "five tries, not forever")
    check("after five tries", len(calls), 5, "")
    check("with four waits", len(waits), 4, "")

    calls, reads, waits = [], [], []
    reader = RangeReader(
        "https://example.invalid/archive.zip",
        urlopen=_scripted([("raise", _http_error(404))], calls, reads),
        sleep=waits.append)
    check("a 404 fails at once", reader.read(0, 9), None,
          "asking again cannot change it")
    check("in one try", len(calls), 1, "")
    check("with no wait", waits, [], "")

    calls, reads, waits = [], [], []
    reader = RangeReader(
        "https://example.invalid/archive.zip",
        urlopen=_scripted([("answer", 200, b"0123456789")], calls, reads),
        sleep=waits.append)
    check("a 200 fails at once", reader.read(0, 9), None,
          "the whole file is not the slice")
    check("and its body is never read", reads, [],
          "reading it would be the download this module avoids")

    calls, reads, waits = [], [], []
    reader = RangeReader(
        "https://example.invalid/archive.zip",
        urlopen=_scripted([("answer", 206, b"012"),
                           ("answer", 206, b"0123456789")], calls, reads),
        sleep=waits.append)
    check("a short body is asked again",
          reader.read(0, 9), b"0123456789",
          "the caller asked for an exact slice")
    check("in two tries", len(calls), 2, "")

    calls, reads, waits = [], [], []
    reader = RangeReader(
        "https://example.invalid/archive.zip",
        urlopen=_scripted(
            [("raise", urllib.error.URLError("the connection dropped")),
             ("answer", 206, b"0123456789")], calls, reads),
        sleep=waits.append)
    check("a dropped connection is asked again",
          reader.read(0, 9), b"0123456789", "")
    check("in two tries", len(calls), 2, "")
    check("after one counted retry", reader.transport_retries, 1,
          "the round's evidence counts every retry the reader took")

    # --- the size without the download -----------------------------------
    calls, reads, waits = [], [], []
    reader = RangeReader(
        "https://example.invalid/archive.zip",
        urlopen=_scripted(
            [("answer", 206, b"0", "bytes 0-0/16776878936")],
            calls, reads),
        sleep=waits.append)
    check("a 206 with a Content-Range total answers the size",
          reader.size(), 16776878936,
          "one byte asked for, only its total kept")
    check("asking for one byte", calls, ["bytes=0-0"], "")
    check("with no retry counted", reader.transport_retries, 0, "")

    calls, reads, waits = [], [], []
    reader = RangeReader(
        "https://example.invalid/archive.zip",
        urlopen=_scripted([("raise", _http_error(429)),
                           ("raise", _http_error(429)),
                           ("answer", 206, b"0", "bytes 0-0/42")],
                          calls, reads),
        sleep=waits.append)
    check("two 429s then a 206 answer the size",
          reader.size(), 42,
          "a 429 here is the same queue as a 429 on a read")
    check("after two counted retries", reader.transport_retries, 2, "")

    calls, reads, waits = [], [], []
    reader = RangeReader(
        "https://example.invalid/archive.zip",
        urlopen=_scripted([("raise", _http_error(404))], calls, reads),
        sleep=waits.append)
    check("a 404 answers no size", reader.size(), None,
          "asking again cannot change it")
    check("in one try", len(calls), 1, "")

    calls, reads, waits = [], [], []
    reader = RangeReader(
        "https://example.invalid/archive.zip",
        urlopen=_scripted([("answer", 206, b"0", "bytes 0-0/*")],
                          calls, reads),
        sleep=waits.append)
    check("a 206 with no total answers no size", reader.size(), None,
          "a total of `*` cannot be asked into sense")
    check("in one try", len(calls), 1, "")

    calls = []
    failing = Archive(RangeReader(
        "https://example.invalid/archive.zip",
        urlopen=_scripted([("raise", _http_error(429))] * READ_TRIES,
                          calls, []),
        sleep=lambda delay: None).read, archive.entries)
    check_raises_kind("a 429 past the retries",
                      lambda: failing.fetch(zip_path_of(*key)),
                      TransportFailed, "local header",
                      "the retries spent is a transport failure, never a "
                      "refusal")
    check("after five tries", len(calls), 5, "")

    # --- the mirror reads the same bytes -----------------------------------
    def _serving(data: bytes, calls: list):
        """A urlopen that serves `data` with honest 206 answers."""

        def urlopen(request, timeout=None):
            header = request.get_header("Range")
            calls.append(header)
            start_s, end_s = header[len("bytes="):].split("-")
            return _Answer(206, data[int(start_s):int(end_s) + 1], [])

        return urlopen

    with tempfile.TemporaryDirectory() as raw:
        mirror = pathlib.Path(raw) / "mirror.zip"
        mirror.write_bytes(blob)
        local = LocalFileReader(mirror)
        try:
            served = RangeReader(
                "https://example.invalid/mirror.zip",
                urlopen=_serving(blob, []), sleep=lambda delay: None)
            via_local = open_archive(local.read, len(blob))
            via_range = open_archive(served.read, len(blob))
            check("the local reader names the same members",
                  sorted(via_local.entries), sorted(via_range.entries),
                  "the same central-directory code, fed by seek")
            check("at the same offsets and sizes",
                  {n: (e.offset, e.csize, e.usize)
                   for n, e in via_local.entries.items()},
                  {n: (e.offset, e.csize, e.usize)
                   for n, e in via_range.entries.items()}, "")
            check("and every member reads the same",
                  {n: via_local.fetch(n) for n in sorted(via_local.entries)},
                  {n: via_range.fetch(n) for n in sorted(via_range.entries)},
                  "seek plus read, never HTTP")
        finally:
            local.close()

        # One entry's offset past 4 GB, carried in its zip64 extra field the
        # way the mirror's own entries carry theirs: the file stays small,
        # the directory claims far, and both readers must parse it alike.
        far, far_name = _with_far_offset(wide, 5_000_000_000)
        check("the far entry is the first page",
              far_name, zip_path_of(*key), "")
        far_path = pathlib.Path(raw) / "far.zip"
        far_path.write_bytes(far)
        local = LocalFileReader(far_path)
        try:
            served = RangeReader(
                "https://example.invalid/far.zip",
                urlopen=_serving(far, []), sleep=lambda delay: None)
            via_local = open_archive(local.read, len(far))
            via_range = open_archive(served.read, len(far))
            check("a zip64 offset past 4 GB parses locally",
                  via_local.entries[far_name].offset, 5_000_000_000, "")
            check("and by range",
                  via_range.entries[far_name].offset, 5_000_000_000, "")
            for road, opened in (("locally", via_local),
                                 ("by range", via_range)):
                check_raises_kind(
                    f"the far member fails {road} as transport",
                    lambda opened=opened: opened.fetch(far_name),
                    TransportFailed, "local header",
                    "past the end is unreadable on either road")
            check("while its near siblings still read locally",
                  via_local.fetch(zip_path_of("BA", "2020", 2)),
                  b"%PDF-1.4 two",
                  "one far member refuses one member, not the set")
            check_set(via_local, 3)
            check_set(via_range, 3)
        finally:
            local.close()

    # --- a wrong CRC32 is refused, never a page --------------------------------
    entry = archive.entries[zip_path_of(*key)]
    head = blob[entry.offset:entry.offset + 30]
    name_len, extra_len = struct.unpack("<HH", head[26:30])
    data_at = entry.offset + 30 + name_len + extra_len
    corrupt = bytearray(blob)
    corrupt[data_at + 2] ^= 0xFF
    bad = open_archive(_bytes_reader(bytes(corrupt)), len(corrupt))
    check_raises_kind("a page whose bytes do not match the directory",
                      lambda: bad.fetch(zip_path_of(*key)), CRCRefused,
                      "CRC32",
                      "a mismatch is a refusal with a reason, never a page")
    check("while its siblings still read",
          bad.fetch("fintabnet/pdf/BA/2020/page_2.pdf"), b"%PDF-1.4 two",
          "one corrupt member refuses one page, not the set")

    # --- the LICENSE and the count refuse the whole set -----------------------
    no_licence = _zip_bytes([m for m in members if m[0] != LICENSE_PATH])
    check_raises("an archive with no LICENSE.txt refuses the set",
                 lambda: check_set(open_archive(
                     _bytes_reader(no_licence), len(no_licence)), 3),
                 "no fintabnet/LICENSE.txt",
                 "without IBM's own terms beside the pages there is no set")
    other_terms = _zip_bytes(
        [(LICENSE_PATH, b"Some Other License 2.0\nrest\n") if n == LICENSE_PATH
         else (n, b) for n, b in members])
    check_raises("a LICENSE that reads differently refuses the set",
                 lambda: check_set(open_archive(
                     _bytes_reader(other_terms), len(other_terms)), 3),
                 "starts with",
                 "a licence tag on a re-upload is not evidence")
    check_raises("a set whose PDF count moved refuses the set",
                 lambda: check_set(archive, 89646), "89646",
                 "the count is what says these are the pages Microsoft "
                 "annotated")

    # --- a stored member is refused with its method ----------------------------
    import zipfile

    stored = io.BytesIO()
    with zipfile.ZipFile(stored, "w") as plain:
        plain.writestr("fintabnet/pdf/BA/2020/page_1.pdf", b"%PDF-1.4 one",
                       compress_type=zipfile.ZIP_STORED)
    stored_archive = open_archive(_bytes_reader(stored.getvalue()),
                                  len(stored.getvalue()))
    check_raises_kind("a member that is not deflate",
                      lambda: stored_archive.fetch(
                          "fintabnet/pdf/BA/2020/page_1.pdf"), MemberRefused,
                      "method 0",
                      "the archive is method 8 throughout; anything else is "
                      "refused rather than guessed at")

    # --- provenance: a line naming another table is refused --------------------
    wrong_id = [_table(5, "FinTabNet_1.0.0_cell_val.jsonl", 0, 999)]
    check_raises_kind("a jsonl line with another table_id",
                      lambda: check_provenance(*key, wrong_id[0], cache),
                      ProvenanceRefused, "table_id",
                      "an annotation that is not IBM's line for this page "
                      "grades a tool against somebody else's table")
    wrong_file = [_table(5, "FinTabNet_1.0.0_cell_val.jsonl", 1, 8)]
    check_raises_kind("a jsonl line naming another page",
                      lambda: check_provenance(*key, wrong_file[0], cache),
                      ProvenanceRefused, "BA/2020/page_1.pdf",
                      "the line must be this page's, not only some table's")
    check_raises_kind("a provenance file the archive does not hold",
                      lambda: cache.lines("FinTabNet_1.0.0_cell_train.jsonl"),
                      ProvenanceRefused, "not in the archive", "")
    nameless = [_table(5, "", 0, 7)]
    check_raises_kind("a table naming no provenance file",
                      lambda: check_provenance(*key, nameless[0], cache),
                      ProvenanceRefused, "no fintabnet_source_file_name",
                      "every table of the set names its line in IBM's jsonl")

    # --- a short-read jsonl is re-fetched once, and the register pins it -------
    # Five provenance failures against one file at once mean the file itself
    # was short-read: the caller re-fetches it once via `refresh` rather than
    # failing every page that points into it. Until the register carries a
    # file's size and sha, the directory's CRC32 is the only check.
    import hashlib as _hashlib

    val_file = "FinTabNet_1.0.0_cell_val.jsonl"
    before = list(cache.lines(val_file))
    cache._lines[val_file] = [b'{"table_id": 0}']
    check("a poisoned cache reads poisoned", cache.lines(val_file),
          [b'{"table_id": 0}'],
          "the cache is what every provenance check reads")
    check("and refresh reads the file again", cache.refresh(val_file), before,
          "re-fetching replaces what is cached, once")
    good = JsonlCache(archive, expected={val_file: {
        "bytes": len(provenance), "sha256": _hashlib.sha256(provenance).hexdigest()}})
    check("a file matching the register reads",
          good.lines(val_file), before,
          "size and sha from DATASETS.toml `jsonl_files`")
    check_raises_kind("a file of another size is refused",
                      lambda: JsonlCache(archive, expected={val_file: {
                          "bytes": len(provenance) + 1}}).lines(val_file),
                      ProvenanceRefused, "the register pins",
                      "a short jsonl would fail every page pointing past its end")
    check_raises_kind("a file of another sha is refused",
                      lambda: JsonlCache(archive, expected={val_file: {
                          "bytes": len(provenance), "sha256": "0" * 64}}).lines(val_file),
                      ProvenanceRefused, "the register pins",
                      "a substituted jsonl grades tools against other tables")
    check("and what was fetched is observed",
          good.observed().get(val_file),
          {"bytes": len(provenance),
           "sha256": _hashlib.sha256(provenance).hexdigest()},
          "a healthy fetch reports what the register should pin")

    # --- the synthetic FinTabNet.c tar: two passes, eval only -------------------
    # BA_2020_page_1 is the kept page above; JPM_2019_page_3 is the same
    # annotation under a train company; BA_2020_page_2 holds only an excluded
    # table; BA_2021_page_1 is not JSON at all.
    kept_json = json.dumps(annotation).encode()
    train_json = json.dumps(annotation).encode()
    excluded_json = json.dumps(
        [_table(9, "FinTabNet_1.0.0_cell_val.jsonl", 0, 7, excluded=True)]).encode()
    tar_blob = io.BytesIO()
    with tarfile.open(fileobj=tar_blob, mode="w:gz") as tar:
        for name, payload in [
                ("FinTabNet.c-PDF_Annotations/BA_2020_page_1_tables.json", kept_json),
                ("FinTabNet.c-PDF_Annotations/JPM_2019_page_3_tables.json", train_json),
                ("FinTabNet.c-PDF_Annotations/BA_2020_page_2_tables.json", excluded_json),
                ("FinTabNet.c-PDF_Annotations/BA_2021_page_1_tables.json", b"{not json"),
                ("FinTabNet.c-PDF_Annotations/README.md", b"notes")]:
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            tar.addfile(info, io.BytesIO(payload))
    tar_bytes = tar_blob.getvalue()
    with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r|gz") as tar:
        names = annotation_names(tar)
    check("the first pass names every annotation member", sorted(names),
          [("BA", "2020", 1), ("BA", "2020", 2), ("BA", "2021", 1),
           ("JPM", "2019", 3)],
          "a file that names no page is skipped, not guessed at")
    lane = eval_names(names)
    check("a train company never comes into the lane", sorted(lane),
          [("BA", "2020", 1), ("BA", "2020", 2), ("BA", "2021", 1)],
          "JPM is train; the lane is eval companies only")
    with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r|gz") as tar:
        parsed, unreadable = read_annotations(tar, set(lane.values()))
    check("the second pass reads only the wanted members", sorted(parsed),
          [("BA", "2020", 1), ("BA", "2020", 2)],
          "in the order the archive has them")
    check("and counts what it cannot read", unreadable, 1,
          "one unreadable member refuses one page, not the set")
    check("the kept page comes out of the tar as it went in",
          parsed[("BA", "2020", 1)], annotation, "")
    check("a page of only excluded tables is no candidate",
          pubtables_cells.tables_by_page(
              kept_tables(parsed[("BA", "2020", 2)])), {},
          "nothing above the floor is nothing to grade")

    # --- exclude_for_structure=true tables are left out -------------------------
    mixed = [_table(9, "FinTabNet_1.0.0_cell_val.jsonl", 0, 7, excluded=True),
             _table(5, "FinTabNet_1.0.0_cell_val.jsonl", 0, 7)]
    survivors = kept_tables(mixed)
    check("excluded tables are left out, the rest kept",
          [t is mixed[1] for t in survivors], [True],
          "a table Microsoft excluded from structure is not ground truth")
    check("a page of only excluded tables is no candidate",
          pubtables_cells.tables_by_page(kept_tables(mixed[:1])), {},
          "nothing above the floor is nothing to grade")

    # --- the caps ----------------------------------------------------------------
    # Thirty pages of one company: twenty taken, ten left, however many
    # reports they came on.
    capped = Caps()
    takes = [capped.take("BA", f"BA_{2020 + (i // 3)}") for i in range(30)]
    check("no more than 20 pages per company", sum(takes), 20,
          "one heavy filer must not weigh like a third of the set")
    single = Caps()
    report = [single.take("BA", "BA_2020") for _ in range(6)]
    check("no more than 4 pages per annual report", sum(report), 4, "")
    check("and a full report says it is full", single.room("BA", "BA_2020"),
          False, "checked before the fetch, so no page is read to be refused")
    check("and says which cap is spent",
          (single.full("BA", "BA_2020"), capped.full("BA", "BA_2030"),
           Caps().full("BA", "BA_2020")),
          ("report", "company", None),
          "the caller counts the two caps apart")

    # --- the small readers ---------------------------------------------------------
    check("an annotation member parses to its page",
          page_key_of("FinTabNet.c-PDF_Annotations/FLS_2012_page_67_tables.json"),
          ("FLS", "2012", 67), "")
    check("and a file that names no page is skipped", page_key_of("tables.json"),
          None, "fetching by a guessed key grades a tool on the wrong page")
    check("the page's path in the archive", zip_path_of("FLS", "2012", 67),
          "fintabnet/pdf/FLS/2012/page_67.pdf", "")
    check("the page as IBM's jsonl names it", ibm_filename("FLS", "2012", 67),
          "FLS/2012/page_67.pdf", "")
    check("one annual report is one document", document_of("FLS", "2012"),
          "FLS_2012", "the unit the per-document headline weighs")
    check("the page as the seeded walk ranks it",
          walk_name("FLS", "2012", 67), "FLS_2012_page_67", "")
    check("what every row of the set carries",
          (SOURCE, DOCUMENT_LICENCE, CATEGORY),
          ("fintabnet/ibm-1.0.0", "CDLA-Permissive-1.0", "financial reports"),
          "the values the spec pins, in one place")
    check("the terms the set is refused without", LICENSE_FIRST_LINE,
          "Community Data License Agreement – Permissive – Version 1.0",
          "IBM's own first line, byte for byte")
    check("the pinned archive as one URL",
          documents_url("Leon1207/FinTabNet", "abc123", "archive.zip"),
          "https://huggingface.co/datasets/Leon1207/FinTabNet/resolve/abc123/archive.zip",
          "the commit, not the branch")

    if failures:
        print(f"[{PREFIX}-archive] {len(failures)} check(s) failed\n",
              file=sys.stderr)
        for line in failures:
            print(f"  - {line}", file=sys.stderr)
        return 1
    print("✓ fintabnet archive: the directory reads once by range, a wrong "
          "CRC32 or provenance line is refused with its reason, the caps "
          "hold, a 429 is retried and never a refusal, and the local mirror "
          "reads the same bytes")
    return 0


if __name__ == "__main__":
    sys.exit(self_test() if "--self-test" in sys.argv else self_test())
