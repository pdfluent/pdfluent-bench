#!/usr/bin/env bash
# Copyright (c) 2026 Innovation Trigger B.V.
#
# PDFluent is available under two licences, at your option: the GNU AGPLv3, or
# the PDFluent Commercial Licence. See the LICENSE file in this repository --
# that file travels with the copy you received, which a URL does not.
#
# Create the pinned tool interpreters a native run needs, one venv each:
#   scripts/make_tools.sh [TOOLS_DIR] [PYTHON]    (Python 3.12 or newer: the ParseBench scorer needs it)
# Prints the environment lines that make `make bench` find them. Idempotent:
# an existing venv is recreated (--clear), never trusted.
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
tools="${1:-${TOOLS:-$here/work/tools}}"
py="${2:-${PY:-python3}}"
mkdir -p "$tools"
# The harness venv holds pytest, the schema checker and the other pinned
# harness requirements; the tool venvs hold one column each.
"$py" -m venv --clear "$tools/harness"
"$tools/harness/bin/python" -m pip install --quiet --disable-pip-version-check \
  -r "$here/requirements-dev.txt"
for name in pymupdf4llm pdfluent parsebench; do
  "$py" -m venv --clear "$tools/$name"
  "$tools/$name/bin/python" -m pip install --quiet --disable-pip-version-check \
    -r "$here/tools/$name.txt"
done
cat > "$tools/env.sh" <<ENV
export BENCH_PY="$tools/harness/bin/python"
export PYMUPDF4LLM_PYTHON="$tools/pymupdf4llm/bin/python"
export PDFLUENT_FAST_PYTHON="$tools/pdfluent/bin/python"
export PARSEBENCH_PYTHON="$tools/parsebench/bin/python"
ENV
echo "[tools] wrote $tools/env.sh"
