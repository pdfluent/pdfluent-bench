#!/usr/bin/env python3
# Copyright (c) 2026 Innovation Trigger B.V.
#
# PDFluent is available under two licences, at your option: the GNU AGPLv3, or
# the PDFluent Commercial Licence. See the LICENSE file in this repository --
# that file travels with the copy you received, which a URL does not.
"""Write fixtures/scoring/golden.json from the ORIGINAL engine scorers.

    scripts/make_goldens.py --engine /path/to/engine --ref <commit> --workdir DIR

The engine files are read with `git show` into DIR (a new directory), never
from the engine working tree, and imported from there. The vendored copies
under engine/ are not used, so the golden file is what the engine itself
computes on fixtures/scoring/cases.json.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bench import equivalence  # noqa: E402

NEEDED = [
    "scripts/ci/loop/structured_bench.py",
    "scripts/ci/structured_holdout_rule.py",
    "scripts/extraction/structured_axes.py",
    "scripts/extraction/pubtables_cells.py",
    "scripts/extraction/fintabnet_archive.py",
    "scripts/extraction/fintabnet_split.py",
    "scripts/extraction/pb_summary.py",
    "scripts/extraction/parsebench_round.py",
    "scripts/extraction/lexicon/ATTRIBUTION",
    "scripts/extraction/lexicon/LEXICON.json",
    "scripts/extraction/lexicon/en_scowl60-rel-2026.02.25.txt",
    "benchmarks/structured/comparison/DATASETS.toml",
    "benchmarks/extraction/comparison/EXTRACTORS.toml",
]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--engine", required=True, type=pathlib.Path)
    ap.add_argument("--ref", required=True)
    ap.add_argument("--workdir", required=True, type=pathlib.Path,
                    help="a NEW directory to hold the original files")
    args = ap.parse_args()
    ref = subprocess.run(["git", "-C", str(args.engine), "rev-parse", args.ref],
                         check=True, capture_output=True, text=True).stdout.strip()
    args.workdir.mkdir(parents=True, exist_ok=False)
    for path in NEEDED:
        data = subprocess.run(["git", "-C", str(args.engine), "show", f"{ref}:{path}"],
                              check=True, capture_output=True).stdout
        target = args.workdir / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    cases = json.loads(equivalence.CASES.read_text())
    golden = equivalence.compute(equivalence.load_modules(args.workdir), cases)
    golden["_engine_ref"] = ref
    equivalence.GOLDEN.write_text(json.dumps(golden, indent=1, sort_keys=True,
                                             ensure_ascii=False) + "\n")
    print(f"[goldens] wrote {equivalence.GOLDEN.relative_to(ROOT)} from engine {ref[:12]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
