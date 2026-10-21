**DRAFT: this report is missing values.** Each is listed here and written `missing` in the table.

- CPU speed ms/page p50 (all threads): pdfluent-balanced-1.2.0 has no speed file
- CPU speed ms/page p50 (all threads): pdfluent-balanced-tatr-1.2.0 has no speed file
- CPU speed ms/page p50 (all threads): pdfluent-fast-1.2.0 has no speed file
- CPU speed ms/page p95 (all threads): pdfluent-balanced-1.2.0 has no speed file
- CPU speed ms/page p95 (all threads): pdfluent-balanced-tatr-1.2.0 has no speed file
- CPU speed ms/page p95 (all threads): pdfluent-fast-1.2.0 has no speed file
- CPU speed pages/s (all threads): pdfluent-balanced-1.2.0 has no speed file
- CPU speed pages/s (all threads): pdfluent-balanced-tatr-1.2.0 has no speed file
- CPU speed pages/s (all threads): pdfluent-fast-1.2.0 has no speed file
- CPU speed peak RSS MB (all threads): pdfluent-balanced-1.2.0 has no speed file
- CPU speed peak RSS MB (all threads): pdfluent-balanced-tatr-1.2.0 has no speed file
- CPU speed peak RSS MB (all threads): pdfluent-fast-1.2.0 has no speed file
- CPU speed ms/page p50 (1 threads): pdfluent-balanced-1.2.0 has no speed file
- CPU speed ms/page p50 (1 threads): pdfluent-balanced-tatr-1.2.0 has no speed file
- CPU speed ms/page p50 (1 threads): pdfluent-fast-1.2.0 has no speed file
- CPU speed ms/page p95 (1 threads): pdfluent-balanced-1.2.0 has no speed file
- CPU speed ms/page p95 (1 threads): pdfluent-balanced-tatr-1.2.0 has no speed file
- CPU speed ms/page p95 (1 threads): pdfluent-fast-1.2.0 has no speed file
- CPU speed pages/s (1 threads): pdfluent-balanced-1.2.0 has no speed file
- CPU speed pages/s (1 threads): pdfluent-balanced-tatr-1.2.0 has no speed file
- CPU speed pages/s (1 threads): pdfluent-fast-1.2.0 has no speed file
- CPU speed peak RSS MB (1 threads): pdfluent-balanced-1.2.0 has no speed file
- CPU speed peak RSS MB (1 threads): pdfluent-balanced-tatr-1.2.0 has no speed file
- CPU speed peak RSS MB (1 threads): pdfluent-fast-1.2.0 has no speed file
- CPU cold start: pdfluent-balanced-1.2.0 has no value
- CPU cold start: pdfluent-balanced-tatr-1.2.0 has no value
- CPU cold start: pdfluent-fast-1.2.0 has no value

# Benchmark overview

- run `bench-n2-standard-8-20261021T085113Z` on n2-standard-8 (Intel(R) Xeon(R) CPU @ 2.80GHz; no GPU), harness 805ada921160, engine scorers e1f136aecf32, datasets 5fec3c16b54e, started 2026-10-21T08:51:13+00:00, wall clock 8140.0 s

Quality cells: document mean per split, tuning / holdout, with the number of documents (ParseBench: examples) in ⟨⟩; – where no document of that half is annotated for the axis. Speed cells: one value per configuration.

| row | pdfluent-balanced-1.2.0 | pdfluent-balanced-tatr-1.2.0 | pdfluent-fast-1.2.0 |
|---|---|---|---|
| FinTabNet TEDS | 0.692 ⟨67⟩ / 0.707 ⟨39⟩ | 0.859 ⟨67⟩ / 0.868 ⟨39⟩ | 0.670 ⟨67⟩ / 0.688 ⟨39⟩ |
| FinTabNet tables F1 | 0.704 ⟨67⟩ / 0.698 ⟨39⟩ | 0.820 ⟨67⟩ / 0.738 ⟨39⟩ | 0.673 ⟨67⟩ / 0.665 ⟨39⟩ |
| FinTabNet words F1 | 0.809 ⟨67⟩ / 0.825 ⟨39⟩ | 0.829 ⟨67⟩ / 0.838 ⟨39⟩ | 0.815 ⟨67⟩ / 0.795 ⟨39⟩ |
| PubTables-1M TEDS | 0.720 ⟨55⟩ / 0.778 ⟨46⟩ | 0.911 ⟨55⟩ / 0.955 ⟨46⟩ | 0.701 ⟨55⟩ / 0.762 ⟨46⟩ |
| PubTables-1M tables F1 | 0.848 ⟨55⟩ / 0.877 ⟨46⟩ | 0.923 ⟨55⟩ / 0.951 ⟨46⟩ | 0.802 ⟨55⟩ / 0.850 ⟨46⟩ |
| PubTables-1M words F1 | 0.914 ⟨55⟩ / 0.876 ⟨46⟩ | 0.930 ⟨55⟩ / 0.894 ⟨46⟩ | 0.894 ⟨55⟩ / 0.875 ⟨46⟩ |
| DocLayNet headings F1 | 0.838 ⟨13⟩ / 0.874 ⟨11⟩ | 0.838 ⟨13⟩ / 0.874 ⟨11⟩ | 0.760 ⟨13⟩ / 0.774 ⟨11⟩ |
| DocLayNet lists F1 | 0.721 ⟨12⟩ / 0.803 ⟨12⟩ | 0.722 ⟨12⟩ / 0.803 ⟨12⟩ | 0.674 ⟨12⟩ / 0.781 ⟨12⟩ |
| DocLayNet tables F1 | 0.531 ⟨8⟩ / 0.314 ⟨6⟩ | 0.589 ⟨8⟩ / 0.424 ⟨6⟩ | 0.522 ⟨8⟩ / 0.417 ⟨6⟩ |
| DocLayNet text 1-NED | 0.590 ⟨13⟩ / 0.568 ⟨12⟩ | 0.594 ⟨13⟩ / 0.567 ⟨12⟩ | 0.578 ⟨13⟩ / 0.554 ⟨12⟩ |
| DocLayNet text 1-NED (geometric) | 0.707 ⟨13⟩ / 0.635 ⟨12⟩ | 0.712 ⟨13⟩ / 0.635 ⟨12⟩ | 0.686 ⟨13⟩ / 0.606 ⟨12⟩ |
| DocLayNet words F1 | 0.923 ⟨12⟩ / 0.913 ⟨12⟩ | 0.925 ⟨12⟩ / 0.914 ⟨12⟩ | 0.913 ⟨12⟩ / 0.913 ⟨12⟩ |
| DocLayNet order (annotated) | 0.781 ⟨12⟩ / 0.720 ⟨12⟩ | 0.778 ⟨12⟩ / 0.718 ⟨12⟩ | 0.781 ⟨12⟩ / 0.708 ⟨12⟩ |
| DocLayNet order (geometric) | 0.946 ⟨12⟩ / 0.908 ⟨12⟩ | 0.945 ⟨12⟩ / 0.908 ⟨12⟩ | 0.941 ⟨12⟩ / 0.898 ⟨12⟩ |
| ParseBench record match | 0.556 ⟨40⟩ / 0.369 ⟨38⟩ | 0.686 ⟨40⟩ / 0.670 ⟨38⟩ | 0.565 ⟨40⟩ / 0.369 ⟨38⟩ |
| ParseBench table GriTS | 0.710 ⟨40⟩ / 0.586 ⟨38⟩ | 0.786 ⟨40⟩ / 0.757 ⟨38⟩ | 0.713 ⟨40⟩ / 0.587 ⟨38⟩ |
| ParseBench text content | 0.676 ⟨40⟩ / 0.651 ⟨40⟩ | 0.676 ⟨40⟩ / 0.651 ⟨40⟩ | 0.686 ⟨40⟩ / 0.687 ⟨40⟩ |
| ParseBench text formatting | 0.442 ⟨37⟩ / 0.494 ⟨36⟩ | 0.442 ⟨37⟩ / 0.494 ⟨36⟩ | 0.442 ⟨37⟩ / 0.487 ⟨36⟩ |
| ParseBench chart | 0.009 ⟨40⟩ / 0.033 ⟨39⟩ | 0.009 ⟨40⟩ / 0.033 ⟨39⟩ | 0.029 ⟨40⟩ / 0.033 ⟨39⟩ |
| CPU ms/page p50, all cores | missing | missing | missing |
| CPU ms/page p95, all cores | missing | missing | missing |
| CPU pages/s, all cores | missing | missing | missing |
| CPU peak RSS MB, all cores | missing | missing | missing |
| CPU ms/page p50, 1 thread | missing | missing | missing |
| CPU ms/page p95, 1 thread | missing | missing | missing |
| CPU pages/s, 1 thread | missing | missing | missing |
| CPU peak RSS MB, 1 thread | missing | missing | missing |
| CPU cold start ms (median) | missing | missing | missing |

Numbers here are measurements, not claims. Anything published goes through the claims register with its run ID and measurement date.
