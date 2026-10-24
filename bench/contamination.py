# Copyright (c) 2026 Innovation Trigger B.V.
#
# PDFluent is available under two licences, at your option: the GNU AGPLv3, or
# the PDFluent Commercial Licence. See the LICENSE file in this repository --
# that file travels with the copy you received, which a URL does not.
"""Contamination matcher (#968): do the DP-Bench documents appear in a training manifest?

The reference is odl-bench/fingerprints.csv (SHA-256 of each PDF, SHA-256 of the first 200
normalised characters of its first three pages). A manifest is a JSON list of entries with any of
`name`, `sha256`, `head200_sha256`, `text`. An entry matches a DP-Bench document when:

  sha256    the file bytes are identical (renaming does not hide it)
  head200   the normalised opening text is identical (re-encoded or re-rendered copy)
  text      same as head200 but computed here from `text`

Documents with fewer than 300 normalised characters (scanned, no text layer) are excluded from the
head200 rule: their opening text is too short to identify anything. They still match on sha256.

    python -m bench.contamination --manifest manifest.json [--reference odl-bench/fingerprints.csv]

Exit 0: no match. Exit 1: at least one match, listed on stdout.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import pathlib
import re
import sys
import unicodedata

ROOT = pathlib.Path(__file__).resolve().parents[1]
REFERENCE = ROOT / "odl-bench" / "fingerprints.csv"
MIN_CHARS = 300
HEAD = 200
SHINGLE = 8


def normalise(text: str) -> str:
    """NFKC, lowercase, non-word characters to spaces, whitespace collapsed (same as the inventory)."""
    text = unicodedata.normalize("NFKC", text).lower()
    return re.sub(r"\s+", " ", re.sub(r"\W+", " ", text)).strip()


def head200_sha256(text: str) -> str:
    return hashlib.sha256(normalise(text)[:HEAD].encode("utf-8")).hexdigest()


def shingles(text: str) -> set[str]:
    words = normalise(text).split(" ")
    return {" ".join(words[i:i + SHINGLE]) for i in range(max(0, len(words) - SHINGLE + 1))}


def load_reference(path: pathlib.Path = REFERENCE) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        r["norm_chars"] = int(r["norm_chars"])
    return rows


def match(entries: list[dict], reference: list[dict]) -> list[dict]:
    by_sha = {r["sha256"]: r for r in reference}
    by_head = {r["head200_sha256"]: r for r in reference if r["norm_chars"] >= MIN_CHARS}
    found = []
    for i, e in enumerate(entries):
        label = e.get("name") or f"entry[{i}]"
        hits = []
        if e.get("sha256") in by_sha:
            hits.append(("sha256", by_sha[e["sha256"]]))
        head = e.get("head200_sha256")
        if head is None and e.get("text") and len(normalise(e["text"])) >= MIN_CHARS:
            head = head200_sha256(e["text"])
        if head in by_head:
            hits.append(("head200", by_head[head]))
        for rule, ref in hits:
            found.append({"entry": label, "dp_bench": ref["file"], "rule": rule})
    return found


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="bench.contamination")
    ap.add_argument("--manifest", required=True, type=pathlib.Path)
    ap.add_argument("--reference", type=pathlib.Path, default=REFERENCE)
    args = ap.parse_args(argv)
    found = match(json.loads(args.manifest.read_text(encoding="utf-8")), load_reference(args.reference))
    for f in found:
        print(f"MATCH {f['entry']} == DP-Bench {f['dp_bench']} ({f['rule']})")
    print(f"{len(found)} match(es)")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
