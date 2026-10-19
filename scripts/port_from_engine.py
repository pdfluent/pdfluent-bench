#!/usr/bin/env python3
# Copyright (c) 2026 Innovation Trigger B.V. All rights reserved.
# Licence pending owner decision: see LICENSE-TBD.md.
"""Vendor the quality rig from the PDFluent engine repository into engine/.

    scripts/port_from_engine.py --engine /path/to/engine --ref <commit>

Reads every file with `git show <ref>:<path>` and never touches the engine
working tree. Each file keeps its engine-relative path under `engine/`, so the
modules' own `REPO / ...` paths resolve inside this repository unchanged and
the patch set stays small.

Patches are exact-string replacements that must match the stated number of
times; a patch that does not match fails the port. The scoring code is never
patched: PROVENANCE.json records a hash of each scoring function's source in
the engine and in the vendored copy, and tests/test_provenance.py checks they
are equal.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import pathlib
import re
import subprocess
import sys
import tomllib

ROOT = pathlib.Path(__file__).resolve().parents[1]
DEST = ROOT / "engine"

# Files copied byte for byte (path in the engine == path under engine/).
VERBATIM = [
    "scripts/ci/structured_holdout_rule.py",
    "scripts/ci/test_fintabnet_split.py",
    "scripts/ci/loop/test_structured_bench_fintabnet_mirror.py",
    "scripts/ci/loop/test_structured_bench_fintabnet_norealign.py",
    "scripts/ci/loop/test_structured_bench_fintabnet_pinned.py",
    "scripts/ci/loop/test_structured_bench_missing_tools.py",
    "scripts/ci/loop/test_structured_bench_order_provenance.py",
    "scripts/extraction/structured_axes.py",
    "scripts/extraction/pubtables_cells.py",
    "scripts/extraction/pb_summary.py",
    "scripts/extraction/fintabnet_archive.py",
    "scripts/extraction/fintabnet_split.py",
    "scripts/extraction/parsebench_harness.py",
    "scripts/extraction/parsebench_manifest.py",
    "scripts/extraction/lexicon/ATTRIBUTION",
    "scripts/extraction/lexicon/LEXICON.json",
    "scripts/extraction/lexicon/en_scowl60-rel-2026.02.25.txt",
    "scripts/extraction/adapters/extract_text.py",
    "scripts/extraction/adapters/teacher_page.py",
    "scripts/extraction/adapters/requirements-best.txt",
    "scripts/extraction/learned/align_labels.py",
    "scripts/extraction/learned/header_band_subset.txt",
    # The sealed manifests: page names and SHA-256 digests, no document content.
    "corpus/structured/DOCLAYNET_TEST.json",
    "corpus/structured/PUBTABLES1M.json",
    "corpus/structured/PARSEBENCH.json",
    "corpus/structured/FINTABNET_DRAW.json",
    "corpus/structured/EXCLUDE_EVAL_OVERLAP.json",
]

BENCH_NOTE = "pdfluent-bench:"

# Comment-only scrub, applied to every ported .py file after the patches: the
# engine's comments credit the tool that reviewed a change, name the cloud
# provider behind its internal round policy and point into a home directory.
# None of that belongs in a public repository. (The words are spelled in
# pieces so this file does not carry them itself.) The scrub never touches
# code: the scoring functions are still checked against the engine, after
# applying the same scrub to the engine text.
SCRUBS = [
    (re.compile(r"\(" + "co" + "dex, (#\\d+)\\)"), r"(\1)"),
    (re.compile(r"\((#\d+), " + "co" + "dex on (#\\d+)\\)"), r"(\1, \2)"),
    (re.compile("het" + "zner_ledger"), "cost_ledger"),
    (re.compile("Het" + "zner"), "cloud"),
    (re.compile(r"~/\.local/share/pdfluent/pb-venv/bin/python"), "<venv>/bin/python"),
    (re.compile(r"~/\.local/share/pdfluent/pb-tuning/data/docs/table/"), "<parsebench-docs>/table/"),
]


def scrub(text: str) -> str:
    for rx, replacement in SCRUBS:
        text = rx.sub(replacement, text)
    return text


# (path, [(old, new, expected_count), ...])
PATCHED: list[tuple[str, list[tuple[str, str, int]]]] = [
    ("scripts/ci/loop/structured_bench.py", [
        # The engine's rig-data guard enforces an internal training-data policy
        # (rig pages may only exist on the round machine). It does not apply to
        # a public benchmark and would refuse every local run on macOS.
        ('    import rig_data_stays_on_the_rig as rig_data\n'
         '    rig_data.refuse_split("doclaynet", "test")\n',
         f'    # {BENCH_NOTE} the engine rig-data guard is not ported (PORTING.md).\n', 1),
        ('    import rig_data_stays_on_the_rig as rig_data\n'
         '    rig_data.refuse_split("pubtables-1m", None)\n',
         f'    # {BENCH_NOTE} the engine rig-data guard is not ported (PORTING.md).\n', 1),
        ('    import rig_data_stays_on_the_rig as rig_data\n\n',
         f'    # {BENCH_NOTE} the engine rig-data guard is not ported (PORTING.md).\n\n', 1),
        ('    rig_data.refuse_split("fintabnet", None)\n', '', 1),
        # Run files go where the bench harness says.
        ('RUNS = REPO / "benchmarks" / "runs" / "structured"\n',
         'RUNS = pathlib.Path(os.environ.get("BENCH_RUNS_DIR")  # ' + BENCH_NOTE + ' override\n'
         '                    or REPO / "benchmarks" / "runs" / "structured")\n', 1),
        # The competitor cache is off unless a directory is named: a benchmark
        # run measures every column it reports.
        ('COMPETITORS = REPO / "benchmarks" / "structured" / "competitors"\n',
         'COMPETITORS = pathlib.Path(os.environ.get("BENCH_COMPETITOR_CACHE")  # ' + BENCH_NOTE
         + ' opt-in\n                           or REPO / "benchmarks" / "structured" / "competitors")\n', 1),
        ('        stored = cache_load(key) if key else None\n',
         '        stored = (cache_load(key) if key and os.environ.get("BENCH_COMPETITOR_CACHE")\n'
         '                  else None)  # ' + BENCH_NOTE + ' cache is opt-in\n', 1),
        ('            if key is not None and probe.get("outcome") == "ok":\n',
         '            if (key is not None and probe.get("outcome") == "ok"\n'
         '                    and os.environ.get("BENCH_COMPETITOR_CACHE")):  # ' + BENCH_NOTE + ' opt-in\n', 1),
        # The cost ledger and the one-run-per-head rule are the engine's
        # internal round policy. The bench's cost guard is infra/gcp.
        ('    spent = money_left()\n    if spent is not None and spent[1] <= 0.0:\n',
         '    return  # ' + BENCH_NOTE + ' cost guard lives in infra/gcp (BUDGET_EUR); PORTING.md\n'
         '    spent = money_left()\n    if spent is not None and spent[1] <= 0.0:\n', 1),
        # The bench's own adapter columns run through the same batch door.
        ('ADAPTED = ("docling", "liteparse", "pymupdf4llm", "unstructured", "mineru", "pdfplumber", "teacher",\n'
         '           "teacher_ep03", "teacher_ens")\n',
         'ADAPTED = ("docling", "liteparse", "pymupdf4llm", "unstructured", "mineru", "pdfplumber", "teacher",\n'
         '           "teacher_ep03", "teacher_ens",\n'
         '           # ' + BENCH_NOTE + ' the bench columns (bench/adapters, PORTING.md)\n'
         '           "docling_minimal", "pdfluent_fast", "pdfluent_balanced", "pdfluent_balanced_tatr")\n', 1),
    ]),
    ("scripts/extraction/parsebench_round.py", [
        # Both halves are measured and reported apart; the engine round read
        # the tuning half only.
        ('def choose(manifest: dict, per_category: int, seed: str) -> list[dict]:\n',
         'def choose(manifest: dict, per_category: int, seed: str,\n'
         '           split: str = "tuning") -> list[dict]:  # ' + BENCH_NOTE + ' split\n', 1),
        ('                if p["split"] == "tuning" and p["category"] == category]\n',
         '                if p["split"] == split and p["category"] == category]\n', 1),
        ('    pages = choose(manifest, args.pages, holdout_rule.seed_for(SET))\n',
         '    pages = choose(manifest, args.pages, holdout_rule.seed_for(SET),\n'
         '                   args.split)  # ' + BENCH_NOTE + ' split\n', 1),
        ('        "half": "tuning", "harness_revision": sb.harness_revision(),\n',
         '        "half": args.split, "harness_revision": sb.harness_revision(),  # ' + BENCH_NOTE + '\n', 1),
        ('    ap.add_argument("--self-test", action="store_true")\n    args = ap.parse_args()\n',
         '    ap.add_argument("--split", choices=("tuning", "holdout"), default="tuning",\n'
         '                    help="' + BENCH_NOTE + ' which half of the sealed manifest")\n'
         '    ap.add_argument("--self-test", action="store_true")\n    args = ap.parse_args()\n', 1),
        ('        stored = cache_load(key) if key else None\n',
         '        stored = (cache_load(key) if key and os.environ.get("BENCH_COMPETITOR_CACHE")\n'
         '                  else None)  # ' + BENCH_NOTE + ' cache is opt-in\n', 1),
        ('        if (key is not None and not refusals\n',
         '        if (key is not None and not refusals and os.environ.get("BENCH_COMPETITOR_CACHE")\n', 1),
        # BENCH_RUNS_DIR and BENCH_COMPETITOR_CACHE may lie outside the tree.
        ('    print(f"[{PREFIX}] run file {path.relative_to(REPO)}")\n',
         '    print(f"[{PREFIX}] run file {path}")  # ' + BENCH_NOTE + ' may be outside REPO\n', 1),
        ('from_cache=str(cache_file(key).relative_to(REPO)))',
         'from_cache=str(cache_file(key)))  # ' + BENCH_NOTE, 1),
        ('str(cache_save(key, tools[tool]).relative_to(REPO))',
         'str(cache_save(key, tools[tool]))', 1),
    ]),
    ("scripts/extraction/adapters/extract_structured.py", [
        ('TOOLS = {\n    "docling": with_docling,\n',
         '# --- ' + BENCH_NOTE + ' bench columns, see bench_columns.py ---\n'
         'sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[4] / "bench" / "adapters"))\n'
         'import bench_columns  # noqa: E402\n\n\n'
         'TOOLS = {\n    "docling": with_docling,\n', 1),
        ('    "pdfplumber": with_pdfplumber,\n}\n',
         '    "pdfplumber": with_pdfplumber,\n'
         '    **bench_columns.TOOLS,  # ' + BENCH_NOTE + '\n}\n', 1),
        ('        written = (batch_docling(paths, args.out_dir, args.ocr) if args.tool == "docling"\n',
         '        written = (batch_docling(paths, args.out_dir, args.ocr) if args.tool == "docling"\n'
         '                   else bench_columns.batch_docling_minimal(paths, args.out_dir, args.ocr, Clock)\n'
         '                   if args.tool == "docling_minimal"  # ' + BENCH_NOTE + '\n', 1),
        ('    if set(TOOLS) != {"docling", "liteparse", "pymupdf4llm", "unstructured", "mineru",\n'
         '                      "pdfplumber", "teacher", "teacher_ep03", "teacher_ens"}:\n',
         '    if set(TOOLS) != {"docling", "liteparse", "pymupdf4llm", "unstructured", "mineru",\n'
         '                      "pdfplumber", "teacher", "teacher_ep03", "teacher_ens",\n'
         '                      *bench_columns.TOOLS}:  # ' + BENCH_NOTE + '\n', 1),
    ]),
    ("scripts/extraction/learned/subset_scorer.py", []),
    ("scripts/extraction/parsebench_round.py", []),
]

# Tests that exercise the engine's remote round script, which is not ported.
NOT_PORTED = {
    "scripts/extraction/structured_bench_remote.sh":
        "the engine's remote round driver; replaced by docker/ images, bench/ and infra/gcp/",
    "scripts/ci/loop/test_structured_bench_remote_sigpipe.py":
        "tests structured_bench_remote.sh, which is not ported",
    "scripts/extraction/adapters/test_best_render.py":
        "needs the engine's render_page_png build; the best column takes it from env",
    "scripts/extraction/benchmark_page.py":
        "the engine's public page, built on structured_parity.py and PAGE.toml claim IDs; "
        "replaced by bench/report.py, which keeps its rules (numbers only from run files, "
        "denominators shown, draft while anything is missing)",
    "scripts/ci/rig_data_stays_on_the_rig.py": "internal training-data policy",
}

# Scoring functions whose source must be identical in engine and vendored copy.
SCORING = {
    "scripts/ci/loop/structured_bench.py": [
        "blocks_of", "_pipe_to_text", "_pipe_to_html", "unmarked", "score_page", "words_row",
        "words_excluded", "_unscored", "_v2_lexicon", "geometric", "order_provenance", "sample",
        "summarise_tool", "summarise_tool_v2", "tails", "macro_by_document", "pages_digest",
        "draw", "_page_of", "_aligned", "measure_batch", "markdown_of", "run_tool",
        "dimensions_of", "cold_start",
        "COMMENT", "FENCE", "IMAGE", "INLINE_TAG", "INLINE_STRONG", "INLINE_EM", "INLINE_STRIKE",
        "HEADING", "BULLET", "TABLE_ROW", "TABLE_RULE", "AXES", "V2_AXES", "BODY", "EVERYTHING",
        "DOCLAYNET_CLASS", "LAUNCHER", "TIMEOUT_S",
    ],
    "scripts/extraction/parsebench_round.py": [
        "evaluation_of", "refusals_of", "score", "build_cases", "check_pages", "write_markdown",
        "headline_of", "HEADLINE", "CATEGORIES", "GROUPS",
    ],
}

# The two registers are reduced to the rows this benchmark runs.
DATASETS_KEEP = ("doclaynet", "pubtables-1m", "fintabnet", "parsebench")
EXTRACTORS_KEEP = ("pdfluent", "docling", "pymupdf4llm", "balanced", "balanced_tatr", "teacher_ens")


def git_show(engine: pathlib.Path, ref: str, path: str) -> bytes:
    return subprocess.run(["git", "-C", str(engine), "show", f"{ref}:{path}"],
                          check=True, capture_output=True).stdout


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def segments(source: str) -> dict[str, str]:
    """Top-level function and assignment sources, by name."""
    tree = ast.parse(source)
    out: dict[str, str] = {}
    for node in tree.body:
        names: list[str] = []
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names = [node.name]
        elif isinstance(node, ast.Assign):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names = [node.target.id]
        for name in names:
            out[name] = ast.get_source_segment(source, node) or ""
    return out


def apply(text: str, patches: list[tuple[str, str, int]], path: str) -> str:
    for old, new, count in patches:
        found = text.count(old)
        if found != count:
            raise SystemExit(f"[port] FATAL: {path}: patch expected {count} match(es), "
                             f"found {found}:\n{old[:200]}")
        text = text.replace(old, new)
    return text


def apply_changed(text: str, original: bytes) -> bool:
    """True when the patches changed the code, not merely the comments."""
    return scrub(text) != scrub(original.decode("utf-8"))


def toml_value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return "[" + ", ".join(toml_value(v) for v in value) + "]"
    if isinstance(value, dict):
        return "{ " + ", ".join(f"{json.dumps(k)} = {toml_value(v)}" for k, v in value.items()) + " }"
    if hasattr(value, "isoformat"):
        return value.isoformat()
    raise TypeError(type(value))


def write_register(loaded: dict, section: str, keep: tuple[str, ...], ref: str,
                   extra: str = "") -> str:
    lines = [f"# Reduced from the engine's register at {ref[:12]} by scripts/port_from_engine.py.",
             "# Only the rows this benchmark runs are kept; the fields are unchanged.",
             "", "[meta]"]
    for key, value in loaded.get("meta", {}).items():
        lines.append(f"{key} = {toml_value(value)}")
    for name in keep:
        lines += ["", f"[{section}.{json.dumps(name) if '-' in name else name}]"]
        for key, value in loaded[section][name].items():
            lines.append(f"{key} = {toml_value(value)}")
    text = "\n".join(lines) + "\n"
    if extra:
        text += "\n" + extra
    tomllib.loads(text)  # must parse
    return text


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--engine", required=True, type=pathlib.Path)
    ap.add_argument("--ref", required=True)
    args = ap.parse_args()
    ref = subprocess.run(["git", "-C", str(args.engine), "rev-parse", args.ref],
                         check=True, capture_output=True, text=True).stdout.strip()
    provenance: dict = {"engine_ref": ref, "files": {}, "not_ported": NOT_PORTED,
                        "scoring_functions": {}}

    def put(path: str, data: bytes, original: bytes, patched: bool) -> None:
        # A .py file is scrubbed on the way in. `patched` means code changed;
        # `scrubbed` means only comments did.
        scrubbed = False
        if path.endswith(".py"):
            cleaned = scrub(data.decode("utf-8")).encode("utf-8")
            scrubbed = cleaned != data
            data = cleaned
        target = DEST / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        provenance["files"][path] = {"engine_sha256": sha256(original),
                                     "vendored_sha256": sha256(data), "patched": patched,
                                     "scrubbed": scrubbed}

    for path in VERBATIM:
        data = git_show(args.engine, ref, path)
        put(path, data, data, False)

    done: dict[str, tuple[bytes, str]] = {}
    for path, patches in PATCHED:
        if path in done:
            original, text = done[path]
        else:
            original = git_show(args.engine, ref, path)
            text = original.decode("utf-8")
        text = apply(text, patches, path)
        done[path] = (original, text)
    for path, (original, text) in done.items():
        put(path, text.encode("utf-8"), original, apply_changed(text, original))

    for path, names in SCORING.items():
        before = segments(git_show(args.engine, ref, path).decode("utf-8"))
        scrubbed_before = segments(scrub(git_show(args.engine, ref, path).decode("utf-8")))
        after = segments((DEST / path).read_text(encoding="utf-8"))
        rows = {}
        for name in names:
            if name not in before:
                raise SystemExit(f"[port] FATAL: {path}: no top-level `{name}` in the engine")
            rows[name] = {"engine_sha256": sha256(before[name].encode()),
                          "vendored_sha256": sha256(after.get(name, "").encode()),
                          "scrubbed": before[name] != scrubbed_before[name]}
            if sha256(scrubbed_before[name].encode()) != rows[name]["vendored_sha256"]:
                raise SystemExit(f"[port] FATAL: {path}: `{name}` differs from the engine; "
                                 "scoring code is never patched")
        provenance["scoring_functions"][path] = rows

    datasets = tomllib.loads(git_show(
        args.engine, ref, "benchmarks/structured/comparison/DATASETS.toml").decode())
    text = write_register(datasets, "datasets", DATASETS_KEEP, ref)
    put("benchmarks/structured/comparison/DATASETS.toml", text.encode(),
        git_show(args.engine, ref, "benchmarks/structured/comparison/DATASETS.toml"), True)

    extractors_raw = git_show(args.engine, ref, "benchmarks/extraction/comparison/EXTRACTORS.toml")
    extra = (ROOT / "scripts" / "bench_extractors.toml").read_text(encoding="utf-8")
    text = write_register(tomllib.loads(extractors_raw.decode()), "extractors",
                          EXTRACTORS_KEEP, ref, extra)
    put("benchmarks/extraction/comparison/EXTRACTORS.toml", text.encode(), extractors_raw, True)

    (DEST / "PROVENANCE.json").write_text(json.dumps(provenance, indent=1, sort_keys=True) + "\n")
    print(f"[port] {len(provenance['files'])} file(s) from {ref[:12]} into {DEST}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
