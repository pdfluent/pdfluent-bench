**DRAFT: this report is missing values.** Each is listed here and written `missing` in the table.

- FinTabNet TEDS tuning: pdfluent-best has no run file (not measured; reason below)
- FinTabNet tables F1 tuning: pdfluent-best has no run file (not measured; reason below)
- FinTabNet words F1 tuning: pdfluent-best has no run file (not measured; reason below)
- PubTables-1M TEDS tuning: pdfluent-best has no run file (not measured; reason below)
- PubTables-1M tables F1 tuning: pdfluent-best has no run file (not measured; reason below)
- PubTables-1M words F1 tuning: pdfluent-best has no run file (not measured; reason below)
- DocLayNet headings F1 tuning: pdfluent-best has no run file (not measured; reason below)
- DocLayNet lists F1 tuning: pdfluent-best has no run file (not measured; reason below)
- DocLayNet tables F1 tuning: pdfluent-best has no run file (not measured; reason below)
- DocLayNet text 1-NED tuning: pdfluent-best has no run file (not measured; reason below)
- DocLayNet text 1-NED (geometric) tuning: pdfluent-best has no run file (not measured; reason below)
- DocLayNet words F1 tuning: pdfluent-best has no run file (not measured; reason below)
- DocLayNet order (annotated) tuning: pdfluent-best has no run file (not measured; reason below)
- DocLayNet order (geometric) tuning: pdfluent-best has no run file (not measured; reason below)
- ParseBench record match tuning: pdfluent-best has no run file (not measured; reason below)
- ParseBench table GriTS tuning: pdfluent-best has no run file (not measured; reason below)
- ParseBench text content tuning: pdfluent-best has no run file (not measured; reason below)
- ParseBench text formatting tuning: pdfluent-best has no run file (not measured; reason below)
- ParseBench chart tuning: pdfluent-best has no run file (not measured; reason below)
- CPU speed ms/page p50 (all threads): pdfluent-best has no speed file (not measured; reason below)
- CPU speed ms/page p95 (all threads): pdfluent-best has no speed file (not measured; reason below)
- CPU speed pages/s (all threads): pdfluent-best has no speed file (not measured; reason below)
- CPU speed peak RSS MB (all threads): pdfluent-best has no speed file (not measured; reason below)
- CPU speed ms/page p50 (1 threads): pdfluent-best has no speed file (not measured; reason below)
- CPU speed ms/page p95 (1 threads): pdfluent-best has no speed file (not measured; reason below)
- CPU speed pages/s (1 threads): pdfluent-best has no speed file (not measured; reason below)
- CPU speed peak RSS MB (1 threads): pdfluent-best has no speed file (not measured; reason below)
- CPU cold start: pdfluent-best has no value

# Benchmark overview

- not measured: pdfluent-best, skipped: image `best` is not built (docker/build.sh best)
- run `bench-c3-standard-8-20261017T153432Z` on c3-standard-8 (Intel(R) Xeon(R) Platinum 8481C CPU @ 2.70GHz; no GPU), harness 946f4f8a00a4, engine scorers e1f136aecf32, datasets 5fec3c16b54e, started 2026-10-17T15:34:32+00:00, wall clock 1605.3 s

Quality cells: document mean per split, tuning, with the number of documents (ParseBench: examples) in ⟨⟩; – where no document of that half is annotated for the axis. Speed cells: one value per configuration.

| row | docling-minimal | docling-standard | pdfluent-balanced | pdfluent-balanced-tatr | pdfluent-best (internal) | pdfluent-fast | pymupdf4llm |
|---|---|---|---|---|---|---|---|
| FinTabNet TEDS | 0.044 ⟨11⟩ | 0.808 ⟨11⟩ | 0.788 ⟨11⟩ | 0.922 ⟨11⟩ | missing | 0.761 ⟨11⟩ | 0.818 ⟨11⟩ |
| FinTabNet tables F1 | 0.746 ⟨11⟩ | 0.746 ⟨11⟩ | 0.780 ⟨11⟩ | 0.898 ⟨11⟩ | missing | 0.742 ⟨11⟩ | 0.746 ⟨11⟩ |
| FinTabNet words F1 | 0.892 ⟨11⟩ | 0.864 ⟨11⟩ | 0.847 ⟨11⟩ | 0.896 ⟨11⟩ | missing | 0.833 ⟨11⟩ | 0.820 ⟨11⟩ |
| PubTables-1M TEDS | 0.041 ⟨8⟩ | 0.890 ⟨8⟩ | 0.698 ⟨8⟩ | 0.949 ⟨8⟩ | missing | 0.698 ⟨8⟩ | 0.906 ⟨8⟩ |
| PubTables-1M tables F1 | 0.958 ⟨8⟩ | 0.938 ⟨8⟩ | 0.896 ⟨8⟩ | 0.938 ⟨8⟩ | missing | 0.896 ⟨8⟩ | 0.938 ⟨8⟩ |
| PubTables-1M words F1 | 0.927 ⟨8⟩ | 0.917 ⟨8⟩ | 0.918 ⟨8⟩ | 0.943 ⟨8⟩ | missing | 0.918 ⟨8⟩ | 0.855 ⟨8⟩ |
| DocLayNet headings F1 | 0.961 ⟨5⟩ | 0.961 ⟨5⟩ | 0.976 ⟨5⟩ | 0.976 ⟨5⟩ | missing | 0.943 ⟨5⟩ | 0.899 ⟨5⟩ |
| DocLayNet lists F1 | 0.928 ⟨4⟩ | 0.928 ⟨4⟩ | 0.917 ⟨4⟩ | 0.917 ⟨4⟩ | missing | 0.896 ⟨4⟩ | 0.900 ⟨4⟩ |
| DocLayNet tables F1 | 0.500 ⟨2⟩ | 0.500 ⟨2⟩ | 1.000 ⟨2⟩ | 1.000 ⟨2⟩ | missing | 1.000 ⟨2⟩ | 1.000 ⟨2⟩ |
| DocLayNet text 1-NED | 0.530 ⟨5⟩ | 0.530 ⟨5⟩ | 0.543 ⟨5⟩ | 0.543 ⟨5⟩ | missing | 0.534 ⟨5⟩ | 0.549 ⟨5⟩ |
| DocLayNet text 1-NED (geometric) | 0.813 ⟨5⟩ | 0.813 ⟨5⟩ | 0.894 ⟨5⟩ | 0.897 ⟨5⟩ | missing | 0.858 ⟨5⟩ | 0.864 ⟨5⟩ |
| DocLayNet words F1 | 0.919 ⟨5⟩ | 0.919 ⟨5⟩ | 0.948 ⟨5⟩ | 0.948 ⟨5⟩ | missing | 0.933 ⟨5⟩ | 0.925 ⟨5⟩ |
| DocLayNet order (annotated) | 0.695 ⟨5⟩ | 0.695 ⟨5⟩ | 0.734 ⟨5⟩ | 0.734 ⟨5⟩ | missing | 0.719 ⟨5⟩ | 0.732 ⟨5⟩ |
| DocLayNet order (geometric) | 0.942 ⟨5⟩ | 0.942 ⟨5⟩ | 0.999 ⟨5⟩ | 0.999 ⟨5⟩ | missing | 1.000 ⟨5⟩ | 0.997 ⟨5⟩ |
| ParseBench record match | 0.000 ⟨7⟩ | 0.238 ⟨7⟩ | 0.353 ⟨7⟩ | 0.360 ⟨7⟩ | missing | 0.353 ⟨7⟩ | 0.129 ⟨7⟩ |
| ParseBench table GriTS | 0.005 ⟨7⟩ | 0.417 ⟨7⟩ | 0.549 ⟨7⟩ | 0.570 ⟨7⟩ | missing | 0.534 ⟨7⟩ | 0.390 ⟨7⟩ |
| ParseBench text content | 0.648 ⟨7⟩ | 0.648 ⟨7⟩ | 0.622 ⟨7⟩ | 0.622 ⟨7⟩ | missing | 0.637 ⟨7⟩ | 0.595 ⟨7⟩ |
| ParseBench text formatting | 0.322 ⟨7⟩ | 0.322 ⟨7⟩ | 0.378 ⟨7⟩ | 0.378 ⟨7⟩ | missing | 0.326 ⟨7⟩ | 0.418 ⟨7⟩ |
| ParseBench chart | 0.000 ⟨7⟩ | 0.000 ⟨7⟩ | 0.000 ⟨7⟩ | 0.000 ⟨7⟩ | missing | 0.100 ⟨7⟩ | 0.000 ⟨7⟩ |
| CPU ms/page p50, all cores | 538 | 1548 | 702 | 899 | missing | 28 | 262 |
| CPU ms/page p95, all cores | 656 | 5561 | 759 | 1072 | missing | 49 | 450 |
| CPU pages/s, all cores | 1.820 | 0.494 | 1.409 | 1.126 | missing | 38.462 | 3.885 |
| CPU peak RSS MB, all cores | 1292.5 | 1675.2 | 875.8 | 891.2 | missing | 42.4 | 369.1 |
| CPU ms/page p50, 1 thread | 1518 | 4304 | 5907 | 7046 | missing | 28 | 262 |
| CPU ms/page p95, 1 thread | 1641 | 15428 | 5992 | 8454 | missing | 50 | 449 |
| CPU pages/s, 1 thread | 0.655 | 0.181 | 0.169 | 0.139 | missing | 38.023 | 3.880 |
| CPU peak RSS MB, 1 thread | 1173.3 | 1534.4 | 871.5 | 909.1 | missing | 42.7 | 371.9 |
| CPU cold start ms (median) | 7074 | 8376 | 1816 | 2318 | missing | 113 | 865 |

Internal rows (not publicly reproducible yet):
- pdfluent-best: the weights and the engine builds this column needs are not published; until the best tier ships, the row is measured internally and marked so in every report

Numbers here are measurements, not claims. Anything published goes through the claims register with its run ID and measurement date.
