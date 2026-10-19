# Copyright (c) 2026 Innovation Trigger B.V.
#
# PDFluent is available under two licences, at your option: the GNU AGPLv3, or
# the PDFluent Commercial Licence. See the LICENSE file in this repository --
# that file travels with the copy you received, which a URL does not.
"""The harness around the scorers: configs, cost guard, run.json, report, refusals."""
import json
import os
import pathlib
import shutil
import subprocess
import sys

import pytest

from bench import configs, cost, quality, report, runinfo, speed
from skipping import skip

ROOT = pathlib.Path(__file__).resolve().parents[1]
MATRIX = {"pdfluent-fast", "pdfluent-balanced", "pdfluent-balanced-tatr", "pdfluent-best",
          "docling-standard", "docling-minimal", "pymupdf4llm"}


def test_the_matrix_is_complete_and_valid():
    assert set(configs.names()) == MATRIX
    register = engine_register()
    for name in MATRIX:
        config = configs.load(name)
        assert config["tool"] in register, f"{name}: tool {config['tool']} has no register row"
        assert config["version"], name
        assert (ROOT / "docker" / config["image"] / "Dockerfile").is_file(), name
        if config["gpu"]["route"]:
            assert config["gpu"].get("image"), name
    assert configs.load("docling-minimal")["vision"] is True  # the layout model always runs
    assert configs.load("pdfluent-fast")["vision"] is False
    assert configs.load("pdfluent-best")["reproducible"] == "internal"


def engine_register() -> dict:
    import tomllib
    path = ROOT / "engine" / "benchmarks" / "extraction" / "comparison" / "EXTRACTORS.toml"
    return tomllib.loads(path.read_text())["extractors"]


def test_every_column_passes_the_register():
    from bench import engine_api
    for name in MATRIX:
        allowed, refused = engine_api.sb().tools_allowed([configs.load(name)["tool"]])
        assert allowed and not refused, (name, refused)


def test_a_broken_config_is_refused(tmp_path, monkeypatch):
    (tmp_path / "bad.yaml").write_text("name: bad\ntool: x\n")
    monkeypatch.setattr(configs, "CONFIGS", tmp_path)
    with pytest.raises(SystemExit, match="must be"):
        configs.load("bad")


def test_cost_guard_refuses_over_budget_and_prices_on_demand():
    ok, estimate = cost.guard("c3-standard-8", 100, 20)
    assert not ok and estimate["eur"] > 20
    ok, estimate = cost.guard("c3-standard-8", 10, 20)
    assert ok
    price = configs.machine("c3-standard-8")["eur_per_hour"]
    assert estimate["eur"] >= 10 * price["on_demand"]
    assert cost.guard("local", 5, 0)[0]
    with pytest.raises(SystemExit):
        cost.guard("c3-standard-8", 0, 20)


def test_cost_cli_exits_non_zero_over_budget():
    done = subprocess.run([sys.executable, "-m", "bench", "cost", "--machine", "c3-standard-8",
                           "--hours", "200", "--budget", "20"], cwd=ROOT,
                          capture_output=True, text=True)
    assert done.returncode == 1 and "REFUSED" in done.stderr
    done = subprocess.run([sys.executable, "-m", "bench", "cost", "--machine", "g2-standard-8",
                           "--hours", "4", "--budget", "20"], cwd=ROOT,
                          capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


def test_profiles():
    assert configs.pages_for("doclaynet", "full") == 200
    assert configs.pages_for("doclaynet", "mini") == 20
    assert configs.pages_for("parsebench", "mini") == 7
    assert configs.pages_for("fixtures", "full") == 0


def test_run_json_records_what_the_plan_asks(tmp_path):
    record = runinfo.start("r1", "local", tmp_path, ["doclaynet", "fixtures"],
                           [configs.load("pymupdf4llm")], ["x"])
    record = runinfo.finish(record, 0.0, 12.5)
    for key in ("run_id", "machine", "images", "harness", "engine_ref", "datasets", "started",
                "finished", "wall_clock_s"):
        assert record.get(key) is not None, key
    machine = record["machine"]
    assert machine["type"] == "local" and machine["cpu_model"] and machine["cpus"]
    assert "gpus" in machine
    assert len(record["datasets"]["hash"]) == 64
    assert record["datasets"]["sets"]["doclaynet"]["sha256"]
    assert record["wall_clock_s"] == 12.5


def test_holdout_needs_a_reason(tmp_path):
    with pytest.raises(SystemExit, match="--because"):
        quality.quality_one(configs.load("pymupdf4llm"), "fixtures", "holdout", 0, tmp_path,
                            tmp_path, "r")
    with pytest.raises(SystemExit, match="unknown split"):
        quality.quality_one(configs.load("pymupdf4llm"), "fixtures", "test", 0, tmp_path,
                            tmp_path, "r")


def test_a_page_that_is_not_the_sealed_one_is_refused(tmp_path):
    entry, pages, manifest, seed = quality.materialised("fixtures", tmp_path)
    chosen = quality.choose(pages, manifest, "tuning", 0, seed)
    quality.verify(chosen, manifest)
    fake = tmp_path / "fake.pdf"
    fake.write_bytes(b"%PDF-1.4 not the page\n")
    with pytest.raises(SystemExit, match="not what the manifest seals"):
        quality.verify([dict(chosen[0], path=str(fake))], manifest)


def test_the_fixture_split_has_both_halves():
    manifest = json.loads((ROOT / "fixtures" / "MANIFEST.json").read_text())
    halves = {p["split"] for p in manifest["pages"]}
    assert halves == {"tuning", "holdout"}


def test_one_thread_means_one():
    env, prefix, how = speed.thread_setting(1)
    assert all(v == "1" for v in env.values()) and "OMP_NUM_THREADS" in env
    env, prefix, how = speed.thread_setting(8)
    assert env["OMP_NUM_THREADS"] == "8" and prefix == []


def _fake_run(tmp_path: pathlib.Path, gpu: bool = False) -> pathlib.Path:
    d = tmp_path / ("gpu" if gpu else "cpu")
    record = runinfo.start("fake", "g2-standard-8" if gpu else "local", tmp_path, ["fixtures"],
                           [configs.load("pymupdf4llm")], [])
    (d / "quality" / "fixtures" / "tuning").mkdir(parents=True)
    rows = [{"name": "a.pdf", "document": "a.pdf", **{
        axis: {"scored": True, key: 0.5} for axis, key, _l in (
            ("text", "similarity", ""), ("headings", "f1", ""), ("lists", "f1", ""),
            ("tables", "f1", ""), ("teds", "teds", ""), ("words", "f1", ""),
            ("order", "score", ""), ("text_geometric", "similarity", ""),
            ("order_geometric", "score", ""))}}]
    rows[0]["lists"] = {"scored": False}
    run_file = {"tools": {"pymupdf4llm": {"pages": 1, "documents": rows, "summary": {}}},
                "bench": {"tool": "pymupdf4llm"}}
    (d / "quality" / "fixtures" / "tuning" / "pymupdf4llm.json").write_text(json.dumps(run_file))
    (d / "run.json").write_text(json.dumps(runinfo.finish(record, 0, 1)))
    return d


def test_report_marks_unannotated_missing_and_na(tmp_path):
    d = _fake_run(tmp_path)
    built = report.build([d])
    rows = {r["label"]: r for r in built["rows"]}
    assert rows["fixtures TEDS"]["cells"]["pymupdf4llm"][0]["mean"] == 0.5
    assert rows["fixtures lists F1"]["cells"]["pymupdf4llm"][0].get("not_annotated")
    # no speed file: every speed cell is missing, and the report is a draft
    assert built["draft"] is True
    assert any("speed" in m for m in built["missing"])
    md = report.markdown(built)
    assert md.startswith("**DRAFT")
    assert "0.500 ⟨1⟩" in md and "–" in md


def test_report_writes_na_for_a_gpu_without_route(tmp_path):
    cpu = _fake_run(tmp_path)
    gpu = _fake_run(tmp_path, gpu=True)
    built = report.build([cpu, gpu])
    gpu_rows = [r for r in built["rows"] if r.get("machine") == "gpu"]
    assert gpu_rows and all("na" in r["cells"]["pymupdf4llm"] for r in gpu_rows)
    assert "n/a" in report.markdown(built)


def test_smoke_run_on_fixtures(tmp_path):
    """The real thing on the fixture pages: pymupdf4llm through the whole harness."""
    python = os.environ.get("PYMUPDF4LLM_PYTHON") or sys.executable
    if configs.installed_version(python, "pymupdf4llm") != "1.28.2":
        skip("pymupdf4llm 1.28.2 is not installed for this test (the CI smoke job runs it)")
    if not (shutil.which("mutool") and shutil.which("pdftotext")):
        skip("mutool and pdftotext are needed by the ported harness")
    out = tmp_path / "out"
    done = subprocess.run(
        [sys.executable, "-m", "bench", "run", "--configs", "pymupdf4llm", "--sets", "fixtures",
         "--splits", "tuning,holdout", "--because", "test", "--reps", "1", "--work",
         str(tmp_path / "work"), "--out", str(out)],
        cwd=ROOT, capture_output=True, text=True, env=dict(os.environ, PYMUPDF4LLM_PYTHON=python))
    assert done.returncode == 0, done.stdout[-3000:] + done.stderr[-3000:]
    run = json.loads((out / "run.json").read_text())
    assert run["failures"] == 0
    for split in ("tuning", "holdout"):
        result = json.loads((out / "quality" / "fixtures" / split / "pymupdf4llm.json").read_text())
        column = result["tools"]["pymupdf4llm"]
        assert column["pages"] >= 1 and column["did_not_answer"] == 0
        assert result["bench"]["version"]["matches_pin"]
    timed = json.loads((out / "speed" / "cpu" / "pymupdf4llm.json").read_text())
    assert timed["threads"]["1"]["ms_p50"] and timed["cold_start_ms"]["median"]
    assert (out / "report.md").is_file() and (out / "report.json").is_file()
