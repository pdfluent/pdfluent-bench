# Copyright (c) 2026 Innovation Trigger B.V.
#
# PDFluent is available under two licences, at your option: the GNU AGPLv3, or
# the PDFluent Commercial Licence. See the LICENSE file in this repository --
# that file travels with the copy you received, which a URL does not.
"""Multi-page speed of the pdfluent package, one process, one repetition.

    multipage_speed.py MANIFEST PDF_DIR OUT.json

For every document, with a fresh open each time (open is inside the timed
region): text of all pages (`Page.extract_text`, the native call), markdown (`quality="fast"`), the first two pages
rendered at 72 dpi, and `tobytes()`. Beside each time it records a hash of what
the call produced, so two versions are compared on output as well as on time.
Runs inside the image's interpreter; the standard library only.
"""
import hashlib
import json
import resource
import sys
import time

import pdfluent


def fnv(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()[:12]


def timed(function):
    start = time.perf_counter()
    value = function()
    return (time.perf_counter() - start) * 1000.0, value


def main(manifest_path: str, pdf_dir: str, out: str) -> int:
    manifest = json.load(open(manifest_path))
    rows = []
    # one untimed document first: imports and allocator warm-up, as the single-page runs do
    warm = pdfluent.open_pdf(f"{pdf_dir}/{manifest['documents'][0]['id']}.pdf")
    warm.extract_markdown(quality="fast", ocr="never")
    for doc in manifest["documents"]:
        path = f"{pdf_dir}/{doc['id']}.pdf"
        row = {"id": doc["id"], "pages": doc["pages"]}

        def text():
            d = pdfluent.open_pdf(path)
            return "\x0c".join(d.load_page(i).extract_text() for i in range(d.page_count))

        def markdown():
            return pdfluent.open_pdf(path).extract_markdown(quality="fast", ocr="never")

        def render():
            d = pdfluent.open_pdf(path)
            return b"".join(d.get_page_pixmap(i, dpi=72).samples for i in range(min(2, d.page_count)))

        def save():
            return pdfluent.open_pdf(path).tobytes()

        for name, function in (("text", text), ("markdown", markdown), ("render", render), ("save", save)):
            ms, value = timed(function)
            data = value.encode() if isinstance(value, str) else bytes(value)
            row[name] = {"ms": round(ms, 3), "hash": fnv(data), "bytes": len(data)}
        rows.append(row)
        print(doc['id'], {k: row[k]['ms'] for k in ('text', 'markdown', 'render', 'save')}, file=sys.stderr, flush=True)
    json.dump({"version": pdfluent.__version__, "peak_rss_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1),
               "documents": rows}, open(out, "w"))
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:4]))
