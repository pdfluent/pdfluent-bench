# Multi-page speed, PDFluent 1.1.0 and 1.2.0 release candidate, 21-10-2026

One spot c3-standard-8 (Xeon Platinum 8481C, 8 vCPU, no GPU, europe-west4-a),
harness commit in `harness-commit.txt`, images in `images.json`. Both versions
ran on the same VM, interleaved (1.1.0 first on repetitions 1 and 3, 1.2.0
first on 2), 3 repetitions, on one CPU (`--cpuset-cpus=0`) and on all eight.
Candidate wheel and models as in `../round-1.2.0-20261021/NOTES.md`.

Set: `datasets/multipage-arxiv-40.json`, 40 arXiv papers at pinned versions,
1 050 pages (8 to 87 pages each), each file checked against its SHA256 at
fetch. Per document, a fresh `open_pdf` inside the timed region: all pages'
text (`Page.extract_text`), markdown with `quality="fast"`, the first two pages
rendered at 72 dpi, and `tobytes()`. `runs/<image>_t<1|all>_r<rep>.json` hold
every time and a hash of every output.

Outputs: text, render and save are byte-identical between the two versions on
all 40 documents in all runs. Markdown differs on 20 of 40 documents (a few
hundred bytes either way): 1.2.0 contains extraction changes besides the speed
work, so the markdown timings compare two versions that do not produce the
same output.

`summary.json` has p50 and p95 of the per-document median time (seconds), the
median-of-repetitions total time and pages per second for every operation. A
page here is a page of the document for text and markdown, and one of the two
rendered pages for render. The Python call `Document.get_page_text` (the
PyMuPDF-compatible path) is not timed: a spot check on one paper with 1.1.0 took
4.1 s against 0.3 s for `Page.extract_text`, so it measures the compatibility layer.
