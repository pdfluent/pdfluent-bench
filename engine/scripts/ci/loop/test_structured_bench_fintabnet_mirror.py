#!/usr/bin/env python3
# Copyright (c) 2026 Innovation Trigger B.V.
#
# PDFluent is available under two licences, at your option: the GNU AGPLv3, or
# the PDFluent Commercial Licence. See the LICENSE file in this repository --
# that file travels with the copy you received, which a URL does not.
"""FinTabNet reads the archive from our GCS mirror when given (#574, T3).

Range reads of IBM's archive.zip through the HuggingFace CDN fail under load
(likely 429), and a throttled round once lost 19,100 of 20,000 tables to what
read as data refusals. So the rig reads the server-side copy through a signed
URL -- env `FINTABNET_ARCHIVE_URL` first, then the file
`/opt/ex/fintabnet/archive.url` -- and the HF URL as today without either.

The rules under test, each red without the change:

  1. a transport that answers 429 twice and then 206 keeps the page, the
     refusals stay empty, and the evidence counts the two retries.
  2. a transport that always answers 429 fails the fetch at once -- one
     attempt, no backoff sleep -- naming the page and "transport", never
     "provenance" or a data refusal.
  3. a mirror whose size differs from the register is refused before any page
     is read, naming both sizes.
  4. the signed URL never appears in stdout, stderr, or the evidence files.
  5. with no mirror configured the source is "hf-cdn" and the fetch walks as
     before.
  6. the file alone configures the mirror, and an empty file falls through.
  7. the seal carries the source and the retry count from the fetch evidence.

Off the rig and off the network: a fake signed URL, injected failures, and
the pinned suite's own fixtures (`_fixtures`, `_Harness`) driven through
`fetch_fintabnet`, the same public path the round takes.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import pathlib
import sys
import tempfile
import urllib.error

REPO = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "scripts" / "ci" / "loop"))
sys.path.insert(0, str(REPO / "scripts" / "extraction"))
sys.path.insert(0, str(REPO / "scripts" / "ci"))

import structured_bench as bench  # noqa: E402
import structured_holdout_rule as holdout_rule  # noqa: E402
import fintabnet_archive as archive_mod  # noqa: E402
import test_structured_bench_fintabnet_pinned as pinned  # noqa: E402

# A signed URL that signs nothing: the readers below serve the fixture bytes
# whatever the address says, so no test here needs the network. Distinctive
# on purpose -- case 4 greps every output for it.
MIRROR_URL = ("https://storage.googleapis.com/fintabnet-mirror-test/"
              "fintabnet_1.0.0.zip?X-Goog-Signature=MIRROR-TOKEN-9f31")


@contextlib.contextmanager
def _mirror_config(url: str | None, url_file: pathlib.Path):
    """The env var and the url file as the case wants them, restored after."""
    # getattr, as the pinned suite does: on the old code these names do not
    # exist, and the cases below must fail on their assertions (red) rather
    # than on an AttributeError.
    env_name = getattr(bench, "FINTABNET_ARCHIVE_URL_ENV",
                       "FINTABNET_ARCHIVE_URL")
    saved_env = os.environ.get(env_name)
    saved_file = getattr(bench, "FINTABNET_ARCHIVE_URL_FILE", None)
    try:
        if url is None:
            os.environ.pop(env_name, None)
        else:
            os.environ[env_name] = url
        bench.FINTABNET_ARCHIVE_URL_FILE = (  # type: ignore[attr-defined]
            url_file)
        yield
    finally:
        if saved_env is None:
            os.environ.pop(env_name, None)
        else:
            os.environ[env_name] = saved_env
        if saved_file is None:
            try:
                delattr(bench, "FINTABNET_ARCHIVE_URL_FILE")
            except AttributeError:
                pass
        else:
            bench.FINTABNET_ARCHIVE_URL_FILE = saved_file


@contextlib.contextmanager
def _reader(reader_cls):
    """`RangeReader` as the case wants it, restored after (in `_Harness`)."""
    saved = archive_mod.RangeReader
    archive_mod.RangeReader = reader_cls  # type: ignore[assignment]
    try:
        yield
    finally:
        archive_mod.RangeReader = saved


_OWN_SIZE = object()


def _serving_reader(zip_bytes: bytes, seen_urls: list | None = None,
                    size=_OWN_SIZE, retries: int = 0):
    """A reader class serving `zip_bytes`, with the mirror's size().

    `size` defaults to the fixture's own length; pass another total -- or
    None, for a mirror that does not answer its size -- to misidentify the
    mirror. Appends the built URL to `seen_urls`, proving which source the
    fetch read.
    """
    total = len(zip_bytes) if size is _OWN_SIZE else size

    class _Serving:
        transport_retries = retries

        def __init__(self, url: str, timeout: int = 120) -> None:
            if seen_urls is not None:
                seen_urls.append(url)

        def read(self, start: int, end: int):
            return zip_bytes[start:end + 1]

        def size(self):
            return total

    return _Serving


class _Answer:
    """A canned 206: its slice, and the total its Content-Range names."""

    def __init__(self, body: bytes, total: int) -> None:
        self.status = 206
        self._body = body
        self._ranged = f"bytes 0-{len(body) - 1}/{total}"

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self) -> bytes:
        return self._body

    def getheader(self, name: str) -> str | None:
        if name.lower() == "content-range":
            return self._ranged
        return None


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(MIRROR_URL, code, "canned", {}, None)


def case_throttled_mirror_keeps_the_page() -> list[str]:
    """429 twice then 206: the page is kept, the refusals stay empty."""
    failures: list[str] = []
    pages = pinned._six_pages()
    tar_bytes, zip_bytes, _annotations = pinned._fixtures(pages)
    with tempfile.TemporaryDirectory(prefix="ftn-mirror-") as tmp:
        tmpdir = pathlib.Path(tmp)
        harness = pinned._Harness(tmpdir, tar_bytes, zip_bytes,
                                  len(pages), 3)
        with harness:
            with _mirror_config(None, tmpdir / "no-url-file"):
                clean = [r["name"] for r in harness.fetch()]
            if len(clean) != 3:
                return [f"the clean fetch kept {len(clean)} pages, not 3."]
            seen_urls: list[str] = []
            calls = {"n": 0}

            def throttled_twice(request, timeout=None):
                # The rig under load: two queued asks, then honest 206s.
                calls["n"] += 1
                if calls["n"] <= 2:
                    raise _http_error(429)
                header = request.get_header("Range")
                start_s, end_s = header[len("bytes="):].split("-")
                start, end = int(start_s), int(end_s)
                return _Answer(zip_bytes[start:end + 1], len(zip_bytes))

            # The real reader, so the retries are the reader's own; the
            # harness saved it aside when it installed its fake.
            real_reader = harness.saved["reader"]

            class _Throttled(real_reader):  # type: ignore[valid-type,misc]
                def __init__(self, url: str, timeout: int = 120) -> None:
                    seen_urls.append(url)
                    super().__init__(url, timeout=timeout,
                                     urlopen=throttled_twice,
                                     sleep=lambda _d: None)

            with _mirror_config(MIRROR_URL, tmpdir / "no-url-file"):
                with _reader(_Throttled):
                    kept = [r["name"] for r in harness.fetch()]
            if kept != clean:
                failures.append(
                    f"a mirror that queued twice kept {kept}, the clean "
                    f"fetch kept {clean} -- the retry substituted the next "
                    "candidate instead of re-reading.")
            if seen_urls != [MIRROR_URL]:
                failures.append(
                    f"the reader was built for {seen_urls}, not the mirror "
                    "URL -- the signed URL was not used.")
            refusals = json.loads(
                (harness.work / "fetch-refusals.json").read_text())
            if refusals.get("refused") != {}:
                failures.append(
                    f"a kept page left refusals {refusals.get('refused')}, "
                    "not empty.")
            if refusals.get("source") != "gcs-mirror":
                failures.append(
                    f"the evidence records source "
                    f"{refusals.get('source')!r}, not 'gcs-mirror'.")
            if refusals.get("transport_retries") != 2:
                failures.append(
                    "the evidence counts "
                    f"{refusals.get('transport_retries')} transport "
                    "retries, not the two queued asks.")
            kept_meta = json.loads(
                (harness.work / "fetch-kept.json").read_text())
            if kept_meta.get("source") != "gcs-mirror":
                failures.append(
                    "fetch-kept.json records source "
                    f"{kept_meta.get('source')!r}, not 'gcs-mirror'.")
    return failures


def case_dead_mirror_fails_at_once() -> list[str]:
    """An always-429 transport fails at once, naming the page and transport."""
    pages = pinned._six_pages()
    tar_bytes, zip_bytes, _annotations = pinned._fixtures(pages)
    with tempfile.TemporaryDirectory(prefix="ftn-mirror-") as tmp:
        tmpdir = pathlib.Path(tmp)
        harness = pinned._Harness(tmpdir, tar_bytes, zip_bytes,
                                  len(pages), 3)
        with harness, _mirror_config(MIRROR_URL, tmpdir / "no-url-file"):
            seen_urls: list[str] = []
            real_fetch = archive_mod.Archive.fetch
            first_pdf: list[str] = []
            member_reads: list[str] = []
            sleeps: list[int] = []
            saved_backoff = bench._sleep_backoff
            bench._sleep_backoff = lambda a: sleeps.append(a)  # noqa: E731

            def fail_first_pdf(self, name: str) -> bytes:
                # What an always-429 reader surfaces as, past the reader's
                # own five tries: the member the transport never answered.
                if name.startswith("fintabnet/pdf/"):
                    member_reads.append(name)
                    if not first_pdf:
                        first_pdf.append(name)
                    if name == first_pdf[0]:
                        raise archive_mod.TransportFailed(
                            "injected: the transport never delivers")
                return real_fetch(self, name)

            archive_mod.Archive.fetch = fail_first_pdf  # type: ignore[assignment]
            try:
                with _reader(_serving_reader(zip_bytes, seen_urls)):
                    try:
                        got = [r["name"] for r in harness.fetch()]
                    except SystemExit as exc:
                        message = str(exc)
                    else:
                        return [f"a dead transport returned {got} instead "
                                "of raising -- the failure was skipped and "
                                "the next candidate substituted."]
            finally:
                archive_mod.Archive.fetch = real_fetch
                bench._sleep_backoff = saved_backoff
            if seen_urls != [MIRROR_URL]:
                return [f"the reader was built for {seen_urls}, not the "
                        "mirror URL -- nothing exercised the mirror road."]
            if not first_pdf:
                return ["the injected failure never fired -- nothing failed."]
            stem = first_pdf[0].rsplit("/", 1)[-1].replace(".pdf", "")
            if stem not in message or ".pdf" not in message:
                return [f"the dead transport raised, but not with the page "
                        f"name ({stem!r} not in {message[:200]!r})."]
            if "transport" not in message:
                return [f"the dead transport raised {message[:200]!r}, "
                        "which does not say transport."]
            for banned in ("provenance", "did not answer"):
                if banned in message:
                    return [f"the dead transport raised {message[:200]!r}, "
                            f"which reads as a data refusal ({banned!r})."]
            if member_reads.count(first_pdf[0]) != 1:
                return [f"the failed member was read "
                        f"{member_reads.count(first_pdf[0])} time(s), not "
                        "once -- the fatal transport retried here, on top "
                        "of the reader's own five tries."]
            if sleeps:
                return [f"the fatal transport slept {len(sleeps)} "
                        "backoff(s) -- the fatal transport retried."]
    return []


def case_wrong_size_mirror_refused() -> list[str]:
    """A mirror of another size is refused before any page is read."""
    failures: list[str] = []
    pages = pinned._six_pages()
    tar_bytes, zip_bytes, _annotations = pinned._fixtures(pages)
    for wrong in (len(zip_bytes) + 1, None):
        with tempfile.TemporaryDirectory(prefix="ftn-mirror-") as tmp:
            tmpdir = pathlib.Path(tmp)
            harness = pinned._Harness(tmpdir, tar_bytes, zip_bytes,
                                      len(pages), 3)
            with harness, _mirror_config(MIRROR_URL, tmpdir / "no-url-file"):
                seen_urls: list[str] = []
                real_fetch = archive_mod.Archive.fetch
                member_reads: list[str] = []

                def count_fetch(self, name: str) -> bytes:
                    member_reads.append(name)
                    return real_fetch(self, name)

                archive_mod.Archive.fetch = count_fetch  # type: ignore[assignment]
                try:
                    with _reader(_serving_reader(zip_bytes, seen_urls,
                                                 size=wrong)):
                        try:
                            got = [r["name"] for r in harness.fetch()]
                        except SystemExit as exc:
                            message = str(exc)
                        else:
                            failures.append(
                                f"a mirror of size {wrong} returned {got} "
                                "instead of refusing -- its bytes were "
                                "trusted.")
                            continue
                finally:
                    archive_mod.Archive.fetch = real_fetch
                if seen_urls != [MIRROR_URL]:
                    failures.append(
                        f"the reader was built for {seen_urls}, not the "
                        "mirror URL.")
                if MIRROR_URL in message or "MIRROR-TOKEN" in message:
                    failures.append("the refusal prints the signed URL.")
                if "GCS mirror" not in message:
                    failures.append(
                        f"the refusal names {message[:160]!r}, not the GCS "
                        "mirror.")
                if wrong is None:
                    if str(len(zip_bytes)) not in message:
                        failures.append(
                            f"the refusal names {message[:160]!r}, not the "
                            "register's size.")
                elif str(wrong) not in message or str(
                        len(zip_bytes)) not in message:
                    failures.append(
                        f"the refusal names {message[:160]!r}, not both "
                        f"sizes ({wrong} and {len(zip_bytes)}).")
                if member_reads:
                    failures.append(
                        f"the refused mirror still had {len(member_reads)} "
                        "member(s) read -- the refusal came after pages.")
                if list((harness.work / "pages").glob("*.pdf")):
                    failures.append("the refused mirror still left pages "
                                    "behind.")
    return failures


def case_url_never_printed() -> list[str]:
    """The signed URL appears in no output and no evidence file."""
    failures: list[str] = []
    pages = pinned._six_pages()
    tar_bytes, zip_bytes, _annotations = pinned._fixtures(pages)
    with tempfile.TemporaryDirectory(prefix="ftn-mirror-") as tmp:
        tmpdir = pathlib.Path(tmp)
        harness = pinned._Harness(tmpdir, tar_bytes, zip_bytes,
                                  len(pages), 3)
        with harness, _mirror_config(MIRROR_URL, tmpdir / "no-url-file"):
            seen_urls: list[str] = []
            out, err = io.StringIO(), io.StringIO()
            with _reader(_serving_reader(zip_bytes, seen_urls)):
                old_out, old_err = sys.stdout, sys.stderr
                sys.stdout, sys.stderr = out, err
                try:
                    kept = [r["name"] for r in harness.fetch()]
                finally:
                    sys.stdout, sys.stderr = old_out, old_err
            if len(kept) != 3:
                return [f"the mirror fetch kept {len(kept)} pages, not 3."]
            if seen_urls != [MIRROR_URL]:
                return [f"the reader was built for {seen_urls}, not the "
                        "mirror URL -- nothing exercised the secret."]
            refusals = json.loads(
                (harness.work / "fetch-refusals.json").read_text())
            if refusals.get("source") != "gcs-mirror":
                failures.append(
                    "the mirror fetch records source "
                    f"{refusals.get('source')!r} -- nothing exercised the "
                    "secret.")
            texts = {
                "stdout": out.getvalue(),
                "stderr": err.getvalue(),
                "fetch-refusals.json":
                    (harness.work / "fetch-refusals.json").read_text(),
                "fetch-kept.json":
                    (harness.work / "fetch-kept.json").read_text(),
            }
            # And a refusal on the mirror road, whose message must name the
            # source without the address.
            with _reader(_serving_reader(zip_bytes, seen_urls)):
                real_fetch = archive_mod.Archive.fetch

                def fail_pdf(self, name: str) -> bytes:
                    if name.startswith("fintabnet/pdf/"):
                        raise archive_mod.TransportFailed(
                            "injected: the transport never delivers")
                    return real_fetch(self, name)

                archive_mod.Archive.fetch = fail_pdf  # type: ignore[assignment]
                old_out, old_err = sys.stdout, sys.stderr
                sys.stdout, sys.stderr = out, err
                try:
                    try:
                        harness.fetch()
                    except SystemExit as exc:
                        texts["transport refusal"] = str(exc)
                    else:
                        failures.append("the dead transport returned "
                                        "instead of raising.")
                finally:
                    sys.stdout, sys.stderr = old_out, old_err
                    archive_mod.Archive.fetch = real_fetch
            texts["stdout"] = out.getvalue()
            texts["stderr"] = err.getvalue()
            for where, text in texts.items():
                for secret in (MIRROR_URL, "MIRROR-TOKEN-9f31",
                               "MIRROR-TOKEN"):
                    if secret in text:
                        failures.append(f"the signed URL leaks into {where}.")
                        break
    return failures


def case_no_mirror_is_hf_cdn() -> list[str]:
    """Without a mirror the source is hf-cdn and the walk is unchanged."""
    failures: list[str] = []
    pages = pinned._six_pages()
    tar_bytes, zip_bytes, _annotations = pinned._fixtures(pages)
    with tempfile.TemporaryDirectory(prefix="ftn-mirror-") as tmp:
        tmpdir = pathlib.Path(tmp)
        harness = pinned._Harness(tmpdir, tar_bytes, zip_bytes,
                                  len(pages), 4)
        with harness, _mirror_config(None, tmpdir / "no-url-file"):
            rows = harness.fetch()
            names = [r["name"] for r in rows]
            if len(names) != 4:
                return [f"a clean fetch kept {len(names)} pages, not 4."]
            refusals = json.loads(
                (harness.work / "fetch-refusals.json").read_text())
            if refusals.get("source") != "hf-cdn":
                failures.append(
                    f"with no mirror the source is "
                    f"{refusals.get('source')!r}, not 'hf-cdn'.")
            if refusals.get("transport_retries") != 0:
                failures.append(
                    "with no mirror the transport retries count "
                    f"{refusals.get('transport_retries')}, not 0.")
            if refusals.get("mode") != "walked, no pin":
                failures.append(
                    f"with no mirror the mode is "
                    f"{refusals.get('mode')!r}, not the walk.")
            kept = json.loads((harness.work / "fetch-kept.json").read_text())
            if [p["name"] for p in kept.get("pages", [])] != names:
                failures.append("fetch-kept.json does not name the kept "
                                "pages in kept order.")
            if kept.get("sha256") != pinned._pin_sha(kept.get("pages", [])):
                failures.append("fetch-kept.json's sha256 is not the "
                                "digest of its own list.")
    return failures


def case_file_configures_mirror() -> list[str]:
    """The url file alone configures the mirror; an empty file does not."""
    failures: list[str] = []
    pages = pinned._six_pages()
    tar_bytes, zip_bytes, _annotations = pinned._fixtures(pages)
    with tempfile.TemporaryDirectory(prefix="ftn-mirror-") as tmp:
        tmpdir = pathlib.Path(tmp)
        url_file = tmpdir / "archive.url"
        url_file.write_text(MIRROR_URL + "\n")
        harness = pinned._Harness(tmpdir, tar_bytes, zip_bytes,
                                  len(pages), 3)
        with harness, _mirror_config(None, url_file):
            seen_urls: list[str] = []
            with _reader(_serving_reader(zip_bytes, seen_urls)):
                kept = [r["name"] for r in harness.fetch()]
            if len(kept) != 3:
                failures.append(
                    f"the file-configured fetch kept {len(kept)} pages, "
                    "not 3.")
            if seen_urls != [MIRROR_URL]:
                failures.append(
                    f"the reader was built for {seen_urls}: the file's URL "
                    "was not used, stripped of its newline.")
            refusals = json.loads(
                (harness.work / "fetch-refusals.json").read_text())
            if refusals.get("source") != "gcs-mirror":
                failures.append(
                    "the file-configured fetch records source "
                    f"{refusals.get('source')!r}.")
        # An empty file is no mirror: the HF URL as today.
        url_file.write_text("\n")
        harness = pinned._Harness(tmpdir, tar_bytes, zip_bytes,
                                  len(pages), 3)
        with harness, _mirror_config(None, url_file):
            kept = [r["name"] for r in harness.fetch()]
            if len(kept) != 3:
                failures.append(
                    f"the empty-file fetch kept {len(kept)} pages, not 3.")
            refusals = json.loads(
                (harness.work / "fetch-refusals.json").read_text())
            if refusals.get("source") != "hf-cdn":
                failures.append(
                    f"an empty url file records source "
                    f"{refusals.get('source')!r}, not 'hf-cdn'.")
    return failures


def case_manifest_carries_source() -> list[str]:
    """The seal carries the source and the retry count from the fetch."""
    failures: list[str] = []
    pages = pinned._six_pages()
    tar_bytes, zip_bytes, _annotations = pinned._fixtures(pages)
    with tempfile.TemporaryDirectory(prefix="ftn-mirror-") as tmp:
        tmpdir = pathlib.Path(tmp)
        harness = pinned._Harness(tmpdir, tar_bytes, zip_bytes,
                                  len(pages), 3)
        with harness, _mirror_config(MIRROR_URL, tmpdir / "no-url-file"):
            seen_urls: list[str] = []
            with _reader(_serving_reader(zip_bytes, seen_urls, retries=2)):
                rows = harness.fetch()
            if seen_urls != [MIRROR_URL]:
                return [f"the reader was built for {seen_urls}, not the "
                        "mirror URL."]
            refusals = json.loads(
                (harness.work / "fetch-refusals.json").read_text())
            if refusals.get("transport_retries") != 2:
                failures.append(
                    "the evidence counts "
                    f"{refusals.get('transport_retries')} transport "
                    "retries, not the reader's 2.")
            # --draw in its own run against the same work dir, the way the
            # round runs it: pages.json and the fetch evidence are read, the
            # seal is written to scratch, never to the repository.
            (harness.work / "pages.json").write_text(json.dumps(rows))
            first = dict(holdout_rule.FIRST_DRAW)
            holdout_rule.FIRST_DRAW["fintabnet"] = tmpdir / "FINTABNET.json"
            saved_argv = sys.argv
            sys.argv = ["structured_bench.py", "--set", "fintabnet",
                        "--draw", "--work", str(harness.work)]
            out = io.StringIO()
            old_out = sys.stdout
            sys.stdout = out
            try:
                if holdout_rule.draw_path_for("fintabnet").parent != tmpdir:
                    return ["draw_path_for(fintabnet) routes outside the "
                            "scratch directory -- refusing to draw into "
                            "the repository."]
                rc = bench.main()
            finally:
                sys.stdout = old_out
                sys.argv = saved_argv
                holdout_rule.FIRST_DRAW.clear()
                holdout_rule.FIRST_DRAW.update(first)
            if rc != 0:
                failures.append(f"--draw exited {rc}, not 0.")
                return failures
            manifest = json.loads((tmpdir / "FINTABNET.json").read_text())
            if manifest.get("fetch_source") != "gcs-mirror":
                failures.append(
                    f"the seal carries fetch_source "
                    f"{manifest.get('fetch_source')!r}, not 'gcs-mirror'.")
            if manifest.get("transport_retries") != 2:
                failures.append(
                    f"the seal carries transport_retries "
                    f"{manifest.get('transport_retries')!r}, not 2.")
            if MIRROR_URL in json.dumps(manifest):
                failures.append("the signed URL leaks into the seal.")
    return failures


def main() -> int:
    failures: list[str] = []
    failures += case_throttled_mirror_keeps_the_page()
    failures += case_dead_mirror_fails_at_once()
    failures += case_wrong_size_mirror_refused()
    failures += case_url_never_printed()
    failures += case_no_mirror_is_hf_cdn()
    failures += case_file_configures_mirror()
    failures += case_manifest_carries_source()
    for line in failures:
        print(f"FAIL {line}")
    if failures:
        print(f"\n{len(failures)} of the mirror's guarantees do not hold.")
        return 1
    print("✓ fintabnet mirror: a queued mirror keeps the page, a dead one "
          "fails at once naming the page and transport, a wrong-sized one "
          "is refused before any page is read, the URL never leaks, no "
          "mirror reads the HF CDN, the file configures, and the seal "
          "carries the source")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
