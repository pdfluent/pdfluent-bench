#!/usr/bin/env python3
# Copyright (c) 2026 Innovation Trigger B.V.
#
# PDFluent is available under two licences, at your option: the GNU AGPLv3, or
# the PDFluent Commercial Licence. See the LICENSE file in this repository --
# that file travels with the copy you received, which a URL does not.
"""Nothing in this repository may point into someone's machine or name a tool.

    scripts/check_public_surface.py [--range REV..REV]

Reads every tracked text file and every commit message (all history, or
--range) and fails on: absolute paths into a home directory, the names of the
AI tools and assistants that were used to work on the code, attribution
trailers, private addresses, and credential-shaped strings.

The tool names are stored encoded, so this file does not itself carry the words
it forbids. Dataset file names that merely contain such a word (a third-party
document called after its publisher) are listed in ALLOWED_PATHS, by path.
A hit prints the file and line, not the matched text, so a log does not
republish it.
"""
from __future__ import annotations

import argparse
import base64
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]

_ENCODED = ["Y2xhdWRl", "YW50aHJvcGlj", "Y29kZXg=", "bXVzZQ==", "b3B1cw==", "c29ubmV0",
            "Y28tYXV0aG9yZWQtYnk=", "Z2VuZXJhdGVkIHdpdGg=", "aGV0em5lcg==", "Y2hhdGdwdA==",
            "Y29waWxvdA=="]
NAMES = re.compile(r"(?<![A-Za-z])(" + "|".join(
    re.escape(base64.b64decode(e).decode()) for e in _ENCODED) + r")(?![a-z])", re.I)

RULES = [
    ("home path", re.compile(r"(/Users/[A-Za-z0-9._-]+|/home/(?!runner\b)[A-Za-z0-9._-]+|~/\.local/share|~/\.config)")),
    # 10.x.x.x is left out on purpose: in a tree full of pinned package versions
    # (nvidia-curand 10.4.0.35) it is indistinguishable from a version number.
    ("private address", re.compile(r"(?<![\d.=])(192\.168\.\d+\.\d+|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+)\b")),
    ("credential", re.compile(r"(gh[pousr]_[A-Za-z0-9]{30,}|-----BEGIN [A-Z ]*PRIVATE KEY|AKIA[0-9A-Z]{16}|"
                              r"xox[baprs]-[A-Za-z0-9-]{10,})")),
    ("tool name", NAMES),
]

# Data, not prose: a word list, and manifests naming third-party documents.
ALLOWED_PATHS = {
    "engine/scripts/extraction/lexicon/en_scowl60-rel-2026.02.25.txt",
    "engine/corpus/structured/PARSEBENCH.json",
}
# The manifests are scanned for every rule except the tool names.
SKIP_RULE_IN = {"engine/corpus/structured/PARSEBENCH.json": {"tool name"}}


def tracked() -> list[str]:
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, text=True,
                         check=True).stdout
    return [p for p in out.split("\0") if p]


def is_text(path: pathlib.Path) -> bool:
    try:
        return b"\0" not in path.read_bytes()[:4096]
    except OSError:
        return False


def scan_text(label: str, text: str, skip: set[str] = frozenset()) -> list[str]:
    found = []
    for number, line in enumerate(text.splitlines(), 1):
        for name, rx in RULES:
            if name not in skip and rx.search(line):
                found.append(f"{label}:{number}: {name}")
    return found


def scan_tree() -> list[str]:
    found = []
    for rel in tracked():
        if rel in ALLOWED_PATHS and rel not in SKIP_RULE_IN:
            continue
        path = ROOT / rel
        if not is_text(path):
            continue
        for name, rx in RULES:  # the file name is part of what is published
            if name == "tool name" and rx.search(rel):
                found.append(f"{rel}: {name} in the file name")
        found += scan_text(rel, path.read_text(encoding="utf-8", errors="replace"),
                           SKIP_RULE_IN.get(rel, set()))
    return found


def scan_history(rev_range: str | None) -> list[str]:
    args = ["git", "log", "--format=%H%x00%an <%ae>%n%B%x01"]
    if rev_range:
        args.append(rev_range)
    out = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, check=True).stdout
    found = []
    for entry in out.split("\x01"):
        entry = entry.strip("\n")
        if not entry:
            continue
        sha, _, body = entry.partition("\0")
        found += scan_text(f"commit {sha[:12]}", body)
    return found


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--range", default=None, help="commit range; default all history")
    ap.add_argument("--no-history", action="store_true")
    args = ap.parse_args(argv)
    found = scan_tree() + ([] if args.no_history else scan_history(args.range))
    for line in found:
        print(f"[public-surface] {line}", file=sys.stderr)
    if found:
        print(f"[public-surface] FAILED: {len(found)} finding(s)", file=sys.stderr)
        return 1
    print("[public-surface] ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
