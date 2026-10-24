# Copyright (c) 2026 Innovation Trigger B.V.
#
# PDFluent is available under two licences, at your option: the GNU AGPLv3, or
# the PDFluent Commercial Licence. See the LICENSE file in this repository --
# that file travels with the copy you received, which a URL does not.
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


FORK_TRIGGERS = {"pull_request", "pull_request_target"}


def labels(job):
    runs_on = job["runs-on"]
    return [runs_on] if isinstance(runs_on, str) else list(runs_on)


def runs_on_the_repository_runner(workflow):
    return all("self-hosted" in labels(j) and "pdfluent-bench" in labels(j)
               for j in workflow["jobs"].values())


def fork_exposure(workflow, text):
    """Why a workflow reachable from a fork may not exist, or [] when it is safe."""
    triggers = set(workflow["on"])
    problems = []
    if "pull_request_target" in triggers:
        problems.append("pull_request_target is never allowed")
    if triggers & FORK_TRIGGERS:
        for name, job in workflow["jobs"].items():
            if any("self-hosted" in label for label in labels(job)):
                problems.append(f"job {name} can run fork code on a self-hosted runner")
        if "secrets." in text:
            problems.append("a pull_request workflow uses secrets")
    return problems


def test_every_job_without_a_fork_trigger_runs_on_the_repository_runner():
    for path in WORKFLOWS:
        workflow = load(path)
        if not set(workflow["on"]) & FORK_TRIGGERS:
            assert runs_on_the_repository_runner(workflow), path.name


def test_no_job_may_fail_softly():
    for path in WORKFLOWS:
        text = path.read_text()
        assert "continue-on-error" not in text and "allow_failure" not in text, path.name
        assert "|| true" not in text, path.name


def test_a_fork_cannot_reach_the_runner_or_a_secret():
    for path in WORKFLOWS:
        assert fork_exposure(load(path), path.read_text()) == [], path.name


def test_mutation_a_self_hosted_pull_request_workflow_is_refused(tmp_path):
    source = ROOT / ".github" / "workflows" / "commit-identity.yml"
    text = source.read_text()
    assert "pull_request" in load(source)["on"]
    mutated = tmp_path / "commit-identity.yml"
    mutated.write_text(text.replace("runs-on: ubuntu-latest", "runs-on: [self-hosted, linux]"))
    assert fork_exposure(load(mutated), mutated.read_text())
    target = tmp_path / "target.yml"
    target.write_text(text.replace("  pull_request:\n", "  pull_request_target:\n"))
    assert fork_exposure(load(target), target.read_text())
    secret = tmp_path / "secret.yml"
    secret.write_text(text.replace("ALIAS:", "TOKEN: ${{ secrets.X }}\n      ALIAS:"))
    assert fork_exposure(load(secret), secret.read_text())


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
