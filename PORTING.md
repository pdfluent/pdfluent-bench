# What came from the engine, and what changed

The quality rig under `engine/` is vendored from the PDFluent engine
repository by `scripts/port_from_engine.py`. The engine commit, the SHA-256 of
every file before and after, and a hash of every scoring function are in
`engine/PROVENANCE.json`. Files keep their engine-relative paths, so the
modules' own `REPO / ...` paths resolve inside `engine/` unchanged.

To re-vendor from a newer engine commit:

    python3 scripts/port_from_engine.py --engine /path/to/engine --ref <commit>

The script reads with `git show` only and fails if a patch no longer matches
or if a scoring function differs from the engine.

## Comment scrub

Every ported `.py` file passes through `scrub()` in `port_from_engine.py`. It
rewrites comments only: it removes the name of the review tool from
`(<tool>, #1922)` attributions (the issue number stays), the name of the
engine's cloud provider and ledger script, and two home-directory paths in
`subset_scorer.py`. `PROVENANCE.json` marks such a file `scrubbed: true`, and
a scoring function whose comments were scrubbed is checked against the
engine's source after the same scrub. `patched` stays reserved for changes to
code, and the scorer modules are never patched.

## Copied unchanged

`structured_axes.py` (text, words, headings, lists, tables F1, TEDS, reading
order), `pubtables_cells.py`, `pb_summary.py`, `structured_holdout_rule.py`
(split rule and seeds), `fintabnet_archive.py`, `fintabnet_split.py`,
`parsebench_harness.py`, `parsebench_manifest.py`, the v2 lexicon, the
adapters `extract_text.py` and `teacher_page.py` with `requirements-best.txt`,
`learned/align_labels.py`, `learned/header_band_subset.txt`, five engine tests
of `structured_bench.py`, `test_fintabnet_split.py`, and the sealed manifests
under `corpus/structured/` (page names and digests only).

## Patched (behaviour, never scoring)

`scripts/ci/loop/structured_bench.py`

| patch | why |
|---|---|
| rig-data guard calls removed | The engine guard enforces an internal training-data rule (rig pages exist only on the round machine). It does not apply here and refuses every local fetch on macOS. |
| `RUNS` takes `BENCH_RUNS_DIR` | Run files go into the bench result directory. |
| competitor cache opt-in via `BENCH_COMPETITOR_CACHE` | A benchmark run measures every column it reports. With the variable unset nothing is read from or written to a cache. |
| `check_budget` returns at once | The engine's cost ledger and the one-run-per-head rule are its internal round policy. The cost guard for this repository is `infra/gcp` (`BUDGET_EUR`). |
| `ADAPTED` gains four names | `docling_minimal`, `pdfluent_fast`, `pdfluent_balanced`, `pdfluent_balanced_tatr` run through the same batch path as the ported columns. |

`scripts/extraction/parsebench_round.py`: a `--split` argument (the engine
measured the tuning half only), and the same opt-in cache.

`scripts/extraction/adapters/extract_structured.py`: imports
`bench/adapters/bench_columns.py` and adds its four columns to the tool table;
the self-test's expected tool set follows.

The two registers (`DATASETS.toml`, `EXTRACTORS.toml`) are reduced to the rows
this benchmark runs, with their fields unchanged. `EXTRACTORS.toml` gets the
four bench rows from `scripts/bench_extractors.toml`.

## Not ported

| engine file | reason |
|---|---|
| `scripts/extraction/structured_bench_remote.sh` | The engine's remote round driver. Its parts map to: tool installs to `docker/*/Dockerfile`; the per-set fetch, draw and measure to `bench/quality.py`; the memory ceiling to `docker run --memory`; evidence shipping to the result directory. |
| `scripts/ci/loop/test_structured_bench_remote_sigpipe.py` | Tests the remote script above. |
| `scripts/extraction/benchmark_page.py` | The engine's public page, built on `structured_parity.py` and claim IDs in `PAGE.toml`. Replaced by `bench/report.py`, which keeps its rules: every number comes from a run file, each cell carries its denominator, and the report is marked DRAFT while anything is missing. |
| `scripts/extraction/adapters/test_best_render.py` | Needs the engine's `render_page_png` build. |
| `scripts/ci/rig_data_stays_on_the_rig.py` | Internal training-data policy. |
| `structured_bench.py --self-test` | Still present but not supported: it reads the remote script and other engine files that are not vendored. The scorer is covered by `tests/`. |

The round scripts kept outside the engine (`run-round.sh`, `round_numbers.py`)
are not vendored. What they do is in `bench/quality.py` (the per-set order:
fetch, draw, measure) and `bench/report.py` (per-axis means read from the run
files, with a refusal when an expected column is missing).

## Columns that differ from the engine rounds

The engine rounds ran `ours` and `balanced` as source builds of the engine
CLI, one process per page. Here the PDFluent columns run the published
`pdfluent` package through the adapter's batch path, like every competitor.
The engine-only extras that hang off the `ours` key (`set_in` typography,
geometry kept for tail pages, the second batch clock) are therefore not
produced. `pdfluent-best` runs the ported `teacher_ens` adapter; it needs the
engine's `structured_markdown`, `render_page_png` and `feature_dump` builds and
the two weight files, none of which are published yet.
