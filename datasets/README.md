# Data layers and the page-hash registry

Story pdfluent-internal#971, epic #970. Hashes and licence records only; no page content, no scores.

- `registry/registry.json` and `registry/holdouts/*.jsonl`: every page of every sealed holdout (DocLayNet
  test, PubTables-1M test, FinTabNet test, ParseBench, DP-Bench, docling-dpbench, olmOCR-bench). The 19-axes
  holdout is the `half: "holdout"` pages of the first four. Each file is sealed in `registry.json`.
  olmOCR-bench is blind: its pages were hashed by program, never read.
- `registry/layers.json`: the datasets registered in a layer (`dev`, `validation`, `training`).
- `layers/<layer>/<dataset>.json`: the page manifest of one dataset, `{"pages": [{file, page, doc,
  file_sha256, text, render}]}`. A manifest that sits in `layers/` but not in `layers.json` fails the check.
  A dataset declared with `repack_of` must carry a page-hash manifest before it is used.
- `arxiv/EVIDENCE.md`: arXiv routes, terms read, licence filter and results (#972).

    python -m bench.registry check          # exit 0 clean, 1 leak or registration problem, 3 holdout incomplete
    python -m bench.registry build --out datasets/registry --corpus <dir with the sealed manifests> \
        --doclaynet <pages> --fintabnet <pages> --parsebench <data> --dp-bench <pdfs> --olmocr-bench <pdfs>

A layer page is a leak when it matches a holdout page by file hash, normalised text hash, render hash
(256-bit ink-map hash within 10 bits) or source document id. Source documents may not span the
train side (`dev` and `training`) and `validation`. See `bench/registry.py` for the rules.
