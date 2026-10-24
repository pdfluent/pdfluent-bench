#!/usr/bin/env bash
# Copyright (c) 2026 Innovation Trigger B.V.
#
# PDFluent is available under two licences, at your option: the GNU AGPLv3, or
# the PDFluent Commercial Licence. See the LICENSE file in this repository --
# that file travels with the copy you received, which a URL does not.
#
# A fresh virtualenv for an odl-bench run: the pinned scorer's dependencies and
# the pdfluent wheel under test. Prints the interpreter path on stdout.
#
#   odl-bench/prepare_env.sh DIR --wheel path/to/pdfluent-X-....whl   a given wheel
#   odl-bench/prepare_env.sh DIR --version 1.2.0                       a released wheel from PyPI
set -euo pipefail
dir="${1:?usage: prepare_env.sh DIR (--wheel FILE | --version X.Y.Z)}"; kind="${2:?}"; what="${3:?}"
python="${PYTHON:-python3.13}"
"$python" -m venv "$dir" >&2
"$dir/bin/pip" install --quiet apted beautifulsoup4 lxml rapidfuzz pytest pyyaml >&2
case "$kind" in
  --wheel) "$dir/bin/pip" install --quiet --no-deps "$what" >&2 ;;
  --version) "$dir/bin/pip" install --quiet "pdfluent==$what" >&2 ;;
  *) echo "unknown option $kind" >&2; exit 2 ;;
esac
echo "$dir/bin/python"
