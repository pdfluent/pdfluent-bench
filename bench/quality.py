# Copyright (c) 2026 Innovation Trigger B.V. All rights reserved.
# Licence pending owner decision: see LICENSE-TBD.md.
"""Quality: one configuration, one set, one split, scored by the ported rig.

The order is the engine round's: the set is materialised by `bench datasets
fetch`, its pages are checked against the sealed manifest, one half is
sampled by whole documents under the set's own seed, and `structured_bench.
measure` runs the column and scores every page. ParseBench goes through the
ported `parsebench_round.py`, which scores with ParseBench's own evaluator.

The holdout half needs a reason (`--because`), written into the run file, as
the engine rule asks: a holdout spent without one cannot be audited later.
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import subprocess
import sys

from bench import configs, engine_api

ROOT = pathlib.Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"


class Refused(SystemExit):
    pass


def _sha256(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def materialised(set_name: str, work: pathlib.Path) -> tuple[dict, list[dict], dict, str]:
    """(register entry, materialised pages, sealed manifest, seed) for a set."""
    sb, rule = engine_api.sb(), engine_api.holdout_rule()
    if set_name == "fixtures":
        manifest = json.loads((FIXTURES / "MANIFEST.json").read_text())
        pages = json.loads((FIXTURES / "pages.json").read_text())
        for page in pages:
            page["path"] = str(ROOT / page["path"])
        entry = {"name": "fixtures", "version": manifest["version"],
                 "licence": manifest["licence"], "publishable": False}
        return entry, pages, manifest, manifest["seed"]
    entry = sb.dataset_entry(set_name)
    listing = work / set_name / "pages.json"
    if not listing.is_file():
        raise Refused(f"[bench] FATAL: {listing} is not there. Materialise the set first: "
                      f"python -m bench datasets fetch --sets {set_name} --work {work}")
    path = rule.draw_path_for(set_name)
    if not path.is_file():
        raise Refused(f"[bench] FATAL: no sealed manifest at {path}; for FinTabNet it is drawn "
                      "by `bench datasets fetch --sets fintabnet`.")
    manifest = json.loads(path.read_text())
    return entry, json.loads(listing.read_text()), manifest, rule.seed_for(set_name)


def choose(pages: list[dict], manifest: dict, split: str, size: int, seed: str) -> list[dict]:
    sb = engine_api.sb()
    wanted = {p["name"] for p in manifest["pages"] if p["split"] == split}
    half = [p for p in pages if p["name"] in wanted]
    chosen = sb.sample(half, size, seed) if size else half
    if not chosen:
        raise Refused(f"[bench] FATAL: no materialised page is in the {split} half of the "
                      "sealed manifest")
    return chosen


def verify(chosen: list[dict], manifest: dict) -> None:
    """Every page is the one the manifest sealed, or the measurement stops."""
    sealed = {p["name"]: p.get("sha256") for p in manifest["pages"]}
    wrong = []
    for page in chosen:
        want = sealed.get(page["name"]) or page.get("sha256")
        if not want or not pathlib.Path(page["path"]).is_file() \
                or _sha256(pathlib.Path(page["path"])) != want:
            wrong.append(page["name"])
    if wrong:
        raise Refused(f"[bench] FATAL: {len(wrong)} page(s) are not what the manifest seals, "
                      f"e.g. {wrong[0]}. A page that is not the sealed one cannot be compared.")


def _register_tool(config: dict) -> tuple[dict, list[dict]]:
    allowed, refused = engine_api.sb().tools_allowed([config["tool"]])
    if not allowed:
        why = refused[0]["why"] if refused else "unknown"
        raise Refused(f"[bench] REFUSED by the register: {config['tool']}: {why}")
    return allowed, refused


def quality_one(config: dict, set_name: str, split: str, size: int, work: pathlib.Path,
                out: pathlib.Path, run_id: str, because: str = "") -> pathlib.Path:
    if split not in ("tuning", "holdout"):
        raise Refused(f"[bench] FATAL: unknown split `{split}`")
    if split == "holdout" and not because:
        raise Refused("[bench] REFUSED: the holdout half without --because. A holdout spent "
                      "without a reason written at the time cannot be audited afterwards.")
    python = configs.tool_python(config)
    why = configs.unavailable(config, python)
    if why:
        raise Refused(f"[bench] FATAL: {config['name']} cannot run here: {why}")
    os.environ[f"{config['tool'].upper()}_PYTHON"] = python
    os.environ.setdefault("DO_NOT_TRACK", "1")
    os.environ.setdefault("SCARF_NO_ANALYTICS", "true")
    target = out / "quality" / set_name / split / f"{config['name']}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    bench = {"config": config["name"], "tool": config["tool"], "set": set_name, "split": split,
             "pages_requested": size, "because": because or None,
             "version": configs.version_record(config, python),
             "reproducible": config["reproducible"], "vision": config["vision"]}
    if set_name == "parsebench":
        run = _parsebench(config, python, split, size, work, run_id)
        run["bench"] = bench
        target.write_text(json.dumps(run, indent=1) + "\n")
        return target
    sb = engine_api.sb()
    entry, pages, manifest, seed = materialised(set_name, work)
    chosen = choose(pages, manifest, split, size, seed)
    verify(chosen, manifest)
    allowed, refused = _register_tool(config)
    text_dir = work / "text" / run_id / set_name / split / config["name"]
    run = sb.measure(set_name, entry, chosen, allowed, refused, {config["tool"]: python},
                     f"{run_id}-{set_name}-{split}-{config['name']}", because or split,
                     text_dir=text_dir, geometry="none")
    bench["seed"] = seed
    bench["pages_digest"] = sb.pages_digest(chosen)
    column = run["tools"].get(config["tool"]) or {}
    if column.get("version") in (None, "", "unknown"):
        # The ported rows ask /opt/ex/<tool>; outside an image the bench's own check answers.
        column["version"] = bench["version"]["installed"]
        column["version_from"] = "pdfluent-bench: importlib.metadata in the column's interpreter"
    run["bench"] = bench
    target.write_text(json.dumps(run, indent=1) + "\n")
    answered = (run["tools"].get(config["tool"]) or {}).get("pages", 0)
    if not answered:
        raise Refused(f"[bench] FAIL: {config['name']} answered none of the {len(chosen)} "
                      f"page(s) of {set_name} {split}; its run file is {target}. Run the "
                      f"adapter on one page to see the error: python {engine_api.ADAPTER} "
                      f"--tool {config['tool']} <page.pdf>")
    return target


def _parsebench(config: dict, python: str, split: str, size: int, work: pathlib.Path,
                run_id: str) -> dict:
    parsebench_py = os.environ.get("PARSEBENCH_PYTHON", "/opt/ex/parsebench/bin/python")
    if not pathlib.Path(parsebench_py).exists():
        raise Refused(f"[bench] FATAL: ParseBench's scorer needs parse-bench==1.0.4 in its own "
                      f"venv; PARSEBENCH_PYTHON={parsebench_py} does not exist")
    _register_tool(config)
    runs = work / "parsebench-runs" / run_id / split / config["name"]
    runs.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, BENCH_RUNS_DIR=str(runs), PARSEBENCH_PYTHON=parsebench_py,
               **{f"{config['tool'].upper()}_PYTHON": python})
    command = [sys.executable, str(engine_api.PARSEBENCH_ROUND), "--work",
               str(work / "parsebench"), "--tools", config["tool"], "--pages", str(size),
               "--split", split, "--fetch", "--measure"]
    done = subprocess.run(command, env=env, check=False)
    files = sorted(runs.glob("structured-parsebench-*.json"))
    if done.returncode != 0 or not files:
        raise Refused(f"[bench] FATAL: the ParseBench round for {config['name']} ({split}) "
                      f"exited {done.returncode} and wrote {len(files)} run file(s)")
    return json.loads(files[-1].read_text())
