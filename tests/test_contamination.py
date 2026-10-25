# Copyright (c) 2026 Innovation Trigger B.V.
#
# PDFluent is available under two licences, at your option: the GNU AGPLv3, or
# the PDFluent Commercial Licence. See the LICENSE file in this repository --
# that file travels with the copy you received, which a URL does not.
"""#968 DoD: a planted DP-Bench duplicate is found, a clean manifest reports nothing."""
import hashlib
import json
import os
import pathlib
import subprocess

from bench import contamination as c
from skipping import skip

ROOT = pathlib.Path(__file__).resolve().parents[1]
FINGERPRINTS_SHA256 = "0bc1d19ee8b139dbfe04b160a2ae416685b70fd3ec71149b3958a35d9ffd5916"
KNOWN = "01030000000001.pdf"

CLEAN = [
    {"name": "doclaynet_0001.png", "sha256": hashlib.sha256(b"not a dp-bench file").hexdigest()},
    {"name": "PMC123456_1.jpg", "text": "an unrelated paragraph about renal physiology " * 20},
    {"name": "short.pdf", "text": "too short"},
]


def reference():
    return c.load_reference()


def test_reference_is_pinned_and_agrees_with_the_corpus_manifest():
    # A changed corpus or fingerprint file invalidates the verdict: fail here, loudly.
    assert hashlib.sha256((ROOT / "odl-bench" / "fingerprints.csv").read_bytes()).hexdigest() == FINGERPRINTS_SHA256
    pdfs = json.loads((ROOT / "odl-bench" / "manifest.json").read_text())["pdfs"]
    assert {r["file"]: r["sha256"] for r in reference()} == pdfs


def test_clean_manifest_reports_no_match():
    assert c.match(CLEAN, reference()) == []


def test_planted_copy_renamed_is_found_by_hash():
    row = next(r for r in reference() if r["file"] == KNOWN)
    planted = CLEAN + [{"name": "doclaynet_planted_page.pdf", "sha256": row["sha256"]}]
    found = c.match(planted, reference())
    assert [(f["entry"], f["dp_bench"], f["rule"]) for f in found] == [
        ("doclaynet_planted_page.pdf", KNOWN, "sha256")]


def test_planted_text_is_found_when_the_bytes_differ():
    # Same opening text, different bytes (a re-rendered copy): only the text rule can see it.
    text = "contamination planted text " * 30
    ref = reference() + [{"file": "synthetic.pdf", "sha256": "0" * 64, "norm_chars": len(c.normalise(text)),
                          "head200_sha256": c.head200_sha256(text)}]
    found = c.match(CLEAN + [{"name": "rerendered.pdf", "text": text.upper()}], ref)
    assert [(f["dp_bench"], f["rule"]) for f in found] == [("synthetic.pdf", "head200")]


def test_a_short_text_is_not_matched_on_its_opening():
    text = "tiny"
    ref = reference() + [{"file": "scan.pdf", "sha256": "1" * 64, "norm_chars": 4,
                          "head200_sha256": c.head200_sha256(text)}]
    assert c.match([{"name": "x", "text": text}], ref) == []


def test_the_real_pdf_text_route_finds_a_planted_dp_bench_document():
    clone = os.environ.get("ODL_CLONE")
    pdf = pathlib.Path(clone or "/nonexistent") / "pdfs" / KNOWN
    if not pdf.exists():
        skip("ODL_CLONE not set or has no pdfs/: the real-PDF plant needs the pinned odl-bench clone")
    text = subprocess.run(["pdftotext", "-l", "3", "-enc", "UTF-8", str(pdf), "-"],
                          capture_output=True, text=True, check=True).stdout
    found = c.match(CLEAN + [{"name": "planted.txt", "text": text}], reference())
    assert [(f["dp_bench"], f["rule"]) for f in found] == [(KNOWN, "head200")]


def test_cli_exit_codes(tmp_path, capsys):
    row = next(r for r in reference() if r["file"] == KNOWN)
    clean, dirty = tmp_path / "clean.json", tmp_path / "dirty.json"
    clean.write_text(json.dumps(CLEAN))
    dirty.write_text(json.dumps(CLEAN + [{"name": "p.pdf", "sha256": row["sha256"]}]))
    assert c.main(["--manifest", str(clean)]) == 0
    assert c.main(["--manifest", str(dirty)]) == 1
    assert f"MATCH p.pdf == DP-Bench {KNOWN} (sha256)" in capsys.readouterr().out
