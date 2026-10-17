# syntax=docker/dockerfile:1.7
# Copyright (c) 2026 Innovation Trigger B.V. All rights reserved.
# Licence pending owner decision: see LICENSE-TBD.md.
#
# Build from the repository root: docker/build.sh (records the image digest).
# Ubuntu 24.04 (noble-20260930.1): its mupdf-tools is 1.23.10, the only
# version found that cuts the PubTables-1M pages to the bytes the manifest
# seals (bench/datasets.py CUT_TOOL); bookworm's 1.21.1 reproduced 225 of 400.
ARG BASE=ubuntu:24.04@sha256:68ee22004a9109b44973313b02ff7b1de250f1de0f5ecb628d73f579aaff33ac
FROM ${BASE}
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-venv ca-certificates \
      poppler-utils mupdf-tools git util-linux \
 && rm -rf /var/lib/apt/lists/* \
 && mutool -v 2>&1 | tee /opt/mutool-version.txt | grep -q "mutool version 1.23.10" \
 && pdftotext -v 2>&1 | head -n 1 > /opt/poppler-version.txt

# The harness interpreter and the ParseBench scorer, the same in every image.
COPY docker/requirements-harness.txt /tmp/requirements-harness.txt
RUN python3 -m venv /opt/bench \
 && /opt/bench/bin/pip install --no-cache-dir -r /tmp/requirements-harness.txt \
 && TIKTOKEN_CACHE_DIR=/opt/tiktoken /opt/bench/bin/python -c "import tiktoken; tiktoken.get_encoding(\"o200k_base\")" \
 && python3 -m venv /opt/ex/parsebench \
 && /opt/ex/parsebench/bin/pip install --no-cache-dir parse-bench==1.0.4
ENV TIKTOKEN_CACHE_DIR=/opt/tiktoken \
    PARSEBENCH_PYTHON=/opt/ex/parsebench/bin/python \
    DO_NOT_TRACK=1 SCARF_NO_ANALYTICS=true HF_HUB_DISABLE_TELEMETRY=1 \
    BENCH_IN_CONTAINER=1
WORKDIR /bench

# TORCH_INDEX selects the torch build: cpu (default) or a CUDA index such as
# https://download.pytorch.org/whl/cu124 for the GPU image (docling-gpu).
# The CUDA wheels carry their own runtime libraries; the host provides the
# driver through the NVIDIA container toolkit.
ARG TORCH_INDEX=https://download.pytorch.org/whl/cpu
RUN python3 -m venv /opt/ex/docling \
 && /opt/ex/docling/bin/pip install --no-cache-dir --extra-index-url ${TORCH_INDEX} docling==2.126.0 \
 && ln -s /opt/ex/docling /opt/ex/docling_minimal \
 && /opt/ex/docling/bin/pip freeze > /opt/ex/docling/FREEZE.txt

# Fetch Docling's model weights at build time, so the image is the whole
# column and a run needs no network: one conversion of a fixture page with
# each pipeline (TableFormer on and off).
ENV HF_HOME=/opt/hf
COPY fixtures/pdf/tables-p1.pdf /warm/warm.pdf
COPY engine/scripts/extraction/adapters /warm/engine/scripts/extraction/adapters
COPY bench/adapters /warm/bench/adapters
RUN /opt/ex/docling/bin/python /warm/engine/scripts/extraction/adapters/extract_structured.py --tool docling /warm/warm.pdf > /dev/null \
 && /opt/ex/docling/bin/python /warm/engine/scripts/extraction/adapters/extract_structured.py --tool docling_minimal /warm/warm.pdf > /dev/null
ENV HF_HUB_OFFLINE=1
