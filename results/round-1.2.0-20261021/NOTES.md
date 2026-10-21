# SDK 1.2.0 release-candidate round, 21-10-2026

This directory combines three runs into one view (`report.md`, `compare.md`).
Nothing here is a claim; figures are published only through the claims register.

| run | what | machine |
|---|---|---|
| `bench-c3-standard-8-20261021T090020Z` | speed, PDFluent 1.1.0 and 1.2.0 interleaved in one run, 3 repetitions, 1 thread and all cores, 75 pages | c3-standard-8 (Xeon 8481C), spot |
| `bench-n2-standard-8-20261021T085113Z` | quality, PDFluent 1.2.0 (`fast`, `balanced`, `balanced + table model`), tuning and holdout, four sets | n2-standard-8, spot |
| `bench-c3-standard-8-20261017T163121Z` | quality of PDFluent 1.1.0, Docling 2.126.0, pymupdf4llm 1.28.2; speed of Docling and pymupdf4llm (not re-measured, owner decision 21-10) | c3-standard-8 |

Competitor columns in the combined files are the 17-10 measurements. PDFluent
1.1.0 quality is also the 17-10 measurement; 1.1.0 speed is the 21-10
measurement beside 1.2.0.

## Candidate bytes

- Wheel: `pdfluent-1.2.0-cp38-abi3-manylinux_2_17_x86_64.manylinux2014_x86_64.whl`,
  SHA256 `abc5ec36dc858acb7c3acb66d7e5ae646d5601a444f12bd3e3f29e4f95be0709`,
  from the engine's build-wheels run 39522471892 at commit `d6f8bb8a18d7bd040e20816b0d337dadd03af45d`.
  The image build checks the SHA256 and `freeze/pdfluent-rc.txt` records it.
- Models: installed with the released 1.1.0 CLI (`pdfluent models install layout tables`).
  The model pins in `crates/pdf-engine/src/model.rs` are the same at v1.1.0 and
  at the release head (`b7cc5d31…` for the layout model is present at both).
- Release head at measurement time: `b463f45650eb2c23ea83cd36cdf95493ccd16f82`.
  `git diff --stat d6f8bb8a1 b463f45650` restricted to `crates/pdf-engine`,
  `pdf-render`, `pdf-interpret`, `pdf-syntax`, `pdf-extract`, `lopdf` and
  `pdf-python` touches only `pdf-python/src/lib.rs`, `src/compat/document.rs`
  and a test: image-edit publishing (`publish_image_edit`, `reparse_engine`
  made `pub(crate)`). No extraction or rendering code. The other changes are in
  `pdf-manip` (image edit), `pdf-redact` (sidecar sweep) and `pdfluent/src/document.rs`.
  The measured wheel is therefore representative of the release head's
  extraction path; this is a code-path argument, not a re-measurement.

## Comparability

- Datasets-manifest hash `5fec3c16b54e2881b202f53b8e084577212902416009975dca813d43ee9b562d`
  in all three runs; engine scorers `e1f136aecf32…`; same page counts per set and split.
- The quality shard ran on an n2-standard-8, not a c3-standard-8, because the
  project's C3 quota (11 vCPUs) allows one c3-standard-8 at a time and the
  speed shard needed it. Scores do not depend on the machine: PDFluent
  `balanced + table model` 1.2.0 equals 1.1.0 (17-10, c3) to three decimals on
  all 19 axes in both halves. Speed figures come from the c3 run only.
- Speed uses the sealed speed subset: 75 single-page PDFs (25 each from
  FinTabNet, PubTables-1M and DocLayNet). Per-lap clock resolution is one whole
  millisecond, so a p50 of 18 against 20 ms is a difference of two ticks;
  pages per second and p95 carry the same information with finer resolution.
  Multi-page documents are not in this subset.
- `pdfluent-best` was not measured (not publicly reproducible).
- Harness commits: speed `ff4eb27`, quality `805ada9`; the commits between
  them change only the VM setup, the retry of a dataset fetch, the machine
  table and the container's `jsonschema` requirement.

## Cost

Spot wall clock: speed 6 880 s, quality 8 140 s; modelled spot cost EUR 0.34
and EUR 0.23 (`run.json`). Three earlier launches failed before measuring
anything (a missing `jsonschema` in the container, a dataset fetch ending
early, a vCPU quota clash); they ran 3 to 10 minutes each. Estimate of the
whole round: below EUR 1.50. The bill is the record.
