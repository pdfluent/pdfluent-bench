#!/usr/bin/env bash
# Copyright (c) 2026 Innovation Trigger B.V.
#
# PDFluent is available under two licences, at your option: the GNU AGPLv3, or
# the PDFluent Commercial Licence. See the LICENSE file in this repository --
# that file travels with the copy you received, which a URL does not.
#
# Build the images a set of configs needs and record their digests.
#   docker/build.sh [family ...]     families: pdfluent docling docling-gpu pymupdf4llm best
# Writes work/images.json: family -> image id (sha256 of the image config),
# the tag, the base image reference and the build time. run.json copies it.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT" || exit 1
families=("$@")
[ ${#families[@]} -gt 0 ] || families=(pdfluent docling pymupdf4llm)
mkdir -p work
rev="$(git rev-parse --short HEAD 2>/dev/null || echo nogit)"
out="work/images.json"
[ -f "$out" ] || echo '{}' > "$out"
for family in "${families[@]}"; do
  args=()
  dir="$family"
  if [ "$family" = docling-gpu ]; then
    dir=docling
    args=(--build-arg TORCH_INDEX=https://download.pytorch.org/whl/cu124)
  fi
  [ -f "docker/$dir/Dockerfile" ] || { echo "FATAL: no docker/$dir/Dockerfile" >&2; exit 2; }
  tag="pdfluent-bench/$family:$rev"
  echo "=== building $tag"
  DOCKER_BUILDKIT=1 docker build "${args[@]}" -f "docker/$dir/Dockerfile" -t "$tag" .
  id="$(docker image inspect --format '{{.Id}}' "$tag")"
  base="$(sed -n 's/^ARG BASE=//p' "docker/$dir/Dockerfile")"
  python3 - "$out" "$family" "$tag" "$id" "$base" <<'PY'
import datetime, json, sys
path, family, tag, image_id, base = sys.argv[1:]
data = json.load(open(path))
data[family] = {"tag": tag, "image_id": image_id, "base": base,
                "built": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")}
json.dump(data, open(path, "w"), indent=1, sort_keys=True)
PY
  echo "=== $family $id"
done
