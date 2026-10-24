# odl-bench

The 200 documents of opendataloader-bench (DP-Bench), scored with its own scorer,
one PDFluent recipe at a time, with a regression gate. Story pdfluent-internal#960.

What is here: `recipes.toml` (the recipes), `manifest.json` (SHA-256 of the 200 PDFs
and 200 ground-truth files), `split-v1.json` (tune/holdout per document, no scores;
even IDs tune, odd holdout; the full class labels of #969 carry other engines' scores and stay private), `prepare_env.sh`, and the runner
`python -m bench.odl` (`bench/odl.py`). What is not here: the corpus. The runner
clones opendataloader-bench at `7af1d8f4d0c09f51ea1a5c6ba5f66e993286d109` and
uses its scorer unchanged. Baselines hold numbers and live with the caller
(the engine repository), not in this public repository.

## Run

    odl-bench/prepare_env.sh venv --version 1.2.0        # or: --wheel path/to/pdfluent-....whl
    venv/bin/python -m bench.odl run --fetch --clone work/odl --recipe fast+footers \
        --baseline path/to/baseline-fast+footers.json --wheel path/to/the.whl --out results/odl-run

Order: pin (HEAD must be the pinned commit), corpus hashes (a changed PDF or
ground-truth file fails by file name), baseline and labels, models, extraction,
scoring, gate. Scores are reported for `all`, `tune` and `holdout`. Output in
`--out`: `run.json` (machine, wheel hash, bench commit, manifest hash, recipe,
wall time, scores, violations), `scores.json`, and the scorer's per-document
`prediction/<recipe>/evaluation.json`.

Exit codes: 0 passed, 1 gate failed, 2 cannot judge (wrong pin, corpus hash,
missing baseline or labels file, scorer not importable), 3 skipped.

## The gate

Against the baseline file: for `all`, `tune` and `holdout`, each of overall, NID,
TEDS, MHS may be at most `tolerance` (0.005) below the baseline; each canary
document (031, 016, 036, 110) may be at most that far below its own score. A
failure line names recipe, scope, metric and the documents that lost most:

    recipe=fast+footers scope=all metric=overall: <got> is below baseline <floor> - tolerance 0.005 (drop <d>); largest per-document drops: <doc> <was>-><now>, ...

A document whose extraction raises also fails the run. The baseline is a decision,
never an automatic update:

    python -m bench.odl run ... --no-gate --out results/odl-new     # records, does not judge
    python -m bench.odl baseline results/odl-new --out baseline-<recipe>.json

## Split and labels

The runner reads `odl-bench/split-v1.json` (document id to tune or holdout, with its
SHA-256, bound to the bench commit and corpus manifest). The private labels file can be passed
instead with `--labels FILE`; its tests run when `ODL_LABELS=FILE` is set (engine CI).
`python -m bench.odl split --labels FILE` regenerates the public file from it.

## Models (balanced recipes)

    export PDFLUENT_MODEL_DIR=<directory with the layout model and tatr-v1.1-all>
    export ORT_DYLIB_PATH=<path to libonnxruntime (.dylib / .so / .dll)>

If either is absent the run prints `SKIPPED (not a pass): <reason>` on stderr and
exits 3, never 0. `--allow-skip` makes the exit code 0 for a job that may run
without models; the line stays in the log.

## Not here yet

The competitor rerun with the paired bootstrap interval (opendataloader, Docling,
Nutrient in one job) and the 19-axes holdout call. Nothing from a run is quoted
anywhere without a `CLAIMS.md` row.
