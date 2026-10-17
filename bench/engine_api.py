# Copyright (c) 2026 Innovation Trigger B.V. All rights reserved.
# Licence pending owner decision: see LICENSE-TBD.md.
"""The vendored engine modules, imported once from engine/."""
from __future__ import annotations

import functools
import importlib
import pathlib
import sys
import types

ROOT = pathlib.Path(__file__).resolve().parents[1]
ENGINE = ROOT / "engine"
ADAPTER = ENGINE / "scripts" / "extraction" / "adapters" / "extract_structured.py"
PARSEBENCH_ROUND = ENGINE / "scripts" / "extraction" / "parsebench_round.py"
PROVENANCE = ENGINE / "PROVENANCE.json"


def _paths() -> None:
    for sub in ("scripts/extraction", "scripts/ci", "scripts/ci/loop"):
        path = str(ENGINE / sub)
        if path not in sys.path:
            sys.path.insert(0, path)


@functools.lru_cache(maxsize=None)
def module(name: str) -> types.ModuleType:
    _paths()
    loaded = importlib.import_module(name)
    origin = pathlib.Path(loaded.__file__ or "").resolve()
    if not origin.is_relative_to(ENGINE):
        raise SystemExit(f"[bench] FATAL: `{name}` was imported from {origin}, not from engine/")
    return loaded


def sb() -> types.ModuleType:
    return module("structured_bench")


def holdout_rule() -> types.ModuleType:
    return module("structured_holdout_rule")
