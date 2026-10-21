# Benchmark overview

- run `bench-c3-standard-8-20261021T090020Z` on c3-standard-8 (Intel(R) Xeon(R) Platinum 8481C CPU @ 2.70GHz; no GPU), harness ff4eb272d500, engine scorers e1f136aecf32, datasets 5fec3c16b54e, started 2026-10-21T09:00:20+00:00, wall clock 6880.1 s

Quality cells: document mean per split, , with the number of documents (ParseBench: examples) in ⟨⟩; – where no document of that half is annotated for the axis. Speed cells: one value per configuration.

| row | pdfluent-balanced | pdfluent-balanced-1.2.0 | pdfluent-balanced-tatr | pdfluent-balanced-tatr-1.2.0 | pdfluent-fast | pdfluent-fast-1.2.0 |
|---|---|---|---|---|---|---|
| CPU ms/page p50, all cores | 701 | 687 | 885 | 864 | 20 | 18 |
| CPU ms/page p95, all cores | 805 | 754 | 1104 | 1074 | 51 | 41 |
| CPU pages/s, all cores | 1.411 | 1.437 | 1.148 | 1.178 | 41.345 | 47.985 |
| CPU peak RSS MB, all cores | 878.1 | 873.8 | 1000.0 | 957.4 | 43.3 | 43.7 |
| CPU ms/page p50, 1 thread | 6147 | 6129 | 7144 | 7127 | 20 | 18 |
| CPU ms/page p95, 1 thread | 6292 | 6261 | 8766 | 8730 | 51 | 41 |
| CPU pages/s, 1 thread | 0.162 | 0.163 | 0.139 | 0.140 | 41.391 | 47.740 |
| CPU peak RSS MB, 1 thread | 874.6 | 876.7 | 934.7 | 979.9 | 42.8 | 43.6 |
| CPU cold start ms (median) | 1867 | 1868 | 2418 | 2368 | 113 | 113 |

Numbers here are measurements, not claims. Anything published goes through the claims register with its run ID and measurement date.
