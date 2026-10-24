# Copyright (c) 2026 Innovation Trigger B.V.
#
# PDFluent is available under two licences, at your option: the GNU AGPLv3, or
# the PDFluent Commercial Licence. See the LICENSE file in this repository --
# that file travels with the copy you received, which a URL does not.
"""The 19 dataset-by-metric cells and a gate over them.

    python -m bench.axes combine <run dir>... --out axis_means.json
    python -m bench.axes gate --baseline <file> --candidate <file> [--tolerance 0.005]

The 19 cells are `report.AXES_BY_SET` for FinTabNet (3), PubTables-1M (3) and
DocLayNet (8), plus the five `report.PARSEBENCH` headlines. `combine` writes
one mean per cell, per configuration and split (`<config>|<split>`), from the
per-set quality files of the run directories; for a configuration present in
several directories the first directory wins, as in `bench report`. A
configuration with no value in any cell is left out.

`gate` compares the holdout half only. Exit codes: 0 no cell dropped by more
than the tolerance; 1 a cell or the 19-cell mean dropped (each is named);
2 unreadable input; 3 a value is missing, so nothing can be said, and
`SKIPPED (not a pass): <reason>` is printed. A drop wins over a skip.

This file holds no scores. The files it reads and writes are measurements and
stay with the run that made them.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

from bench import report

SETS = ("fintabnet", "pubtables-1m", "doclaynet", "parsebench")
HOLDOUT = "holdout"
SKIP_EXIT = 3


def cells() -> list[tuple[str, str, str]]:
    """(set, key in `report.quality_numbers`, label in the means file), 19 of them."""
    out = []
    for set_name in SETS:
        if set_name == "parsebench":
            out += [(set_name, f"{g}.{m}", f"{report.SET_TITLE[set_name]} {label}")
                    for g, m, label in report.PARSEBENCH]
        else:
            out += [(set_name, axis, f"{report.SET_TITLE[set_name]} {label}")
                    for axis, label in report.AXES_BY_SET[set_name]]
    return out


def labels() -> list[str]:
    return [label for _s, _k, label in cells()]


def combine(dirs: list[pathlib.Path]) -> dict:
    _runs, quality, _speed = report._load_runs(dirs)
    wanted = cells()
    out: dict[str, dict] = {}
    for (_kind, set_name, split, config), (_run_id, run_file) in sorted(quality.items()):
        if set_name not in SETS:
            continue
        numbers = report.quality_numbers(run_file, set_name)
        row = out.setdefault(f"{config}|{split}", {label: None for _s, _k, label in wanted})
        for s, key, label in wanted:
            if s == set_name:
                row[label] = (numbers.get(key) or {}).get("mean")
    return {k: v for k, v in sorted(out.items()) if any(x is not None for x in v.values())}


def mean(row: dict) -> float | None:
    values = [row.get(label) for label in labels()]
    if any(v is None for v in values):
        return None
    return sum(values) / len(values)


def gate(baseline: dict, candidate: dict, tolerance: float,
         configs: list[str] | None = None) -> tuple[int, list[str], list[str]]:
    """(exit code, failures, skips). Only `<config>|holdout` rows count."""
    failures: list[str] = []
    skips: list[str] = []
    names = configs or sorted(k.split("|")[0] for k in candidate if k.endswith("|" + HOLDOUT))
    if not names:
        skips.append(f"the candidate has no {HOLDOUT} rows")
    for name in names:
        key = f"{name}|{HOLDOUT}"
        base, cand = baseline.get(key), candidate.get(key)
        if base is None or cand is None:
            skips.append(f"{key} is missing from the {'baseline' if base is None else 'candidate'}")
            continue
        for label in labels():
            b, c = base.get(label), cand.get(label)
            if b is None or c is None:
                skips.append(f"{key} {label}: no value in the {'baseline' if b is None else 'candidate'}")
            elif b - c - tolerance > 1e-12:
                failures.append(f"{key} {label}: {b:.4f} -> {c:.4f} (drop {b - c:.4f}, "
                                f"tolerance {tolerance})")
        mb, mc = mean(base), mean(cand)
        if mb is not None and mc is not None and mb - mc - tolerance > 1e-12:
            failures.append(f"{key} mean of 19: {mb:.4f} -> {mc:.4f} (drop {mb - mc:.4f}, "
                            f"tolerance {tolerance})")
    return (1 if failures else SKIP_EXIT if skips else 0), failures, skips


def _fatal(why: str):
    print(f"[axes] FATAL: {why}", file=sys.stderr)
    raise SystemExit(2)


def _read(path: str) -> dict:
    try:
        data = json.loads(pathlib.Path(path).read_text())
    except (OSError, ValueError) as exc:
        _fatal(f"cannot read {path}: {exc}")
    if not isinstance(data, dict):
        _fatal(f"{path} is not a JSON object")
    return data


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m bench.axes", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="command", required=True)
    co = sub.add_parser("combine", help="write axis_means.json from run directories")
    co.add_argument("runs", nargs="+", type=pathlib.Path)
    co.add_argument("--out", type=pathlib.Path, required=True)
    ga = sub.add_parser("gate", help="fail when a holdout cell or the mean drops")
    ga.add_argument("--baseline", required=True)
    ga.add_argument("--candidate", required=True)
    ga.add_argument("--tolerance", type=float, default=0.005)
    ga.add_argument("--config", action="append",
                    help="gate only this configuration (repeatable); default: every one in the candidate")
    args = ap.parse_args(argv)
    if args.command == "combine":
        missing = [d for d in args.runs if not (d / "run.json").is_file()]
        if missing:
            _fatal(f"no run.json in {missing[0]}")
        data = combine(args.runs)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(data, indent=1) + "\n")
        print(f"[axes] {len(data)} rows written to {args.out}")
        return 0
    code, failures, skips = gate(_read(args.baseline), _read(args.candidate),
                                 args.tolerance, args.config)
    for line in failures:
        print(f"[axes] FAIL {line}", file=sys.stderr)
    for line in skips:
        print(f"SKIPPED (not a pass): {line}", file=sys.stderr)
    if code == 0:
        print("[axes] ok: no holdout cell and no mean dropped by more than the tolerance")
    return code


if __name__ == "__main__":
    sys.exit(main())
