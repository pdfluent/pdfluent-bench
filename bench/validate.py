# Copyright (c) 2026 Innovation Trigger B.V.
#
# PDFluent is available under two licences, at your option: the GNU AGPLv3, or
# the PDFluent Commercial Licence. See the LICENSE file in this repository --
# that file travels with the copy you received, which a URL does not.
"""Results schema: every file a run writes is checked against schema/*.json.

    python -m bench validate results/<run> [results/<run> ...]

A run directory is valid when run.json, every quality file, every speed file and
report.json match their schema, and every configuration named in run.json has the
files its steps promise. Exit status 1 on the first run with a problem.
"""
from __future__ import annotations

import json
import pathlib
import sys

from jsonschema import Draft202012Validator

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCHEMA_DIR = ROOT / "schema"
KINDS = {"run": "run.json", "quality": "quality/*/*/*.json", "speed": "speed/*/*.json",
         "report": "report.json"}


def schema(kind: str) -> Draft202012Validator:
    return Draft202012Validator(json.loads((SCHEMA_DIR / f"{kind}.schema.json").read_text()))


def problems_in(path: pathlib.Path, kind: str) -> list[str]:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        return [f"{path}: unreadable: {exc}"]
    return [f"{path}: {'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}"
            for e in schema(kind).iter_errors(data)]


def check_run(run: pathlib.Path) -> list[str]:
    found: list[str] = []
    if not (run / "run.json").is_file():
        return [f"{run}: no run.json"]
    for kind, pattern in KINDS.items():
        files = sorted(run.glob(pattern))
        if kind in ("run", "report") and not files:
            found.append(f"{run}: no {pattern}")
        for path in files:
            found += problems_in(path, kind)
    record = json.loads((run / "run.json").read_text())
    for step in record.get("steps", []):
        if step.get("status") != "ok":
            continue
        if step.get("kind") == "quality":
            want = run / "quality" / step["set"] / step["split"] / f"{step['config']}.json"
            if not want.is_file():
                found.append(f"{run}: step says ok but {want.relative_to(run)} is missing")
    return found


def main(runs: list[str]) -> int:
    bad = 0
    for name in runs:
        found = check_run(pathlib.Path(name))
        for line in found:
            print(f"[validate] {line}", file=sys.stderr)
        print(f"[validate] {name}: {'INVALID' if found else 'ok'}")
        bad += bool(found)
    return 1 if bad else 0
