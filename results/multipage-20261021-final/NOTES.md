# Multi-page speed, PDFluent 1.1.0 and the 1.2.0 release wheel, 21-10-2026 (evening)

Repeat of `../multipage-20261021/` on the final release wheel: `pdfluent-1.2.0-cp38-abi3-manylinux_2_17_x86_64.manylinux2014_x86_64.whl`,
SHA256 `ac45648f67a38a481520f0d459613269ad2ca26c1c780c48fbc26d044f9e4093`, built from engine commit `bd658c93d`.
Everything else as in the earlier directory: same set (`datasets/multipage-arxiv-40.json`), same script,
one spot c3-standard-8 (Xeon 8481C, europe-west4-a), 1.1.0 (PyPI image) and the wheel interleaved,
3 repetitions, 1 CPU (`--cpuset-cpus=0`) and all 8. Harness commit in `harness-commit.txt`
(data-only commits since `8aceaf6`; the scripts are unchanged). Images in `images.json`.
`summary.json` uses the same definitions as before (the earlier file is reproduced exactly from its `runs/`).

Text, render and save are byte-identical between the versions on all 40 documents; markdown differs on 20 of 40, as before.

Cost: one spot VM, about 13 minutes, about EUR 0.05 (estimate at EUR 0.18 per hour; the bill is the record). The VM was deleted by the script.
