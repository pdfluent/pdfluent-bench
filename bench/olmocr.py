# Copyright (c) 2026 Innovation Trigger B.V.
#
# PDFluent is available under two licences, at your option: the GNU AGPLv3, or
# the PDFluent Commercial Licence. See the LICENSE file in this repository --
# that file travels with the copy you received, which a URL does not.
"""olmocr-bench: a BLIND holdout. Internal evaluation only.

    python -m bench.olmocr manifest --root DIR           write olmocr-bench/manifest.json
    python -m bench.olmocr verify   --root DIR [--fetch] check the data against it
    python -m bench.olmocr run      --recipe R --root DIR --baseline FILE
    python -m bench.olmocr baseline RUN_DIR --out FILE   turn a run into a baseline (by hand)

Blind means nobody tunes on it. This module therefore produces aggregates and
nothing else: an overall score and one score per test category. Per-document
scores are never computed, printed, or written. The scorer's own output (which
lists failing tests by document) is parsed in memory and dropped. The
predictions it needs on disk stay in the private root, next to a note that says
not to read them.

The data is the allenai/olmOCR-bench dataset at a pinned revision, downloaded at
run time into --root (or OLMOCR_BENCH_ROOT). It is never committed here, and the
root may not be under the home directory. The scorer is the benchmark's own
(allenai/olmocr, pinned commit), run unchanged in a subprocess.

Exit codes: 0 passed, 1 gate failed, 2 cannot judge (pin, manifest, baseline,
scorer), 3 skipped because a model or tool is absent
(`SKIPPED (not a pass): <reason>` on stderr; --allow-skip turns the code into 0).

Data: Allen Institute for AI, olmOCR-bench, licensed ODC-BY-1.0
(https://huggingface.co/datasets/allenai/olmOCR-bench). Scores are not published.
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import pathlib
import platform
import re
import subprocess
import sys
import time

from bench import odl
from bench.odl import (DEFAULT_TOLERANCE, EPS, EXIT_FAIL, EXIT_OK, EXIT_SKIPPED, EXIT_UNJUDGED, Skipped,
                       Unjudged, combined_hash, skipped_line)

ROOT = pathlib.Path(__file__).resolve().parents[1]
DIR = ROOT / "olmocr-bench"
RECIPES = DIR / "recipes.toml"
MANIFEST = DIR / "manifest.json"

HF_REPO = "allenai/olmOCR-bench"
HF_REVISION = "54a96a6fb6a2bd3b297e59869491db4d3625b711"
SCORER_REPO = "https://github.com/allenai/olmocr.git"
SCORER_PIN = "f7cfe4c22098b154c76b6ec950d1c0a464eecf8d"
DATA_SUBDIR = "data/bench_data"
SCORER_SUBDIR = "scorer"
NOTE_NAME = "DO-NOT-INSPECT-FOR-TUNING.txt"
NOTE = ("olmOCR-bench is a blind holdout. Do not open the files in this directory to tune on them.\n"
        "Only aggregate scores (overall and per category) are ever read from a run.\n")
ATTRIBUTION = ("olmOCR-bench (c) Allen Institute for AI, licensed ODC-BY-1.0, "
               "https://huggingface.co/datasets/allenai/olmOCR-bench. Internal evaluation only.")

# Keys that would carry a per-document result. No output file may contain one.
FORBIDDEN_KEYS = frozenset({"documents", "per_document", "per_doc", "pdf", "pdfs", "tests", "failures", "doc_id"})


# --- paths -----------------------------------------------------------------

def resolve_root(value: str | None, home: pathlib.Path | None = None) -> pathlib.Path:
    """The private root: --root or OLMOCR_BENCH_ROOT. Never under the home directory."""
    value = value or os.environ.get("OLMOCR_BENCH_ROOT")
    if not value:
        raise Unjudged("no data root: pass --root or set OLMOCR_BENCH_ROOT (an external volume, "
                       "never the home directory)")
    root = pathlib.Path(value).expanduser().resolve()
    home = (home or pathlib.Path.home()).resolve()
    if root == home or home in root.parents:
        raise Unjudged(f"the data root {root} is under the home directory; use an external volume")
    return root


def data_dir(root: pathlib.Path) -> pathlib.Path:
    return root / DATA_SUBDIR


# --- manifest and pin ------------------------------------------------------

def file_digests(data: pathlib.Path) -> dict[str, str]:
    """relative path -> sha256, over the test files and the PDFs."""
    files = sorted(data.glob("*.jsonl")) + sorted((data / "pdfs").rglob("*.pdf"))
    return {p.relative_to(data).as_posix(): odl._sha256(p) for p in files}


def build_manifest(data: pathlib.Path, revision: str = HF_REVISION) -> dict:
    files = file_digests(data)
    return {"dataset": HF_REPO, "hf_revision": revision, "scorer_repo": SCORER_REPO, "scorer_commit": SCORER_PIN,
            "licence": "ODC-BY-1.0 (dataset); terms of the PDFs unconfirmed, internal evaluation only",
            "files": files, "manifest_hash": combined_hash(files)}


def load_manifest(path: pathlib.Path = MANIFEST) -> dict:
    if not path.is_file():
        raise Unjudged(f"manifest not found: {path} (create it with: python -m bench.olmocr manifest --root DIR)")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("hf_revision") != HF_REVISION:
        raise Unjudged(f"manifest is for dataset revision {manifest.get('hf_revision')}, the pin is {HF_REVISION}")
    if manifest.get("scorer_commit") != SCORER_PIN:
        raise Unjudged(f"manifest is for scorer commit {manifest.get('scorer_commit')}, the pin is {SCORER_PIN}")
    if manifest.get("manifest_hash") != combined_hash(manifest["files"]):
        raise Unjudged("manifest_hash does not match the manifest's own file list")
    return manifest


def manifest_problems(data: pathlib.Path, manifest: dict) -> list[str]:
    """Every file that differs, by name. Empty means the data is the pinned revision."""
    want, have = manifest["files"], file_digests(data) if data.is_dir() else {}
    problems = []
    for name in sorted(set(want) | set(have)):
        if name not in have:
            problems.append(f"{name}: missing (manifest has it)")
        elif name not in want:
            problems.append(f"{name}: not in the manifest")
        elif want[name] != have[name]:
            problems.append(f"{name}: sha256 {have[name][:16]} != manifest {want[name][:16]}")
    return problems


def fetch_dataset(root: pathlib.Path) -> None:
    """Download the pinned revision into the root. Never anywhere else."""
    try:
        hub = importlib.import_module("huggingface_hub")
    except ImportError as exc:
        raise Unjudged("huggingface_hub is not installed; pip install huggingface_hub") from exc
    os.environ.setdefault("HF_HOME", str(root / ".hf"))
    hub.snapshot_download(HF_REPO, repo_type="dataset", revision=HF_REVISION, local_dir=str(root / "data"),
                          allow_patterns=["bench_data/*.jsonl", "bench_data/pdfs/**/*.pdf"])


def ensure_scorer(root: pathlib.Path, fetch: bool) -> pathlib.Path:
    clone = root / SCORER_SUBDIR
    if not (clone / ".git").exists():
        if not fetch:
            raise Unjudged(f"no scorer at {clone}; pass --fetch to clone {SCORER_REPO} at {SCORER_PIN}")
        clone.parent.mkdir(parents=True, exist_ok=True)
        done = subprocess.run(["git", "clone", "--quiet", SCORER_REPO, str(clone)], capture_output=True, text=True)
        if done.returncode:
            raise Unjudged(f"git clone of the scorer failed: {done.stderr.strip()}")
        odl._git(clone, "checkout", "--quiet", "--detach", SCORER_PIN)
    head = odl._git(clone, "rev-parse", "HEAD")
    if head != SCORER_PIN:
        raise Unjudged(f"scorer clone {clone} is at {head}, the pin is {SCORER_PIN}")
    if odl._git(clone, "status", "--porcelain", "--untracked-files=no"):
        raise Unjudged(f"scorer clone {clone} has local changes; the scorer must run unchanged")
    return clone


# --- parsing the scorer's output (in memory, aggregates only) --------------

CATEGORY_LINE = re.compile(r"^\s+([A-Za-z0-9_]+)\.jsonl\s*:\s*[0-9.]+%\s*\((\d+)/(\d+) tests\)\s*$")


def parse_summary(stdout: str) -> dict:
    """Overall and per-category scores from the scorer's final summary.

    Everything before `Results by JSONL file` (the per-test [FAIL] lines, which name
    documents) is ignored. The overall score is the scorer's own definition: the mean
    of the per-category pass rates, computed here from the printed counts so no
    precision is lost to its one-decimal percentage. Raises Unjudged if the summary
    is absent or the scorer reported errors."""
    head, marker, tail = stdout.partition("Final Summary")
    if not marker:
        raise Unjudged("the scorer printed no final summary")
    if "FAILED (errors)" in tail:
        raise Unjudged("the scorer reported errors for the candidate (details withheld: they name documents)")
    _, marker, results = tail.partition("Results by JSONL file")
    if not marker:
        raise Unjudged("the scorer's summary has no per-category results")
    counts: dict[str, tuple[int, int]] = {}
    for line in results.splitlines():
        m = CATEGORY_LINE.match(line)
        if m:
            counts[m.group(1)] = (int(m.group(2)), int(m.group(3)))
    if not counts:
        raise Unjudged("no category line found in the scorer's summary")
    categories = {name: passed / total for name, (passed, total) in sorted(counts.items()) if total}
    return {"overall": sum(categories.values()) / len(categories), "categories": categories,
            "test_counts": {name: total for name, (_, total) in sorted(counts.items())}}


def assert_aggregates_only(obj, where: str = "result") -> None:
    """Blindness guard: refuse to write anything shaped like a per-document result."""
    if isinstance(obj, dict):
        for key, value in obj.items():
            if str(key).lower() in FORBIDDEN_KEYS:
                raise Unjudged(f"{where}: key {key!r} would carry per-document data")
            assert_aggregates_only(value, f"{where}.{key}")
    elif isinstance(obj, list):
        for item in obj:
            assert_aggregates_only(item, where)


def blindness_violations(out_dir: pathlib.Path, doc_names: list[str]) -> list[str]:
    """Output files that mention a document by name (stem or path). Empty means blind."""
    stems = [n.rsplit("/", 1)[-1].removesuffix(".pdf") for n in doc_names]
    found = []
    for path in sorted(p for p in out_dir.rglob("*") if p.is_file()):
        text = path.read_text(encoding="utf-8", errors="replace")
        for name, stem in zip(doc_names, stems):
            if name in text or (len(stem) >= 6 and stem in text):
                found.append(f"{path.name}: mentions a document ({stem[:4]}...)")
                break
    return found


# --- the gate --------------------------------------------------------------

def check(result: dict, baseline: dict, show: bool = False) -> list[str]:
    """Violations, naming recipe and scope (overall or a category). Numbers only if `show`."""
    recipe = result["recipe"]
    if baseline.get("recipe") != recipe:
        return [f"recipe={recipe}: the baseline is for recipe {baseline.get('recipe')!r}"]
    if baseline.get("manifest_hash") != result.get("manifest_hash"):
        return [f"recipe={recipe}: the baseline was measured on another manifest than this run"]
    tol = float(baseline.get("tolerance", DEFAULT_TOLERANCE))
    floors = {"overall": baseline["scores"]["overall"], **baseline["scores"]["categories"]}
    have = {"overall": result["scores"]["overall"], **result["scores"]["categories"]}
    out = []
    for scope, floor in floors.items():
        got = have.get(scope)
        if got is None:
            out.append(f"recipe={recipe} scope={scope}: in the baseline, absent from the run")
        elif got < floor - tol - EPS:
            line = f"recipe={recipe} scope={scope}: dropped more than the tolerance {tol} below the baseline"
            if show:
                line += f" ({got:.4f} vs {floor:.4f}, drop {floor - got:.4f})"
            out.append(line)
    return out


def make_baseline(result: dict, tolerance: float = DEFAULT_TOLERANCE) -> dict:
    baseline = {"schema": "olmocr-bench-baseline/v1", "recipe": result["recipe"],
                "manifest_hash": result["manifest_hash"], "hf_revision": HF_REVISION,
                "scorer_commit": SCORER_PIN, "pdfluent": result.get("pdfluent"), "tolerance": tolerance,
                "scores": result["scores"], "attribution": ATTRIBUTION}
    assert_aggregates_only(baseline, "baseline")
    return baseline


# --- running ---------------------------------------------------------------

def scorer_problem(python: str, env: dict) -> str | None:
    """Why the scorer cannot run here (a missing browser for the math tests), or None."""
    probe = ("from playwright.sync_api import sync_playwright\n"
             "with sync_playwright() as p:\n    p.chromium.launch().close()\n")
    done = subprocess.run([python, "-c", probe], capture_output=True, text=True, env=env)
    if done.returncode:
        return "the scorer's math tests need a Playwright chromium (python -m playwright install chromium)"
    return None


def scorer_env(root: pathlib.Path, clone: pathlib.Path) -> dict:
    home = root / "scorer-home"  # the scorer caches rendered equations under $HOME
    home.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "HOME": str(home), "PYTHONPATH": str(clone), "TMPDIR": str(root / "tmp")}
    (root / "tmp").mkdir(parents=True, exist_ok=True)
    return env


def extract(recipe: dict, data: pathlib.Path, names: list[str], candidate: pathlib.Path) -> tuple[float, int]:
    """Run the recipe over every PDF. Returns wall time and the NUMBER of documents that raised."""
    pdfluent = importlib.import_module("pdfluent")
    kwargs = odl.recipe_kwargs(recipe, os.environ.get("PDFLUENT_MODEL_DIR"))
    failed, start = 0, time.time()
    for name in names:
        try:
            text = pdfluent.open(str(data / name)).extract_markdown(**kwargs)
        except TypeError:
            raise  # an option the wheel does not know: the recipe is wrong, stop
        except Exception:
            failed += 1  # counted, never named: a name would let someone look the document up
            text = ""
        target = candidate / (name.removeprefix("pdfs/").removesuffix(".pdf") + "_pg1_repeat1.md")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return time.time() - start, failed


def score(root: pathlib.Path, clone: pathlib.Path, candidate_name: str) -> dict:
    """The pinned scorer, unchanged, in a subprocess. Its stdout is parsed in memory and dropped."""
    env = scorer_env(root, clone)
    done = subprocess.run([sys.executable, "-m", "olmocr.bench.benchmark", "--dir", str(data_dir(root)),
                           "--candidate", candidate_name], cwd=clone, env=env, capture_output=True, text=True)
    if done.returncode:
        tail = (done.stderr.strip().splitlines() or ["no message"])[-1][:200]
        raise Unjudged(f"the scorer exited with {done.returncode}: {tail}")
    return parse_summary(done.stdout)


def cmd_run(args: argparse.Namespace) -> int:
    recipes = odl.load_recipes(pathlib.Path(args.recipes))
    if args.recipe not in recipes:
        raise Unjudged(f"unknown recipe {args.recipe!r}; recipes: {', '.join(recipes)}")
    recipe = recipes[args.recipe]
    root = resolve_root(args.root)
    data = data_dir(root)
    if args.fetch and not data.is_dir():
        fetch_dataset(root)
    manifest = load_manifest()
    problems = manifest_problems(data, manifest)
    if problems:
        raise Unjudged("the data is not the pinned revision:\n  " + "\n  ".join(problems[:20]))
    clone = ensure_scorer(root, args.fetch)
    if args.no_gate:
        baseline = {"recipe": args.recipe, "manifest_hash": manifest["manifest_hash"]}
    elif not args.baseline or not pathlib.Path(args.baseline).is_file():
        raise Unjudged(f"baseline not found: {args.baseline} (run --no-gate, then baseline RUN_DIR --out FILE)")
    else:
        baseline = json.loads(pathlib.Path(args.baseline).read_text(encoding="utf-8"))
    reason = odl.model_problem(recipe)
    if reason:
        raise Skipped(f"recipe {args.recipe}: {reason}; set PDFLUENT_MODEL_DIR and ORT_DYLIB_PATH")
    reason = scorer_problem(sys.executable, scorer_env(root, clone))
    if reason:
        raise Skipped(reason)
    build = odl.installed_pdfluent(pathlib.Path(args.wheel) if args.wheel else None)

    out = pathlib.Path(args.out) if args.out else root / "runs" / time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    out.mkdir(parents=True, exist_ok=True)
    candidate_name = f"pdfluent-{args.recipe}"
    candidate = data / candidate_name
    candidate.mkdir(parents=True, exist_ok=True)
    (candidate / NOTE_NAME).write_text(NOTE, encoding="utf-8")
    names = [n for n in manifest["files"] if n.endswith(".pdf")]
    seconds, failed = extract(recipe, data, names, candidate)
    scores = score(root, clone, candidate_name)
    result = {"recipe": args.recipe, "manifest_hash": manifest["manifest_hash"], "scores": scores,
              "pdfluent": {"version": build.get("version"), "wheel_sha256": build.get("wheel_sha256")}}
    violations = [] if args.no_gate else check(result, baseline, show=args.show_scores)
    if failed:
        violations.insert(0, f"recipe={args.recipe}: extraction raised an error on {failed} document(s)")
    run_json = {"schema": "olmocr-bench-run/v1", "recipe": args.recipe,
                "recipe_config": {"name": args.recipe, "file": odl.recipes_version(pathlib.Path(args.recipes)),
                                  "config": recipe,
                                  "extract_markdown_kwargs": odl.recipe_kwargs(recipe, "<PDFLUENT_MODEL_DIR>")},
                "pdfluent": build, "hf_revision": HF_REVISION, "scorer_commit": SCORER_PIN,
                "manifest_hash": manifest["manifest_hash"],
                "machine": {"cpu": odl._cpu(), "os": platform.platform(), "python": platform.python_version()},
                "wall_seconds": round(seconds, 3), "extraction_errors": failed, "scores": scores,
                "status": "failed" if violations else "passed", "violations": violations,
                "bench_repo_commit": odl._own_commit(), "attribution": ATTRIBUTION}
    assert_aggregates_only(run_json, "run.json")
    (out / "run.json").write_text(json.dumps(run_json, indent=2) + "\n", encoding="utf-8")
    (out / "scores.json").write_text(json.dumps({**result, "attribution": ATTRIBUTION}, indent=1) + "\n",
                                     encoding="utf-8")
    leaks = blindness_violations(out, names)
    if leaks:
        raise Unjudged("blindness rule broken, output files name documents: " + "; ".join(leaks))
    if args.show_scores:
        print(f"{args.recipe} overall={scores['overall']:.4f} " +
              " ".join(f"{k}={v:.4f}" for k, v in scores["categories"].items()))
    if args.no_gate:
        print("NOT GATED (--no-gate): this run only records scores", file=sys.stderr)
        return EXIT_OK if not failed else EXIT_FAIL
    if violations:
        print("\nGATE FAILED:", file=sys.stderr)
        for line in violations:
            print(f"  {line}", file=sys.stderr)
        return EXIT_FAIL
    print("gate passed")
    return EXIT_OK


def cmd_manifest(args: argparse.Namespace) -> int:
    data = data_dir(resolve_root(args.root))
    manifest = build_manifest(data)
    n_pdf = sum(1 for n in manifest["files"] if n.endswith(".pdf"))
    if n_pdf != 1403:
        raise Unjudged(f"expected 1403 PDFs, found {n_pdf}")
    MANIFEST.write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    print(f"{len(manifest['files'])} files, manifest {manifest['manifest_hash'][:16]}")
    return EXIT_OK


def cmd_verify(args: argparse.Namespace) -> int:
    root = resolve_root(args.root)
    if args.fetch and not data_dir(root).is_dir():
        fetch_dataset(root)
    problems = manifest_problems(data_dir(root), load_manifest())
    for line in problems:
        print(line, file=sys.stderr)
    print("manifest ok" if not problems else f"{len(problems)} file(s) differ")
    return EXIT_FAIL if problems else EXIT_OK


def cmd_baseline(args: argparse.Namespace) -> int:
    result = json.loads((pathlib.Path(args.run) / "scores.json").read_text(encoding="utf-8"))
    pathlib.Path(args.out).write_text(json.dumps(make_baseline(result, args.tolerance), indent=1) + "\n",
                                      encoding="utf-8")
    print(f"baseline for {result['recipe']} written to {args.out}")
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m bench.olmocr", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("manifest", "verify", "run"):
        p = sub.add_parser(name)
        p.add_argument("--root", default=None, help="private data root (or OLMOCR_BENCH_ROOT); not under $HOME")
        p.add_argument("--fetch", action="store_true", help="download the pinned dataset and scorer if absent")
    run = sub.choices["run"]
    run.add_argument("--recipe", required=True)
    run.add_argument("--recipes", default=str(RECIPES))
    run.add_argument("--baseline", default=os.environ.get("OLMOCR_BASELINE"))
    run.add_argument("--wheel", default=None, help="the wheel file that is installed, to record its hash")
    run.add_argument("--no-gate", action="store_true", help="record aggregates without a baseline")
    run.add_argument("--show-scores", action="store_true",
                     help="print aggregate numbers (off by default: logs may be public)")
    run.add_argument("--out", default=None, help="result directory (default: ROOT/runs/<utc>)")
    run.add_argument("--allow-skip", action="store_true")
    base = sub.add_parser("baseline")
    base.add_argument("run")
    base.add_argument("--out", required=True)
    base.add_argument("--tolerance", type=float, default=DEFAULT_TOLERANCE)
    args = parser.parse_args(argv)
    handler = {"manifest": cmd_manifest, "verify": cmd_verify, "run": cmd_run, "baseline": cmd_baseline}[args.command]
    try:
        return handler(args)
    except Skipped as exc:
        skipped_line(str(exc))
        return EXIT_OK if getattr(args, "allow_skip", False) else EXIT_SKIPPED
    except Unjudged as exc:
        print(f"olmocr-bench: cannot judge this run: {exc}", file=sys.stderr)
        return EXIT_UNJUDGED


if __name__ == "__main__":
    sys.exit(main())
