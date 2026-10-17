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
- run `bench-c3-standard-8-20261017T163121Z` on c3-standard-8 (Intel(R) Xeon(R) Platinum 8481C CPU @ 2.70GHz; no GPU), harness cbdae12b9c6b, engine scorers e1f136aecf32, datasets 5fec3c16b54e, started 2026-10-17T16:31:21+00:00, wall clock 22401.5 s
- run `bench-g2-standard-8-20261017T162220Z` on g2-standard-8 (Intel(R) Xeon(R) CPU @ 2.20GHz; NVIDIA L4), harness cbdae12b9c6b, engine scorers e1f136aecf32, datasets 9cc3856426d8, started 2026-10-17T16:22:20+00:00, wall clock 632.8 s

Quality cells: document mean per split, tuning / holdout, with the number of documents (ParseBench: examples) in ⟨⟩; – where no document of that half is annotated for the axis. Speed cells: one value per configuration.

| row | docling-minimal | docling-standard | pdfluent-balanced | pdfluent-balanced-tatr | pdfluent-best (internal) | pdfluent-fast | pymupdf4llm |
|---|---|---|---|---|---|---|---|
| FinTabNet TEDS | 0.057 ⟨67⟩ / 0.057 ⟨39⟩ | 0.709 ⟨67⟩ / 0.750 ⟨39⟩ | 0.687 ⟨67⟩ / 0.696 ⟨39⟩ | 0.859 ⟨67⟩ / 0.868 ⟨39⟩ | missing / missing | 0.663 ⟨67⟩ / 0.677 ⟨39⟩ | 0.727 ⟨67⟩ / 0.779 ⟨39⟩ |
| FinTabNet tables F1 | 0.682 ⟨67⟩ / 0.656 ⟨39⟩ | 0.615 ⟨67⟩ / 0.612 ⟨39⟩ | 0.701 ⟨67⟩ / 0.658 ⟨39⟩ | 0.820 ⟨67⟩ / 0.738 ⟨39⟩ | missing / missing | 0.663 ⟨67⟩ / 0.625 ⟨39⟩ | 0.635 ⟨67⟩ / 0.640 ⟨39⟩ |
| FinTabNet words F1 | 0.836 ⟨67⟩ / 0.833 ⟨39⟩ | 0.793 ⟨67⟩ / 0.795 ⟨39⟩ | 0.809 ⟨67⟩ / 0.827 ⟨39⟩ | 0.829 ⟨67⟩ / 0.838 ⟨39⟩ | missing / missing | 0.811 ⟨67⟩ / 0.790 ⟨39⟩ | 0.748 ⟨67⟩ / 0.724 ⟨39⟩ |
| PubTables-1M TEDS | 0.034 ⟨55⟩ / 0.053 ⟨46⟩ | 0.825 ⟨55⟩ / 0.878 ⟨46⟩ | 0.717 ⟨55⟩ / 0.778 ⟨46⟩ | 0.912 ⟨55⟩ / 0.955 ⟨46⟩ | missing / missing | 0.698 ⟨55⟩ / 0.762 ⟨46⟩ | 0.830 ⟨55⟩ / 0.862 ⟨46⟩ |
| PubTables-1M tables F1 | 0.923 ⟨55⟩ / 0.957 ⟨46⟩ | 0.871 ⟨55⟩ / 0.928 ⟨46⟩ | 0.848 ⟨55⟩ / 0.877 ⟨46⟩ | 0.923 ⟨55⟩ / 0.951 ⟨46⟩ | missing / missing | 0.802 ⟨55⟩ / 0.850 ⟨46⟩ | 0.906 ⟨55⟩ / 0.938 ⟨46⟩ |
| PubTables-1M words F1 | 0.886 ⟨55⟩ / 0.867 ⟨46⟩ | 0.868 ⟨55⟩ / 0.872 ⟨46⟩ | 0.914 ⟨55⟩ / 0.876 ⟨46⟩ | 0.930 ⟨55⟩ / 0.894 ⟨46⟩ | missing / missing | 0.894 ⟨55⟩ / 0.875 ⟨46⟩ | 0.856 ⟨55⟩ / 0.871 ⟨46⟩ |
| DocLayNet headings F1 | 0.835 ⟨13⟩ / 0.782 ⟨11⟩ | 0.835 ⟨13⟩ / 0.782 ⟨11⟩ | 0.838 ⟨13⟩ / 0.874 ⟨11⟩ | 0.838 ⟨13⟩ / 0.874 ⟨11⟩ | missing / missing | 0.758 ⟨13⟩ / 0.774 ⟨11⟩ | 0.790 ⟨13⟩ / 0.726 ⟨11⟩ |
| DocLayNet lists F1 | 0.676 ⟨12⟩ / 0.667 ⟨12⟩ | 0.676 ⟨12⟩ / 0.667 ⟨12⟩ | 0.721 ⟨12⟩ / 0.803 ⟨12⟩ | 0.722 ⟨12⟩ / 0.803 ⟨12⟩ | missing / missing | 0.674 ⟨12⟩ / 0.781 ⟨12⟩ | 0.707 ⟨12⟩ / 0.815 ⟨12⟩ |
| DocLayNet tables F1 | 0.617 ⟨8⟩ / 0.653 ⟨6⟩ | 0.576 ⟨8⟩ / 0.393 ⟨6⟩ | 0.540 ⟨8⟩ / 0.319 ⟨6⟩ | 0.589 ⟨8⟩ / 0.424 ⟨6⟩ | missing / missing | 0.531 ⟨8⟩ / 0.406 ⟨6⟩ | 0.760 ⟨8⟩ / 0.693 ⟨6⟩ |
| DocLayNet text 1-NED | 0.593 ⟨13⟩ / 0.585 ⟨12⟩ | 0.586 ⟨13⟩ / 0.574 ⟨12⟩ | 0.592 ⟨13⟩ / 0.568 ⟨12⟩ | 0.594 ⟨13⟩ / 0.567 ⟨12⟩ | missing / missing | 0.581 ⟨13⟩ / 0.553 ⟨12⟩ | 0.583 ⟨13⟩ / 0.564 ⟨12⟩ |
| DocLayNet text 1-NED (geometric) | 0.668 ⟨13⟩ / 0.617 ⟨12⟩ | 0.661 ⟨13⟩ / 0.616 ⟨12⟩ | 0.710 ⟨13⟩ / 0.635 ⟨12⟩ | 0.712 ⟨13⟩ / 0.636 ⟨12⟩ | missing / missing | 0.688 ⟨13⟩ / 0.606 ⟨12⟩ | 0.676 ⟨13⟩ / 0.639 ⟨12⟩ |
| DocLayNet words F1 | 0.924 ⟨12⟩ / 0.929 ⟨12⟩ | 0.922 ⟨12⟩ / 0.923 ⟨12⟩ | 0.923 ⟨12⟩ / 0.913 ⟨12⟩ | 0.925 ⟨12⟩ / 0.914 ⟨12⟩ | missing / missing | 0.913 ⟨12⟩ / 0.913 ⟨12⟩ | 0.894 ⟨12⟩ / 0.894 ⟨12⟩ |
| DocLayNet order (annotated) | 0.776 ⟨12⟩ / 0.678 ⟨12⟩ | 0.776 ⟨12⟩ / 0.694 ⟨12⟩ | 0.782 ⟨12⟩ / 0.720 ⟨12⟩ | 0.778 ⟨12⟩ / 0.718 ⟨12⟩ | missing / missing | 0.782 ⟨12⟩ / 0.708 ⟨12⟩ | 0.786 ⟨12⟩ / 0.719 ⟨12⟩ |
| DocLayNet order (geometric) | 0.913 ⟨12⟩ / 0.865 ⟨12⟩ | 0.913 ⟨12⟩ / 0.862 ⟨12⟩ | 0.946 ⟨12⟩ / 0.908 ⟨12⟩ | 0.945 ⟨12⟩ / 0.908 ⟨12⟩ | missing / missing | 0.941 ⟨12⟩ / 0.898 ⟨12⟩ | 0.927 ⟨12⟩ / 0.922 ⟨12⟩ |
| ParseBench record match | 0.000 ⟨40⟩ / 0.000 ⟨38⟩ | 0.672 ⟨40⟩ / 0.668 ⟨38⟩ | 0.551 ⟨40⟩ / 0.345 ⟨38⟩ | 0.686 ⟨40⟩ / 0.670 ⟨38⟩ | missing / missing | 0.560 ⟨40⟩ / 0.345 ⟨38⟩ | 0.416 ⟨40⟩ / 0.518 ⟨38⟩ |
| ParseBench table GriTS | 0.015 ⟨40⟩ / 0.008 ⟨38⟩ | 0.776 ⟨40⟩ / 0.764 ⟨38⟩ | 0.708 ⟨40⟩ / 0.569 ⟨38⟩ | 0.786 ⟨40⟩ / 0.757 ⟨38⟩ | missing / missing | 0.710 ⟨40⟩ / 0.570 ⟨38⟩ | 0.646 ⟨40⟩ / 0.705 ⟨38⟩ |
| ParseBench text content | 0.649 ⟨40⟩ / 0.647 ⟨40⟩ | 0.646 ⟨40⟩ / 0.648 ⟨40⟩ | 0.676 ⟨40⟩ / 0.651 ⟨40⟩ | 0.676 ⟨40⟩ / 0.651 ⟨40⟩ | missing / missing | 0.686 ⟨40⟩ / 0.687 ⟨40⟩ | 0.620 ⟨40⟩ / 0.636 ⟨40⟩ |
| ParseBench text formatting | 0.282 ⟨37⟩ / 0.326 ⟨36⟩ | 0.282 ⟨37⟩ / 0.326 ⟨36⟩ | 0.442 ⟨37⟩ / 0.494 ⟨36⟩ | 0.442 ⟨37⟩ / 0.494 ⟨36⟩ | missing / missing | 0.442 ⟨37⟩ / 0.487 ⟨36⟩ | 0.471 ⟨37⟩ / 0.469 ⟨36⟩ |
| ParseBench chart | 0.000 ⟨40⟩ / 0.000 ⟨39⟩ | 0.000 ⟨40⟩ / 0.019 ⟨39⟩ | 0.009 ⟨40⟩ / 0.033 ⟨39⟩ | 0.009 ⟨40⟩ / 0.033 ⟨39⟩ | missing / missing | 0.029 ⟨40⟩ / 0.033 ⟨39⟩ | 0.000 ⟨40⟩ / 0.045 ⟨39⟩ |
| CPU ms/page p50, all cores | 529 | 1526 | 686 | 885 | missing | 19 | 232 |
| CPU ms/page p95, all cores | 581 | 6402 | 789 | 1105 | missing | 51 | 451 |
| CPU pages/s, all cores | 1.874 | 0.480 | 1.433 | 1.158 | missing | 42.373 | 4.072 |
| CPU peak RSS MB, all cores | 1327.9 | 1628.8 | 853.7 | 964.0 | missing | 43.0 | 412.4 |
| CPU ms/page p50, 1 thread | 1482 | 4187 | 5802 | 7053 | missing | 20 | 231 |
| CPU ms/page p95, 1 thread | 1559 | 16797 | 5968 | 8821 | missing | 50 | 455 |
| CPU pages/s, 1 thread | 0.672 | 0.178 | 0.172 | 0.141 | missing | 42.301 | 4.067 |
| CPU peak RSS MB, 1 thread | 1300.3 | 1640.2 | 874.9 | 943.7 | missing | 43.5 | 425.4 |
| CPU cold start ms (median) | 6874 | 10280 | 1816 | 2368 | missing | 113 | 915 |
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
