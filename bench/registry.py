# Copyright (c) 2026 Innovation Trigger B.V.
#
# PDFluent is available under two licences, at your option: the GNU AGPLv3, or
# the PDFluent Commercial Licence. See the LICENSE file in this repository --
# that file travels with the copy you received, which a URL does not.
"""Page-hash registry and leak check across data layers and sealed holdouts (#971).

    python -m bench.registry build  --out datasets/registry --dp-bench <pdfs> --olmocr-bench <pdfs> [...]
    python -m bench.registry check  [--registry datasets/registry] [--layers datasets/layers]

Layers: `dev` (clustering, training), `validation` (not inspected while tuning), `training`.
Sealed holdouts are never used for tuning or training and live in `datasets/registry/holdouts/`.

What is stored per page, and nothing else (no content, no scores):

    file_sha256   SHA-256 of the file the page sits in (the page bytes for a one-page PDF)
    text          SHA-256 of the normalised page text, or null below MIN_TEXT characters
    render        256-bit ink-map hash of the page rendered at a fixed pixel size (cell darker than the page), as hex,
                  or null for a near-uniform page. A re-render at another DPI, or a scan of the
                  page in another container, lands within HAMMING bits of the original.
    doc           canonical source document ID, `<namespace>:<id>` (arxiv:2502.15977, pmc:PMC5120491)

A layer page matches a holdout page on `sha`, `text`, `render` or `doc`; a match is a violation that
names the page hash, the holdout and the layer. A `render` match counts only when at least one of the two
pages has no text layer (a scan, an image-only copy): two pages with text and a similar layout are
different pages unless their text matches, and the render hash cannot tell a form template from its copy.
Known gap: a re-scan whose new OCR text differs from the original is neither a text nor a render match. `doc` also covers the layer boundary: a source
document may not have pages on both sides of it. The sides are `train` (dev and training; training
is drawn from dev), `validation`, and each holdout.

Exit codes of `check`: 0 clean, 1 violation or registration problem, 3 clean but a holdout is
missing or only partly fingerprinted (`SKIPPED (not a pass): <reason>` per holdout, on stderr).
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor

from bench.contamination import normalise

ROOT = pathlib.Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "datasets" / "registry"
LAYERS = ROOT / "datasets" / "layers"
LAYER_NAMES = ("dev", "validation", "training")
SIDE = {"dev": "train", "training": "train", "validation": "validation"}

MIN_TEXT = 200          # normalised characters; below this the text says nothing about the page
HAMMING = 10            # of 256 render bits; measured against the committed registry in tests/test_registry.py
MIN_BITS, MAX_BITS = 12, 244   # a render hash outside this is a near-uniform page and is never matched
GRID = 16                      # 16 x 16 cells = 256 bits
RENDER_PX = 512                # the page is rendered into a RENDER_PX x RENDER_PX raster
CELL = RENDER_PX // GRID
RULES = ("sha", "text", "render", "doc")

# Every sealed holdout the epic (#970) names. A missing one is reported by name.
REQUIRED = {
    "doclaynet-test": "DocLayNet test split",
    "pubtables-1m-test": "PubTables-1M test split",
    "fintabnet-test": "FinTabNet test split",
    "parsebench": "ParseBench",
    "dp-bench": "DP-Bench",
    "docling-dpbench": "docling-dpbench (DP-Bench re-render)",
    "olmocr-bench": "olmOCR-bench (blind)",
}
# The 19-axes holdout is the holdout half of these four; it is derived, not stored twice.
AXES19 = ("doclaynet-test", "pubtables-1m-test", "fintabnet-test", "parsebench")
ARXIV_ID = re.compile(r"^(\d{4}\.\d{4,5}|[a-z\-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?$")


# ---------------------------------------------------------------- fingerprints

def canonical_doc(doc: str, ns: str | None = None) -> str:
    """`ns:id`, arXiv versions stripped, PMC ids upper-cased."""
    doc = doc.strip()
    if ":" not in doc:
        if ns is None:
            raise ValueError(f"document id {doc!r} has no namespace")
        doc = f"{ns}:{doc}"
    head, _, ident = doc.partition(":")
    head = head.lower()
    if head == "arxiv":
        ident = re.sub(r"v\d+$", "", ident)
    elif head == "pmc":
        ident = ident.upper()
        if not ident.startswith("PMC"):
            ident = "PMC" + ident
    return f"{head}:{ident}"


def text_fingerprint(text: str) -> str | None:
    norm = normalise(text)
    if len(norm) < MIN_TEXT:
        return None
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


def render_hash(pixels: bytes, width: int, height: int) -> str | None:
    """256 bits, one per cell of a 16x16 grid over an 8-bit grey raster: 1 where the cell is darker than the page.

    The raster is RENDER_PX x RENDER_PX. It is large on purpose: poppler samples a scanned page's image
    coarsely when it renders small, so a re-render of the same page lands far away at 128 px (measured 17-50
    bits against 5 at 512 px).
    """
    if width != RENDER_PX or height != RENDER_PX or len(pixels) != width * height:
        return None     # a rotated page renders transposed; it keeps its byte and text fingerprints
    cells = []
    for gy in range(GRID):
        row = [0] * GRID
        for y in range(gy * CELL, (gy + 1) * CELL):
            base = y * width
            for gx in range(GRID):
                row[gx] += sum(pixels[base + gx * CELL: base + (gx + 1) * CELL])
        cells += row
    mean = sum(cells) / len(cells)
    margin = CELL * CELL      # one grey level per pixel: noise below it is not a difference
    bits = 0
    for total in cells:
        bits = (bits << 1) | (1 if total < mean - margin else 0)
    if not MIN_BITS <= bits.bit_count() <= MAX_BITS:
        return None
    return f"{bits:064x}"


def hamming(a: str, b: str) -> int:
    return (int(a, 16) ^ int(b, 16)).bit_count()


def _read_pgm(path: str) -> tuple[bytes, int, int]:
    data = pathlib.Path(path).read_bytes()
    parts = data.split(None, 4)
    if parts[0] != b"P5" or int(parts[3]) > 255:
        raise ValueError(f"{path}: not an 8-bit binary PGM")
    width, height = int(parts[1]), int(parts[2])
    return parts[4][-(width * height):], width, height


def page_fingerprints(pdf: pathlib.Path) -> list[dict]:
    """One {page, text, render} per page of a PDF, through pdftotext and pdftoppm."""
    text = subprocess.run(["pdftotext", "-enc", "UTF-8", str(pdf), "-"], capture_output=True, check=True).stdout
    texts = text.decode("utf-8", "replace").split("\f")
    if texts and texts[-1] == "":
        texts.pop()
    with tempfile.TemporaryDirectory(prefix="registry-") as tmp:
        subprocess.run(["pdftoppm", "-gray", "-scale-to-x", str(RENDER_PX), "-scale-to-y", str(RENDER_PX),
                        str(pdf), str(pathlib.Path(tmp) / "p")], capture_output=True, check=True)
        rasters = sorted(glob.glob(os.path.join(tmp, "p-*.pgm")),
                         key=lambda p: int(re.search(r"p-(\d+)\.pgm$", p).group(1)))
        renders = [render_hash(*_read_pgm(p)) for p in rasters]
    return [{"page": i + 1,
             "text": text_fingerprint(texts[i]) if i < len(texts) else None,
             "render": renders[i] if i < len(renders) else None}
            for i in range(max(len(texts), len(renders)))]


def file_sha256(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fingerprint_pdf(path: pathlib.Path, **base) -> list[dict]:
    """Registry entries for every page of one PDF, ready to be written into a layer manifest."""
    sha = file_sha256(path)
    return [{**base, "file_sha256": sha, **fp} for fp in page_fingerprints(path)]


# ---------------------------------------------------------------- the match index

class Index:
    """Lookup of registered pages by sha, text, render (multi-index hashing) and document."""

    def __init__(self, entries: list[dict], rules: tuple[str, ...] = RULES, max_bits: int = HAMMING):
        self.rules = rules
        self.max_bits = max_bits
        self.by_sha: dict[str, list[dict]] = {}
        self.by_text: dict[str, list[dict]] = {}
        self.by_doc: dict[str, list[dict]] = {}
        self.chunks = max_bits + 1            # pigeonhole: a match within d bits shares one of d+1 chunks
        self.bounds = [round(256 * i / self.chunks) for i in range(self.chunks + 1)]
        self.by_chunk: list[dict[int, list[dict]]] = [{} for _ in range(self.chunks)]
        for e in entries:
            if e.get("file_sha256"):
                self.by_sha.setdefault(e["file_sha256"], []).append(e)
            if e.get("text"):
                self.by_text.setdefault(e["text"], []).append(e)
            if e.get("doc"):
                self.by_doc.setdefault(e["doc"], []).append(e)
            if e.get("render"):
                value = int(e["render"], 16)
                for c in range(self.chunks):
                    self.by_chunk[c].setdefault(self._chunk(value, c), []).append(e)

    def _chunk(self, value: int, c: int) -> int:
        lo, hi = self.bounds[c], self.bounds[c + 1]
        return (value >> (256 - hi)) & ((1 << (hi - lo)) - 1)

    def match(self, page: dict) -> list[tuple[str, dict]]:
        hits: list[tuple[str, dict]] = []
        if "sha" in self.rules and page.get("file_sha256"):
            hits += [("sha", e) for e in self.by_sha.get(page["file_sha256"], [])]
        if "text" in self.rules and page.get("text"):
            hits += [("text", e) for e in self.by_text.get(page["text"], [])]
        if "render" in self.rules and page.get("render"):
            value = int(page["render"], 16)
            seen = set()
            for c in range(self.chunks):
                for e in self.by_chunk[c].get(self._chunk(value, c), ()):
                    if id(e) in seen or (value ^ int(e["render"], 16)).bit_count() > self.max_bits:
                        continue
                    if not (page.get("text") and e.get("text")):
                        seen.add(id(e))
                        hits.append(("render", e))
        if "doc" in self.rules and page.get("doc"):
            hits += [("doc", e) for e in self.by_doc.get(page["doc"], [])]
        return hits


# ---------------------------------------------------------------- registry files

def seal(entries: list[dict]) -> str:
    h = hashlib.sha256()
    for e in entries:
        h.update(json.dumps(e, sort_keys=True, separators=(",", ":")).encode())
        h.update(b"\n")
    return h.hexdigest()


def read_jsonl(path: pathlib.Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path: pathlib.Path, entries: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(e, sort_keys=True, separators=(",", ":")) + "\n" for e in entries),
                    encoding="utf-8")


def load_registry(registry: pathlib.Path = REGISTRY) -> dict:
    """{"holdouts": {name: {meta..., "entries": [...]}}}; an altered holdout file is an error."""
    index = json.loads((registry / "registry.json").read_text(encoding="utf-8"))
    for meta in index["holdouts"].values():
        meta["entries"] = []
        if meta.get("file"):
            meta["entries"] = read_jsonl(registry / meta["file"])
            if seal(meta["entries"]) != meta["seal"]:
                raise SystemExit(f"registry: {meta['file']} does not match its seal; rebuild or restore it")
    return index


def load_layers(layers: pathlib.Path = LAYERS, registry: pathlib.Path = REGISTRY) -> tuple[list[dict], list[str]]:
    """Layer manifests that are registered in layers.json, plus problems with registration itself."""
    problems = []
    reg_path = registry / "layers.json"
    registered = json.loads(reg_path.read_text(encoding="utf-8")) if reg_path.exists() else {"datasets": []}
    declared = {d["manifest"]: d for d in registered["datasets"]}
    on_disk = {f"{layer}/{p.name}": p for layer in LAYER_NAMES for p in sorted((layers / layer).glob("*.json"))}
    for rel in sorted(on_disk):
        if rel not in declared:
            problems.append(f"dataset not registered: {rel} is in layer '{rel.split('/')[0]}' but not in layers.json")
    manifests = []
    for rel, decl in sorted(declared.items()):
        if decl["layer"] not in LAYER_NAMES:
            problems.append(f"{decl['name']}: unknown layer {decl['layer']!r}")
            continue
        if not rel.startswith(decl["layer"] + "/"):
            problems.append(f"{decl['name']}: manifest {rel} is not in the {decl['layer']} directory")
        if rel not in on_disk:
            problems.append(f"{decl['name']}: registered manifest {rel} does not exist")
            continue
        doc = json.loads(on_disk[rel].read_text(encoding="utf-8"))
        if decl.get("repack_of") and not doc.get("pages"):
            problems.append(f"{decl['name']}: a repack of {decl['repack_of']} needs a page-hash manifest before use")
        manifests.append({**decl, "pages": doc.get("pages", []), "doc_ns": decl.get("doc_ns") or doc.get("doc_ns")})
    return manifests, problems


# ---------------------------------------------------------------- the check

def _violation(rule: str, layer: str, dataset: str, against: str, hit: dict, page: dict) -> dict:
    return {"rule": rule, "layer": layer, "dataset": dataset, "holdout": against,
            "page_hash": hit.get("file_sha256") or page.get("file_sha256") or "",
            "holdout_page": f"{hit.get('file')}#{hit.get('page')}",
            "layer_page": f"{page.get('file') or page.get('doc')}#{page.get('page')}"}


def check(registry: dict, manifests: list[dict], rules: tuple[str, ...] = RULES, max_bits: int = HAMMING) -> dict:
    """{"violations": [...], "missing": [...], "partial": [...]}; nothing is read from disk here."""
    holdouts = registry["holdouts"]
    missing, partial = [], []
    for name, label in REQUIRED.items():
        meta = holdouts.get(name)
        if meta is None or not meta.get("entries"):
            missing.append({"holdout": name, "label": label,
                            "reason": (meta or {}).get("reason") or "not in the registry"})
        elif meta.get("status") == "partial":
            partial.append({"holdout": name, "label": label, "reason": meta.get("reason", "")})

    held = [{**e, "_holdout": name} for name, meta in holdouts.items() for e in meta["entries"]]
    holdout_index = Index(held, rules, max_bits)
    pages = []
    for m in manifests:
        for p in m["pages"]:
            page = dict(p)
            if page.get("doc"):
                page["doc"] = canonical_doc(page["doc"], m.get("doc_ns"))
            pages.append((m, page))

    violations = []
    for m, page in pages:
        for rule, hit in holdout_index.match(page):
            axes = " (19-axes holdout)" if hit["_holdout"] in AXES19 and hit.get("half") == "holdout" else ""
            violations.append(_violation(rule, m["layer"], m["name"], hit["_holdout"] + axes, hit, page))
    train_index = Index([{**p, "_ds": m["name"], "_layer": m["layer"]} for m, p in pages
                         if SIDE[m["layer"]] == "train"], rules, max_bits)
    for m, page in pages:
        if SIDE[m["layer"]] == "validation":
            for rule, hit in train_index.match(page):
                violations.append(_violation(rule, "validation", m["name"], f"{hit['_layer']}:{hit['_ds']}", hit, page))
    seen, unique = set(), []
    for v in violations:
        key = tuple(v.values())
        if key not in seen:
            seen.add(key)
            unique.append(v)
    return {"violations": unique, "missing": missing, "partial": partial}


def report(result: dict, problems: list[str], out=None, err=None) -> int:
    out, err = out or sys.stdout, err or sys.stderr
    for p in problems:
        print(f"REGISTRATION {p}", file=out)
    for v in result["violations"]:
        print(f"LEAK ({v['rule']}) page {v['page_hash'] or '-'} of {v['holdout']} [{v['holdout_page']}] "
              f"is in layer {v['layer']}, dataset {v['dataset']} [{v['layer_page']}]", file=out)
    for m in result["missing"]:
        print(f"SKIPPED (not a pass): holdout {m['holdout']} ({m['label']}) is missing: {m['reason']}", file=err)
    for m in result["partial"]:
        print(f"SKIPPED (not a pass): holdout {m['holdout']} ({m['label']}) is only partly fingerprinted: "
              f"{m['reason']}", file=err)
    if problems or result["violations"]:
        print(f"{len(result['violations'])} leak(s), {len(problems)} registration problem(s)", file=out)
        return 1
    print("no leak", file=out)
    return 3 if result["missing"] or result["partial"] else 0


# ---------------------------------------------------------------- build (maintainer machine, needs the data)

def _fingerprint_many(items: list[tuple[pathlib.Path, dict]], workers: int = 6) -> list[dict]:
    with ThreadPoolExecutor(workers) as pool:
        return [e for batch in pool.map(lambda it: fingerprint_pdf(it[0], **it[1]), items) for e in batch]


def _manifest_entries(manifest_path: pathlib.Path, root: pathlib.Path | None, ns: str, name: str) -> tuple[list[dict], dict]:
    """Sealed structured manifests: every page, fingerprinted when its file is on disk, else byte hash only."""
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    on_disk, off_disk = [], []
    for p in manifest["pages"]:
        doc = p["document"] if ns == "pmc" else p["document"].removesuffix(".pdf")
        base = {"holdout": name, "source": p["source"], "file": p["name"], "half": p["split"],
                "doc": canonical_doc(doc, ns)}
        if p.get("licence"):
            base["licence"] = p["licence"]
        path = root / p["name"] if root else None
        (on_disk if path and path.exists() else off_disk).append((path, base, p))
    entries = _fingerprint_many([(path, base) for path, base, _ in on_disk])
    for _, base, p in off_disk:
        entries.append({**base, "page": p.get("page_no", 1), "file_sha256": p["sha256"], "text": None, "render": None})
    return entries, {"source": manifest["source"], "revision": manifest["revision"], "licence": manifest["licence"]}


def build(out: pathlib.Path, roots: dict[str, pathlib.Path], corpus: pathlib.Path | None = None) -> None:
    corpus = corpus or ROOT / "engine" / "corpus" / "structured"
    index: dict = {"version": 1, "hamming": HAMMING, "holdouts": {}}

    def put(name: str, entries: list[dict], meta: dict, reason: str = "") -> None:
        entries.sort(key=lambda e: (e["file"], e["page"]))
        write_jsonl(out / "holdouts" / f"{name}.jsonl", entries)
        index["holdouts"][name] = {**meta, "file": f"holdouts/{name}.jsonl", "pages": len(entries),
                                   "with_render": sum(1 for e in entries if e["render"]),
                                   "with_text": sum(1 for e in entries if e["text"]),
                                   "seal": seal(entries), "status": "partial" if reason else "complete",
                                   **({"reason": reason} if reason else {})}

    for name, fname, ns, key in [("doclaynet-test", "DOCLAYNET_TEST.json", "doclaynet", "doclaynet"),
                                 ("fintabnet-test", "FINTABNET.json", "fintabnet", "fintabnet"),
                                 ("parsebench", "PARSEBENCH.json", "parsebench", "parsebench"),
                                 ("pubtables-1m-test", "PUBTABLES1M.json", "pmc", "pubtables")]:
        entries, meta = _manifest_entries(corpus / fname, roots.get(key), ns, name)
        bare = sum(1 for e in entries if not e["render"] and not e["text"] and e["file_sha256"])
        reason = ""
        if bare:
            reason = (f"{bare} of {len(entries)} pages are registered by byte hash and document id only: "
                      f"their files are not on this machine, or they have neither a text layer nor a usable render")
            if name == "pubtables-1m-test":
                reason = (f"{bare} of {len(entries)} pages are registered by byte hash and document id only: the "
                          f"pages are cut with mutool 1.23.10 and this machine has another version, so they are "
                          f"not materialised")
        put(name, entries, meta, reason)

    dp = roots["dp-bench"]
    put("dp-bench", _fingerprint_many([(dp / n, {"holdout": "dp-bench", "source": "opendataloader-bench", "file": n,
                                               "half": None, "doc": f"dp-bench:{n.removesuffix('.pdf')}"})
                                       for n in sorted(os.listdir(dp)) if n.endswith(".pdf")]),
        {"source": "opendataloader-bench @ 7af1d8f4d0c09f51ea1a5c6ba5f66e993286d109"})
    index["holdouts"]["docling-dpbench"] = {
        "status": "skipped", "file": None, "pages": 0, "seal": "",
        "reason": "docling-dpbench is not on this machine; its pages re-render the DP-Bench documents, so the "
                  "render hashes of dp-bench match them once it is fetched"}

    olm = roots["olmocr-bench"]
    items = []
    for cat in sorted(os.listdir(olm)):
        if not (olm / cat).is_dir():
            continue
        for n in sorted(os.listdir(olm / cat)):
            if n.endswith(".pdf"):
                stem = re.sub(r"_pg\d+$", "", n.removesuffix(".pdf"))
                doc = f"arxiv:{stem}" if cat == "arxiv_math" and ARXIV_ID.match(stem) else f"olmocr-bench:{cat}/{stem}"
                items.append((olm / cat / n, {"holdout": "olmocr-bench", "source": "olmOCR-bench", "file": f"{cat}/{n}",
                                              "half": None, "doc": canonical_doc(doc)}))
    put("olmocr-bench", _fingerprint_many(items), {"source": "allenai/olmOCR-bench, hashes only (blind)"})

    index["axes19"] = {"derived_from": list(AXES19), "half": "holdout"}
    out.mkdir(parents=True, exist_ok=True)
    (out / "registry.json").write_text(json.dumps(index, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    if not (out / "layers.json").exists():
        (out / "layers.json").write_text(json.dumps({"datasets": []}, indent=1) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="bench.registry")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("--registry", type=pathlib.Path, default=REGISTRY)
    c.add_argument("--layers", type=pathlib.Path, default=LAYERS)
    b = sub.add_parser("build")
    b.add_argument("--out", type=pathlib.Path, default=REGISTRY)
    for key in ("doclaynet", "fintabnet", "parsebench"):
        b.add_argument(f"--{key}", type=pathlib.Path)
    b.add_argument("--corpus", type=pathlib.Path, help="directory with the sealed manifests (default engine/corpus/structured)")
    b.add_argument("--dp-bench", type=pathlib.Path, required=True)
    b.add_argument("--olmocr-bench", type=pathlib.Path, required=True)
    args = ap.parse_args(argv)
    if args.cmd == "check":
        manifests, problems = load_layers(args.layers, args.registry)
        return report(check(load_registry(args.registry), manifests), problems)
    roots = {k: v for k, v in {"doclaynet": args.doclaynet, "fintabnet": args.fintabnet, "parsebench": args.parsebench,
                               "dp-bench": args.dp_bench, "olmocr-bench": args.olmocr_bench}.items() if v}
    build(args.out, roots, args.corpus)
    return 0


if __name__ == "__main__":
    sys.exit(main())
