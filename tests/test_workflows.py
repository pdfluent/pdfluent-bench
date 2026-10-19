# Copyright (c) 2026 Innovation Trigger B.V. All rights reserved.
# Licence pending owner decision: see LICENSE-TBD.md.
"""The CI contract: own runner, no soft failure, nothing a fork can trigger."""
import pathlib

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))


def load(path):
    data = yaml.safe_load(path.read_text())
    data["on"] = data.pop(True, data.get("on"))  # YAML reads a bare `on` as True
    return data


def test_there_is_a_workflow():
    assert WORKFLOWS


def test_every_job_runs_on_the_repository_runner_not_a_hosted_one():
    for path in WORKFLOWS:
        for name, job in load(path)["jobs"].items():
            labels = job["runs-on"]
            assert isinstance(labels, list) and "self-hosted" in labels, f"{path.name}:{name}"
            assert "pdfluent-bench" in labels, f"{path.name}:{name}"


def test_no_job_may_fail_softly():
    for path in WORKFLOWS:
        text = path.read_text()
        assert "continue-on-error" not in text and "allow_failure" not in text, path.name
        assert "|| true" not in text, path.name


def test_a_fork_cannot_reach_the_runner():
    for path in WORKFLOWS:
        triggers = load(path)["on"]
        assert set(triggers) <= {"push", "workflow_dispatch", "schedule"}, path.name


def test_every_push_runs_the_real_data_subset():
    ci = load(ROOT / ".github" / "workflows" / "ci.yml")
    assert "push" in ci["on"]
    steps = ci["jobs"]["subset"]["steps"]
    assert any("make repro" in step.get("run", "") for step in steps)
    assert "if" not in ci["jobs"]["subset"]


def test_the_full_round_is_manual_only():
    ci = load(ROOT / ".github" / "workflows" / "ci.yml")
    full = ci["jobs"]["full"]
    assert "workflow_dispatch" in full["if"] and "inputs.full" in full["if"]


def test_make_repro_does_what_the_workflow_relies_on():
    rule = (ROOT / "Makefile").read_text().split("repro: tools", 1)[1]
    for needle in ("pytest", "check_public_surface", "datasets fetch", "bench run", "bench validate"):
        assert needle in rule, needle
