# Copyright (c) 2026 Innovation Trigger B.V. All rights reserved.
# Licence pending owner decision: see LICENSE-TBD.md.
"""Datasets: fetched from their public sources, never stored in this repository.

    python -m bench datasets plan  --sets all              list the files and sizes, fetch nothing
    python -m bench datasets fetch --sets doclaynet --work work [--limit N]

The fetch functions are the ported engine ones (`structured_bench.fetch`,
`fetch_pubtables`, `fetch_fintabnet`, `parsebench_round.fetch`): pinned
revisions from engine/benchmarks/structured/comparison/DATASETS.toml, per
document licence filtering for PubTables-1M, CRC and alignment checks for
FinTabNet, SHA-256 checks for ParseBench. Materialised pages are checked
against the sealed manifests in engine/corpus/structured/ before any column
reads them (`bench.quality.verify`).

FinTabNet's split is drawn at fetch time from the pinned page list
(FINTABNET_DRAW.json) under its fixed seed, which reproduces the engine's
draw; the other three sets are already sealed and are never redrawn here.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import shutil
import subprocess

from bench import configs, engine_api

# The tool that cut a set's sealed page bytes. PubTables-1M pages are cut out
# of their articles with `mutool merge`; the cut bytes differ between MuPDF
# versions, and the manifest seals the bytes. Measured 17-10 on the same
# articles: 1.23.10 reproduced 17/17 pages, 1.23.7 13/17, 1.19.0-1.22.2 8/17,
# 1.16.1 and 1.24.2-1.28.5 0/17.
CUT_TOOL = {"pubtables-1m": "1.23.10"}


def mutool_version() -> str | None:
    if not shutil.which("mutool"):
        return None
    done = subprocess.run(["mutool", "-v"], capture_output=True, text=True, check=False)
    found = re.search(r"mutool version (\S+)", (done.stdout or "") + (done.stderr or ""))
    return found.group(1) if found else None


def check_cut_tool(set_name: str) -> None:
    """Refuse a fetch whose cut tool cannot reproduce the sealed bytes, before it starts."""
    want = CUT_TOOL.get(set_name)
    if want is None:
        return
    have = mutool_version()
    if have != want:
        raise SystemExit(f"[datasets] REFUSED: {set_name} pages are cut with mutool {want}, the "
                         f"version that cut the sealed manifest; this machine has "
                         f"{have or 'no mutool'}, and its pages would not be the sealed ones")


def _pages_json(work: pathlib.Path, pages: list[dict]) -> pathlib.Path:
    work.mkdir(parents=True, exist_ok=True)
    path = work / "pages.json"
    path.write_text(json.dumps(pages))
    return path


def fetch(set_name: str, work: pathlib.Path, limit: int | None = None,
          profile: str = "full") -> dict:
    check_cut_tool(set_name)
    sb = engine_api.sb()
    entry = sb.dataset_entry(set_name)
    target = work / set_name
    if set_name == "parsebench":
        pbr = engine_api.module("parsebench_round")
        rule = engine_api.holdout_rule()
        manifest = json.loads(rule.manifest_for("parsebench").read_text())
        size = configs.pages_for("parsebench", profile)
        pages = []
        for split in ("tuning", "holdout"):
            pages += pbr.choose(manifest, size, rule.seed_for("parsebench"), split)
        if limit:
            pages = pages[:limit]
        data = target / "data"
        pbr.fetch(entry, data, pages)
        pbr.check_pages(pages, data)
        return {"set": set_name, "pages": len(pages), "where": str(data)}
    if set_name == "doclaynet":
        pages = sb.fetch(entry, target, limit)
    elif set_name == "pubtables-1m":
        pages = sb.fetch_pubtables(entry, target, limit)
    elif set_name == "fintabnet":
        pages = sb.fetch_fintabnet(entry, target, limit)
    else:
        raise SystemExit(f"[datasets] FATAL: unknown set `{set_name}`")
    _pages_json(target, pages)
    out = {"set": set_name, "pages": len(pages), "where": str(target)}
    if set_name == "fintabnet":
        refusals = target / "fetch-refusals.json"
        seen = json.loads(refusals.read_text()) if refusals.is_file() else {}
        manifest = sb.draw("fintabnet", entry, pages,
                           fetch_source=seen.get("source") if isinstance(seen.get("source"), str) else None,
                           transport_retries=seen.get("transport_retries")
                           if isinstance(seen.get("transport_retries"), int) else None)
        out["seal"] = manifest["seal"]
        out["counts"] = manifest["counts"]
    else:
        sealed = json.loads(engine_api.holdout_rule().draw_path_for(set_name).read_text())
        names = {p["name"] for p in sealed["pages"]}
        out["in_manifest"] = sum(1 for p in pages if p["name"] in names)
        out["manifest_pages"] = len(names)
    return out


def plan(sets: list[str]) -> list[dict]:
    """What a full fetch downloads, from the Hub's own file listing. Fetches no data."""
    from huggingface_hub import HfApi

    sb = engine_api.sb()
    api = HfApi()
    rows = []
    for set_name in sets:
        entry = sb.dataset_entry(set_name)
        repo = entry.get("repo_id")
        revision = entry.get("revision")
        info = api.dataset_info(repo, revision=revision, files_metadata=True)
        files = {s.rfilename: s.size for s in info.siblings or []}
        if set_name == "doclaynet":
            parts = entry.get("parts", 6)
            wanted = [f"data/test-{i:05d}-of-{parts:05d}.parquet" for i in range(parts)]
            note = "whole files"
        elif set_name == "pubtables-1m":
            wanted = [entry["annotations_file"]]
            files[entry["annotations_file"]] = entry.get("annotations_bytes")
            note = ("annotations archive (whole); the documents are fetched one article at a "
                    "time from the PMC Open Access bucket, licence code first, at most "
                    f"{entry.get('materialise_pages')} pages")
        elif set_name == "fintabnet":
            wanted = [entry["annotations_file"]]
            files[entry["annotations_file"]] = entry.get("annotations_bytes")
            note = (f"FinTabNet.c annotations ({entry.get('annotations_repo_id')}) whole; the "
                    f"documents archive ({entry.get('documents_archive_bytes')} bytes) is read by "
                    "HTTP range: its central directory and the pinned pages' members only")
        else:
            groups = engine_api.module("parsebench_round").GROUPS
            wanted = [f"{group}.jsonl" for group in groups]
            note = "rule files of the measured groups; plus the sampled pages only, one file each"
        rows.append({"set": set_name, "repo": repo, "revision": revision, "note": note,
                     "files": {n: files.get(n) for n in wanted},
                     "bytes": sum(files.get(n) or 0 for n in wanted)})
    return rows


def env_note() -> str:
    url = os.environ.get("FINTABNET_ARCHIVE_URL")
    return ("FINTABNET_ARCHIVE_URL is set: FinTabNet is read from that mirror"
            if url else "FinTabNet is read from the Hugging Face CDN (FINTABNET_ARCHIVE_URL unset)")
