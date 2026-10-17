#!/usr/bin/env python3
# Copyright (c) 2026 Innovation Trigger B.V.
#
# PDFluent is available under two licences, at your option: the GNU AGPLv3, or
# the PDFluent Commercial Licence. See the LICENSE file in this repository --
# that file travels with the copy you received, which a URL does not.
"""ParseBench as the third set of a round: every column, ParseBench's own scorer (#623).

    parsebench_round.py --work /opt/structured/parsebench --tools all --pages 40 --fetch --measure
    parsebench_round.py --self-test

DocLayNet and PubTables are scored by our axes on blocks; ParseBench cannot be,
because its ground truth is not blocks but RULES -- tables as HTML, charts as
data points, text as missing/extra sentences and pairwise order, formatting as
style tests (DATASETS.toml). So this measures it the only way that makes the
numbers ParseBench numbers: each column's Markdown through ParseBench's own
evaluator, untouched (`parsebench_harness.py`). What is ours is the
configuration, and it is the one the other two sets use: the same register,
the same adapters, the batch per tool (`structured_bench.measure_batch`), and
OCR off wherever a tool lets it be switched off. Where it does not -- Unstructured's
hi_res runs Tesseract inside its own pipeline -- the run file and the table carry
the register's `ocr` beside the column (codex, #1975), because on ParseBench's
scanned and OCR-leaning pages that difference is most of the score.

WHAT IS MEASURED AND WHAT IS NOT
================================
Three of its four PDF categories: `table`, `chart` and `text` (which ParseBench
scores twice, as `text_content` and `text_formatting`). NOT `layout`: ParseBench
scores it from boxes in a LayoutOutput, and every column here hands it Markdown,
so each would score nothing for a reason that is ours. The run file says so
under `not_measured` rather than leaving a zero in a table.

The headline per category is ParseBench's own default (`_DEFAULT_METRICS` in
parse_bench 1.0.4's aggregation report): GriTS+TRM for tables,
content_faithfulness for text content, semantic_formatting for formatting, and
the rule pass rate for charts, which has no default of its own. Every other
average rides along in the run file.

READING THE NUMBERS
===================
ParseBench numbers are survivor-only means from `_evaluation_report.json`;
compare runs with `pb_summary.py pair`; never join the CSV by row. The CSV's
row order varies per run -- the scorer drains a set of futures -- and its
missing cells are empty strings, not zeros. The headline here is
`pb_summary.py`'s mean, so the rig and every measurement job report the
same number; the run file says the convention beside the scorer.

THE SEALED FIFTH IS NOT READ
============================
Pages come from the tuning half of corpus/structured/PARSEBENCH.json, a seeded
sample per category by whole documents (`structured_bench.sample`, the split's
own seed). A page is fetched at the pinned revision and checked against its
SHA-256 in the manifest before any tool sees it; a mismatch ends the round,
because a page that is not the one sealed is a page nobody can compare.

NOTHING OF A DOCUMENT LEAVES THE MACHINE
========================================
The verdict in DATASETS.toml clears measuring on three conditions, and two of
them are enforced here: the run file carries scores and file names and no text
(`evaluation_of` keeps metric names and values only), and the Markdown each
column wrote stays under `--work`, which the remote round does not ship -- only
`text/` directories travel, and this writes none.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "ci" / "loop"))
sys.path.insert(0, str(REPO / "scripts" / "ci"))
sys.path.insert(0, str(REPO / "scripts" / "extraction"))

import pb_summary  # noqa: E402
import structured_bench as sb  # noqa: E402
import structured_holdout_rule as holdout_rule  # noqa: E402

SET = "parsebench"
PREFIX = "parsebench"
HARNESS = REPO / "scripts" / "extraction" / "parsebench_harness.py"
PARSEBENCH_PY = os.environ.get("PARSEBENCH_PYTHON", "/opt/ex/parsebench/bin/python")

# Manifest category -> the JSONL files ParseBench scores it with.
CATEGORIES = {
    "table": ("table",),
    "chart": ("chart",),
    "text": ("text_content", "text_formatting"),
}
NOT_MEASURED = {
    "layout": ("ParseBench scores layout from boxes in its LayoutOutput; every column here "
               "hands it Markdown, so each would score nothing for a reason that is ours"),
}
# parse_bench 1.0.4, analysis/aggregation_report.py `_DEFAULT_METRICS`; chart has
# no entry there and falls back to `rule_pass_rate`, as its report does.
HEADLINE = {
    "table": "grits_trm_composite",
    "chart": "rule_pass_rate",
    "text_content": "content_faithfulness",
    "text_formatting": "semantic_formatting",
}
GROUPS = tuple(group for groups in CATEGORIES.values() for group in groups)
# What a per-page metric name may look like to be kept: an identifier.
METRIC_NAME = re.compile(r"[a-z][a-z0-9_]{0,79}")
# The shape of what a cache entry records. Bumped when a cached row would lack
# something a round now reads, so the entry is a miss rather than a hole.
# 3 on 28-09-2026: rows of 2 carry no per-page numbers (the `name` field).
# 4 on 04-10-2026: rows of 3 carry no coverage or guard record (#574).
RECORD = 4


def choose(manifest: dict, per_category: int, seed: str,
           split: str = "tuning") -> list[dict]:  # pdfluent-bench: split
    """A seeded sample of the TUNING half per measured category, whole documents."""
    chosen: list[dict] = []
    for category in CATEGORIES:
        half = [p for p in manifest["pages"]
                if p["split"] == split and p["category"] == category]
        chosen += sb.sample(half, per_category, seed) if per_category else half
    return chosen


def sha256_of(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def check_pages(pages: list[dict], data: pathlib.Path) -> None:
    """Every page is the one the manifest sealed, or the round stops here."""
    wrong = [p["name"] for p in pages
             if not (data / p["name"]).is_file() or sha256_of(data / p["name"]) != p["sha256"]]
    if wrong:
        raise SystemExit(f"[{PREFIX}] FATAL: {len(wrong)} page(s) are not what "
                         f"{holdout_rule.manifest_for(SET).relative_to(REPO)} pins, e.g. "
                         f"{wrong[0]}. A page that is not the one sealed cannot be compared.")


def fetch(entry: dict, data: pathlib.Path, pages: list[dict]) -> None:
    """The rule files and the chosen pages, at the pinned revision, to this machine only."""
    from huggingface_hub import hf_hub_download

    for name in [f"{group}.jsonl" for group in GROUPS] + [p["name"] for p in pages]:
        if (data / name).is_file():
            continue
        hf_hub_download(repo_id=entry["repo_id"], filename=name, repo_type="dataset",
                        revision=entry["revision"], local_dir=str(data))


def build_cases(data: pathlib.Path, cases: pathlib.Path, pages: list[dict]) -> dict:
    """ParseBench's directory layout, holding only the chosen pages and their rules.

    Returns rules per group, so the run file says how many tests each score is over.
    """
    if cases.exists():
        shutil.rmtree(cases)
    wanted = {p["name"] for p in pages}
    counts: dict[str, int] = {}
    for page in pages:
        target = cases / page["name"]
        target.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(data / page["name"], target)
    for group in GROUPS:
        kept = []
        for line in (data / f"{group}.jsonl").read_text(encoding="utf-8").splitlines():
            if line.strip() and json.loads(line).get("pdf") in wanted:
                kept.append(line)
        (cases / f"{group}.jsonl").write_text("\n".join(kept) + ("\n" if kept else ""))
        counts[group] = len(kept)
    return counts


def write_markdown(tool: str, pages: list[dict], binaries: dict, md: pathlib.Path,
                   scratch: pathlib.Path) -> dict:
    """Each column's Markdown per page, at `<md>/<page name>.md`, as the other sets run it."""
    answered = 0
    for category in CATEGORIES:
        mine = [p for p in pages if p["category"] == category]
        if tool in sb.BATCHED:
            # One batch PER CATEGORY: a batch names its outputs by file name, and
            # one page sits in two categories under one name.
            produced, _ms = sb.measure_batch(tool, mine, binaries, scratch / category)
        else:
            produced = {}
            for page in mine:
                markdown, _ms = sb.markdown_of(tool, {}, pathlib.Path(page["path"]), binaries)
                if markdown is not None:
                    produced[page["name"]] = markdown
        for name, markdown in produced.items():
            out = md / f"{name}.md"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(markdown)
            answered += 1
    return {"answered": answered, "asked": len(pages)}


def evaluation_of(report: dict) -> dict:
    """Scores, and nothing of a page: metric names and numbers, test ids, no text.

    The means are `pb_summary`'s -- the same function the measurement jobs
    summarise with -- so the rig's headline and theirs cannot drift apart
    again. `coverage` carries the denominator beside them.
    """
    summary = pb_summary.summarize_report(report)
    metrics = {name: row["mean"] for name, row in summary["metrics"].items()
               if isinstance(row["mean"], (int, float))}
    coverage = {"total": summary["n_total"],
                "scored": {name: row["n_scored"] for name, row in summary["metrics"].items()}}
    per_page = {}
    for row in report.get("per_example_results") or []:
        per_page[row.get("test_id", "")] = {
            "success": bool(row.get("success")),
            # EVERY NUMBER PER PAGE, BY METRIC NAME (#623, COS 27-09): the
            # clustering of a category needs which rules a page failed, not
            # only its headline. Names only when they are identifiers --
            # `rule_missing_sentence_percent_pass_rate` -- so a name that
            # carried a rule's own text could never ride along.
            # `metric_name` is parse_bench 1.0.4's field (schemas/evaluation.py
            # MetricValue); reading `name` kept nothing but `success` in the
            # joint round of 28-09.
            **{str(m.get("metric_name") or m.get("name")): m["value"]
               for m in row.get("metrics") or []
               if isinstance(m, dict) and isinstance(m.get("value"), (int, float))
               and METRIC_NAME.fullmatch(str(m.get("metric_name") or m.get("name") or ""))},
        }
    return {"examples": report.get("total_examples"), "successful": report.get("successful"),
            "metrics": metrics, "coverage": coverage, "per_page": per_page}


def refusals_of(tool: str, scans: dict[str, dict], stall_ids: list[str]) -> list[dict]:
    """One refusal per bad document: the tool, the test id, and the reason.

    Like the FinTabNet transient rule: a timeout or a failed evaluation fails
    the run loudly and never prints as a number, because a genuine failure is
    zero-padded into the scorer's own average and would move the headline.
    """
    out = []
    for group in sorted(scans):
        scan = scans[group]
        for test_id in scan["rule_timeout_docs"]:
            out.append({"tool": tool, "group": group, "test_id": test_id, "reason": "rule_timeout"})
        for test_id in scan["over_budget_docs"]:
            out.append({"tool": tool, "group": group, "test_id": test_id, "reason": "over_budget"})
        for test_id in scan["normalize_docs"]:
            out.append({"tool": tool, "group": group, "test_id": test_id,
                        "reason": "normalize_timeout"})
        for test_id in scan["failed_docs"]:
            out.append({"tool": tool, "group": group, "test_id": test_id,
                        "reason": "evaluation_failed"})
    for test_id in stall_ids:
        out.append({"tool": tool, "group": "", "test_id": test_id, "reason": "worker_timeout"})
    return sorted(out, key=lambda r: (r["test_id"], r["reason"], r["group"]))


def score(tool: str, md: pathlib.Path, cases: pathlib.Path,
          out: pathlib.Path) -> tuple[dict, dict, list]:
    """ParseBench's evaluator over one column: its groups, guards, refusals."""
    if out.exists():
        shutil.rmtree(out)
    # PYTHONHASHSEED=0 fixes the scorer's two hash-seed-dependent orders by
    # construction: the set[str] its normalised category scores iterate, and
    # the set of futures its row order drains from. Score-neutral (m241).
    done = subprocess.run([PARSEBENCH_PY, str(HARNESS), "--tool", tool, "--markdown-dir",
                           str(md), "--input-dir", str(cases), "--output-dir", str(out)],
                          capture_output=True, text=True, check=False,
                          env={**os.environ, "PYTHONHASHSEED": "0"})
    if done.returncode != 0:
        print(done.stdout[-2000:], done.stderr[-2000:], sep="\n", file=sys.stderr)
    log = (done.stdout or "") + "\n" + (done.stderr or "")
    groups: dict = {}
    scans: dict[str, dict] = {}
    for group in GROUPS:
        report = out / f"harness_{tool}" / group / "_evaluation_report.json"
        if not report.is_file():
            groups[group] = {"missing": f"exit {done.returncode}"}
            continue
        raw = json.loads(report.read_text())
        groups[group] = evaluation_of(raw)
        scans[group] = pb_summary.scan_guards(raw)
    stalls = pb_summary.stall_ids_in(log)
    guards = {
        "rule_timeouts": sum(s["rule_timeouts"] for s in scans.values()),
        "skipped_over_budget": sum(s["skipped_over_budget"] for s in scans.values()),
        "normalize_timeouts": sum(s["normalize_timeouts"] for s in scans.values()),
        "failed_docs": sorted({t for s in scans.values() for t in s["failed_docs"]} | set(stalls)),
        "skipped": sum(s["skipped"] for s in scans.values()),
    }
    return groups, guards, refusals_of(tool, scans, stalls)


def cache_file(key: dict) -> pathlib.Path:
    tag = hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()[:16]
    return sb.COMPETITORS / SET / f"{key['tool']}-{tag}.json"


def cache_load(key: dict) -> dict | None:
    path = cache_file(key)
    if not path.is_file():
        return None
    stored = json.loads(path.read_text())
    return stored if stored.get("key") == key else None


def cache_save(key: dict, row: dict) -> pathlib.Path:
    """Scores and page names only -- `evaluation_of` already holds no text."""
    path = cache_file(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"key": key, "row": row}, indent=1, sort_keys=True) + "\n")
    return path


def headline_of(group: str, evaluation: dict) -> float | None:
    value = evaluation.get("metrics", {}).get(HEADLINE[group])
    return round(value, 4) if isinstance(value, (int, float)) else None


def table_of(run: dict) -> str:
    lines = [f"### {run['run_id']}", "",
             f"ParseBench at {run['revision'][:8]} (tuning half, {run['pages_measured']} pages: "
             + ", ".join(f"{c} {n}" for c, n in run["pages_per_category"].items())
             + "), scored by parse_bench's own evaluator; headline per category is its default "
               "metric. Layout not measured: " + NOT_MEASURED["layout"] + ".", "",
             "| tool | " + " | ".join(f"{g} ({HEADLINE[g]})" for g in GROUPS)
             + " | pages answered | OCR |",
             "|---|" + "---|" * (len(GROUPS) + 2)]
    for tool in run["tools"].values():
        cells = []
        for group in GROUPS:
            value = headline_of(group, tool["groups"].get(group, {}))
            cells.append("n/a" if value is None else f"{value:.3f}")
        lines.append(f"| {tool['name']} | " + " | ".join(cells)
                     + f" | {tool['markdown']['answered']}/{tool['markdown']['asked']} | "
                     + f"{(tool.get('ocr') or '—').split(':')[0]} |")
    for refusal in run["refused"]:
        lines.append(f"| {refusal['tool']} | not run: {refusal['why']} |")
    scores = {key: {group: headline_of(group, tool["groups"].get(group, {})) for group in GROUPS}
              for key, tool in run["tools"].items()}
    lines += sb.reference_lines("Against the references:",
                                [(group, f"{group} ({HEADLINE[group]})") for group in GROUPS],
                                scores, {key: tool["name"] for key, tool in run["tools"].items()})
    return "\n".join(lines) + "\n"


def measure(args: argparse.Namespace) -> int:
    entry = sb.dataset_entry(SET)  # refuses a set that is not on the rig
    manifest = json.loads(holdout_rule.manifest_for(SET).read_text())
    if manifest.get("revision") != entry.get("revision"):
        raise SystemExit(f"[{PREFIX}] FATAL: the manifest pins {manifest.get('revision')} and "
                         f"the register {entry.get('revision')}; one of them moved.")
    sb.check_budget(args.anyway, SET)
    work = pathlib.Path(args.work)
    data, cases = work / "data", work / "cases"
    pages = choose(manifest, args.pages, holdout_rule.seed_for(SET),
                   args.split)  # pdfluent-bench: split
    if args.fetch:
        fetch(entry, data, pages)
    check_pages(pages, data)
    for page in pages:
        page["path"] = str(cases / page["name"])
    rules = build_cases(data, cases, pages)

    asked = [t.strip() for t in args.tools.split(",") if t.strip()]
    if asked == ["all"]:
        register, _meta = sb.load_register(sb.EXTRACTORS, "extractors")
        asked = [key for key, row in register.items() if "on_the_structured_rig" in row]
    allowed, refused = sb.tools_allowed(asked)
    os.environ.setdefault("DO_NOT_TRACK", "1")
    os.environ.setdefault("SCARF_NO_ANALYTICS", "true")
    binaries = {
        sb.OURS: os.environ.get("PDFLUENT_STRUCTURED_BIN")
        or str(REPO / "target" / "release" / "examples" / "structured_markdown"),
        **{tool: os.environ.get(f"{tool.upper()}_PYTHON", f"/opt/ex/{tool}/bin/python")
           for tool in sb.ADAPTED},
    }
    tools = {}
    guard_refusals: list[dict] = []
    scorer = scorer_version()
    # The rules the scores were computed against, by content (codex, #1982).
    rules_digest = hashlib.sha256(b"".join(
        (cases / f"{group}.jsonl").read_bytes() for group in GROUPS)).hexdigest()
    for tool, tool_entry in allowed.items():
        # COMPETITORS ONCE (owner, 24-09-2026), and on this set as SCORES only:
        # the verdict forbids keeping any page's output, so a changed scorer is a
        # cache miss and the tool runs again (`cache_key` names the scorer).
        key = None if tool == sb.OURS else {
            "set": SET, "tool": tool, "version_pin": str(tool_entry.get("version_pin") or ""),
            "pages_digest": sb.pages_digest(pages), "adapter": sb.adapter_digest(),
            "scorer": scorer, "revision": entry["revision"], "rules": rules_digest,
            "record": RECORD}
        stored = (cache_load(key) if key and os.environ.get("BENCH_COMPETITOR_CACHE")
                  else None)  # pdfluent-bench: cache is opt-in
        if stored is not None:
            tools[tool] = dict(stored["row"], from_cache=str(cache_file(key).relative_to(REPO)))
            continue
        print(f"[{PREFIX}] {tool}: {len(pages)} page(s)", flush=True)
        md = work / "md" / tool
        if md.exists():
            shutil.rmtree(md)
        written = write_markdown(tool, pages, binaries, md, work / "batch" / tool)
        groups, guards, refusals = score(tool, md, cases, work / "out" / tool)
        guard_refusals += refusals
        tools[tool] = {"name": tool_entry.get("name", tool),
                       "version": sb.tool_version(tool, tool_entry, binaries),
                       "publishable": bool(tool_entry.get("publishable")),
                       "ocr": tool_entry.get("ocr"),
                       "markdown": written,
                       "groups": groups,
                       "guards": guards}
        # A refused column is not cached: its scores carry a timeout or a
        # failed document, and the next round must measure it again.
        if (key is not None and not refusals and os.environ.get("BENCH_COMPETITOR_CACHE")
                and all(g.get("successful") for g in tools[tool]["groups"].values())):
            tools[tool]["cached_to"] = str(cache_save(key, tools[tool]).relative_to(REPO))
    # A SCORER THAT READ NOTHING IS NOT A ZERO. A column that wrote Markdown
    # and got no evaluated example back failed in the harness, not on the page,
    # and a table printing `n/a` for it would read as the tool's verdict.
    broken = sorted(f"{tool}/{group}" for tool, row in tools.items()
                    if row["markdown"]["answered"]
                    for group, evaluation in row["groups"].items()
                    if not evaluation.get("successful"))
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run = {
        "run_id": f"structured-{SET}-{stamp}", "set": SET, "set_version": entry.get("version"),
        "set_licence": entry.get("licence"), "revision": entry["revision"],
        "half": args.split, "harness_revision": sb.harness_revision(),  # pdfluent-bench:
        **sb.line_measured_on(), "machine": sb.machine(),
        "scorer": {"package": "parse-bench", "version": scorer,
                   "headline": HEADLINE, "convention": pb_summary.CONVENTION},
        "pages_measured": len(pages),
        "pages_per_category": {c: sum(p["category"] == c for p in pages) for c in CATEGORIES},
        "rules_per_group": rules, "pages_digest": sb.pages_digest(pages),
        "sample": {"per_category": args.pages, "seed": holdout_rule.seed_for(SET),
                   "rule": "whole documents per category (structured_bench.sample)"},
        "not_measured": NOT_MEASURED,
        "conditions": "DATASETS.toml [datasets.parsebench]: scores and file names only; "
                      "every column's output stayed on the machine",
        "tools": tools, "refused": refused, "scorer_failed": broken,
        "guard_refusals": guard_refusals,
    }
    sb.RUNS.mkdir(parents=True, exist_ok=True)
    path = sb.RUNS / f"{run['run_id']}.json"
    path.write_text(json.dumps(run, indent=1) + "\n")
    (sb.RUNS / f"{run['run_id']}.md").write_text(table_of(run))
    print(table_of(run))
    print(f"[{PREFIX}] run file {path.relative_to(REPO)}")
    if broken:
        print(f"[{PREFIX}] FAIL: the scorer evaluated nothing for {', '.join(broken)}",
              file=sys.stderr)
        return 1
    if guard_refusals:
        for refusal in guard_refusals:
            print(f"[{PREFIX}] FAIL: {refusal['tool']}/{refusal['test_id']}: "
                  f"{refusal['reason']}", file=sys.stderr)
        print(f"[{PREFIX}] FAIL: {len(guard_refusals)} document(s) timed out or failed; "
              f"the run is refused, like a transient fetch", file=sys.stderr)
        return 1
    return 0


def scorer_version() -> str:
    done = subprocess.run([PARSEBENCH_PY, "-c", "from importlib.metadata import version; "
                           "print(version('parse-bench'))"],
                          capture_output=True, text=True, check=False)
    return done.stdout.strip() or "unknown"


def self_test() -> int:
    """What can be wrong without a machine: the sample, the check, the cases, the run file."""
    failures: list[str] = []
    manifest = json.loads(holdout_rule.manifest_for(SET).read_text())
    seed = holdout_rule.seed_for(SET)
    pages = choose(manifest, 30, seed)
    if any(p["split"] != "tuning" for p in pages):
        failures.append("a sealed page was chosen")
    if {p["category"] for p in pages} != set(CATEGORIES):
        failures.append("a measured category has no pages")
    if any(p["category"] in NOT_MEASURED for p in pages):
        failures.append("a category this cannot score was chosen")
    if [p["name"] for p in choose(manifest, 30, seed)] != [p["name"] for p in pages]:
        failures.append("the sample is not seeded")
    if not all(sum(p["category"] == c for p in pages) <= 30 for c in CATEGORIES):
        failures.append("a category took more pages than it asked for")

    with tempfile.TemporaryDirectory() as tmp:
        data = pathlib.Path(tmp) / "data"
        few = [dict(p) for p in pages[:2]]
        for i, page in enumerate(few):
            (data / page["name"]).parent.mkdir(parents=True, exist_ok=True)
            (data / page["name"]).write_bytes(f"page {i}".encode())
            page["sha256"] = sha256_of(data / page["name"])
        try:
            check_pages(few, data)
        except SystemExit:
            failures.append("a page that matches its pin was refused")
        (data / few[0]["name"]).write_bytes(b"another page")
        try:
            check_pages(few, data)
            failures.append("a page that is not the sealed one was accepted")
        except SystemExit:
            pass
        (data / few[0]["name"]).write_bytes(b"page 0")
        for group in GROUPS:
            rows = [{"pdf": p["name"], "id": f"{group}-{i}"} for i, p in enumerate(few)]
            rows.append({"pdf": "docs/table/not-chosen.pdf", "id": f"{group}-x"})
            (data / f"{group}.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
        cases = pathlib.Path(tmp) / "cases"
        counts = build_cases(data, cases, few)
        if any(n != len(few) for n in counts.values()):
            failures.append(f"the cases carry rules for pages not chosen: {counts}")
        if not all((cases / p["name"]).resolve() == (data / p["name"]).resolve() for p in few):
            failures.append("a case is not the fetched page")

    kept = evaluation_of({"per_example_results": [{"test_id": "text/p", "success": True, "metrics": [
        {"metric_name": "rule_unexpected_word_percent_pass_rate", "value": 0.4},
        {"metric_name": "Contains 'Net income rose'", "value": 1.0}]}]})["per_page"]["text/p"]
    if kept.get("rule_unexpected_word_percent_pass_rate") != 0.4:
        failures.append("a page's rule metric is not kept for clustering")
    if any("Net income" in k for k in kept):
        failures.append("a metric named by page text travelled into the run file")
    report = {"total_examples": 2, "successful": 1,
              "aggregate_metrics": {"avg_grits_trm_composite": 0.5, "min_grits_trm_composite": 0.1},
              "per_example_results": [
                  {"test_id": "table/p1", "success": True,
                   "metrics": [{"metric_name": "grits_trm_composite", "value": 0.5,
                                "details": {"expected": "a sentence from the page"}},
                               {"metric_name": "diff_text", "value": "a sentence from the page"}]}]}
    evaluation = evaluation_of(report)
    if "a sentence from the page" in json.dumps(evaluation):
        failures.append("page text travels in the run file")
    if headline_of("table", evaluation) != 0.5:
        failures.append("the headline is not ParseBench's own default metric")
    if "min_grits_trm_composite" in evaluation["metrics"]:
        failures.append("the run file keeps what is not an average")

    # The headline is pb_summary's mean on the same fixture (#574). With an
    # empty document the survivor-only mean and the zero-filled mean differ,
    # and with no aggregate block at all only the shared function answers --
    # reading the block directly goes quiet there.
    mixed = {"total_examples": 3, "successful": 2,
             "aggregate_metrics": {"avg_grits_trm_composite": 0.5},
             "per_example_results": [
                 {"test_id": "table/p1", "success": True,
                  "metrics": [{"metric_name": "grits_trm_composite", "value": 0.4}]},
                 {"test_id": "table/p2", "success": True,
                  "metrics": [{"metric_name": "grits_trm_composite", "value": 0.6}]},
                 {"test_id": "table/empty", "success": False,
                  "error": "Evaluation error: boom", "metrics": []}]}
    shared = pb_summary.summarize_report(mixed)["metrics"]["grits_trm_composite"]
    if headline_of("table", evaluation_of(mixed)) != round(shared["mean"], 4):
        failures.append("the round's headline is not pb_summary's mean")
    if evaluation_of(mixed)["coverage"] != {"total": 3, "scored": {"grits_trm_composite": 2}}:
        failures.append("the run file does not carry the headline's denominator")
    if headline_of("table", evaluation_of(mixed)) == round(shared["mean_zero_filled"], 4):
        failures.append("the headline is the zero-filled mean, not the survivor-only one")
    bare = dict(mixed, aggregate_metrics={})
    if headline_of("table", evaluation_of(bare)) != 0.5:
        failures.append("the headline without an aggregate block is not the shared recomputation")

    # A timed-out rule refuses the run and names the document (#574); a
    # stalled worker, which never reaches the JSON, is refused from the logs.
    bad = {"total_examples": 2, "successful": 2, "failed": 0, "skipped": 0,
           "aggregate_metrics": {"avg_rule_pass_rate": 0.5},
           "per_example_results": [
               {"test_id": "chart/timed", "success": True,
                "metrics": [{"metric_name": "rule_pass_rate", "value": 0.5,
                             "metadata": {"skipped_over_budget": 0, "rule_results": [
                                 {"explanation": "Rule timed out after 120s",
                                  "passed": False}]}}]},
               {"test_id": "chart/ok", "success": True,
                "metrics": [{"metric_name": "rule_pass_rate", "value": 0.5,
                             "metadata": {"skipped_over_budget": 0, "rule_results": []}}]}]}
    refused = refusals_of("t", {"chart": pb_summary.scan_guards(bad)}, [])
    if ([r["test_id"] for r in refused] != ["chart/timed"]
            or refused[0]["reason"] != "rule_timeout"):
        failures.append(f"the timed-out rule is not refused by name: {refused}")
    stalled = refusals_of("t", {"table": pb_summary.scan_guards(report)}, ["chart/gone"])
    if ([r["test_id"] for r in stalled] != ["chart/gone"]
            or stalled[0]["reason"] != "worker_timeout"):
        failures.append(f"a stalled worker's document is not refused by name: {stalled}")
    if refusals_of("t", {"table": pb_summary.scan_guards(report)}, []):
        failures.append("a healthy report refuses")

    run = {"run_id": "structured-parsebench-x", "revision": "2805a1d9" * 5,
           "pages_measured": 2, "pages_per_category": {"table": 2},
           "tools": {"t": {"name": "T", "groups": {"table": evaluation},
                           "ocr": "ON, inside hi_res: Tesseract",
                           "markdown": {"answered": 1, "asked": 2}}},
           "refused": [{"tool": "Marker", "why": "weights licence"}]}
    table = table_of(run)
    if "| T | 0.500 | n/a | n/a | n/a | 1/2 | ON, inside hi_res |" not in table or "Marker" not in table:
        failures.append("the table does not say what was measured, with OCR on which "
                        "column, and what was refused")

    real = sb.COMPETITORS
    with tempfile.TemporaryDirectory() as tmp:
        sb.COMPETITORS = pathlib.Path(tmp)
        try:
            key = {"set": SET, "tool": "t", "version_pin": "1", "pages_digest": "d",
                   "adapter": "a", "scorer": "1.0.4"}
            cache_save(key, {"name": "T", "groups": {"table": evaluation}})
            back = cache_load(key)
            if not back or back["row"]["groups"]["table"] != evaluation:
                failures.append("a competitor's scores do not come back from the cache")
            if cache_load(dict(key, scorer="1.0.5")) is not None:
                failures.append("a new scorer read the old scorer's cached scores")
        finally:
            sb.COMPETITORS = real

    for problem in failures:
        print(f"FAIL {problem}")
    if failures:
        return 1
    print("OK parsebench round: tuning only and seeded, pages checked against their pins, "
          "rules only for chosen pages, no page text in the run file, ParseBench's headline "
          "through pb_summary, a timed-out document refused by name")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--work", default="/opt/structured/parsebench")
    ap.add_argument("--tools", default="all")
    ap.add_argument("--pages", type=int, default=40, help="about this many pages per category")
    ap.add_argument("--fetch", action="store_true")
    ap.add_argument("--measure", action="store_true")
    ap.add_argument("--anyway", action="store_true",
                    help="past the one-run-per-head rule, with the reason on the ticket")
    ap.add_argument("--split", choices=("tuning", "holdout"), default="tuning",
                    help="pdfluent-bench: which half of the sealed manifest")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        return self_test()
    if not args.measure:
        ap.error("nothing to do: --measure (with --fetch on a fresh machine)")
    return measure(args)


if __name__ == "__main__":
    sys.exit(main())
