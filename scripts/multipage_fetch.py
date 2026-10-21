# Copyright (c) 2026 Innovation Trigger B.V.
#
# PDFluent is available under two licences, at your option: the GNU AGPLv3, or
# the PDFluent Commercial Licence. See the LICENSE file in this repository --
# that file travels with the copy you received, which a URL does not.
"""Fetch the multi-page speed set: every PDF from its URL, checked against the
SHA256 in datasets/multipage-arxiv-40.json. A file that differs is refused.

    python3 scripts/multipage_fetch.py DEST_DIR
"""
import hashlib
import json
import pathlib
import sys
import time
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
UA = "pdfluent-bench (research; hello@pdfluent.com)"


def main(dest: pathlib.Path) -> int:
    manifest = json.loads((ROOT / "datasets" / "multipage-arxiv-40.json").read_text())
    dest.mkdir(parents=True, exist_ok=True)
    bad = 0
    for doc in manifest["documents"]:
        target = dest / f"{doc['id']}.pdf"
        if target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest() == doc["sha256"]:
            continue
        for attempt in range(4):
            try:
                request = urllib.request.Request(doc["url"], headers={"User-Agent": UA})
                data = urllib.request.urlopen(request, timeout=120).read()
                break
            except OSError as exc:
                print(f"{doc['id']}: attempt {attempt + 1} failed: {exc}", file=sys.stderr)
                time.sleep(10)
        else:
            bad += 1
            continue
        if hashlib.sha256(data).hexdigest() != doc["sha256"]:
            print(f"REFUSED: {doc['id']} differs from the manifest", file=sys.stderr)
            bad += 1
            continue
        target.write_bytes(data)
        time.sleep(3)  # arXiv asks for a slow client
    print(f"fetched {len(manifest['documents']) - bad} of {len(manifest['documents'])}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(pathlib.Path(sys.argv[1])))
