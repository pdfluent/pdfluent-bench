# olmocr-bench

Internal evaluation only; no scores are published until Allen AI confirms the terms for the PDFs.

A blind holdout for every landing train of milestone SDK 1.3.0 (story pdfluent-internal#959):
the olmOCR-bench dataset (1,403 single-page PDFs, 7,010 unit tests), scored with the
benchmark's own scorer, one PDFluent recipe at a time, with a regression gate.

Data and scorer: Allen Institute for AI, olmOCR-bench, licensed ODC-BY-1.0,
https://huggingface.co/datasets/allenai/olmOCR-bench. Scorer: https://github.com/allenai/olmocr
(`olmocr/bench`, Apache-2.0), run unchanged.

## Blind

Nobody tunes on this set. `python -m bench.olmocr` computes, prints and writes an overall score and one
score per test category, and nothing else. It never computes, prints or writes a per-document score. The
scorer's own output lists failing tests by document; it is parsed in memory and dropped. Failing
extractions are counted, never named. Predictions stay in the private data root next to
`DO-NOT-INSPECT-FOR-TUNING.txt`. Numbers are not printed unless `--show-scores` is passed, because logs
can be public. Tests enforce this (`tests/test_olmocr_bench.py`): a planted per-document file in a run's
output fails the blindness check, and the writer refuses any key that would carry per-document data.

## What is here, and what is not

`recipes.toml` (recipes, same format as `odl-bench/recipes.toml`), `manifest.json` (SHA-256 of the 1,403
PDFs and the 7 test files at the pinned revision; hashes and names, no content) and the runner. Not here:
the PDFs, the tests, the predictions, any score, any baseline. The runner downloads the data at run time
into a private root outside the home directory (`--root`, or `OLMOCR_BENCH_ROOT`), and refuses a root under
`$HOME`. Nothing from the data is committed or redistributed.

Pins: dataset revision `54a96a6fb6a2bd3b297e59869491db4d3625b711`; scorer commit
`f7cfe4c22098b154c76b6ec950d1c0a464eecf8d`.

## Run

    olmocr-bench/prepare_env.sh VENV --version 1.2.0
    export OLMOCR_BENCH_ROOT=<directory on an external volume>
    export PLAYWRIGHT_BROWSERS_PATH=<directory for the scorer's chromium>   # math tests render with KaTeX
    VENV/bin/python -m playwright install chromium
    VENV/bin/python -m bench.olmocr run --fetch --recipe fast --no-gate        # records, does not judge
    VENV/bin/python -m bench.olmocr baseline <run dir> --out baseline-fast.json
    VENV/bin/python -m bench.olmocr run --recipe fast --baseline baseline-fast.json

`run` order: manifest and pin, scorer pin (no local changes), baseline, models, scorer browser, extraction,
scoring, gate. Output in `--out` (default `ROOT/runs/<utc>`): `run.json` (machine, wheel hash, revision pins,
manifest hash, the recipe with its resolved `extract_markdown` arguments, wall time, aggregates, verdict) and
`scores.json`. Both carry the attribution line.

Exit codes: 0 passed, 1 gate failed, 2 cannot judge (pin, manifest, baseline, scorer), 3 skipped. Balanced
recipes need `PDFLUENT_MODEL_DIR` and `ORT_DYLIB_PATH` as in `odl-bench/README.md`; without them the run prints
`SKIPPED (not a pass): <reason>` on stderr and exits 3 (`--allow-skip` makes it 0, the line stays).

## The gate

Against the baseline file (aggregates only, kept by the caller, never in this repository): the overall score
and each category (the seven test files and the scorer's `baseline` group) may be at most 0.005 below the
baseline. A failure names recipe and scope; numbers only with `--show-scores`. The overall score is the
scorer's own: the mean of the per-category pass rates. The baseline is a decision, never an automatic update.

## Licence findings (checked 24-10-2026)

Dataset revision `54a96a6fb6a2bd3b297e59869491db4d3625b711` (last modified 2026-02-19).

- Dataset card, https://huggingface.co/datasets/allenai/olmOCR-bench: "This dataset is licensed under
  ODC-BY-1.0. It is intended for research and educational use in accordance with AI2's Responsible Use
  Guidelines." Metadata: `license: odc-by`.
- The card, the repository files and the benchmark README
  (https://github.com/allenai/olmocr/blob/main/olmocr/bench/README.md) say nothing about the licence of the
  PDFs themselves. The olmocr code is Apache-2.0, which covers code, not data.
- Provenance of the PDFs (card, Table 2, and the benchmark README): 685 of 1,403 (tables, headers/footers,
  multi-column) come from "Internal repository", the "internal crawled PDF repository used to create
  olmOCR-Mix"; 522 are arXiv papers (per-paper arXiv licences); 98 are Library of Congress scans; 36 are
  "old, public domain math textbooks from the Internet Archive"; 62 are Internet Archive documents with no
  stated terms.
- Conclusion: ODC-BY-1.0 covers the dataset and annotations. The terms for the PDFs are unconfirmed. Hence:
  internal evaluation only, PDFs downloaded at run time and never committed or redistributed, no olmOCR score
  published anywhere (this file, issues, posts) until Allen AI confirms the terms. The private tracker may
  carry aggregates.

Attribution (ODC-BY-1.0), to accompany any result that is ever shared: "olmOCR-bench, Allen Institute for AI,
licensed ODC-BY-1.0, https://huggingface.co/datasets/allenai/olmOCR-bench".
