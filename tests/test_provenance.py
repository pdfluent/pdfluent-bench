# Copyright (c) 2026 Innovation Trigger B.V. All rights reserved.
# Licence pending owner decision: see LICENSE-TBD.md.
"""engine/ is what PROVENANCE.json says it is, and no scoring function drifted."""
import hashlib
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import port_from_engine  # noqa: E402

PROVENANCE = json.loads((ROOT / "engine" / "PROVENANCE.json").read_text())


def test_every_vendored_file_matches_its_recorded_hash():
    for path, row in PROVENANCE["files"].items():
        data = (ROOT / "engine" / path).read_bytes()
        assert hashlib.sha256(data).hexdigest() == row["vendored_sha256"], path
        if not row["patched"]:
            assert row["vendored_sha256"] == row["engine_sha256"], path


def test_scoring_functions_are_byte_identical_to_the_engine():
    for path, functions in PROVENANCE["scoring_functions"].items():
        source = (ROOT / "engine" / path).read_text(encoding="utf-8")
        segments = port_from_engine.segments(source)
        for name, row in functions.items():
            assert row["engine_sha256"] == row["vendored_sha256"], f"{path}:{name}"
            now = hashlib.sha256(segments[name].encode()).hexdigest()
            assert now == row["engine_sha256"], f"{path}:{name} changed since the port"


def test_scorer_modules_are_not_patched():
    for path in ("scripts/extraction/structured_axes.py", "scripts/extraction/pubtables_cells.py",
                 "scripts/extraction/pb_summary.py", "scripts/ci/structured_holdout_rule.py",
                 "scripts/extraction/lexicon/en_scowl60-rel-2026.02.25.txt"):
        assert PROVENANCE["files"][path]["patched"] is False, path
