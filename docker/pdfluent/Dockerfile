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

# The three PDFluent columns share one venv; the harness looks for
# /opt/ex/<tool>/bin/python, so each column name points at it.
RUN python -m venv /opt/ex/pdfluent \
 && /opt/ex/pdfluent/bin/pip install --no-cache-dir pdfluent==1.1.0 \
 && for t in pdfluent_fast pdfluent_balanced pdfluent_balanced_tatr; do ln -s /opt/ex/pdfluent /opt/ex/$t; done \
 && /opt/ex/pdfluent/bin/pip freeze > /opt/ex/pdfluent/FREEZE.txt

# The public model files, checked against the digests the engine pins.
ADD --checksum=sha256:b7cc5d31f3223c74cc477a1386b894793618aeb398ca59cf5eb7264ec61d8afa \
    https://github.com/pdfluent/pdfluent-sdk/releases/download/models-v1/heron_ep03.onnx \
    /opt/models/layout-heron-ep03/heron_ep03.onnx
ADD --checksum=sha256:8c28418690b896a845a53633736a62c22ef8c9a515a23c06e9976e61360f1fcf \
    https://github.com/pdfluent/pdfluent-sdk/releases/download/models-v1/tatr-v1.1-all.onnx \
    /opt/models/tatr-v1.1-all/tatr-v1.1-all.onnx
ENV PDFLUENT_MODEL_DIR=/opt/models \
    PDFLUENT_TABLE_MODEL_DIR=/opt/models
