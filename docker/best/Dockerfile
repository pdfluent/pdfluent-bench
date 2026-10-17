# syntax=docker/dockerfile:1.7
# Copyright (c) 2026 Innovation Trigger B.V. All rights reserved.
# Licence pending owner decision: see LICENSE-TBD.md.
#
# Build from the repository root: docker/build.sh (records the image digest).
ARG BASE=python:3.12.12-slim-bookworm@sha256:593bd06efe90efa80dc4eee3948be7c0fde4134606dd40d8dd8dbcade98e669c
FROM ${BASE}
RUN apt-get update \
 && apt-get install -y --no-install-recommends poppler-utils mupdf-tools git util-linux \
 && rm -rf /var/lib/apt/lists/*

# The harness interpreter and the ParseBench scorer, the same in every image.
COPY docker/requirements-harness.txt /tmp/requirements-harness.txt
RUN python -m venv /opt/bench \
 && /opt/bench/bin/pip install --no-cache-dir -r /tmp/requirements-harness.txt \
 && TIKTOKEN_CACHE_DIR=/opt/tiktoken /opt/bench/bin/python -c "import tiktoken; tiktoken.get_encoding(\"o200k_base\")" \
 && python -m venv /opt/ex/parsebench \
 && /opt/ex/parsebench/bin/pip install --no-cache-dir parse-bench==1.0.4
ENV TIKTOKEN_CACHE_DIR=/opt/tiktoken \
    PARSEBENCH_PYTHON=/opt/ex/parsebench/bin/python \
    DO_NOT_TRACK=1 SCARF_NO_ANALYTICS=true HF_HUB_DISABLE_TELEMETRY=1 \
    BENCH_IN_CONTAINER=1
WORKDIR /bench

# The best tier's environment, exactly as the engine pins it.
COPY engine/scripts/extraction/adapters/requirements-best.txt /tmp/requirements-best.txt
RUN python -m venv /opt/ex/teacher_ens \
 && /opt/ex/teacher_ens/bin/pip install --no-cache-dir -r /tmp/requirements-best.txt \
 && /opt/ex/teacher_ens/bin/pip freeze > /opt/ex/teacher_ens/FREEZE.txt

# Not in the image, because none of it is published yet: the two weight
# directories and three engine builds. Mount them and name them:
#   TEACHER_ENS_MODEL_A, TEACHER_ENS_MODEL_B,
#   STRUCTURED_MARKDOWN, RENDER_PAGE_PNG, FEATURE_DUMP
