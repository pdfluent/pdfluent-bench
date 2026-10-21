# Benchmark overview

- run `bench-c3-standard-8-20261021T204944Z` on c3-standard-8 (Intel(R) Xeon(R) Platinum 8481C CPU @ 2.70GHz; no GPU), harness 66f6259c9314, engine scorers e1f136aecf32, datasets 5fec3c16b54e, started 2026-10-21T20:49:44+00:00, wall clock 6734.8 s

Quality cells: document mean per split, , with the number of documents (ParseBench: examples) in ⟨⟩; – where no document of that half is annotated for the axis. Speed cells: one value per configuration.

| row | pdfluent-balanced | pdfluent-balanced-1.2.0 | pdfluent-balanced-tatr | pdfluent-balanced-tatr-1.2.0 | pdfluent-fast | pdfluent-fast-1.2.0 |
|---|---|---|---|---|---|---|
| CPU ms/page p50, all cores | 695 | 680 | 879 | 863 | 19 | 17 |
| CPU ms/page p95, all cores | 792 | 742 | 1104 | 1067 | 50 | 40 |
| CPU pages/s, all cores | 1.423 | 1.450 | 1.154 | 1.174 | 42.857 | 50.201 |
| CPU peak RSS MB, all cores | 856.1 | 878.3 | 997.0 | 967.0 | 43.2 | 43.5 |
| CPU ms/page p50, 1 thread | 5989 | 5848 | 6937 | 7107 | 19 | 17 |
| CPU ms/page p95, 1 thread | 6238 | 6080 | 8561 | 8740 | 50 | 39 |
| CPU pages/s, 1 thread | 0.167 | 0.172 | 0.142 | 0.141 | 42.857 | 50.607 |
| CPU peak RSS MB, 1 thread | 845.1 | 870.8 | 1004.2 | 980.5 | 43.2 | 43.7 |
| CPU cold start ms (median) | 1866 | 1866 | 2419 | 2369 | 113 | 113 |

Numbers here are measurements, not claims. Anything published goes through the claims register with its run ID and measurement date.
