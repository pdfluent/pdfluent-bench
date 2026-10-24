# pdfluent-bench

A benchmark harness for structured extraction from PDF: headings, lists,
tables, running text and reading order, plus speed and memory. It runs each
tool on the same pages, on the same machine, with the same scorer, and writes
everything a reader needs to rerun it: the tool versions, the container image
digests, the dataset manifests and the harness commit.

The results of one published round are in `results/`, one directory per run.
A number from a run is quoted elsewhere only with its claim ID, the run ID and
the date it was measured (see [Published figures](#published-figures)).

## What is measured

Configurations (`configs/*.yaml`, one file each, versions pinned):

| configuration | tool | model on the page image |
|---|---|---|
| `pdfluent-fast` | `pdfluent` `quality="fast"` | no |
| `pdfluent-balanced` | `pdfluent` `quality="balanced"` | yes, layout model |
| `pdfluent-balanced-tatr` | balanced plus the Table Transformer | yes, layout and table models |
| `pdfluent-best` | the ensemble of two layout models | yes; not publicly reproducible yet |
| `docling-standard` | Docling, layout model and TableFormer, OCR off | yes |
| `docling-minimal` | Docling, TableFormer off, OCR off | yes: Docling always runs its layout model |
| `pymupdf4llm` | `pymupdf4llm.to_markdown`, OCR off | no |

OCR is off for every configuration.

Datasets (see [DATASETS.md](DATASETS.md) for sources and licences): FinTabNet,
PubTables-1M, DocLayNet and ParseBench. Each set has a sealed manifest that
splits it into a tuning half and a holdout half by document, under a fixed
seed. The two halves are measured and reported separately.

Quality axes, scored per page and summarised per document:

- text: 1 minus the normalised edit distance over the page's running text
- words: order-free F1 over the words
- headings, lists: F1 over the annotated items
- tables: F1 over found tables, and TEDS over table structure where the set
  annotates cells
- reading order: agreement over matched blocks, against the annotated order
  and against a plain geometric order
- ParseBench: its own evaluator and its default metric per category

Speed, per configuration: milliseconds per page (p50 and p95), pages per
second, peak resident memory of the process tree, and cold start (a fresh
process converting one page) measured separately. Three repetitions by
default, at one thread and at all cores.

## Where the scorer comes from

The scorers and adapters under `engine/` are copied from the PDFluent engine
repository, with their engine-relative paths. `engine/PROVENANCE.json` names
the engine commit and the hash of every file and every scoring function.
`tests/test_scorer_equivalence.py` checks that the copied scorers give the
same output as the engine's own files on the fixture cases.
[PORTING.md](PORTING.md) lists what was changed and what was left out.

## Reproduce it in one command

```sh
make repro PY=python3.12
```

From a clean checkout this builds the pinned tool environments (`tools/*.txt`,
exact versions, one virtualenv per tool), runs the tests, checks the tree for
anything that should not be published, downloads a small public subset of real
ParseBench pages (7 per category) from their pinned revision, runs it through
`pymupdf4llm` and `pdfluent-fast`, and checks everything the run wrote against
the schema. It needs Python 3.12, `pdftotext` and `mutool` on the path, and network
access to PyPI and Hugging Face. It exits non-zero if any step fails; nothing is skipped quietly.

The full matrix (all configurations, all four sets, both halves) runs in the
containers under `docker/`, whose base image is pinned by digest. See below.

## Running it

Requirements: Python 3.12 or newer, `pip install -r requirements-dev.txt`,
and `pdftotext` (poppler-utils) and `mutool` (mupdf-tools) on the path.

```sh
make test                  # scorer tests, equivalence with the engine, harness tests
make smoke                 # fixture pages through pymupdf4llm and pdfluent-fast
make datasets-plan         # what a fetch downloads; fetches nothing
make datasets SETS=parsebench PROFILE=mini
make bench MACHINE=local CONFIGS=pymupdf4llm SETS=parsebench PROFILE=mini
make bench MACHINE=c3-standard-8 CONFIGS=all SETS=all SPLITS=tuning,holdout BECAUSE="monthly round" RUNNER=docker
make report RUNS="results/<cpu run> results/<gpu run>"
make compare RUNS="results/<cpu run> ..."   # paired per-document diffs, 95% bootstrap intervals
```

Each column runs in its own interpreter. Natively that is the interpreter in
`<TOOL>_PYTHON` (for example `PYMUPDF4LLM_PYTHON`), or the one running the
harness. With `RUNNER=docker` each column runs in its image; build the images
first with `make images`, which records their digests in `work/images.json`.

Measuring the holdout half needs `BECAUSE="<reason>"`. The reason is written
into every run file of that half.

A configuration that cannot run on the machine fails the run, unless it is
named in `OPTIONAL=`, in which case the run prints
`SKIPPED (not a pass): <reason>` and goes on.

## Results schema

Every file a run writes has a JSON Schema in [schema/](schema/):
`run.schema.json` (run.json), `quality.schema.json` (one configuration on one
half of one set), `speed.schema.json` and `report.schema.json`.
`python -m bench validate results/<run>` checks a run directory against them
and against the steps it claims to have finished; `make repro` and CI run it on
every subset run, and the tests run it on every committed result. A run that is
not valid is not a result.

## Output

`results/<run id>/`:

- `run.json`: machine type, CPU model, GPUs, image digests, harness commit,
  engine scorer commit, dataset manifest hashes, timestamps, wall clock, each
  step with its status, and a cost estimate for cloud machines
- `quality/<set>/<split>/<config>.json`: the ported harness's run file, with
  every page's scores and a `bench` block naming the configuration, version
  check, sample size and seed
- `speed/<cpu|gpu>/<config>.json`: every repetition, every lap
- `report.md`, `report.json`: one overview table, quality per axis per
  configuration and speed on CPU and GPU. A missing value is written `missing`
  and the report is then marked DRAFT; a GPU value for a configuration without
  a GPU route is `n/a` with the reason.

The markdown each tool wrote stays in `work/`; it contains dataset text and is
never copied into `results/`.

## Continuous integration

`.github/workflows/ci.yml` runs on a runner labelled `pdfluent-bench`, not on
hosted minutes. On every push it runs `make repro`: the tests, the surface
check, and the real-data subset above. No job is allowed to fail softly, and
`tests/test_workflows.py` fails if one is. The workflow reacts to `push` and
manual dispatch only, never to pull requests from forks, because a self-hosted
runner executes the code it is given. A full round is started by hand
(`workflow_dispatch`, `full: true`) and records its reason for measuring the
holdout half in every run file.

## A round on GCP

`infra/gcp/run_round.sh` runs a round on one spot VM per machine type
(`c3-standard-8` for CPU, `g2-standard-8` with one L4 for GPU) in project
`pdfluent`, region `europe-west4`. Without `CONFIRM=yes` it prints the plan,
the cost ceiling and the commands, and creates nothing. The ceiling is the
VM's capped lifetime at the on-demand price plus disk; the script refuses when
it exceeds `BUDGET_EUR` (default 20). Each VM is deleted when the script exits
and, failing that, by GCP when its lifetime runs out. `make cost
MACHINE=<type>` prints the time model behind the ceiling.

## Runs that are not cited

`runs-unclaimed/` holds two earlier rounds of 17-10 (`…T145209Z`, a partial
tuning-half run, and `…T153432Z`, a tuning-half run with speed). No claim cites
them; they are kept so the history of the measurements is complete. Only the
run directories in `results/` back published figures.

## Published figures

Figures from this harness are published only through the PDFluent claims
register, each with an ID, the run it comes from and the date it was measured.
The register is kept by PDFluent; the IDs below say which published figures come
from which run in this repository.

| claim | what it covers | run |
|---|---|---|
| A41 | average quality over the 19 axes, holdout half | `bench-c3-standard-8-20261017T163121Z` |
| A42 | table structure (TEDS), holdout, paired per-document intervals | same |
| A43 | ParseBench tables, holdout | same |
| A44 | the `fast` tier over the same axes, holdout | same |
| C22 | speed and memory on one `c3-standard-8` (and Docling on an L4) | same, and `bench-g2-standard-8-20261017T162220Z` |
| A38 to A40 | the tuning-half figures of the 14-10 round | reproduced exactly by the tuning half of the run above |

A figure that has no ID in the register is not a claim. The `best` tier is not
publicly reproducible yet and is marked internal in every report that includes
it.

## Licence

PDFluent is available under two licences, at your option: the GNU AGPLv3
([LICENSE](LICENSE)) or the PDFluent Commercial Licence
([LICENSE-COMMERCIAL](LICENSE-COMMERCIAL)). [LICENSE-OFFER](LICENSE-OFFER)
states the offer. This covers the files written for this repository and the
files under `engine/`, which are copied from the PDFluent engine and carry the
same header. The datasets are not part of this repository and keep their own
licences, listed in [DATASETS.md](DATASETS.md).

## More from PDFluent

- [PDFluent SDK](https://github.com/pdfluent/pdfluent-sdk) and its [documentation](https://pdfluent.com/docs): the engine under test.
- [How we benchmark](https://pdfluent.com/benchmarks/how-we-measure/) and the [published results](https://pdfluent.com/benchmarks/) on pdfluent.com.
- [pdfluent-mcp](https://github.com/pdfluent/pdfluent-mcp) and the [examples](https://github.com/pdfluent/examples).
- [PDFluent editor](https://github.com/pdfluent/pdfluent): a free desktop PDF editor, including for commercial use.
