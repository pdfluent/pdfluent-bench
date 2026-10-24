# odl-bench labels v1

`odl-bench-labels-v1.json` labels the 200 opendataloader-bench documents (story pdfluent-internal#969).

- Bench commit: `7af1d8f4d0c09f51ea1a5c6ba5f66e993286d109`. Manifest SHA-256 (400 files, PDFs and GT markdown): see `manifest_sha256` in the file.
- Per document: `primary_class` (one of A, T1, B, C, T2, D, E), `secondary_class` (or `none`), `has_table_in_gt`, `split`, scores for ours (balanced+TATR), opendataloader-hybrid, docling and nutrient (overall, nid, teds, mhs), and loss against the leader and Docling.
- Split: `tune` if the numeric doc ID is even, `holdout` if odd. 100 / 100.
- Class totals and losses reproduce the 24-10 numbers (5/20/4/47/14/13/97 documents).

The labels file was built by `release/plan-lead-odl-bench-scripts/build_odl_bench_labels.py` in the engine
release scripts (it needs the scoring scripts and the stored predictions of the other engines, which are not here).
In this repository `tests/test_odl_bench.py` re-derives the class totals and losses from the stored scores,
fails on a missing, duplicate or double-split document, checks the split rule, and `bench.odl.load_split`
refuses a corpus whose manifest hash differs. CI runs it on every push.
