# Copyright (c) 2026 Innovation Trigger B.V.
#
# PDFluent is available under two licences, at your option: the GNU AGPLv3, or
# the PDFluent Commercial Licence. See the LICENSE file in this repository --
# that file travels with the copy you received, which a URL does not.
"""Every committed result matches the schema, and the schema rejects a damaged file."""
import copy
import json
import pathlib
import shutil

import pytest

from bench import runinfo, validate

ROOT = pathlib.Path(__file__).resolve().parents[1]
RUNS = sorted(p for top in ("results", "runs-unclaimed") for p in (ROOT / top).iterdir()
              if (p / "run.json").is_file())
FINAL = ROOT / "results" / "bench-c3-standard-8-20261017T163121Z"


@pytest.mark.parametrize("run", RUNS, ids=lambda p: p.name)
def test_committed_run_matches_the_schema(run):
    assert validate.check_run(run) == []


def test_there_are_runs_to_check():
    assert FINAL in RUNS and len(RUNS) >= 3


@pytest.mark.parametrize("kind, rel, key", [
    ("run", "run.json", "engine_ref"),
    ("run", "run.json", "datasets"),
    ("quality", "quality/parsebench/holdout/pdfluent-fast.json", "bench"),
    ("speed", "speed/cpu/pdfluent-fast.json", "threads"),
    ("report", "report.json", "rows"),
])
def test_a_missing_key_is_refused(kind, rel, key, tmp_path):
    data = json.loads((FINAL / rel).read_text())
    assert validate.schema(kind).is_valid(data)
    broken = copy.deepcopy(data)
    del broken[key]
    target = tmp_path / "broken.json"
    target.write_text(json.dumps(broken))
    assert validate.problems_in(target, kind), f"{kind} without {key} passed"


def test_a_promised_quality_file_that_is_missing_is_refused(tmp_path):
    copy_of = tmp_path / "run"
    shutil.copytree(FINAL, copy_of)
    victim = next((copy_of / "quality").glob("*/*/pymupdf4llm.json"))
    victim.unlink()
    assert any("missing" in line for line in validate.check_run(copy_of))


def test_a_bad_pin_flag_type_is_refused(tmp_path):
    data = json.loads((FINAL / "quality/parsebench/holdout/pdfluent-fast.json").read_text())
    data["bench"]["version"]["matches_pin"] = "yes"
    target = tmp_path / "q.json"
    target.write_text(json.dumps(data))
    assert validate.problems_in(target, "quality")


def test_the_recorded_command_carries_no_checkout_path():
    assert runinfo.portable_command(["/somewhere/else/bench/__main__.py", "run", "--machine", "local"]) \
        == ["python", "-m", "bench", "run", "--machine", "local"]
