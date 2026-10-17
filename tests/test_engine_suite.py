# Copyright (c) 2026 Innovation Trigger B.V. All rights reserved.
# Licence pending owner decision: see LICENSE-TBD.md.
"""The engine's own tests and self-tests, run against the vendored tree."""
import pathlib
import subprocess
import sys

import pytest

from skipping import skip

ROOT = pathlib.Path(__file__).resolve().parents[1]
ENGINE = ROOT / "engine" / "scripts"

TESTS = sorted((ENGINE / "ci" / "loop").glob("test_*.py")) + [ENGINE / "ci" / "test_fintabnet_split.py"]
SELF_TESTS = [ENGINE / "extraction" / name for name in (
    "structured_axes.py", "pubtables_cells.py", "pb_summary.py", "fintabnet_archive.py",
    "parsebench_round.py", "parsebench_harness.py", "adapters/extract_structured.py")]


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, *args], capture_output=True, text=True,
                          timeout=600, cwd=ROOT)


@pytest.mark.parametrize("path", TESTS, ids=lambda p: p.name)
def test_engine_test(path):
    done = _run([str(path)])
    assert done.returncode == 0, done.stdout[-3000:] + done.stderr[-3000:]


@pytest.mark.parametrize("path", SELF_TESTS, ids=lambda p: p.name)
def test_engine_self_test(path):
    done = _run([str(path), "--self-test"])
    said = done.stdout + done.stderr
    if "SKIPPED (not a pass)" in said:
        skip(f"{path.name} --self-test: " + said.split("SKIPPED (not a pass):", 1)[1].strip()[:200])
    assert done.returncode == 0, said[-4000:]
