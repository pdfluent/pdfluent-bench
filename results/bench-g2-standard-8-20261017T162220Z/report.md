# Benchmark overview

- run `bench-g2-standard-8-20261017T162220Z` on g2-standard-8 (Intel(R) Xeon(R) CPU @ 2.20GHz; NVIDIA L4), harness cbdae12b9c6b, engine scorers e1f136aecf32, datasets 9cc3856426d8, started 2026-10-17T16:22:20+00:00, wall clock 632.8 s

Quality cells: document mean per split, , with the number of documents (ParseBench: examples) in ⟨⟩; – where no document of that half is annotated for the axis. Speed cells: one value per configuration.

| row | docling-minimal | docling-standard | pdfluent-balanced | pdfluent-balanced-tatr | pdfluent-best (internal) | pdfluent-fast | pymupdf4llm |
|---|---|---|---|---|---|---|---|
| GPU ms/page p50, all cores | 152 | 592 | n/a | n/a | n/a | n/a | n/a |
| GPU ms/page p95, all cores | 297 | 2161 | n/a | n/a | n/a | n/a | n/a |
| GPU pages/s, all cores | 6.020 | 1.321 | n/a | n/a | n/a | n/a | n/a |
| GPU peak RSS MB, all cores | 2028.1 | 2327.3 | n/a | n/a | n/a | n/a | n/a |
| GPU ms/page p50, 1 thread | 155 | 595 | n/a | n/a | n/a | n/a | n/a |
| GPU ms/page p95, 1 thread | 303 | 2128 | n/a | n/a | n/a | n/a | n/a |
| GPU pages/s, 1 thread | 5.916 | 1.323 | n/a | n/a | n/a | n/a | n/a |
| GPU peak RSS MB, 1 thread | 2072.1 | 2334.5 | n/a | n/a | n/a | n/a | n/a |
| GPU cold start ms (median) | 11337 | 12043 | n/a | n/a | n/a | n/a | n/a |

n/a:
- pdfluent-balanced: the ONNX Runtime that `pdfluent models install` installs for 1.1.0 is a CPU build; no CUDA route is published
- pdfluent-balanced-tatr: the ONNX Runtime that `pdfluent models install` installs for 1.1.0 is a CPU build; no CUDA route is published
- pdfluent-best: the ported adapter pins the detectors to device="cpu"
- pdfluent-fast: the fast tier runs no model
- pymupdf4llm: pymupdf4llm has no GPU path

Internal rows (not publicly reproducible yet):
- pdfluent-best: the weights and the engine builds this column needs are not published; until the best tier ships, the row is measured internally and marked so in every report

Numbers here are measurements, not claims. Anything published goes through the claims register with its run ID and measurement date.
