# Copyright (c) 2026 Innovation Trigger B.V.
#
# PDFluent is available under two licences, at your option: the GNU AGPLv3, or
# the PDFluent Commercial Licence. See the LICENSE file in this repository --
# that file travels with the copy you received, which a URL does not.
"""python -m bench <command>: run, quality-one, report, compare, datasets, cost.

`run` is what `make bench` calls. Per configuration it measures quality on
every set and split asked for, then speed, writes run.json beside the
results and the overview report, and exits non-zero if any mandatory step
failed. A configuration named in --optional that cannot run here is reported
as `SKIPPED (not a pass): <reason>` on stderr; any other one that cannot run
fails the run.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import shlex
import subprocess
import sys
import time

from bench import compare, configs, cost, datasets, quality, report, runinfo, speed, validate

ROOT = pathlib.Path(__file__).resolve().parents[1]


def skipped(reason: str) -> None:
    print(f"SKIPPED (not a pass): {reason}", file=sys.stderr, flush=True)


def _docker_image(config: dict, gpu: bool, work: pathlib.Path) -> tuple[str, str]:
    family = config["gpu"].get("image") if gpu and config["gpu"]["route"] else config["image"]
    built = runinfo.images(work).get(family)
    if not built:
        raise SystemExit(f"[bench] FATAL: image `{family}` is not built; run docker/build.sh {family}")
    return built["tag"], built["image_id"]


def docker_command(config: dict, gpu: bool, work: pathlib.Path, out: pathlib.Path,
                   args: list[str]) -> list[str]:
    tag, image_id = _docker_image(config, gpu, work)
    command = ["docker", "run", "--rm", f"--memory={os.environ.get('BENCH_MEMORY', '12g')}",
               "-v", f"{ROOT}:/bench:ro", "-v", f"{work.resolve()}:/work",
               "-v", f"{out.resolve()}:/out", "-e", f"BENCH_IMAGE_ID={image_id}",
               "-e", "BENCH_ALLOW_VERSION_MISMATCH",
               # git inside the container reads the mounted repository's commit
               "-e", "GIT_CONFIG_COUNT=1", "-e", "GIT_CONFIG_KEY_0=safe.directory",
               "-e", "GIT_CONFIG_VALUE_0=*"]
    if gpu and config["gpu"]["route"]:
        command += ["--gpus", "all"]
    # Only what the host has: `-e NAME` for an unset NAME clears the image's
    # own value (the pdfluent image sets PDFLUENT_TABLE_MODEL_DIR itself).
    for name in [*(config.get("required_env") or []), "FINTABNET_ARCHIVE_URL"]:
        if os.environ.get(name):
            command += ["-e", name]
    command += shlex.split(os.environ.get("BENCH_DOCKER_ARGS", ""))
    command += [tag, "/opt/bench/bin/python", "-m", "bench", *args]
    return command


def _docker(config: dict, gpu: bool, work: pathlib.Path, out: pathlib.Path,
            args: list[str]) -> int:
    return subprocess.run(docker_command(config, gpu, work, out, args), check=False).returncode


def _docker_unavailable(config: dict, gpu: bool, work: pathlib.Path) -> str | None:
    family = config["gpu"].get("image") if gpu and config["gpu"]["route"] else config["image"]
    if not runinfo.images(work).get(family):
        return f"image `{family}` is not built (docker/build.sh {family})"
    return None


def cmd_run(args: argparse.Namespace) -> int:
    started = time.monotonic()
    chosen = configs.resolve(args.configs)
    sets = configs.resolve_sets(args.sets)
    splits = [s.strip() for s in args.splits.split(",") if s.strip()]
    optional = {s.strip() for s in (args.optional or "").split(",") if s.strip()}
    machine = configs.machine(args.machine)
    gpu = bool(machine.get("gpu"))
    work = pathlib.Path(args.work)
    work.mkdir(parents=True, exist_ok=True)
    run_id = args.run_id or runinfo.run_id(args.machine)
    out = pathlib.Path(args.out or ROOT / "results" / run_id)
    out.mkdir(parents=True, exist_ok=True)
    record = runinfo.start(run_id, args.machine, work, sets, chosen, sys.argv)
    record["profile"], record["splits"], record["reps"] = args.profile, splits, args.reps
    record["runner"] = args.runner
    failures = 0

    def save() -> None:
        # Written after every step, so a partial copy of a round that never
        # finished (a preempted spot VM) still says what ran and how.
        (out / "run.json").write_text(json.dumps(record, indent=1) + "\n")

    def step(kind: str, config: dict, **fields) -> dict:
        row = {"kind": kind, "config": config["name"], **fields, "started": runinfo.now()}
        record["steps"].append(row)
        save()
        return row

    for config in chosen:
        if gpu and not config["gpu"]["route"]:
            step("config", config, status="n/a", why=config["gpu"]["why"])
            continue
        if args.runner == "docker":
            why = _docker_unavailable(config, gpu, work)
            if why:
                if config["name"] in optional:
                    skipped(f"{config['name']}: {why}")
                    step("config", config, status="skipped", why=why)
                    continue
                print(f"[bench] FAIL: {config['name']} cannot run here: {why}", file=sys.stderr)
                step("config", config, status="failed", why=why)
                failures += 1
                continue
        if args.runner == "native":
            why = configs.unavailable(config, configs.tool_python(config))
            if why:
                if config["name"] in optional:
                    skipped(f"{config['name']}: {why}")
                    step("config", config, status="skipped", why=why)
                    continue
                print(f"[bench] FAIL: {config['name']} cannot run here: {why}", file=sys.stderr)
                step("config", config, status="failed", why=why)
                failures += 1
                continue
            record["configs"][config["name"]]["version"] = configs.version_record(
                config, configs.tool_python(config))
        if not args.no_quality:
            for set_name in sets:
                size = configs.pages_for(set_name, args.profile)
                for split in splits:
                    row = step("quality", config, set=set_name, split=split, pages=size)
                    t0 = time.monotonic()
                    try:
                        if args.runner == "docker":
                            code = _docker(config, gpu, work, out, [
                                "quality-one", "--config", config["name"], "--set", set_name,
                                "--split", split, "--size", str(size), "--work", "/work",
                                "--out", "/out", "--run-id", run_id,
                                *(["--because", args.because] if args.because else [])])
                            if code:
                                raise SystemExit(f"container exited {code}")
                        else:
                            quality.quality_one(config, set_name, split, size, work, out,
                                                run_id, args.because)
                        row["status"] = "ok"
                    except SystemExit as exc:
                        row["status"], row["why"] = "failed", str(exc)
                        print(f"[bench] FAIL: quality {config['name']} {set_name} {split}: {exc}",
                              file=sys.stderr)
                        failures += 1
                    row["seconds"] = round(time.monotonic() - t0, 1)
                    save()
        if not args.no_speed and all(s == "parsebench" for s in sets):
            step("speed", config, status="n/a",
                 why="ParseBench pages are not timed apart; add another set to time this column")
        elif not args.no_speed:
            row = step("speed", config, reps=args.reps)
            t0 = time.monotonic()
            try:
                if args.runner == "docker":
                    code = _docker(config, gpu, work, out, [
                        "speed-one", "--config", config["name"], "--sets", ",".join(sets),
                        "--profile", args.profile, "--reps", str(args.reps), "--work", "/work",
                        "--out", "/out", "--run-id", run_id,
                        "--machine-kind", "gpu" if gpu else "cpu"])
                    if code:
                        raise SystemExit(f"container exited {code}")
                else:
                    _speed(config, sets, args.profile, args.reps, work, out, run_id,
                           "gpu" if gpu else "cpu")
                row["status"] = "ok"
            except SystemExit as exc:
                row["status"], row["why"] = "failed", str(exc)
                print(f"[bench] FAIL: speed {config['name']}: {exc}", file=sys.stderr)
                failures += 1
            row["seconds"] = round(time.monotonic() - t0, 1)
            save()

    runinfo.finish(record, started, time.monotonic())
    record["failures"] = failures
    (out / "run.json").write_text(json.dumps(record, indent=1) + "\n")
    if any((out / "quality").glob("*/*/*.json")) or any((out / "speed").glob("*/*.json")):
        md, _js = report.write([out], out)
        print(f"[bench] report {md}")
    print(f"[bench] run {run_id}: {failures} failure(s); results in {out}")
    return 1 if failures else 0


def speed_pages(sets: list[str], profile: str, work: pathlib.Path) -> list[dict]:
    pages = []
    for set_name in sets:
        if set_name == "parsebench":
            continue  # ParseBench pages are timed inside its quality run only
        entry, materialised, manifest, seed = quality.materialised(set_name, work)
        size = configs.pages_for(set_name, profile, "speed_pages")
        chosen = quality.choose(materialised, manifest, "tuning", size, seed)
        quality.verify(chosen, manifest)
        pages += chosen
    return pages


def _speed(config: dict, sets: list[str], profile: str, reps: int, work: pathlib.Path,
           out: pathlib.Path, run_id: str, machine_kind: str) -> pathlib.Path:
    pages = speed_pages(sets, profile, work)
    result = speed.speed_one(config, pages, reps, work / "speed" / run_id / config["name"])
    result["sets"] = sets
    return speed.write(result, out, machine_kind)


def cmd_quality_one(args: argparse.Namespace) -> int:
    path = quality.quality_one(configs.load(args.config), args.set, args.split, args.size,
                               pathlib.Path(args.work), pathlib.Path(args.out), args.run_id,
                               args.because)
    print(f"[bench] {path}")
    return 0


def cmd_speed_one(args: argparse.Namespace) -> int:
    path = _speed(configs.load(args.config), configs.resolve_sets(args.sets), args.profile,
                  args.reps, pathlib.Path(args.work), pathlib.Path(args.out), args.run_id,
                  args.machine_kind)
    print(f"[bench] {path}")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    dirs = [pathlib.Path(d) for d in args.runs]
    md, js = report.write(dirs, pathlib.Path(args.out or dirs[0]))
    print(f"[report] {md}\n[report] {js}")
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    dirs = [pathlib.Path(d) for d in args.runs]
    md, js = compare.write(dirs, pathlib.Path(args.out or dirs[0]))
    print(f"[compare] {md}\n[compare] {js}")
    return 0


def fetch_command(image: str, sets: str, work: pathlib.Path, profile: str,
                  limit: int | None) -> list[str]:
    built = runinfo.images(work).get(image)
    if not built:
        raise SystemExit(f"[bench] FATAL: image `{image}` is not built")
    # Read-write: FinTabNet's draw writes engine/corpus/structured/FINTABNET.json.
    # HF_HUB_OFFLINE=0: the Docling images are offline for measuring, and a
    # fetch is the one step that must reach the Hub.
    command = ["docker", "run", "--rm", "-v", f"{ROOT}:/bench", "-v", f"{work.resolve()}:/work",
               "-e", "HF_HUB_OFFLINE=0"]
    if os.environ.get("FINTABNET_ARCHIVE_URL"):
        command += ["-e", "FINTABNET_ARCHIVE_URL"]
    return command + [built["tag"], "/opt/bench/bin/python", "-m", "bench", "datasets", "fetch",
                      "--sets", sets, "--work", "/work", "--profile", profile,
                      *(["--limit", str(limit)] if limit else [])]


def cmd_datasets(args: argparse.Namespace) -> int:
    sets = configs.resolve_sets(args.sets)
    if args.action == "plan":
        rows = datasets.plan([s for s in sets if s != "fixtures"])
        total = 0
        for row in rows:
            print(f"{row['set']}: {row['repo']}@{row['revision'][:12]} -- {row['note']}")
            for name, size in row["files"].items():
                print(f"    {name}: {size if size is not None else '?'} bytes")
            total += row["bytes"]
        print(f"whole files listed: {total} bytes; {datasets.env_note()}")
        return 0
    work = pathlib.Path(args.work)
    if args.in_image:
        command = fetch_command(args.in_image, args.sets, work, args.profile, args.limit)
        return subprocess.run(command, check=False).returncode
    for set_name in sets:
        print(json.dumps(datasets.fetch(set_name, work, args.limit, args.profile)))
    return 0


def cmd_cost(args: argparse.Namespace) -> int:
    machine = configs.machine(args.machine)
    kind = "gpu" if machine.get("gpu") else "cpu"
    model = cost.round_hours(configs.resolve(args.configs), configs.resolve_sets(args.sets),
                             [s for s in args.splits.split(",") if s], args.profile, kind, args.reps,
                             speed=not args.no_speed, quality=not args.no_quality)
    for line in model["lines"]:
        print(f"  {line}")
    print(f"modelled round: {model['hours']} h on {args.machine}")
    hours = args.hours if args.hours else max(1.0, round(model["hours"] * 1.25 + 0.49))
    ok, estimate = cost.guard(args.machine, hours, args.budget)
    print(f"ceiling: EUR {estimate['eur']} ({estimate.get('basis', estimate.get('why'))}); "
          f"spot at today's price: EUR {estimate.get('spot_eur', 0.0)}; budget EUR {args.budget}")
    print(f"max_hours={hours}")
    if not ok:
        print(f"[cost] REFUSED: the ceiling EUR {estimate['eur']} is over BUDGET_EUR "
              f"{args.budget}", file=sys.stderr)
        return 1
    return 0


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="bench", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="quality and speed for a matrix, then the report")
    run.add_argument("--machine", default="local")
    run.add_argument("--configs", default="all")
    run.add_argument("--sets", default="all")
    run.add_argument("--splits", default="tuning")
    run.add_argument("--profile", default="full")
    run.add_argument("--reps", type=int, default=3)
    run.add_argument("--runner", choices=("native", "docker"), default="native")
    run.add_argument("--work", default=str(ROOT / "work"))
    run.add_argument("--out", default=None)
    run.add_argument("--run-id", default=None)
    run.add_argument("--because", default="", help="why the holdout is measured; required for it")
    run.add_argument("--optional", default="", help="configs that may be unavailable here")
    run.add_argument("--no-speed", action="store_true")
    run.add_argument("--no-quality", action="store_true")
    run.set_defaults(func=cmd_run)

    one = sub.add_parser("quality-one")
    one.add_argument("--config", required=True)
    one.add_argument("--set", required=True)
    one.add_argument("--split", required=True)
    one.add_argument("--size", type=int, required=True)
    one.add_argument("--work", required=True)
    one.add_argument("--out", required=True)
    one.add_argument("--run-id", required=True)
    one.add_argument("--because", default="")
    one.set_defaults(func=cmd_quality_one)

    sp = sub.add_parser("speed-one")
    sp.add_argument("--config", required=True)
    sp.add_argument("--sets", required=True)
    sp.add_argument("--profile", default="full")
    sp.add_argument("--reps", type=int, default=3)
    sp.add_argument("--work", required=True)
    sp.add_argument("--out", required=True)
    sp.add_argument("--run-id", required=True)
    sp.add_argument("--machine-kind", choices=("cpu", "gpu"), default="cpu")
    sp.set_defaults(func=cmd_speed_one)

    rep = sub.add_parser("report", help="the overview table from run directories")
    rep.add_argument("runs", nargs="+")
    rep.add_argument("--out", default=None)
    rep.set_defaults(func=cmd_report)

    cmp_ = sub.add_parser("compare", help="paired per-document comparisons with 95% intervals")
    cmp_.add_argument("runs", nargs="+")
    cmp_.add_argument("--out", default=None)
    cmp_.set_defaults(func=cmd_compare)

    val = sub.add_parser("validate", help="check run directories against schema/*.json")
    val.add_argument("runs", nargs="+")
    val.set_defaults(func=lambda args: validate.main(args.runs))

    ds = sub.add_parser("datasets")
    ds.add_argument("action", choices=("plan", "fetch"))
    ds.add_argument("--sets", default="all")
    ds.add_argument("--work", default=str(ROOT / "work"))
    ds.add_argument("--limit", type=int, default=None)
    ds.add_argument("--profile", default="full")
    ds.add_argument("--in-image", default=None, help="run the fetch inside this built image")
    ds.set_defaults(func=cmd_datasets)

    co = sub.add_parser("cost", help="the modelled round time and the cost guard")
    co.add_argument("--machine", required=True)
    co.add_argument("--configs", default="all")
    co.add_argument("--sets", default="all")
    co.add_argument("--splits", default="tuning,holdout")
    co.add_argument("--profile", default="full")
    co.add_argument("--reps", type=int, default=3)
    co.add_argument("--hours", type=float, default=None, help="max VM lifetime; default: model x 1.25")
    co.add_argument("--budget", type=float, default=float(os.environ.get("BUDGET_EUR", "20")))
    co.add_argument("--no-speed", action="store_true", help="the round measures no speed")
    co.add_argument("--no-quality", action="store_true", help="the round measures no quality")
    co.set_defaults(func=cmd_cost)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    return args.func(args)
