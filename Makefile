# pdfluent-bench. `make help` lists the targets.
PY       ?= python3
MACHINE  ?= local
CONFIGS  ?= all
SETS     ?= all
SPLITS   ?= tuning
PROFILE  ?= full
REPS     ?= 3
RUNNER   ?= native
WORK     ?= work
OPTIONAL ?=
BECAUSE  ?=
RUNS     ?=
# Extra `bench run` flags, e.g. EXTRA=--no-speed for a quality-only shard.
EXTRA    ?=
FAMILIES ?= pdfluent docling pymupdf4llm
TOOLS    ?= $(WORK)/tools
# The public subset `make repro` and CI run: ParseBench, 7 pages per category,
# two columns that install from PyPI alone.
REPRO_CONFIGS ?= pymupdf4llm,pdfluent-fast
REPRO_SETS    ?= parsebench

.PHONY: axes-combine axes-gate help test bench smoke report compare datasets datasets-plan images cost fixtures goldens port tools repro validate public-check odl-verify odl-run

help:
	@echo "make repro                        the whole public subset from a clean checkout: pinned tools,"
	@echo "                                  tests, real ParseBench pages, run, schema check, surface check"
	@echo "make test                         unit tests: scorers, equivalence, harness"
	@echo "make validate RUNS='results/<id>' check run directories against schema/"
	@echo "make odl-verify                  odl-bench: clone the pinned commit, check the corpus hashes"
	@echo "make odl-run RECIPE=fast+footers BASELINE=<file>   odl-bench: one recipe, gated (see odl-bench/README.md)"
	@echo "make public-check                 no home paths, tool names or credentials in the tree or history"
	@echo "make bench MACHINE=<type> CONFIGS=<list|all> SETS=<list|all> [SPLITS=tuning,holdout BECAUSE=...]"
	@echo "                                  [PROFILE=full|mini] [REPS=3] [RUNNER=native|docker]"
	@echo "make smoke                        fixture pages, pymupdf4llm and pdfluent-fast"
	@echo "make datasets SETS=<list|all>     fetch the sets from their public sources into WORK"
	@echo "make datasets-plan SETS=...       list what a fetch downloads, fetch nothing"
	@echo "make images [FAMILIES=...]        build the containers and record their digests"
	@echo "make report RUNS='results/<id> ...'"
	@echo "make compare RUNS='results/<id> ...'  paired per-document diffs with 95% intervals"
	@echo "make cost MACHINE=c3-standard-8   modelled round time and the cost guard"
	@echo "infra/gcp/run_round.sh            a round on GCP (read it first; it spends money)"

test:
	$(PY) -m pytest -q tests

bench:
	$(PY) -m bench run --machine $(MACHINE) --configs $(CONFIGS) --sets $(SETS) \
	  --splits $(SPLITS) --profile $(PROFILE) --reps $(REPS) --runner $(RUNNER) --work $(WORK) \
	  $(if $(OPTIONAL),--optional $(OPTIONAL)) $(if $(BECAUSE),--because "$(BECAUSE)") $(EXTRA)

# The CI smoke: both halves of the fixture set; pdfluent-fast is optional
# because it needs pdfluent >= 1.1.0 from PyPI.
smoke:
	$(PY) -m bench run --machine local --configs pymupdf4llm,pdfluent-fast --sets fixtures \
	  --splits tuning,holdout --because "smoke run on the synthetic fixture set" --reps 1 \
	  --work $(WORK) --out results/smoke-$$(date -u +%Y%m%dT%H%M%SZ) --optional pdfluent-fast

datasets:
	$(PY) -m bench datasets fetch --sets $(SETS) --work $(WORK) --profile $(PROFILE)

datasets-plan:
	$(PY) -m bench datasets plan --sets $(SETS)

images:
	docker/build.sh $(FAMILIES)

report:
	$(PY) -m bench report $(RUNS)

compare:
	$(PY) -m bench compare $(RUNS)

cost:
	$(PY) -m bench cost --machine $(MACHINE) --configs $(CONFIGS) --sets $(SETS) \
	  --splits tuning,holdout --profile $(PROFILE) --reps $(REPS)

fixtures:
	$(PY) fixtures/make_fixtures.py

# Maintainers only: these read the engine repository with `git show`.
goldens:
	$(PY) scripts/make_goldens.py --engine $(ENGINE) --ref $(REF) --workdir $(WORKDIR)

port:
	$(PY) scripts/port_from_engine.py --engine $(ENGINE) --ref $(REF)

tools:
	scripts/make_tools.sh $(TOOLS) $(PY)

validate:
	$(PY) -m bench validate $(RUNS)

ODL_CLONE ?= $(WORK)/odl-bench-clone
RECIPE    ?= fast+footers
BASELINE  ?=
odl-verify:
	$(PY) -m bench.odl verify --fetch --clone $(ODL_CLONE)

odl-run:
	$(PY) -m bench.odl run --fetch --clone $(ODL_CLONE) --recipe "$(RECIPE)" --baseline "$(BASELINE)" \
	  --out results/odl-$$(date -u +%Y%m%dT%H%M%SZ)

# The 19 holdout cells: `make axes-combine RUNS='results/<a> results/<b>' OUT=axis_means.json`,
# `make axes-gate BASELINE=<file> CANDIDATE=<file>` (exit 1 on a drop, 3 when a value is missing).
OUT       ?= axis_means.json
CANDIDATE ?=
axes-combine:
	$(PY) -m bench.axes combine $(RUNS) --out $(OUT)

axes-gate:
	$(PY) -m bench.axes gate --baseline "$(BASELINE)" --candidate "$(CANDIDATE)" --tolerance 0.005

public-check:
	$(PY) scripts/check_public_surface.py

# One command, nothing assumed but python3, pdftotext and mutool on the path:
# pinned tool venvs, the tests, the mini subset of real ParseBench pages through
# two columns, the schema check on what the run wrote, and the surface check.
# The run lands in results/ci-<UTC> (ignored by git; commit a result by hand).
repro: tools
	set -e; . $(TOOLS)/env.sh; \
	$$BENCH_PY -m pytest -q tests; \
	$$BENCH_PY scripts/check_public_surface.py; \
	$$BENCH_PY -m bench datasets fetch --sets $(REPRO_SETS) --profile mini --work $(WORK); \
	out=results/ci-$$(date -u +%Y%m%dT%H%M%SZ); \
	$$BENCH_PY -m bench run --machine local --configs $(REPRO_CONFIGS) --sets $(REPRO_SETS) \
	  --splits tuning --profile mini --reps 1 --work $(WORK) --out $$out; \
	$$BENCH_PY -m bench validate $$out; \
	echo "[repro] ok: $$out"
