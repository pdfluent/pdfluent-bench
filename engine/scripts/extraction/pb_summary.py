#!/usr/bin/env python3
# Copyright (c) 2026 Innovation Trigger B.V.
#
# PDFluent is available under two licences, at your option: the GNU AGPLv3, or
# the PDFluent Commercial Licence. See the LICENSE file in this repository --
# that file travels with the copy you received, which a URL does not.
"""One canonical ParseBench summary, and the paired comparison (#574).

WHY ONE. The parse_bench 1.0.4 scorer is deterministic, but two jobs summarised
identical scorer output two ways: one averaged the CSV with missing cells as 0
over n=100/96, the other used the JSON's survivor-only `aggregate_metrics`.
That differs by up to 0.077 on styling-class metrics. Every measurement job
writes its own summariser, so this one is the summariser: the rig's structured
round (`parsebench_round.py`) headlines through this same function, and every
ParseBench comparison is `pb_summary.py pair`.

ParseBench numbers are survivor-only means from `_evaluation_report.json`;
compare runs with `pb_summary.py pair`; never join the CSV by row. The CSV's
row order varies per run (the scorer drains a set of futures), its columns are
the union of metrics so a missing cell is an empty string rather than a zero,
and averaging those empties as 0 is the 0.077 above. Per-document values here
are keyed by test_id, never by row order.

WHAT SURVIVOR-ONLY MEANS. The mean is the scorer's own `aggregate_metrics`
`avg_<metric>`, which parse_bench 1.0.4 computes over the successful examples:
genuine failures are zero-padded into it, but every failure this harness can
produce is an infra failure (`Evaluation error: ...`, never padded) or a row
that never appears, and the guard refuses a run with any of those before a
number is printed. So on every run this summarises, the aggregate IS the mean
over the scored documents. The zero-filled mean rides along as a second,
clearly named column for the comparison that explicitly wants it.

Usage:
    pb_summary.py <report dir> [--json]
    pb_summary.py pair <dirA> <dirB> [--json]
    pb_summary.py --self-test

A report dir is a directory holding `_evaluation_report.json`, or one holding
a subdirectory per group each with that file (the scorer's own layout:
`<pipeline>/<group>/_evaluation_report.json`). A bare path to the JSON file
is accepted too.
"""
from __future__ import annotations

import argparse
import csv
import json
import pathlib
import random
import re
import sys
import tempfile

# Said in the run file beside the headline, so a reader knows the denominator.
CONVENTION = ("survivor-only means from _evaluation_report.json aggregate_metrics "
              "(n_scored/n_total per metric)")
REPORT = "_evaluation_report.json"

# The paired bootstrap: 2000 resampled means over the paired per-document
# diffs, seed 0; a document counts up/down past +-0.005, inside is tied.
BOOTSTRAP_SEED = 0
BOOTSTRAP_DRAWS = 2000
PAIR_DELTA = 0.005

# Guard markers, parse_bench 1.0.4's own strings: rule_based_metric.py writes
# "Rule timed out after {120}s" per rule and "Skipped: normalization ..." for
# the document whose pre-normalisation blew the same budget; runner.py counts
# a stalled worker only in its logs ("FAILED (worker timed out after ...)"),
# never as a row, and marks a legitimate skip "No layout data".
RULE_TIMEOUT_PREFIX = "Rule timed out after"
NORMALIZE_SKIP_PREFIX = "Skipped: normalization"
SKIP_MARK = "No layout data"
STALL_MARK = "worker timed out after"
_STALL_LINE = re.compile(r"\[\d+/\d+\]\s+(.*): FAILED \(worker timed out")


def _is_num(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _rows_by_id(report: dict) -> dict[str, dict]:
    rows: dict[str, dict] = {}
    for row in report.get("per_example_results") or []:
        if isinstance(row, dict) and row.get("test_id"):
            rows[str(row["test_id"])] = row
    return rows


def _metric_values(row: dict) -> dict[str, float]:
    """This row's numeric metric values, by metric name (1.0.4's field first)."""
    values: dict[str, float] = {}
    for metric in row.get("metrics") or []:
        if not isinstance(metric, dict):
            continue
        name = metric.get("metric_name") or metric.get("name")
        value = metric.get("value")
        if name and _is_num(value):
            values[str(name)] = float(value)
    return values


def summarize_report(report: dict) -> dict:
    """One group: the scorer's mean, n_scored/n_total, empty ids, zero-filled.

    `mean` is the scorer's own `avg_<metric>`; only when the aggregate block
    lacks the metric is it recomputed over the survivors (`source` says which).
    `n_scored` counts the successful rows carrying the metric, `empty_ids`
    names every other row, and `mean_zero_filled` is the sum over all rows --
    missing as 0 -- over n_total. `per_doc` keys every row's values by test_id.
    """
    by_id = _rows_by_id(report)
    total = report.get("total_examples")
    n_total = total if isinstance(total, int) and not isinstance(total, bool) else len(by_id)
    aggregate = report.get("aggregate_metrics") or {}
    per_doc = {test_id: _metric_values(row) for test_id, row in by_id.items()}
    names = sorted({name for values in per_doc.values() for name in values}
                   | {key[4:] for key in aggregate if key.startswith("avg_")})
    metrics: dict[str, dict] = {}
    for name in names:
        scored = sorted(test_id for test_id, values in per_doc.items()
                        if name in values and by_id[test_id].get("success"))
        in_scored = set(scored)
        empty_ids = sorted(test_id for test_id in by_id if test_id not in in_scored)
        average = aggregate.get(f"avg_{name}")
        if _is_num(average):
            mean, source = float(average), "aggregate"
        elif scored:
            mean = sum(per_doc[test_id][name] for test_id in scored) / len(scored)
            source = "recomputed"
        else:
            mean, source = None, "none"
        zero_sum = sum(values.get(name, 0.0) for values in per_doc.values())
        metrics[name] = {
            "mean": mean,
            "n_scored": len(scored),
            "n_total": n_total,
            "empty_ids": empty_ids,
            "mean_zero_filled": zero_sum / n_total if n_total else None,
            "source": source,
        }
    return {
        "n_total": n_total,
        "n_successful": sum(1 for row in by_id.values() if row.get("success")),
        "failed_ids": sorted(t for t, r in by_id.items() if not r.get("success")),
        "metrics": metrics,
        "per_doc": per_doc,
    }


def report_files(root: pathlib.Path) -> dict[str, pathlib.Path]:
    """Group name -> `_evaluation_report.json`, for a file, a group, or a run."""
    if root.is_file():
        return {root.parent.name: root}
    direct = root / REPORT
    if direct.is_file():
        return {root.name: direct}
    found = {path.parent.name: path for path in sorted(root.glob(f"*/{REPORT}")) if path.is_file()}
    if not found:
        raise SystemExit(f"[pb_summary] FATAL: no {REPORT} under {root}")
    return found


def summarize_dir(root: pathlib.Path) -> dict:
    """Every group under a report dir, through `summarize_report`."""
    groups = {}
    for group, path in report_files(root).items():
        groups[group] = summarize_report(json.loads(path.read_text(encoding="utf-8")))
    return {"convention": CONVENTION, "root": str(root), "groups": groups}


def _bootstrap(diffs: list[float]) -> dict | None:
    """2000 resampled means over the paired diffs (seed 0), and P(B > A)."""
    if not diffs:
        return None
    rng = random.Random(BOOTSTRAP_SEED)
    draws = sorted(sum(diffs[rng.randrange(len(diffs))] for _ in range(len(diffs))) / len(diffs)
                   for _ in range(BOOTSTRAP_DRAWS))
    return {
        "seed": BOOTSTRAP_SEED,
        "draws": BOOTSTRAP_DRAWS,
        "ci_low": draws[int(0.025 * BOOTSTRAP_DRAWS)],
        "ci_high": draws[int(0.975 * BOOTSTRAP_DRAWS) - 1],
        "p_b_better": sum(1 for mean in draws if mean > 0) / BOOTSTRAP_DRAWS,
    }


def pair_dirs(a_root: pathlib.Path, b_root: pathlib.Path) -> dict:
    """Per-document paired diffs B-A joined on test_id, plus the bootstrap."""
    rundown_a = summarize_dir(a_root)
    rundown_b = summarize_dir(b_root)
    groups: dict[str, dict] = {}
    for group in sorted(set(rundown_a["groups"]) | set(rundown_b["groups"])):
        docs_a = rundown_a["groups"].get(group, {}).get("per_doc", {})
        docs_b = rundown_b["groups"].get(group, {}).get("per_doc", {})
        names = sorted({n for v in docs_a.values() for n in v}
                       | {n for v in docs_b.values() for n in v})
        metrics: dict[str, dict] = {}
        for name in names:
            paired = sorted(t for t in docs_a if t in docs_b and name in docs_a[t] and name in docs_b[t])
            diffs = {t: docs_b[t][name] - docs_a[t][name] for t in paired}
            values = [diffs[t] for t in paired]
            up = sum(1 for v in values if v > PAIR_DELTA)
            down = sum(1 for v in values if v < -PAIR_DELTA)
            metrics[name] = {
                "mean_diff": sum(values) / len(values) if values else None,
                "n_paired": len(paired),
                "up": up,
                "down": down,
                "tied": len(paired) - up - down,
                "bootstrap": _bootstrap(values),
                "diffs": diffs,
                "only_a": sorted(t for t in docs_a if name in docs_a[t]
                                 and (t not in docs_b or name not in docs_b[t])),
                "only_b": sorted(t for t in docs_b if name in docs_b[t]
                                 and (t not in docs_a or name not in docs_a[t])),
            }
        groups[group] = {"metrics": metrics}
    return {"convention": CONVENTION, "a": str(a_root), "b": str(b_root), "groups": groups}


def scan_guards(report: dict) -> dict:
    """What would refuse this group: timeouts, over-budget, failed docs.

    From the JSON's rule results: a rule that timed out explains itself
    "Rule timed out after ...s"; a document past the 600 s rule budget carries
    `skipped_over_budget` (a normalisation timeout sets it to every rule and
    says so in `note`); a row with success false is a failed document, except
    a "No layout data" skip, which this harness never produces. Counts are
    per document first -- the per-doc maximum, so two metric entries carrying
    the same budget do not count it twice -- then summed.
    """
    by_id = _rows_by_id(report)
    rule_timeouts = 0
    rule_docs: set[str] = set()
    over_budget = 0
    over_docs: set[str] = set()
    normalise = 0
    normalise_docs: set[str] = set()
    failed: set[str] = set()
    for test_id, row in by_id.items():
        if not row.get("success") and SKIP_MARK not in str(row.get("error") or ""):
            failed.add(test_id)
        worst_over = 0
        timed = 0
        normalise_hit = False
        for metric in row.get("metrics") or []:
            metadata = metric.get("metadata") if isinstance(metric, dict) else None
            if not isinstance(metadata, dict):
                continue
            over = metadata.get("skipped_over_budget")
            if isinstance(over, int) and not isinstance(over, bool):
                worst_over = max(worst_over, over)
            if str(metadata.get("note") or "").startswith(NORMALIZE_SKIP_PREFIX):
                normalise_hit = True
            for rule in metadata.get("rule_results") or []:
                if (isinstance(rule, dict)
                        and str(rule.get("explanation") or "").startswith(RULE_TIMEOUT_PREFIX)):
                    timed += 1
        if timed:
            rule_timeouts += timed
            rule_docs.add(test_id)
        if worst_over:
            over_budget += worst_over
            over_docs.add(test_id)
        if normalise_hit:
            normalise += 1
            normalise_docs.add(test_id)
    skipped = report.get("skipped")
    return {
        "rule_timeouts": rule_timeouts,
        "rule_timeout_docs": sorted(rule_docs),
        "skipped_over_budget": over_budget,
        "over_budget_docs": sorted(over_docs),
        "normalize_timeouts": normalise,
        "normalize_docs": sorted(normalise_docs),
        "failed_docs": sorted(failed),
        "skipped": skipped if isinstance(skipped, int) and not isinstance(skipped, bool) else 0,
    }


def stall_ids_in(log_text: str) -> list[str]:
    """Documents the eval pool gave up on: they reach the logs, never the JSON."""
    return sorted({found.group(1).strip() for line in (log_text or "").splitlines()
                   if STALL_MARK in line for found in [_STALL_LINE.search(line)] if found})


def _text_summary(rundown: dict) -> str:
    lines = [f"ParseBench summary of {rundown['root']} ({rundown['convention']})"]
    for group in sorted(rundown["groups"]):
        summary = rundown["groups"][group]
        lines += ["", f"[{group}] n_total={summary['n_total']} "
                f"successful={summary['n_successful']}"]
        for name in sorted(summary["metrics"]):
            row = summary["metrics"][name]
            mean = "n/a" if row["mean"] is None else f"{row['mean']:.4f}"
            zero = "n/a" if row["mean_zero_filled"] is None else f"{row['mean_zero_filled']:.4f}"
            empty = ", ".join(row["empty_ids"]) or "none"
            lines.append(f"  {name:44} mean {mean}  n {row['n_scored']}/{row['n_total']}  "
                         f"mean_zero_filled {zero}  empty: {empty}")
    return "\n".join(lines) + "\n"


def _text_pair(paired: dict) -> str:
    lines = [f"ParseBench pair: {paired['a']} vs {paired['b']} ({paired['convention']})"]
    for group in sorted(paired["groups"]):
        lines += ["", f"[{group}]"]
        for name in sorted(paired["groups"][group]["metrics"]):
            row = paired["groups"][group]["metrics"][name]
            if row["mean_diff"] is None:
                lines.append(f"  {name:44} no paired documents")
                continue
            boot = row["bootstrap"] or {}
            lines.append(f"  {name:44} d {row['mean_diff']:+.4f}  "
                         f"95% CI [{boot.get('ci_low', float('nan')):+.4f},"
                         f"{boot.get('ci_high', float('nan')):+.4f}]  "
                         f"up {row['up']}/down {row['down']}/tied {row['tied']} "
                         f"(n={row['n_paired']})  P(B>A) {boot.get('p_b_better', float('nan')):.3f}")
            if row["only_a"]:
                lines.append(f"    only in A: {', '.join(row['only_a'])}")
            if row["only_b"]:
                lines.append(f"    only in B: {', '.join(row['only_b'])}")
    return "\n".join(lines) + "\n"


def _fixture_report(rows: list[dict], aggregate: dict | None = None) -> dict:
    report = {"total_examples": len(rows), "successful": sum(1 for r in rows if r.get("success")),
              "failed": sum(1 for r in rows if not r.get("success")), "skipped": 0,
              "aggregate_metrics": dict(aggregate or {}), "per_example_results": rows}
    if aggregate is None:
        report.pop("aggregate_metrics")
    return report


def _row(test_id: str, metrics: dict[str, float], success: bool = True,
         error: str | None = None, metadata: dict | None = None) -> dict:
    row: dict = {"test_id": test_id, "success": success,
                 "metrics": [{"metric_name": n, "value": v} for n, v in metrics.items()]}
    if error is not None:
        row["error"] = error
    if metadata is not None:
        for metric in row["metrics"]:
            metric["metadata"] = dict(metadata)
    return row


def self_test() -> int:
    """The four behaviours, each failing when its feature is removed."""
    failures: list[str] = []

    # 1. Two empty documents out of five: the mean is the survivor-only mean.
    scored = {"table/p1": 0.2, "table/p2": 0.4, "table/p3": 0.6}
    rows = ([_row(t, {"grits_trm_composite": v}) for t, v in scored.items()]
            + [_row("table/empty1", {}, success=False, error="Evaluation error: boom"),
               _row("table/empty2", {}, success=False, error="Evaluation error: boom")])
    summary = summarize_report(_fixture_report(rows, {"avg_grits_trm_composite": 0.4}))
    metric = summary["metrics"]["grits_trm_composite"]
    if metric["mean"] != 0.4:
        failures.append(f"the mean is {metric['mean']}, not the survivor-only 0.4")
    if abs((metric["mean_zero_filled"] or 0) - 0.24) > 1e-12:
        failures.append("the zero-filled mean is not 1.2/5")
    if metric["mean"] == metric["mean_zero_filled"]:
        failures.append("the two means agree, so the fixture cannot tell them apart")
    if (metric["n_scored"], metric["n_total"]) != (3, 5):
        failures.append("n is not 3/5")
    if metric["empty_ids"] != ["table/empty1", "table/empty2"]:
        failures.append(f"the empty ids are {metric['empty_ids']}")
    if set(summary["per_doc"]) != {r["test_id"] for r in rows}:
        failures.append("per-document values are not keyed by test_id")

    # 2. The same scores in a different row order pair to all zeros.
    with tempfile.TemporaryDirectory() as tmp:
        scores = {f"table/p{i}": 0.1 * i for i in range(1, 6)}
        dir_a = pathlib.Path(tmp) / "a" / "table"
        dir_b = pathlib.Path(tmp) / "b" / "table"
        dir_a.mkdir(parents=True)
        dir_b.mkdir(parents=True)
        ordered = list(scores.items())
        (dir_a / REPORT).write_text(json.dumps(_fixture_report(
            [_row(t, {"rule_pass_rate": v}) for t, v in ordered])) + "\n")
        (dir_b / REPORT).write_text(json.dumps(_fixture_report(
            [_row(t, {"rule_pass_rate": v}) for t, v in reversed(ordered)])) + "\n")
        # The CSVs the scorer would write beside them, in opposite row orders.
        for path, items in ((dir_a / "_evaluation_results.csv", ordered),
                            (dir_b / "_evaluation_results.csv", list(reversed(ordered)))):
            with path.open("w", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=["test_id", "rule_pass_rate"])
                writer.writeheader()
                for test_id, value in items:
                    writer.writerow({"test_id": test_id, "rule_pass_rate": value})
        order_a = [r["test_id"] for r in csv.DictReader((dir_a / "_evaluation_results.csv").open())]
        order_b = [r["test_id"] for r in csv.DictReader((dir_b / "_evaluation_results.csv").open())]
        if order_a == order_b:
            failures.append("the fixture CSVs agree in row order, so nothing varies")
        paired = pair_dirs(pathlib.Path(tmp) / "a", pathlib.Path(tmp) / "b")
        compared = paired["groups"]["table"]["metrics"]["rule_pass_rate"]
        if compared["mean_diff"] != 0.0 or any(compared["diffs"].values()):
            failures.append(f"identical scores in another row order diff to {compared['diffs']}")
        if (compared["up"], compared["down"], compared["tied"]) != (0, 0, 5):
            failures.append("identical scores are not all tied")
        boot = compared["bootstrap"] or {}
        if (boot.get("ci_low"), boot.get("ci_high")) != (0.0, 0.0):
            failures.append("the bootstrap CI over zero diffs is not [0, 0]")
        # Positive control: a moved document is measured, not zeroed.
        moved = dict(ordered)
        moved["table/p5"] += 0.1
        (dir_b / REPORT).write_text(json.dumps(_fixture_report(
            [_row(t, {"rule_pass_rate": v}) for t, v in moved.items()])) + "\n")
        changed = pair_dirs(pathlib.Path(tmp) / "a", pathlib.Path(tmp) / "b")
        changed_metric = changed["groups"]["table"]["metrics"]["rule_pass_rate"]
        if abs((changed_metric["mean_diff"] or 0) - 0.02) > 1e-12:
            failures.append("a +0.1 move over five documents is not a +0.02 mean diff")
        if (changed_metric["up"], changed_metric["down"]) != (1, 0):
            failures.append("the moved document is not counted up")

    # 3. The guard scan: one timed-out rule, one over-budget document, one
    # normalisation timeout, one failed row, one stalled worker -- each named.
    timed = _row("chart/timed", {"rule_pass_rate": 0.5}, metadata={
        "skipped_over_budget": 0,
        "rule_results": [{"explanation": "Rule timed out after 120s", "passed": False},
                         {"explanation": "ok", "passed": True}]})
    over = _row("chart/over", {"rule_pass_rate": 0.0},
                metadata={"skipped_over_budget": 7, "rule_results": []})
    normalised = _row("chart/norm", {"rule_pass_rate": 0.0}, metadata={
        "skipped_over_budget": 5, "note": "Skipped: normalization exceeded 120s",
        "rule_results": []})
    failed = _row("chart/broken", {}, success=False, error="Evaluation error: boom")
    healthy = _row("chart/fine", {"rule_pass_rate": 0.9},
                   metadata={"skipped_over_budget": 0, "rule_results": []})
    scan = scan_guards(_fixture_report([timed, over, normalised, failed, healthy]))
    if scan["rule_timeouts"] != 1 or scan["rule_timeout_docs"] != ["chart/timed"]:
        failures.append(f"the timed-out rule is not counted and named: {scan}")
    if scan["skipped_over_budget"] != 12 or scan["over_budget_docs"] != ["chart/norm", "chart/over"]:
        failures.append(f"the over-budget documents are not counted and named: {scan}")
    if scan["normalize_timeouts"] != 1 or scan["normalize_docs"] != ["chart/norm"]:
        failures.append(f"the normalisation timeout is not counted and named: {scan}")
    if scan["failed_docs"] != ["chart/broken"]:
        failures.append(f"the failed document is not named: {scan}")
    if stall_ids_in("  [7/9] chart/stalled_page: FAILED (worker timed out after 480s)\n") != [
            "chart/stalled_page"]:
        failures.append("a stalled worker's document is not read from the logs")
    clean = scan_guards(_fixture_report([healthy]))
    if any([clean["rule_timeouts"], clean["skipped_over_budget"], clean["normalize_timeouts"],
            clean["failed_docs"]]):
        failures.append(f"a healthy report trips the guard: {clean}")

    for problem in failures:
        print(f"FAIL {problem}")
    if failures:
        return 1
    print("OK pb_summary: survivor-only means with n_scored/n_total, pair joined on "
          "test_id, guards naming every timed-out, over-budget and failed document")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("words", nargs="*", help="<report dir> | pair <dirA> <dirB>")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        return self_test()
    if len(args.words) == 3 and args.words[0] == "pair":
        paired = pair_dirs(pathlib.Path(args.words[1]), pathlib.Path(args.words[2]))
        print(json.dumps(paired, indent=1, sort_keys=True) if args.json else _text_pair(paired))
        return 0
    if len(args.words) == 1:
        rundown = summarize_dir(pathlib.Path(args.words[0]))
        print(json.dumps(rundown, indent=1, sort_keys=True) if args.json else _text_summary(rundown))
        return 0
    ap.error("usage: pb_summary.py <report dir> [--json] | pair <dirA> <dirB> [--json]")


if __name__ == "__main__":
    sys.exit(main())
