#!/usr/bin/env python3
# Copyright (c) 2026 Innovation Trigger B.V.
#
# PDFluent is available under two licences, at your option: the GNU AGPLv3, or
# the PDFluent Commercial Licence. See the LICENSE file in this repository --
# that file travels with the copy you received, which a URL does not.
"""Pin ParseBench and seal its fifth (#622): corpus/structured/PARSEBENCH.json.

WHAT IS PINNED. Every PDF page under `docs/` of llamaindex/ParseBench at the
revision DATASETS.toml names, by path and SHA-256 -- read from the Hub's tree
API, where a large file's LFS object id IS its SHA-256. A small file is stored
in git rather than LFS and the API gives only its git blob id, so those are
fetched and hashed in memory, never written anywhere: the set is fetched to
the round's machine, not to this one. The 42 JPG/PNG layout pages are left
out; a text-layer extractor cannot read a picture.

BY DOCUMENT, ACROSS CATEGORIES. A page may sit under two categories (a layout
page that is also a chart page), and several pages come from one report
(`..._p101.pdf`, `..._page39.pdf`). The document is the file name with its page
suffix removed, whatever directory it is in, so every page of one report --
and every copy of one page -- lands on the same side of the split
(benchmarks/HOLDOUTS.md, "why by document").

THE SPLIT is `scripts/ci/structured_holdout_rule.py` under this set's own
seed, drawn here, before anything is measured on it.

Usage:
    parsebench_manifest.py [--known-hashes FILE] [--out PATH]
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import pathlib
import re
import sys
import tomllib
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "ci"))
import structured_holdout_rule as rule  # noqa: E402

SET = "parsebench"
PAGE_SUFFIX = re.compile(r"_p(?:age)?\d+$", re.I)


def row() -> dict:
    data = tomllib.loads((REPO / "benchmarks" / "structured" / "comparison"
                          / "DATASETS.toml").read_text())
    return data["datasets"][SET]


def tree(repo_id: str, revision: str) -> list[dict]:
    url = (f"https://huggingface.co/api/datasets/{repo_id}/tree/{revision}"
           "?recursive=true&expand=true")
    files: list[dict] = []
    while url:
        with urllib.request.urlopen(url, timeout=60) as response:
            files += json.load(response)
            link = response.headers.get("Link") or ""
        url = link.split(";")[0].strip("<>") if 'rel="next"' in link else None
    return [f for f in files if f.get("type") == "file"]


def sha256_of(repo_id: str, revision: str, entry: dict) -> str:
    if entry.get("lfs"):
        return entry["lfs"]["oid"]
    url = (f"https://huggingface.co/datasets/{repo_id}/resolve/{revision}/"
           + urllib.parse.quote(entry["path"]))
    for _attempt in range(4):
        try:
            with urllib.request.urlopen(url, timeout=60) as response:
                body = response.read()
        except OSError:
            continue
        if len(body) == entry["size"]:
            return hashlib.sha256(body).hexdigest()
    raise SystemExit(f"[parsebench] FATAL: could not hash {entry['path']}")


def document_of(path: str) -> str:
    stem = pathlib.PurePosixPath(path).stem
    return PAGE_SUFFIX.sub("", stem) + ".pdf"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--known-hashes", type=pathlib.Path,
                    help="path -> sha256 of the non-LFS files, from an earlier run")
    ap.add_argument("--out", type=pathlib.Path,
                    default=REPO / "corpus" / "structured" / "PARSEBENCH.json")
    args = ap.parse_args()
    entry = row()
    repo_id, revision = entry["repo_id"], entry["revision"]
    known = json.loads(args.known_hashes.read_text()) if args.known_hashes else {}
    files = sorted((f for f in tree(repo_id, revision)
                    if f["path"].startswith("docs/") and f["path"].lower().endswith(".pdf")),
                   key=lambda f: f["path"])

    def hashed(f: dict) -> str:
        return known.get(f["path"]) or sha256_of(repo_id, revision, f)

    with ThreadPoolExecutor(8) as pool:
        digests = list(pool.map(hashed, files))
    pages = [{"source": f"{SET}/{f['path'].split('/')[1]}", "name": f["path"],
              "document": document_of(f["path"]), "category": f["path"].split("/")[1],
              "sha256": digest, "size": f["size"]}
             for f, digest in zip(files, digests)]
    seed = rule.seed_for(SET)
    tuning, holdout = rule.split(pages, seed=seed)
    held = {e["name"] for e in holdout}
    for page in pages:
        page["split"] = "holdout" if page["name"] in held else "tuning"
    manifest = {
        "set": SET,
        "version": entry["version"],
        "revision": revision,
        "source": entry["source"],
        "licence": entry["licence"],
        "drawn": dt.date.today().isoformat(),
        "seed": seed,
        "rule": "scripts/ci/structured_holdout_rule.py",
        "share": rule.HOLDOUT_SHARE,
        "counts": {"pages": len(pages), "tuning": len(tuning), "holdout": len(holdout),
                   "documents": len(rule.documents_of(pages))},
        "seal": {"tuning": rule.digest_of(tuning), "holdout": rule.digest_of(holdout)},
        "pages": pages,
    }
    args.out.write_text(json.dumps(manifest, indent=1) + "\n")
    print(f"[parsebench] {len(pages)} pages in {manifest['counts']['documents']} documents; "
          f"holdout {len(holdout)}; seal {manifest['seal']['holdout']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
