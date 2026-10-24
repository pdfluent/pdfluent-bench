# Copyright (c) 2026 Innovation Trigger B.V.
#
# PDFluent is available under two licences, at your option: the GNU AGPLv3, or
# the PDFluent Commercial Licence. See the LICENSE file in this repository --
# that file travels with the copy you received, which a URL does not.
"""odl-bench: the 200 DP-Bench documents, one recipe at a time, with a gate.

    python -m bench.odl manifest  --clone DIR            write odl-bench/manifest.json
    python -m bench.odl verify    --clone DIR            check the clone against it
    python -m bench.odl run       --recipe R --clone DIR --baseline FILE --split-file FILE
    python -m bench.odl baseline  RUN_DIR --out FILE     turn a run into a baseline (by hand)
    python -m bench.odl split     --labels FILE          scoreless split file from the private labels

The corpus is never copied into this repository. `run` uses a clone of
opendataloader-bench at the pinned commit and its scorer unchanged; the
adapter and the recipes are ours (odl-bench/recipes.toml). Order of a run:
pin, manifest hashes, models, extraction, scoring, split, gate.

Exit codes: 0 gate passed, 1 gate failed (the cause is printed: recipe, scope,
metric, documents), 2 the run could not be judged (pin, manifest, split file,
baseline, scorer missing), 3 skipped because a model is absent
(`SKIPPED (not a pass): <reason>` on stderr; --allow-skip turns the code into 0
and the line stays).

Baselines hold numbers and are not kept in this public repository: pass the
file with --baseline.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib
import json
import os
import pathlib
import platform
import subprocess
import sys
import time
import tomllib

ROOT = pathlib.Path(__file__).resolve().parents[1]
ODL = ROOT / "odl-bench"
RECIPES = ODL / "recipes.toml"
MANIFEST = ODL / "manifest.json"

UPSTREAM = "https://github.com/opendataloader-project/opendataloader-bench.git"
PIN = "7af1d8f4d0c09f51ea1a5c6ba5f66e993286d109"
PDF_DIR = "pdfs"
GT_DIR = "ground-truth/markdown"
METRICS = ("overall", "nid", "teds", "mhs")
SCOPES = ("all", "tune", "holdout")
DEFAULT_TOLERANCE = 0.005
EPS = 1e-9

EXIT_OK, EXIT_FAIL, EXIT_UNJUDGED, EXIT_SKIPPED = 0, 1, 2, 3


class Unjudged(Exception):
    """The run cannot be judged: a precondition failed. Maps to exit code 2."""


class Skipped(Exception):
    """A model is absent. Maps to exit code 3, never to a pass."""


def skipped_line(reason: str) -> None:
    print(f"SKIPPED (not a pass): {reason}", file=sys.stderr, flush=True)


# --- recipes ---------------------------------------------------------------

def recipes_version(path: pathlib.Path = RECIPES) -> dict:
    """Version and SHA-256 of the recipes file: what every result names."""
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data.get("version"), int):
        raise Unjudged(f"{path}: top-level integer `version` is missing")
    return {"version": data["version"], "sha256": _sha256(path)}


def load_recipes(path: pathlib.Path = RECIPES) -> dict[str, dict]:
    raw = tomllib.loads(path.read_text(encoding="utf-8")).get("recipe", {})
    recipes = {}
    for name, body in raw.items():
        quality = body.get("quality")
        if quality not in ("fast", "balanced"):
            raise Unjudged(f"recipe {name!r}: quality must be fast or balanced, got {quality!r}")
        recipes[name] = {"quality": quality, "models": bool(body.get("models", False)),
                         "tatr": bool(body.get("tatr", False)), "options": dict(body.get("options", {}))}
        if recipes[name]["tatr"] and not recipes[name]["models"]:
            raise Unjudged(f"recipe {name!r}: tatr needs models = true")
    return recipes


def recipe_kwargs(recipe: dict, model_dir: str | None) -> dict:
    """The keyword arguments `extract_markdown` gets. Nothing is added silently."""
    kwargs = {"quality": recipe["quality"], **recipe["options"]}
    if recipe["models"]:
        kwargs["model_dir"] = model_dir
    if recipe["tatr"]:
        kwargs["table_model_dir"] = model_dir
    return kwargs


def model_problem(recipe: dict, env: dict[str, str] | None = None) -> str | None:
    """Why the recipe cannot run here, or None. Reads PDFLUENT_MODEL_DIR and ORT_DYLIB_PATH."""
    if not recipe["models"]:
        return None
    env = os.environ if env is None else env
    model_dir, ort = env.get("PDFLUENT_MODEL_DIR", ""), env.get("ORT_DYLIB_PATH", "")
    if not model_dir:
        return "PDFLUENT_MODEL_DIR is not set"
    if not pathlib.Path(model_dir).is_dir():
        return f"PDFLUENT_MODEL_DIR is not a directory: {model_dir}"
    if not ort:
        return "ORT_DYLIB_PATH is not set"
    if not pathlib.Path(ort).is_file():
        return f"ORT_DYLIB_PATH is not a file: {ort}"
    return None


# --- manifest --------------------------------------------------------------

def _sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def combined_hash(files: dict[str, str]) -> str:
    """One hash over the sorted file list, in `sha256sum` line format."""
    return hashlib.sha256("".join(f"{files[n]}  {n}\n" for n in sorted(files)).encode()).hexdigest()


def digests(directory: pathlib.Path, suffix: str) -> dict[str, str]:
    return {p.name: _sha256(p) for p in sorted(directory.glob(f"*{suffix}"))}


def build_manifest(clone: pathlib.Path) -> dict:
    pdfs, truth = digests(clone / PDF_DIR, ".pdf"), digests(clone / GT_DIR, ".md")
    return {"bench_commit": PIN, "pdfs": pdfs, "ground_truth": truth,
            "pdfs_combined": combined_hash(pdfs), "ground_truth_combined": combined_hash(truth),
            "manifest_hash": manifest_hash(pdfs, truth)}


def with_labels_hash(manifest: dict) -> dict:
    return {**manifest, "labels_manifest_sha256": labels_hash(manifest)}


def labels_hash(manifest: dict) -> str:
    """The hash the labels file (#969) carries: sha256 of the sorted lines
    '<file sha256>  <path relative to the bench root>' over pdfs/ and ground-truth/markdown/."""
    lines = [f"{h}  {PDF_DIR}/{n}" for n, h in manifest["pdfs"].items()]
    lines += [f"{h}  {GT_DIR}/{n}" for n, h in manifest["ground_truth"].items()]
    return hashlib.sha256("\n".join(sorted(lines)).encode()).hexdigest()


def manifest_hash(pdfs: dict[str, str], truth: dict[str, str]) -> str:
    return hashlib.sha256((combined_hash(pdfs) + combined_hash(truth)).encode()).hexdigest()


def manifest_problems(clone: pathlib.Path, manifest: dict) -> list[str]:
    """Every file that differs, by name. Empty list means the corpus is the pinned one."""
    problems = []
    for key, directory, suffix in (("pdfs", PDF_DIR, ".pdf"), ("ground_truth", GT_DIR, ".md")):
        want, have = manifest[key], digests(clone / directory, suffix)
        for name in sorted(set(want) | set(have)):
            if name not in have:
                problems.append(f"{directory}/{name}: missing (manifest has it)")
            elif name not in want:
                problems.append(f"{directory}/{name}: not in the manifest")
            elif want[name] != have[name]:
                problems.append(f"{directory}/{name}: sha256 {have[name][:16]} != manifest {want[name][:16]}")
    return problems


def load_manifest(path: pathlib.Path = MANIFEST) -> dict:
    if not path.is_file():
        raise Unjudged(f"manifest not found: {path} (create it with: python -m bench.odl manifest --clone DIR)")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("bench_commit") != PIN:
        raise Unjudged(f"manifest is for commit {manifest.get('bench_commit')}, the pin is {PIN}")
    return manifest


# --- clone -----------------------------------------------------------------

def _git(clone: pathlib.Path, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(clone), *args], capture_output=True, text=True, check=False)
    if done.returncode:
        raise Unjudged(f"git {' '.join(args)} failed in {clone}: {done.stderr.strip()}")
    return done.stdout.strip()


def ensure_clone(clone: pathlib.Path, fetch: bool) -> None:
    """Clone at run time (never vendored) when asked, then require HEAD == PIN."""
    if not (clone / ".git").exists():
        if not fetch:
            raise Unjudged(f"no clone at {clone}; pass --fetch to clone {UPSTREAM} at {PIN}")
        clone.parent.mkdir(parents=True, exist_ok=True)
        done = subprocess.run(["git", "clone", "--quiet", UPSTREAM, str(clone)], capture_output=True, text=True)
        if done.returncode:
            raise Unjudged(f"git clone failed: {done.stderr.strip()}")
        _git(clone, "checkout", "--quiet", "--detach", PIN)
    head = _git(clone, "rev-parse", "HEAD")
    if head != PIN:
        raise Unjudged(f"clone {clone} is at {head}, the pin is {PIN}")


# --- split -----------------------------------------------------------------

SPLIT = ODL / "split-v1.json"
SPLIT_SCHEMA = "odl-bench-split/v1"
LABELS_SCHEMA = "odl-bench-labels/v1"


def split_digest(split: dict[str, str]) -> str:
    """sha256 over the sorted lines '<doc id> <split>' (the file's own `split_sha256`)."""
    return hashlib.sha256("".join(f"{d} {split[d]}\n" for d in sorted(split)).encode()).hexdigest()


def load_split(path: pathlib.Path | None, doc_ids: list[str], lhash: str) -> dict[str, str]:
    """document id -> "tune" | "holdout". Fails clearly.

    Reads the scoreless public `split-v1.json`, or the private full labels file (#969,
    `odl-bench-labels/v1`, passed with --labels) of which only the split is used.
    `lhash` is the labels-scheme manifest hash of the corpus on disk (labels_hash)."""
    if path is None:
        raise Unjudged("no tune/holdout split: pass --split-file / --labels (or ODL_SPLIT_FILE), "
                       "normally odl-bench/split-v1.json; or --no-split to score without the halves")
    if not path.is_file():
        raise Unjudged(f"tune/holdout split not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") not in (SPLIT_SCHEMA, LABELS_SCHEMA):
        raise Unjudged(f"{path}: schema {data.get('schema')!r}, expected {SPLIT_SCHEMA} or {LABELS_SCHEMA}")
    if data.get("bench_commit") != PIN:
        raise Unjudged(f"{path} was built on bench commit {data.get('bench_commit')}, the pin is {PIN}")
    if data.get("manifest_sha256") != lhash:
        raise Unjudged(f"{path} was built on manifest {str(data.get('manifest_sha256'))[:16]}, "
                       f"this corpus is {lhash[:16]}")
    labels: dict[str, str] = {}
    if data["schema"] == SPLIT_SCHEMA:
        labels = dict(data["documents"])
        if data.get("split_sha256") != split_digest(labels):
            raise Unjudged(f"{path}: split_sha256 does not match its documents")
    else:
        for doc in data["documents"]:
            if doc["doc_id"] in labels:
                raise Unjudged(f"{path}: document {doc['doc_id']} appears twice")
            labels[doc["doc_id"]] = doc["split"]
    bad = {k: v for k, v in labels.items() if v not in ("tune", "holdout")}
    if bad:
        raise Unjudged(f"{path}: labels other than tune/holdout, e.g. {next(iter(bad.items()))}")
    missing = [d for d in doc_ids if d not in labels]
    extra = [d for d in labels if d not in set(doc_ids)]
    if missing or extra:
        raise Unjudged(f"{path} does not cover the 200 documents: missing {missing[:5]}, unknown {extra[:5]}")
    return {d: labels[d] for d in doc_ids}


def cmd_split(args: argparse.Namespace) -> int:
    """Write the scoreless public split file from the private labels file."""
    data = json.loads(pathlib.Path(args.labels).read_text(encoding="utf-8"))
    if data.get("schema") != LABELS_SCHEMA:
        raise Unjudged(f"{args.labels}: schema {data.get('schema')!r}, expected {LABELS_SCHEMA}")
    split = {d["doc_id"]: d["split"] for d in data["documents"]}
    out = {"schema": SPLIT_SCHEMA, "bench_commit": data["bench_commit"],
           "manifest_sha256": data["manifest_sha256"], "split_rule": data["split_rule"],
           "split_sha256": split_digest(split), "documents": dict(sorted(split.items()))}
    pathlib.Path(args.out).write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
    print(f"split written to {args.out} ({split_digest(split)[:16]})")
    return EXIT_OK


# --- scores ----------------------------------------------------------------

def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def aggregate(documents: dict[str, dict], ids: list[str] | None = None) -> dict[str, float | None]:
    """Mean per metric over the documents that have one, as the scorer does."""
    ids = list(documents) if ids is None else ids
    return {m: _mean([documents[d][m] for d in ids if documents[d].get(m) is not None]) for m in METRICS}


def scores_by_scope(documents: dict[str, dict], split: dict[str, str] | None) -> dict[str, dict]:
    out = {"all": aggregate(documents)}
    if split is not None:
        for scope in ("tune", "holdout"):
            out[scope] = aggregate(documents, [d for d in documents if split.get(d) == scope])
    return out


# --- the gate --------------------------------------------------------------

def check(result: dict, baseline: dict) -> list[str]:
    """Violations, each naming recipe, scope, metric and (where known) documents. Empty means green."""
    recipe = result["recipe"]
    out: list[str] = []
    if baseline.get("recipe") != recipe:
        return [f"recipe={recipe}: the baseline is for recipe {baseline.get('recipe')!r}"]
    if baseline.get("manifest_hash") != result.get("manifest_hash"):
        return [f"recipe={recipe}: baseline was measured on manifest {str(baseline.get('manifest_hash'))[:16]}, "
                f"this run is on {str(result.get('manifest_hash'))[:16]}"]
    tol = float(baseline.get("tolerance", DEFAULT_TOLERANCE))
    base_docs = baseline.get("documents", {})
    for scope, floors in baseline.get("scores", {}).items():
        have = result["scores"].get(scope)
        if have is None:
            out.append(f"recipe={recipe} scope={scope}: the baseline has this scope and the run has none "
                       f"(no split labels?)")
            continue
        for metric, floor in floors.items():
            if floor is None:
                continue  # the baseline had no score for this metric either
            got = have.get(metric)
            if got is None:
                out.append(f"recipe={recipe} scope={scope} metric={metric}: no score in the run, baseline {floor:.4f}")
            elif got < floor - tol - EPS:
                line = (f"recipe={recipe} scope={scope} metric={metric}: {got:.4f} is below baseline "
                        f"{floor:.4f} - tolerance {tol} (drop {floor - got:.4f})")
                worst = _worst_drops(result["documents"], base_docs, metric, scope, result.get("split"))
                if worst:
                    line += "; largest per-document drops: " + ", ".join(f"{d} {b:.3f}->{g:.3f}" for d, b, g in worst)
                out.append(line)
    for doc, floors in baseline.get("canaries", {}).items():
        for metric, floor in floors.items():
            got = result["documents"].get(doc, {}).get(metric)
            if got is None:
                out.append(f"recipe={recipe} canary document={doc} metric={metric}: no score in the run")
            elif got < floor - tol - EPS:
                out.append(f"recipe={recipe} canary document={doc} metric={metric}: {got:.4f} is below its floor "
                           f"{floor:.4f} - tolerance {tol}")
    return out


def _worst_drops(docs: dict, base_docs: dict, metric: str, scope: str, split: dict | None, n: int = 3):
    drops = []
    for doc, base in base_docs.items():
        if scope != "all" and (split or {}).get(doc) != scope:
            continue
        b, g = base.get(metric), docs.get(doc, {}).get(metric)
        if b is not None and g is not None and g < b - EPS:
            drops.append((b - g, doc, b, g))
    return [(d, b, g) for _, d, b, g in sorted(drops, reverse=True)[:n]]


def make_baseline(result: dict, tolerance: float = DEFAULT_TOLERANCE, canaries: list[str] | None = None) -> dict:
    canaries = CANARIES if canaries is None else canaries
    return {"recipe": result["recipe"], "manifest_hash": result["manifest_hash"], "tolerance": tolerance,
            "bench_commit": result["bench_commit"], "scores": result["scores"],
            "canaries": {c: {"overall": result["documents"][c]["overall"]} for c in canaries},
            "documents": result["documents"]}


# Documents 31 (crop box), 16 (contents page), 36 (columns), 110 (raster table).
CANARIES = [f"0103000000{n:04d}" for n in (31, 16, 36, 110)]


# --- running ---------------------------------------------------------------

def installed_pdfluent(wheel: pathlib.Path | None) -> dict:
    import importlib.metadata as md
    try:
        dist = md.distribution("pdfluent")
    except md.PackageNotFoundError:
        raise Unjudged("the pdfluent wheel is not installed in this interpreter") from None
    info = {"version": dist.version}
    if wheel is not None:
        if not wheel.is_file():
            raise Unjudged(f"--wheel {wheel} does not exist")
        if f"pdfluent-{dist.version}-" not in wheel.name:
            raise Unjudged(f"--wheel {wheel.name} is not the installed pdfluent {dist.version}")
        info["wheel_sha256"] = _sha256(wheel)
    else:
        record = next((f for f in dist.files or [] if f.name == "RECORD"), None)
        info["wheel_sha256"] = None
        info["record_sha256"] = _sha256(pathlib.Path(dist.locate_file(record))) if record else None
    return info


def extract(recipe: dict, pdfs: list[pathlib.Path], out_dir: pathlib.Path) -> tuple[float, list[str]]:
    """Run the recipe over every document. Returns wall time and the documents that failed."""
    pdfluent = importlib.import_module("pdfluent")
    kwargs = recipe_kwargs(recipe, os.environ.get("PDFLUENT_MODEL_DIR"))
    out_dir.mkdir(parents=True, exist_ok=True)
    failed: list[str] = []
    start = time.time()
    for pdf in pdfs:
        try:
            text = pdfluent.open(str(pdf)).extract_markdown(**kwargs)
        except TypeError:
            raise  # an option the wheel does not know: the recipe is wrong, stop
        except Exception as exc:  # a document failing is a finding, not a pass
            print(f"FAILED {pdf.stem}: {exc}", file=sys.stderr)
            failed.append(pdf.stem)
            text = ""
        (out_dir / f"{pdf.stem}.md").write_text(text, encoding="utf-8")
    return time.time() - start, failed


def score(clone: pathlib.Path, prediction_dir: pathlib.Path) -> dict[str, dict]:
    """The pinned scorer, unchanged. Returns document id -> {overall, nid, teds, mhs}."""
    sys.path.insert(0, str(clone / "src"))
    try:
        evaluator = importlib.import_module("evaluator")
    except ImportError as exc:
        raise Unjudged(f"the pinned scorer cannot be imported ({exc}); install the clone's dependencies "
                       f"(apted, beautifulsoup4, lxml, rapidfuzz)") from exc
    path = evaluator._evaluate_engine_version(clone / GT_DIR, prediction_dir, "evaluation.json")
    if path is None:
        raise Unjudged("the scorer produced no evaluation")
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {d["document_id"]: {m: d["scores"].get(m) for m in METRICS} for d in payload["documents"]}


def judge(recipe_name: str, documents: dict[str, dict], split: dict[str, str] | None, mhash: str,
          baseline: dict) -> tuple[dict, list[str]]:
    result = {"recipe": recipe_name, "manifest_hash": mhash, "bench_commit": PIN, "split": split,
              "scores": scores_by_scope(documents, split), "documents": documents}
    return result, check(result, baseline)


def _print_scores(result: dict) -> None:
    for scope, s in result["scores"].items():
        print(f"{result['recipe']} {scope:8s} " + " ".join(
            f"{m}={s[m]:.4f}" if s[m] is not None else f"{m}=n/a" for m in METRICS))


def cmd_run(args: argparse.Namespace) -> int:
    recipes = load_recipes(pathlib.Path(args.recipes))
    if args.recipe not in recipes:
        raise Unjudged(f"unknown recipe {args.recipe!r}; recipes: {', '.join(recipes)}")
    recipe = recipes[args.recipe]
    clone = pathlib.Path(args.clone)
    ensure_clone(clone, args.fetch)
    manifest = load_manifest()
    problems = manifest_problems(clone, manifest)
    if problems:
        raise Unjudged("the corpus is not the pinned one:\n  " + "\n  ".join(problems[:20]))
    if args.no_gate:
        baseline = {"recipe": args.recipe, "manifest_hash": manifest["manifest_hash"]}
    elif not args.baseline or not pathlib.Path(args.baseline).is_file():
        raise Unjudged(f"baseline not found: {args.baseline} (make one from an ungated run: "
                       f"run --no-gate, then baseline RUN_DIR --out FILE)")
    else:
        baseline = json.loads(pathlib.Path(args.baseline).read_text(encoding="utf-8"))
    ids = [p[:-4] for p in sorted(manifest["pdfs"])]
    split = None if args.no_split else load_split(
        pathlib.Path(args.split_file) if args.split_file else None, ids, labels_hash(manifest))
    reason = model_problem(recipe)
    if reason:
        raise Skipped(f"recipe {args.recipe}: {reason}; set PDFLUENT_MODEL_DIR and ORT_DYLIB_PATH")
    build = installed_pdfluent(pathlib.Path(args.wheel) if args.wheel else None)

    out = pathlib.Path(args.out)
    prediction = out / "prediction" / args.recipe
    seconds, failed = extract(recipe, [clone / PDF_DIR / f"{d}.pdf" for d in ids], prediction / "markdown")
    documents = score(clone, prediction)
    result, violations = judge(args.recipe, documents, split, manifest["manifest_hash"], baseline)
    violations = [f"recipe={args.recipe} document={d}: extraction raised an error" for d in failed] + violations
    recipe_info = {"name": args.recipe, "file": recipes_version(pathlib.Path(args.recipes)),
                   "config": recipe, "extract_markdown_kwargs": recipe_kwargs(recipe, "<PDFLUENT_MODEL_DIR>")}
    run_json = {"recipe": args.recipe, "recipe_config": recipe_info, "pdfluent": build,
                "bench_commit": PIN, "manifest_hash": manifest["manifest_hash"],
                "pdfs_combined": manifest["pdfs_combined"], "ground_truth_combined": manifest["ground_truth_combined"],
                "machine": {"cpu": _cpu(), "os": platform.platform(), "python": platform.python_version()},
                "wall_seconds": round(seconds, 3), "documents": len(ids), "split_file": args.split_file,
                "scores": result["scores"], "status": "failed" if violations else "passed",
                "violations": violations, "bench_repo_commit": _own_commit()}
    (out / "run.json").write_text(json.dumps(run_json, indent=2) + "\n", encoding="utf-8")
    (out / "scores.json").write_text(json.dumps({"recipe": args.recipe, "manifest_hash": manifest["manifest_hash"],
                                                 "bench_commit": PIN, "recipe_config": recipe_info,
                                                 "scores": result["scores"], "documents": documents, "split": split}, indent=1) + "\n",
                                     encoding="utf-8")
    _print_scores(result)
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


def _cpu() -> str:
    cpuinfo = pathlib.Path("/proc/cpuinfo")
    if cpuinfo.is_file():
        for line in cpuinfo.read_text(errors="replace").splitlines():
            if line.lower().startswith("model name"):
                return line.split(":", 1)[1].strip()
    done = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True)
    return done.stdout.strip() if done.returncode == 0 and done.stdout.strip() else platform.processor() or "unknown"


def _own_commit() -> str:
    done = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True)
    return done.stdout.strip() if done.returncode == 0 else "unknown"


def cmd_manifest(args: argparse.Namespace) -> int:
    clone = pathlib.Path(args.clone)
    ensure_clone(clone, False)
    manifest = with_labels_hash(build_manifest(clone))
    if len(manifest["pdfs"]) != 200 or len(manifest["ground_truth"]) != 200:
        raise Unjudged(f"expected 200 PDFs and 200 ground-truth files, found "
                       f"{len(manifest['pdfs'])} and {len(manifest['ground_truth'])}")
    MANIFEST.write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    print(f"pdfs {manifest['pdfs_combined'][:16]}  ground truth {manifest['ground_truth_combined'][:16]}")
    return EXIT_OK


def cmd_verify(args: argparse.Namespace) -> int:
    clone = pathlib.Path(args.clone)
    ensure_clone(clone, args.fetch)
    problems = manifest_problems(clone, load_manifest())
    for line in problems:
        print(line, file=sys.stderr)
    print("manifest ok" if not problems else f"{len(problems)} file(s) differ")
    return EXIT_FAIL if problems else EXIT_OK


def cmd_baseline(args: argparse.Namespace) -> int:
    scores = json.loads((pathlib.Path(args.run) / "scores.json").read_text(encoding="utf-8"))
    result = {"recipe": scores["recipe"], "manifest_hash": scores["manifest_hash"], "bench_commit": scores["bench_commit"],
              "scores": scores["scores"], "documents": scores["documents"]}
    pathlib.Path(args.out).write_text(json.dumps(make_baseline(result, args.tolerance), indent=1) + "\n",
                                      encoding="utf-8")
    print(f"baseline for {result['recipe']} written to {args.out}")
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m bench.odl", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    clone_default = os.environ.get("ODL_CLONE", "work/odl-bench/opendataloader-bench")
    for name in ("manifest", "verify", "run"):
        p = sub.add_parser(name)
        p.add_argument("--clone", default=clone_default)
        p.add_argument("--fetch", action="store_true", help="clone the pinned commit if there is no clone")
    run = sub.choices["run"]
    run.add_argument("--recipe", required=True)
    run.add_argument("--recipes", default=str(RECIPES), help="recipes file (default: odl-bench/recipes.toml)")
    run.add_argument("--baseline", default=os.environ.get("ODL_BASELINE"))
    run.add_argument("--split-file", "--labels", dest="split_file",
                     default=os.environ.get("ODL_SPLIT_FILE", str(SPLIT)),
                     help="scoreless odl-bench/split-v1.json (default), or the private labels file of #969")
    run.add_argument("--no-split", action="store_true", help="score without the tune/holdout halves")
    run.add_argument("--wheel", default=None, help="the wheel file that is installed, to record its hash")
    run.add_argument("--no-gate", action="store_true", help="record scores without a baseline (to make one)")
    run.add_argument("--out", default="results/odl-bench")
    run.add_argument("--allow-skip", action="store_true", help="a skipped recipe exits 0 (the line is still printed)")
    sp = sub.add_parser("split")
    sp.add_argument("--labels", required=True, help="the private labels file (odl-bench-labels/v1)")
    sp.add_argument("--out", default=str(SPLIT))
    base = sub.add_parser("baseline")
    base.add_argument("run")
    base.add_argument("--out", required=True)
    base.add_argument("--tolerance", type=float, default=DEFAULT_TOLERANCE)
    args = parser.parse_args(argv)
    handler = {"manifest": cmd_manifest, "verify": cmd_verify, "run": cmd_run, "baseline": cmd_baseline,
               "split": cmd_split}[args.command]
    try:
        pathlib.Path(getattr(args, "out", "results")).mkdir(parents=True, exist_ok=True) if args.command == "run" else None
        return handler(args)
    except Skipped as exc:
        skipped_line(str(exc))
        return EXIT_OK if getattr(args, "allow_skip", False) else EXIT_SKIPPED
    except Unjudged as exc:
        print(f"odl-bench: cannot judge this run: {exc}", file=sys.stderr)
        return EXIT_UNJUDGED


if __name__ == "__main__":
    sys.exit(main())
