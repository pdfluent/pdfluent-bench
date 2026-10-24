# Copyright (c) 2026 Innovation Trigger B.V.
#
# PDFluent is available under two licences, at your option: the GNU AGPLv3, or
# the PDFluent Commercial Licence. See the LICENSE file in this repository --
# that file travels with the copy you received, which a URL does not.
"""bench.axes: combine from synthetic run directories, the gate's thresholds, its bite, its skips."""
import copy
import json
import pathlib
import subprocess
import sys

import pytest

from bench import axes, report
from skipping import skip

ROOT = pathlib.Path(__file__).resolve().parents[1]
AXIS_KEY = {"text": "similarity", "text_geometric": "similarity", "words": "f1", "headings": "f1",
            "lists": "f1", "tables": "f1", "teds": "teds", "order": "score",
            "order_geometric": "score"}


def _quality_file(set_name: str, values: dict) -> dict:
    """A per-set quality file whose cells equal `values` (axis key -> number)."""
    if set_name == "parsebench":
        groups: dict = {}
        for key, v in values.items():
            group, metric = key.split(".")
            groups.setdefault(group, {"examples": 4, "metrics": {}})["metrics"][metric] = v
        row = {"pages": 4, "groups": groups}
    else:
        doc = {"name": "d-1", "document": "d"}
        for axis, v in values.items():
            doc[axis] = {"scored": True, AXIS_KEY[axis]: v}
        row = {"pages": 1, "documents": [doc]}
    return {"bench": {"tool": "t"}, "tools": {"t": row}}


def _write_run(root: pathlib.Path, name: str, table: dict) -> pathlib.Path:
    """table: {(config, split): {label: number}} -> a run directory."""
    d = root / name
    d.mkdir(parents=True)
    (d / "run.json").write_text(json.dumps({"run_id": name, "machine": {"gpus": [], "spec": {}}}))
    for (config, split), row in table.items():
        for set_name in axes.SETS:
            values = {k: row[label] for s, k, label in axes.cells() if s == set_name and row.get(label) is not None}
            if not values:
                continue
            path = d / "quality" / set_name / split / f"{config}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(_quality_file(set_name, values)))
    return d


def _row(base: float) -> dict:
    return {label: round(base + 0.01 * i, 6) for i, label in enumerate(axes.labels())}


def test_there_are_nineteen_cells_in_four_sets():
    assert len(axes.cells()) == 19 and len(set(axes.labels())) == 19
    per_set = {s: sum(1 for c in axes.cells() if c[0] == s) for s in axes.SETS}
    assert per_set == {"fintabnet": 3, "pubtables-1m": 3, "doclaynet": 8, "parsebench": 5}


def test_combine_reproduces_the_inputs_for_every_config_and_split(tmp_path):
    table = {("eng-a", "holdout"): _row(0.30), ("eng-a", "tuning"): _row(0.35),
             ("eng-b", "holdout"): _row(0.40)}
    run = _write_run(tmp_path, "run-1", table)
    got = axes.combine([run])
    assert got == {f"{c}|{s}": row for (c, s), row in table.items()}
    assert axes.mean(got["eng-a|holdout"]) == pytest.approx(sum(table[("eng-a", "holdout")].values()) / 19)


def test_combine_first_directory_wins_and_two_runs_merge(tmp_path):
    one = _write_run(tmp_path, "one", {("eng-a", "holdout"): _row(0.30)})
    two = _write_run(tmp_path, "two", {("eng-a", "holdout"): _row(0.90), ("eng-b", "holdout"): _row(0.40)})
    got = axes.combine([one, two])
    assert got["eng-a|holdout"] == _row(0.30) and got["eng-b|holdout"] == _row(0.40)


def test_combine_leaves_out_an_unmeasured_config_and_keeps_holes_as_null(tmp_path):
    row = _row(0.5)
    row["ParseBench chart"] = None
    run = _write_run(tmp_path, "run", {("eng-a", "holdout"): row})
    (run / "quality" / "doclaynet" / "holdout" / "empty.json").write_text(
        json.dumps({"bench": {"tool": "t"}, "tools": {"t": {"pages": 0, "documents": []}}}))
    got = axes.combine([run])
    assert "empty|holdout" not in got
    assert got["eng-a|holdout"]["ParseBench chart"] is None
    assert axes.mean(got["eng-a|holdout"]) is None


def test_combine_cli_writes_the_file(tmp_path):
    run = _write_run(tmp_path, "run", {("eng-a", "holdout"): _row(0.5)})
    out = tmp_path / "o" / "axis_means.json"
    assert axes.main(["combine", str(run), "--out", str(out)]) == 0
    assert json.loads(out.read_text()) == {"eng-a|holdout": _row(0.5)}


def _pair(**override):
    base = {"eng-a|holdout": _row(0.5), "eng-a|tuning": _row(0.5)}
    cand = copy.deepcopy(base)
    cand["eng-a|holdout"].update(override)
    return base, cand


def test_gate_passes_identical_and_improved():
    base, cand = _pair()
    assert axes.gate(base, cand, 0.005)[0] == 0
    better = {k: dict(v) for k, v in base.items()}
    better["eng-a|holdout"] = {k: v + 0.1 for k, v in better["eng-a|holdout"].items()}
    assert axes.gate(base, better, 0.005)[0] == 0


def test_gate_tolerance_boundary_and_cell_name():
    label = "DocLayNet lists F1"
    base, cand = _pair()
    cand["eng-a|holdout"][label] = base["eng-a|holdout"][label] - 0.005
    assert axes.gate(base, cand, 0.005)[0] == 0          # exactly the tolerance passes
    cand["eng-a|holdout"][label] -= 0.001
    code, failures, _ = axes.gate(base, cand, 0.005)
    assert code == 1 and any(label in f and "eng-a|holdout" in f for f in failures)


def test_gate_fails_on_the_mean_when_no_single_cell_crosses():
    base, cand = _pair()
    cand["eng-a|holdout"] = {k: v - 0.004 for k, v in cand["eng-a|holdout"].items()}
    code, failures, _ = axes.gate(base, cand, 0.005)
    assert code == 0
    cand["eng-a|holdout"] = {k: v - 0.0045 for k, v in base["eng-a|holdout"].items()}
    assert axes.gate(base, cand, 0.0044)[0] == 1
    cand["eng-a|holdout"] = {k: v - 0.004 for k, v in base["eng-a|holdout"].items()}
    code, failures, _ = axes.gate(base, cand, 0.0035)
    assert code == 1 and any("mean of 19" in f for f in failures)


def test_gate_ignores_the_tuning_half():
    base, cand = _pair()
    cand["eng-a|tuning"] = {k: 0.0 for k in cand["eng-a|tuning"]}
    assert axes.gate(base, cand, 0.005)[0] == 0


def test_mutation_the_gate_bites_every_one_of_the_nineteen_cells():
    for label in axes.labels():
        base, cand = _pair(**{label: 0.0})
        code, failures, _ = axes.gate(base, cand, 0.005)
        assert code == 1 and any(label in f for f in failures), label


def test_mutation_a_gate_without_the_comparison_would_pass_a_collapse():
    # The same collapse must flip to a pass if the comparison is removed: proves the
    # failing assertions above rest on the comparison and not on a side effect.
    base, cand = _pair(**{"FinTabNet TEDS": 0.0})
    assert axes.gate(base, cand, 0.005)[0] == 1
    assert axes.gate(base, cand, 1.0)[0] == 0


def test_gate_skips_when_a_value_or_a_row_is_missing():
    base, cand = _pair(**{"ParseBench chart": None})
    code, failures, skips = axes.gate(base, cand, 0.005)
    assert code == axes.SKIP_EXIT == 3 and not failures and any("ParseBench chart" in s for s in skips)
    base, cand = _pair()
    del base["eng-a|holdout"]
    assert axes.gate(base, cand, 0.005)[0] == 3
    assert axes.gate({}, {}, 0.005)[0] == 3


def test_a_drop_wins_over_a_skip():
    base, cand = _pair(**{"ParseBench chart": None, "FinTabNet TEDS": 0.0})
    assert axes.gate(base, cand, 0.005)[0] == 1


def _cli(*args):
    return subprocess.run([sys.executable, "-m", "bench.axes", *args], cwd=ROOT,
                          capture_output=True, text=True)


def test_cli_exit_codes_and_skip_line(tmp_path):
    base, cand = _pair()
    b, c = tmp_path / "b.json", tmp_path / "c.json"
    b.write_text(json.dumps(base))
    c.write_text(json.dumps(cand))
    assert _cli("gate", "--baseline", str(b), "--candidate", str(c)).returncode == 0
    cand["eng-a|holdout"]["FinTabNet TEDS"] = 0.0
    c.write_text(json.dumps(cand))
    res = _cli("gate", "--baseline", str(b), "--candidate", str(c))
    assert res.returncode == 1 and "FinTabNet TEDS" in res.stderr
    cand["eng-a|holdout"]["FinTabNet TEDS"] = None
    c.write_text(json.dumps(cand))
    res = _cli("gate", "--baseline", str(b), "--candidate", str(c))
    assert res.returncode == 3 and "SKIPPED (not a pass): " in res.stderr
    assert _cli("gate", "--baseline", str(tmp_path / "none"), "--candidate", str(c)).returncode == 2


def test_the_committed_round_regenerates_byte_for_byte():
    """The 1.2.0 means file is reproducible from the run directories it names."""
    results = ROOT / "results"
    dirs = [results / "bench-n2-standard-8-20261021T085113Z", results / "bench-c3-standard-8-20261017T163121Z"]
    target = results / "round-1.2.0-20261021" / "axis_means.json"
    if not target.is_file() or not all((d / "run.json").is_file() for d in dirs):
        skip("round 1.2.0 run directories or axis_means.json are not in this checkout")
    assert axes.combine(dirs) == json.loads(target.read_text())
