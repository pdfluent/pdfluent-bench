**DRAFT: this report is missing values.** Each is listed here and written `missing` in the table.

- FinTabNet TEDS tuning: pdfluent-best has no run file (not measured; reason below)
- FinTabNet TEDS holdout: pdfluent-best has no run file (not measured; reason below)
- FinTabNet tables F1 tuning: pdfluent-best has no run file (not measured; reason below)
- FinTabNet tables F1 holdout: pdfluent-best has no run file (not measured; reason below)
- FinTabNet words F1 tuning: pdfluent-best has no run file (not measured; reason below)
- FinTabNet words F1 holdout: pdfluent-best has no run file (not measured; reason below)
- PubTables-1M TEDS tuning: pdfluent-best has no run file (not measured; reason below)
- PubTables-1M TEDS holdout: pdfluent-best has no run file (not measured; reason below)
- PubTables-1M tables F1 tuning: pdfluent-best has no run file (not measured; reason below)
- PubTables-1M tables F1 holdout: pdfluent-best has no run file (not measured; reason below)
- PubTables-1M words F1 tuning: pdfluent-best has no run file (not measured; reason below)
- PubTables-1M words F1 holdout: pdfluent-best has no run file (not measured; reason below)
- DocLayNet headings F1 tuning: pdfluent-best has no run file (not measured; reason below)
- DocLayNet headings F1 holdout: pdfluent-best has no run file (not measured; reason below)
- DocLayNet lists F1 tuning: pdfluent-best has no run file (not measured; reason below)
- DocLayNet lists F1 holdout: pdfluent-best has no run file (not measured; reason below)
- DocLayNet tables F1 tuning: pdfluent-best has no run file (not measured; reason below)
- DocLayNet tables F1 holdout: pdfluent-best has no run file (not measured; reason below)
- DocLayNet text 1-NED tuning: pdfluent-best has no run file (not measured; reason below)
- DocLayNet text 1-NED holdout: pdfluent-best has no run file (not measured; reason below)
- DocLayNet text 1-NED (geometric) tuning: pdfluent-best has no run file (not measured; reason below)
- DocLayNet text 1-NED (geometric) holdout: pdfluent-best has no run file (not measured; reason below)
- DocLayNet words F1 tuning: pdfluent-best has no run file (not measured; reason below)
- DocLayNet words F1 holdout: pdfluent-best has no run file (not measured; reason below)
- DocLayNet order (annotated) tuning: pdfluent-best has no run file (not measured; reason below)
- DocLayNet order (annotated) holdout: pdfluent-best has no run file (not measured; reason below)
- DocLayNet order (geometric) tuning: pdfluent-best has no run file (not measured; reason below)
- DocLayNet order (geometric) holdout: pdfluent-best has no run file (not measured; reason below)
- ParseBench record match tuning: pdfluent-best has no run file (not measured; reason below)
- ParseBench record match holdout: pdfluent-best has no run file (not measured; reason below)
- ParseBench table GriTS tuning: pdfluent-best has no run file (not measured; reason below)
- ParseBench table GriTS holdout: pdfluent-best has no run file (not measured; reason below)
- ParseBench text content tuning: pdfluent-best has no run file (not measured; reason below)
- ParseBench text content holdout: pdfluent-best has no run file (not measured; reason below)
- ParseBench text formatting tuning: pdfluent-best has no run file (not measured; reason below)
- ParseBench text formatting holdout: pdfluent-best has no run file (not measured; reason below)
- ParseBench chart tuning: pdfluent-best has no run file (not measured; reason below)
- ParseBench chart holdout: pdfluent-best has no run file (not measured; reason below)
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
- run `bench-c3-standard-8-20261021T090020Z` on c3-standard-8 (Intel(R) Xeon(R) Platinum 8481C CPU @ 2.70GHz; no GPU), harness ff4eb272d500, engine scorers e1f136aecf32, datasets 5fec3c16b54e, started 2026-10-21T09:00:20+00:00, wall clock 6880.1 s
- run `bench-n2-standard-8-20261021T085113Z` on n2-standard-8 (Intel(R) Xeon(R) CPU @ 2.80GHz; no GPU), harness 805ada921160, engine scorers e1f136aecf32, datasets 5fec3c16b54e, started 2026-10-21T08:51:13+00:00, wall clock 8140.0 s
- run `bench-c3-standard-8-20261017T163121Z` on c3-standard-8 (Intel(R) Xeon(R) Platinum 8481C CPU @ 2.70GHz; no GPU), harness cbdae12b9c6b, engine scorers e1f136aecf32, datasets 5fec3c16b54e, started 2026-10-17T16:31:21+00:00, wall clock 22401.5 s

Quality cells: document mean per split, tuning / holdout, with the number of documents (ParseBench: examples) in ⟨⟩; – where no document of that half is annotated for the axis. Speed cells: one value per configuration.

| row | docling-minimal | docling-standard | pdfluent-balanced | pdfluent-balanced-1.2.0 | pdfluent-balanced-tatr | pdfluent-balanced-tatr-1.2.0 | pdfluent-best (internal) | pdfluent-fast | pdfluent-fast-1.2.0 | pymupdf4llm |
|---|---|---|---|---|---|---|---|---|---|---|
| FinTabNet TEDS | 0.057 ⟨67⟩ / 0.057 ⟨39⟩ | 0.709 ⟨67⟩ / 0.750 ⟨39⟩ | 0.687 ⟨67⟩ / 0.696 ⟨39⟩ | 0.692 ⟨67⟩ / 0.707 ⟨39⟩ | 0.859 ⟨67⟩ / 0.868 ⟨39⟩ | 0.859 ⟨67⟩ / 0.868 ⟨39⟩ | missing / missing | 0.663 ⟨67⟩ / 0.677 ⟨39⟩ | 0.670 ⟨67⟩ / 0.688 ⟨39⟩ | 0.727 ⟨67⟩ / 0.779 ⟨39⟩ |
| FinTabNet tables F1 | 0.682 ⟨67⟩ / 0.656 ⟨39⟩ | 0.615 ⟨67⟩ / 0.612 ⟨39⟩ | 0.701 ⟨67⟩ / 0.658 ⟨39⟩ | 0.704 ⟨67⟩ / 0.698 ⟨39⟩ | 0.820 ⟨67⟩ / 0.738 ⟨39⟩ | 0.820 ⟨67⟩ / 0.738 ⟨39⟩ | missing / missing | 0.663 ⟨67⟩ / 0.625 ⟨39⟩ | 0.673 ⟨67⟩ / 0.665 ⟨39⟩ | 0.635 ⟨67⟩ / 0.640 ⟨39⟩ |
| FinTabNet words F1 | 0.836 ⟨67⟩ / 0.833 ⟨39⟩ | 0.793 ⟨67⟩ / 0.795 ⟨39⟩ | 0.809 ⟨67⟩ / 0.827 ⟨39⟩ | 0.809 ⟨67⟩ / 0.825 ⟨39⟩ | 0.829 ⟨67⟩ / 0.838 ⟨39⟩ | 0.829 ⟨67⟩ / 0.838 ⟨39⟩ | missing / missing | 0.811 ⟨67⟩ / 0.790 ⟨39⟩ | 0.815 ⟨67⟩ / 0.795 ⟨39⟩ | 0.748 ⟨67⟩ / 0.724 ⟨39⟩ |
| PubTables-1M TEDS | 0.034 ⟨55⟩ / 0.053 ⟨46⟩ | 0.825 ⟨55⟩ / 0.878 ⟨46⟩ | 0.717 ⟨55⟩ / 0.778 ⟨46⟩ | 0.720 ⟨55⟩ / 0.778 ⟨46⟩ | 0.912 ⟨55⟩ / 0.955 ⟨46⟩ | 0.911 ⟨55⟩ / 0.955 ⟨46⟩ | missing / missing | 0.698 ⟨55⟩ / 0.762 ⟨46⟩ | 0.701 ⟨55⟩ / 0.762 ⟨46⟩ | 0.830 ⟨55⟩ / 0.862 ⟨46⟩ |
| PubTables-1M tables F1 | 0.923 ⟨55⟩ / 0.957 ⟨46⟩ | 0.871 ⟨55⟩ / 0.928 ⟨46⟩ | 0.848 ⟨55⟩ / 0.877 ⟨46⟩ | 0.848 ⟨55⟩ / 0.877 ⟨46⟩ | 0.923 ⟨55⟩ / 0.951 ⟨46⟩ | 0.923 ⟨55⟩ / 0.951 ⟨46⟩ | missing / missing | 0.802 ⟨55⟩ / 0.850 ⟨46⟩ | 0.802 ⟨55⟩ / 0.850 ⟨46⟩ | 0.906 ⟨55⟩ / 0.938 ⟨46⟩ |
| PubTables-1M words F1 | 0.886 ⟨55⟩ / 0.867 ⟨46⟩ | 0.868 ⟨55⟩ / 0.872 ⟨46⟩ | 0.914 ⟨55⟩ / 0.876 ⟨46⟩ | 0.914 ⟨55⟩ / 0.876 ⟨46⟩ | 0.930 ⟨55⟩ / 0.894 ⟨46⟩ | 0.930 ⟨55⟩ / 0.894 ⟨46⟩ | missing / missing | 0.894 ⟨55⟩ / 0.875 ⟨46⟩ | 0.894 ⟨55⟩ / 0.875 ⟨46⟩ | 0.856 ⟨55⟩ / 0.871 ⟨46⟩ |
| DocLayNet headings F1 | 0.835 ⟨13⟩ / 0.782 ⟨11⟩ | 0.835 ⟨13⟩ / 0.782 ⟨11⟩ | 0.838 ⟨13⟩ / 0.874 ⟨11⟩ | 0.838 ⟨13⟩ / 0.874 ⟨11⟩ | 0.838 ⟨13⟩ / 0.874 ⟨11⟩ | 0.838 ⟨13⟩ / 0.874 ⟨11⟩ | missing / missing | 0.758 ⟨13⟩ / 0.774 ⟨11⟩ | 0.760 ⟨13⟩ / 0.774 ⟨11⟩ | 0.790 ⟨13⟩ / 0.726 ⟨11⟩ |
| DocLayNet lists F1 | 0.676 ⟨12⟩ / 0.667 ⟨12⟩ | 0.676 ⟨12⟩ / 0.667 ⟨12⟩ | 0.721 ⟨12⟩ / 0.803 ⟨12⟩ | 0.721 ⟨12⟩ / 0.803 ⟨12⟩ | 0.722 ⟨12⟩ / 0.803 ⟨12⟩ | 0.722 ⟨12⟩ / 0.803 ⟨12⟩ | missing / missing | 0.674 ⟨12⟩ / 0.781 ⟨12⟩ | 0.674 ⟨12⟩ / 0.781 ⟨12⟩ | 0.707 ⟨12⟩ / 0.815 ⟨12⟩ |
| DocLayNet tables F1 | 0.617 ⟨8⟩ / 0.653 ⟨6⟩ | 0.576 ⟨8⟩ / 0.393 ⟨6⟩ | 0.540 ⟨8⟩ / 0.319 ⟨6⟩ | 0.531 ⟨8⟩ / 0.314 ⟨6⟩ | 0.589 ⟨8⟩ / 0.424 ⟨6⟩ | 0.589 ⟨8⟩ / 0.424 ⟨6⟩ | missing / missing | 0.531 ⟨8⟩ / 0.406 ⟨6⟩ | 0.522 ⟨8⟩ / 0.417 ⟨6⟩ | 0.760 ⟨8⟩ / 0.693 ⟨6⟩ |
| DocLayNet text 1-NED | 0.593 ⟨13⟩ / 0.585 ⟨12⟩ | 0.586 ⟨13⟩ / 0.574 ⟨12⟩ | 0.592 ⟨13⟩ / 0.568 ⟨12⟩ | 0.590 ⟨13⟩ / 0.568 ⟨12⟩ | 0.594 ⟨13⟩ / 0.567 ⟨12⟩ | 0.594 ⟨13⟩ / 0.567 ⟨12⟩ | missing / missing | 0.581 ⟨13⟩ / 0.553 ⟨12⟩ | 0.578 ⟨13⟩ / 0.554 ⟨12⟩ | 0.583 ⟨13⟩ / 0.564 ⟨12⟩ |
| DocLayNet text 1-NED (geometric) | 0.668 ⟨13⟩ / 0.617 ⟨12⟩ | 0.661 ⟨13⟩ / 0.616 ⟨12⟩ | 0.710 ⟨13⟩ / 0.635 ⟨12⟩ | 0.707 ⟨13⟩ / 0.635 ⟨12⟩ | 0.712 ⟨13⟩ / 0.636 ⟨12⟩ | 0.712 ⟨13⟩ / 0.635 ⟨12⟩ | missing / missing | 0.688 ⟨13⟩ / 0.606 ⟨12⟩ | 0.686 ⟨13⟩ / 0.606 ⟨12⟩ | 0.676 ⟨13⟩ / 0.639 ⟨12⟩ |
| DocLayNet words F1 | 0.924 ⟨12⟩ / 0.929 ⟨12⟩ | 0.922 ⟨12⟩ / 0.923 ⟨12⟩ | 0.923 ⟨12⟩ / 0.913 ⟨12⟩ | 0.923 ⟨12⟩ / 0.913 ⟨12⟩ | 0.925 ⟨12⟩ / 0.914 ⟨12⟩ | 0.925 ⟨12⟩ / 0.914 ⟨12⟩ | missing / missing | 0.913 ⟨12⟩ / 0.913 ⟨12⟩ | 0.913 ⟨12⟩ / 0.913 ⟨12⟩ | 0.894 ⟨12⟩ / 0.894 ⟨12⟩ |
| DocLayNet order (annotated) | 0.776 ⟨12⟩ / 0.678 ⟨12⟩ | 0.776 ⟨12⟩ / 0.694 ⟨12⟩ | 0.782 ⟨12⟩ / 0.720 ⟨12⟩ | 0.781 ⟨12⟩ / 0.720 ⟨12⟩ | 0.778 ⟨12⟩ / 0.718 ⟨12⟩ | 0.778 ⟨12⟩ / 0.718 ⟨12⟩ | missing / missing | 0.782 ⟨12⟩ / 0.708 ⟨12⟩ | 0.781 ⟨12⟩ / 0.708 ⟨12⟩ | 0.786 ⟨12⟩ / 0.719 ⟨12⟩ |
| DocLayNet order (geometric) | 0.913 ⟨12⟩ / 0.865 ⟨12⟩ | 0.913 ⟨12⟩ / 0.862 ⟨12⟩ | 0.946 ⟨12⟩ / 0.908 ⟨12⟩ | 0.946 ⟨12⟩ / 0.908 ⟨12⟩ | 0.945 ⟨12⟩ / 0.908 ⟨12⟩ | 0.945 ⟨12⟩ / 0.908 ⟨12⟩ | missing / missing | 0.941 ⟨12⟩ / 0.898 ⟨12⟩ | 0.941 ⟨12⟩ / 0.898 ⟨12⟩ | 0.927 ⟨12⟩ / 0.922 ⟨12⟩ |
| ParseBench record match | 0.000 ⟨40⟩ / 0.000 ⟨38⟩ | 0.672 ⟨40⟩ / 0.668 ⟨38⟩ | 0.551 ⟨40⟩ / 0.345 ⟨38⟩ | 0.556 ⟨40⟩ / 0.369 ⟨38⟩ | 0.686 ⟨40⟩ / 0.670 ⟨38⟩ | 0.686 ⟨40⟩ / 0.670 ⟨38⟩ | missing / missing | 0.560 ⟨40⟩ / 0.345 ⟨38⟩ | 0.565 ⟨40⟩ / 0.369 ⟨38⟩ | 0.416 ⟨40⟩ / 0.518 ⟨38⟩ |
| ParseBench table GriTS | 0.015 ⟨40⟩ / 0.008 ⟨38⟩ | 0.776 ⟨40⟩ / 0.764 ⟨38⟩ | 0.708 ⟨40⟩ / 0.569 ⟨38⟩ | 0.710 ⟨40⟩ / 0.586 ⟨38⟩ | 0.786 ⟨40⟩ / 0.757 ⟨38⟩ | 0.786 ⟨40⟩ / 0.757 ⟨38⟩ | missing / missing | 0.710 ⟨40⟩ / 0.570 ⟨38⟩ | 0.713 ⟨40⟩ / 0.587 ⟨38⟩ | 0.646 ⟨40⟩ / 0.705 ⟨38⟩ |
| ParseBench text content | 0.649 ⟨40⟩ / 0.647 ⟨40⟩ | 0.646 ⟨40⟩ / 0.648 ⟨40⟩ | 0.676 ⟨40⟩ / 0.651 ⟨40⟩ | 0.676 ⟨40⟩ / 0.651 ⟨40⟩ | 0.676 ⟨40⟩ / 0.651 ⟨40⟩ | 0.676 ⟨40⟩ / 0.651 ⟨40⟩ | missing / missing | 0.686 ⟨40⟩ / 0.687 ⟨40⟩ | 0.686 ⟨40⟩ / 0.687 ⟨40⟩ | 0.620 ⟨40⟩ / 0.636 ⟨40⟩ |
| ParseBench text formatting | 0.282 ⟨37⟩ / 0.326 ⟨36⟩ | 0.282 ⟨37⟩ / 0.326 ⟨36⟩ | 0.442 ⟨37⟩ / 0.494 ⟨36⟩ | 0.442 ⟨37⟩ / 0.494 ⟨36⟩ | 0.442 ⟨37⟩ / 0.494 ⟨36⟩ | 0.442 ⟨37⟩ / 0.494 ⟨36⟩ | missing / missing | 0.442 ⟨37⟩ / 0.487 ⟨36⟩ | 0.442 ⟨37⟩ / 0.487 ⟨36⟩ | 0.471 ⟨37⟩ / 0.469 ⟨36⟩ |
| ParseBench chart | 0.000 ⟨40⟩ / 0.000 ⟨39⟩ | 0.000 ⟨40⟩ / 0.019 ⟨39⟩ | 0.009 ⟨40⟩ / 0.033 ⟨39⟩ | 0.009 ⟨40⟩ / 0.033 ⟨39⟩ | 0.009 ⟨40⟩ / 0.033 ⟨39⟩ | 0.009 ⟨40⟩ / 0.033 ⟨39⟩ | missing / missing | 0.029 ⟨40⟩ / 0.033 ⟨39⟩ | 0.029 ⟨40⟩ / 0.033 ⟨39⟩ | 0.000 ⟨40⟩ / 0.045 ⟨39⟩ |
| CPU ms/page p50, all cores | 529 | 1526 | 701 | 687 | 885 | 864 | missing | 20 | 18 | 232 |
| CPU ms/page p95, all cores | 581 | 6402 | 805 | 754 | 1104 | 1074 | missing | 51 | 41 | 451 |
| CPU pages/s, all cores | 1.874 | 0.480 | 1.411 | 1.437 | 1.148 | 1.178 | missing | 41.345 | 47.985 | 4.072 |
| CPU peak RSS MB, all cores | 1327.9 | 1628.8 | 878.1 | 873.8 | 1000.0 | 957.4 | missing | 43.3 | 43.7 | 412.4 |
| CPU ms/page p50, 1 thread | 1482 | 4187 | 6147 | 6129 | 7144 | 7127 | missing | 20 | 18 | 231 |
| CPU ms/page p95, 1 thread | 1559 | 16797 | 6292 | 6261 | 8766 | 8730 | missing | 51 | 41 | 455 |
| CPU pages/s, 1 thread | 0.672 | 0.178 | 0.162 | 0.163 | 0.139 | 0.140 | missing | 41.391 | 47.740 | 4.067 |
| CPU peak RSS MB, 1 thread | 1300.3 | 1640.2 | 874.6 | 876.7 | 934.7 | 979.9 | missing | 42.8 | 43.6 | 425.4 |
| CPU cold start ms (median) | 6874 | 10280 | 1867 | 1868 | 2418 | 2368 | missing | 113 | 113 | 915 |

Internal rows (not publicly reproducible yet):
- pdfluent-best: the weights and the engine builds this column needs are not published; until the best tier ships, the row is measured internally and marked so in every report

Numbers here are measurements, not claims. Anything published goes through the claims register with its run ID and measurement date.
