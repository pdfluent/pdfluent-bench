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
FAMILIES ?= pdfluent docling pymupdf4llm

.PHONY: help test bench smoke report datasets datasets-plan images cost fixtures goldens port

help:
	@echo "make test                         unit tests: scorers, equivalence, harness"
	@echo "make bench MACHINE=<type> CONFIGS=<list|all> SETS=<list|all> [SPLITS=tuning,holdout BECAUSE=...]"
	@echo "                                  [PROFILE=full|mini] [REPS=3] [RUNNER=native|docker]"
	@echo "make smoke                        fixture pages, pymupdf4llm and pdfluent-fast"
	@echo "make datasets SETS=<list|all>     fetch the sets from their public sources into WORK"
	@echo "make datasets-plan SETS=...       list what a fetch downloads, fetch nothing"
	@echo "make images [FAMILIES=...]        build the containers and record their digests"
	@echo "make report RUNS='results/<id> ...'"
	@echo "make cost MACHINE=c3-standard-8   modelled round time and the cost guard"
	@echo "infra/gcp/run_round.sh            a round on GCP (read it first; it spends money)"

test:
	$(PY) -m pytest -q tests

bench:
	$(PY) -m bench run --machine $(MACHINE) --configs $(CONFIGS) --sets $(SETS) \
	  --splits $(SPLITS) --profile $(PROFILE) --reps $(REPS) --runner $(RUNNER) --work $(WORK) \
	  $(if $(OPTIONAL),--optional $(OPTIONAL)) $(if $(BECAUSE),--because "$(BECAUSE)")

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
