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

# The models and the ONNX Runtime they run on, installed the way a user
# installs them: `pdfluent models install layout tables` from the released
# 1.1.0 CLI, which checks every file against the pins built into it. The wheel
# itself ships no ONNX Runtime; it loads the one this command puts in the
# model dir. The CLI archive is pinned by its digest in the release's
# SHA256SUMS (github.com/pdfluent/pdfluent-sdk, tag v1.1.0).
ARG PDFLUENT_CLI_VERSION=1.1.0
ARG PDFLUENT_CLI_SHA256_X86_64=5d6782dff39c6a621305dea743d6392738c56edb6dbdcdfa4273eda9d4e7eb39
ARG PDFLUENT_CLI_SHA256_AARCH64=c1a0abacc96ca5bebcd3b4f4fdafdb2392ead41a5b0033d58aad21ee4ea84d24
ENV PDFLUENT_MODEL_DIR=/opt/models \
    PDFLUENT_TABLE_MODEL_DIR=/opt/models
RUN arch="$(uname -m)" \
 && case "$arch" in \
      x86_64) want="$PDFLUENT_CLI_SHA256_X86_64";; \
      aarch64) want="$PDFLUENT_CLI_SHA256_AARCH64";; \
      *) echo "no pdfluent CLI archive for $arch" >&2; exit 2;; \
    esac \
 && name="pdfluent-${PDFLUENT_CLI_VERSION}-${arch}-unknown-linux-gnu.tar.gz" \
 && python -c "import sys, urllib.request; urllib.request.urlretrieve(sys.argv[1], sys.argv[2])" \
      "https://github.com/pdfluent/pdfluent-sdk/releases/download/v${PDFLUENT_CLI_VERSION}/$name" "/tmp/$name" \
 && echo "$want  /tmp/$name" | sha256sum -c - \
 && mkdir -p /opt/pdfluent-cli && tar xzf "/tmp/$name" -C /opt/pdfluent-cli \
 && rm "/tmp/$name" \
 && cli="$(find /opt/pdfluent-cli -type f -name pdfluent | head -n 1)" \
 && ln -s "$cli" /usr/local/bin/pdfluent \
 && pdfluent --version > /opt/models.cli-version.txt \
 && pdfluent models install layout tables \
 && pdfluent models status > /opt/models/STATUS.txt \
 && cat /opt/models/STATUS.txt \
 && grep -q "todo pins: none" /opt/models/STATUS.txt \
 && (cd /opt/models && find . -type f ! -name STATUS.txt -exec sha256sum {} + | sort -k 2) > /opt/models/SHA256SUMS.installed

# Convert one fixture page with each model column while the image is built,
# so an image that cannot serve a column does not build.
COPY fixtures/pdf/tables-p1.pdf /warm/warm.pdf
COPY engine/scripts/extraction/adapters /warm/engine/scripts/extraction/adapters
COPY bench/adapters /warm/bench/adapters
RUN /opt/ex/pdfluent/bin/python /warm/engine/scripts/extraction/adapters/extract_structured.py --tool pdfluent_balanced /warm/warm.pdf > /dev/null \
 && /opt/ex/pdfluent/bin/python /warm/engine/scripts/extraction/adapters/extract_structured.py --tool pdfluent_balanced_tatr /warm/warm.pdf > /tmp/warm.md \
 && grep -q "| Retail |" /tmp/warm.md
