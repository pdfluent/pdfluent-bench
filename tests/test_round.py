# Copyright (c) 2026 Innovation Trigger B.V. All rights reserved.
# Licence pending owner decision: see LICENSE-TBD.md.
"""The images and the GCP round script: what a round needs before it spends money."""
import json
import os
import pathlib
import re
import stat
import subprocess
import sys

from bench import configs, cost

ROOT = pathlib.Path(__file__).resolve().parents[1]
ROUND = ROOT / "infra" / "gcp" / "run_round.sh"


def _dockerfile(image: str) -> str:
    return (ROOT / "docker" / image / "Dockerfile").read_text()


def _run_lines(text: str) -> str:
    """The Dockerfile's RUN instructions, continuation lines joined."""
    joined = re.sub(r"\\\n", " ", text)
    return "\n".join(line for line in joined.splitlines() if line.lstrip().startswith("RUN "))


def test_every_model_column_converts_a_page_at_build_time():
    # The pdfluent image once held the two .onnx files but no ONNX Runtime, so
    # every balanced page failed on the round machine while fast passed. A
    # column that needs a model converts one page while its image is built, so
    # an image that cannot serve it does not build.
    for name in configs.names():
        config = configs.load(name)
        if not config["models"] or config["reproducible"] != "public":
            continue
        runs = _run_lines(_dockerfile(config["image"]))
        assert f"--tool {config['tool']} " in runs, (
            f"{name}: docker/{config['image']}/Dockerfile converts no page with "
            f"--tool {config['tool']} at build time")


def test_pdfluent_models_come_from_models_install():
    text = _dockerfile("pdfluent")
    runs = _run_lines(text)
    assert "models install layout tables" in runs
    assert "models status" in runs
    # the CLI archive is pinned by digest, per architecture
    assert "releases/download/v${PDFLUENT_CLI_VERSION}/" in text
    assert "-unknown-linux-gnu.tar.gz" in text and "sha256sum -c" in runs
    # x86_64 and aarch64 digests from the v1.1.0 release's SHA256SUMS
    assert "5d6782dff39c6a621305dea743d6392738c56edb6dbdcdfa4273eda9d4e7eb39" in text
    assert "c1a0abacc96ca5bebcd3b4f4fdafdb2392ead41a5b0033d58aad21ee4ea84d24" in text


def _fake_gcloud(tmp_path: pathlib.Path, family_exists: bool) -> dict:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "gcloud.log"
    script = bin_dir / "gcloud"
    body = 'echo "$*" >> "$GCLOUD_LOG"\n'
    body += ('case "$*" in *describe-from-family*) '
             + ('echo "fake-image READY"; exit 0;;' if family_exists
                else 'echo "ERROR: was not found" >&2; exit 1;;')
             + ' esac\n')
    body += 'case "$*" in *"instances create"*) echo "create must not run" >&2; exit 9;; esac\n'
    script.write_text("#!/usr/bin/env bash\n" + body)
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}", GCLOUD_LOG=str(log),
                PY=sys.executable)


def test_round_refuses_an_image_family_that_does_not_exist(tmp_path):
    # common-cu124-debian-11-py310 was the default GPU family and no longer
    # exists in deeplearning-platform-release; the round must stop before any
    # VM is created, not fail in `instances create` after the CPU VM is up.
    env = _fake_gcloud(tmp_path, family_exists=False)
    env.update(MACHINES="g2-standard-8", CONFIGS="docling-standard", SETS="doclaynet",
               SPLITS="tuning", HOURS="2", BUDGET_EUR="20")
    done = subprocess.run(["bash", str(ROUND)], cwd=ROOT, env=env, capture_output=True,
                          text=True)
    assert done.returncode != 0
    assert "image family" in done.stderr and "REFUSED" in done.stderr
    assert "instances create" not in (tmp_path / "gcloud.log").read_text()


def test_round_plan_passes_when_the_family_exists(tmp_path):
    env = _fake_gcloud(tmp_path, family_exists=True)
    env.update(MACHINES="g2-standard-8", CONFIGS="docling-standard", SETS="doclaynet",
               SPLITS="tuning", HOURS="2", BUDGET_EUR="20")
    done = subprocess.run(["bash", str(ROUND)], cwd=ROOT, env=env, capture_output=True,
                          text=True)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "plan only" in done.stdout


def test_gpu_round_measures_speed_only(tmp_path):
    # Quality is measured on the CPU machine; the GPU machine adds the speed
    # column. Measuring quality again on the GPU paid for a duplicate column.
    env = _fake_gcloud(tmp_path, family_exists=True)
    env.update(MACHINES="g2-standard-8", CONFIGS="docling-standard", SETS="doclaynet",
               SPLITS="tuning", HOURS="2", BUDGET_EUR="20")
    done = subprocess.run(["bash", str(ROUND)], cwd=ROOT, env=env, capture_output=True,
                          text=True)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "--no-quality" in done.stdout
    gpu = cost.round_hours([configs.load("docling-standard")], ["doclaynet"], ["tuning"],
                           "full", "gpu", quality=False)
    with_quality = cost.round_hours([configs.load("docling-standard")], ["doclaynet"],
                                    ["tuning"], "full", "gpu")
    assert gpu["hours"] < with_quality["hours"]


def test_a_cpu_shard_can_skip_speed(tmp_path):
    env = _fake_gcloud(tmp_path, family_exists=True)
    env.update(MACHINES="c3-standard-8", CONFIGS="pymupdf4llm", SETS="doclaynet",
               SPLITS="tuning", HOURS="2", BUDGET_EUR="20", SPEED="no")
    done = subprocess.run(["bash", str(ROUND)], cwd=ROOT, env=env, capture_output=True,
                          text=True)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "--no-speed" in done.stdout and "--no-quality" not in done.stdout


def test_run_json_is_written_after_every_step(tmp_path, monkeypatch):
    # A spot VM can be preempted mid-round, and the partial copy the round
    # script takes is all that is left of it. Without a run.json beside the
    # finished steps those files cannot be reported or audited.
    from bench import cli, quality

    seen = []

    def fake_quality_one(config, set_name, split, size, work, out, run_id, because=""):
        seen.append((split, (out / "run.json").is_file()))
        if split == "holdout":
            meta = json.loads((out / "run.json").read_text())
            assert [s.get("status") for s in meta["steps"] if s["kind"] == "quality"] == ["ok", None]
            assert meta["finished"] is None
        target = out / "quality" / set_name / split / f"{config['name']}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("{}")
        return target

    monkeypatch.setattr(quality, "quality_one", fake_quality_one)
    monkeypatch.setattr(configs, "unavailable", lambda config, python: None)
    monkeypatch.setattr(configs, "version_record", lambda config, python: {})
    monkeypatch.setattr(cli.report, "write", lambda dirs, out: (out / "r.md", out / "r.json"))
    out = tmp_path / "out"
    code = cli.main(["run", "--configs", "pymupdf4llm", "--sets", "fixtures", "--splits",
                     "tuning,holdout", "--because", "test", "--no-speed", "--work",
                     str(tmp_path / "work"), "--out", str(out), "--run-id", "r1"])
    assert code == 0
    assert seen == [("tuning", True), ("holdout", True)]
    assert json.loads((out / "run.json").read_text())["finished"]


def test_every_image_cuts_pages_with_the_mutool_that_sealed_them():
    # The PubTables-1M pages are cut out of their articles with `mutool merge`,
    # and the sealed manifest holds the cut bytes. On 17-10 the bookworm images
    # (mutool 1.21.1) reproduced 225 of the 400 sealed pages; of thirteen
    # versions tried on the same articles only 1.23.10 (Ubuntu 24.04)
    # reproduced every one (1.19.0-1.22.2: 8/17, 1.23.7: 13/17, 1.24 and later
    # and 1.16: 0/17).
    for image in ("pdfluent", "docling", "pymupdf4llm", "best"):
        text = _dockerfile(image)
        assert re.search(r"^ARG BASE=ubuntu:24\.04@sha256:[0-9a-f]{64}$", text, re.M), image
        assert "mutool -v" in _run_lines(text) and "1.23.10" in _run_lines(text), image


def test_fetch_refuses_a_cut_tool_other_than_the_sealing_one(monkeypatch):
    from bench import datasets
    monkeypatch.setattr(datasets, "mutool_version", lambda: "1.21.1")
    import pytest
    with pytest.raises(SystemExit, match="1.23.10"):
        datasets.check_cut_tool("pubtables-1m")
    monkeypatch.setattr(datasets, "mutool_version", lambda: "1.23.10")
    datasets.check_cut_tool("pubtables-1m")
    datasets.check_cut_tool("doclaynet")  # not cut: any version


def test_docker_passes_only_the_variables_the_host_has(tmp_path, monkeypatch):
    # `docker run -e NAME` with NAME unset on the host cleared the image's own
    # PDFLUENT_TABLE_MODEL_DIR, and every pdfluent-balanced-tatr step of the
    # 17-10 mini round failed with "needs PDFLUENT_TABLE_MODEL_DIR".
    from bench import cli, runinfo
    monkeypatch.setattr(runinfo, "images", lambda work: {"pdfluent": {"tag": "t", "image_id": "i"}})
    config = configs.load("pdfluent-balanced-tatr")
    monkeypatch.delenv("PDFLUENT_TABLE_MODEL_DIR", raising=False)
    command = cli.docker_command(config, False, tmp_path, tmp_path, ["x"])
    assert "PDFLUENT_TABLE_MODEL_DIR" not in " ".join(command)
    monkeypatch.setenv("PDFLUENT_TABLE_MODEL_DIR", "/elsewhere")
    command = cli.docker_command(config, False, tmp_path, tmp_path, ["x"])
    assert "PDFLUENT_TABLE_MODEL_DIR" in command


def test_docker_runner_skips_an_optional_column_without_its_image(tmp_path, monkeypatch):
    # pdfluent-best is optional and its image is not built without its private
    # weights; the docker runner failed it five times instead of skipping it.
    from bench import cli, runinfo
    monkeypatch.setattr(runinfo, "images", lambda work: {})
    monkeypatch.setattr(cli.report, "write", lambda dirs, out: (out / "r.md", out / "r.json"))
    out = tmp_path / "out"
    args = ["run", "--configs", "pdfluent-best", "--sets", "fixtures", "--splits", "tuning",
            "--runner", "docker", "--work", str(tmp_path / "w"), "--out", str(out), "--run-id", "r"]
    assert cli.main(args + ["--optional", "pdfluent-best"]) == 0
    steps = json.loads((out / "run.json").read_text())["steps"]
    assert [(s["kind"], s["status"]) for s in steps] == [("config", "skipped")]
    assert "not built" in steps[0]["why"]
    assert cli.main(args) == 1  # not optional: the run fails


def test_vm_setup_installs_buildx():
    # Ubuntu 24.04's docker.io (the GPU image family) has no BuildKit builder
    # of its own; docker/build.sh sets DOCKER_BUILDKIT=1 and the GPU round of
    # 17-10 stopped at "BuildKit is enabled but the buildx component is missing".
    text = (ROOT / "infra" / "gcp" / "vm_setup.sh").read_text()
    assert "docker-buildx" in text
    assert "docker buildx version" in text


def test_zone_can_be_overridden(tmp_path):
    # Spot L4s ran out in europe-west4-a on 17-10 (create: "stockout"); the
    # round has to be able to go to another zone of the same region.
    env = _fake_gcloud(tmp_path, family_exists=True)
    env.update(MACHINES="g2-standard-8", CONFIGS="docling-standard", SETS="doclaynet",
               SPLITS="tuning", HOURS="2", BUDGET_EUR="20", ZONE="europe-west4-b")
    done = subprocess.run(["bash", str(ROUND)], cwd=ROOT, env=env, capture_output=True,
                          text=True)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "--zone=europe-west4-b" in done.stdout and "europe-west4-a" not in done.stdout
    env["ZONE"] = "us-central1-a"
    done = subprocess.run(["bash", str(ROUND)], cwd=ROOT, env=env, capture_output=True,
                          text=True)
    assert done.returncode != 0 and "REFUSED" in done.stderr


def test_dataset_fetch_in_an_offline_image_may_reach_the_hub(tmp_path, monkeypatch):
    # The Docling images set HF_HUB_OFFLINE=1 so that a measurement never
    # downloads a model; the GPU VM fetches the datasets inside docling-gpu
    # and on 17-10 every DocLayNet file was refused in offline mode.
    from bench import cli, runinfo
    monkeypatch.setattr(runinfo, "images", lambda work: {"docling-gpu": {"tag": "t", "image_id": "i"}})
    command = cli.fetch_command("docling-gpu", "doclaynet", tmp_path, "full", None)
    assert command[command.index("HF_HUB_OFFLINE=0") - 1] == "-e"
    assert command.index("HF_HUB_OFFLINE=0") < command.index("t")
