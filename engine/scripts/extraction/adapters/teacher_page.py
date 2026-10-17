#!/usr/bin/env python3
# Copyright (c) 2026 Innovation Trigger B.V.
#
# PDFluent is available under two licences, at your option: the GNU AGPLv3, or
# the PDFluent Commercial Licence. See the LICENSE file in this repository --
# that file travels with the copy you received, which a URL does not.
"""#641 teacher as a benchmark column: a layout detector plus our own PDF text, page in -> markdown out.

    teacher_page.py --model DIR --feature-dump BIN --batch LIST --out-dir OUT [--thr 0.5] [--device cpu]
    teacher_page.py --check-parity CPU.jsonl GPU.jsonl [--parity-tol 0.01] [--parity-min-pages 100]

One process for the whole batch (the model loads once). Per page:
1. render page 1 at 96 dpi, RGB on white, with our own engine (render_page_png);
2. the detector (an RT-DETRv2 checkpoint, e.g. Heron-101 fine-tuned on DocLayNet /train) gives regions;
3. our `feature_dump` gives the page's words and blocks; each of our lines (column-split, align_labels.lines_of)
   goes to the TIGHTEST region that holds >= 50% of its ink (so items inside a larger box stay items);
4. a region's lines become one block of its class, placed where its first line falls in OUR reading order;
   unclaimed lines stay in our own blocks as paragraphs; running heads and footers are dropped;
5. headings as `## `, list items as `- `, a table as the grid of OUR engine's table in the same place
   (`structured_markdown --json` geometry filters, text similarity ranks), else the grid the engine
   builds inside the region (`structured_markdown --region`), else the region's lines as paragraphs.
Writes OUT/<pdf name>.md and OUT/timings.tsv (name, ms, outcome), like extract_structured.py.
"""
import argparse, collections, contextlib, difflib, hashlib, html, io, json, math, os, pathlib, re, shutil, string, subprocess, sys, tempfile, time
import unicodedata

REPO = pathlib.Path(__file__).resolve().parents[3]
KIND = {"section_header": "heading", "title": "heading", "list_item": "list_item", "table": "table",
        "page_header": "furniture", "page_footer": "furniture"}

#: The strongest teacher (#641): Heron-ft ep03 (A) plus Egret-ft ep00 (B), per-class thresholds, union.
#: Each detector's boxes are post-processed at ENSEMBLE_POST, kept per class at ENSEMBLE_THRESHOLDS, then
#: unioned: every kept A box, plus every kept B box whose IoU with EVERY kept A box is below ENSEMBLE_IOU
#: (B fills A's misses; where they overlap, A wins). The thresholds are the /train-holdout choices of
#: holdout_thresholds.json -- tuned on DocLayNet /train pages no model trained on, not on the proxy -- and
#: they live in this ONE table, so a later tuning changes one place.
ENSEMBLE_POST = 0.05
ENSEMBLE_IOU = 0.5
#: A = Heron-ft ep03 (RT-DETRv2-R101), B = Egret-ft ep00 (D-FINE-X). `title` counts as `section_header`,
#: `page_header`/`page_footer` as furniture; `default` is everything else.
ENSEMBLE_THRESHOLDS = {
    "A": {"section_header": 0.25, "title": 0.25, "list_item": 0.25, "table": 0.75,
          "page_header": 0.20, "page_footer": 0.20, "default": 0.50},
    "B": {"section_header": 0.55, "title": 0.55, "list_item": 0.55, "table": 0.65,
          "page_header": 0.55, "page_footer": 0.55, "default": 0.50},
}


def ensemble_threshold(det: str, label: str) -> float:
    """The per-class threshold for one detector (`A` or `B`) and one DocLayNet class name."""
    table = ENSEMBLE_THRESHOLDS[det]
    return table.get(label, table["default"])


#: R-1: the best tier renders with OUR engine. Both `markdown_of` entries render page 1 at 96 dpi,
#: RGB on white, through the `render_page_png` example beside `structured_markdown` -- never through
#: mutool or PyMuPDF, whose pixels no pin holds and whose AGPL would ride into every commercial SDK
#: (see docs/LICENSE_POLICY.toml). The binary's path comes from the same place as `structured_markdown`'s:
#: the constructor, else the RENDER_PAGE_PNG environment variable, else this tree's release build,
#: else its debug build.
RENDER_ENV = "RENDER_PAGE_PNG"
#: The adapter renders the first page at 96 dpi. Passed explicitly rather than relying on the
#: binary's defaults, the way REGION_PAGE passes the region entry's page.
RENDER_PAGE = 0
RENDER_DPI = 96


def render_binary_path(explicit: str | None = None, repo=REPO) -> str:
    """The `render_page_png` binary to run: as configured, else this tree's build.

    An explicit constructor path wins, then RENDER_PAGE_PNG, then the release example beside
    `structured_markdown`, then the debug one (CARGO_TARGET_DIR honoured, as in run_local.py).
    A configured path is returned as given -- a miss fails loudly at the call, never by falling
    back to another renderer. With nothing configured and nothing built, the release path, so
    the failure names where the build goes.
    """
    if explicit:
        return str(explicit)
    if os.environ.get(RENDER_ENV):
        return os.environ[RENDER_ENV]
    target = pathlib.Path(os.environ.get("CARGO_TARGET_DIR", pathlib.Path(repo) / "target"))
    cands = [target / "release" / "examples" / "render_page_png",
             target / "debug" / "examples" / "render_page_png"]
    for cand in cands:
        if cand.is_file():
            return str(cand)
    return str(cands[0])


def render_argv(render_bin: str, pdf, out) -> list[str]:
    """The render invocation: the first page at 96 dpi, RGB on white.

    The one call every entry uses -- both `markdown_of` methods and the R-4 self-test render --
    so the pin guards exactly what the product runs.
    """
    return [render_bin, str(pdf), str(out),
            "--page", str(RENDER_PAGE), "--dpi", str(RENDER_DPI)]


#: R-4: the pixel-hash pin. The adapter self-test renders this committed page with the binary above
#: and compares the PNG's sha256 to this value: a renderer drift fails loudly. Re-pin by rendering
#: with the release binary and putting the new hash here, in the commit that changes the renderer,
#: saying what moved -- the way a re-rendered visual baseline declares itself. The fixture is
#: text-only in the standard fonts the renderer embeds, so the hash holds on every machine.
RENDER_PIN_PAGE = "fixtures/structured.pdf"
RENDER_PIN_SHA256 = "5a54736e0487f5272852fad071b9336a62ab518183cc198ea5efe13851b96320"


#: R5: a line of at most this many words may become a heading on a weak detector-A signal.
SHORT_HEADING_WORDS = 12


def iou(one: list[float], other: list[float]) -> float:
    """Intersection over union of two `[x0, y0, x1, y1]` boxes."""
    w = max(0.0, min(one[2], other[2]) - max(one[0], other[0]))
    h = max(0.0, min(one[3], other[3]) - max(one[1], other[1]))
    inter = w * h
    denom = max(0.0, one[2] - one[0]) * max(0.0, one[3] - one[1]) + \
        max(0.0, other[2] - other[0]) * max(0.0, other[3] - other[1]) - inter
    return inter / denom if denom > 0.0 else 0.0


def union_ensemble(kept_a: list[dict], kept_b: list[dict], iou_thr: float = ENSEMBLE_IOU) -> list[dict]:
    """Every kept A box, plus every kept B box overlapping no kept A box at `iou_thr` or more."""
    return list(kept_a) + [b for b in kept_b
                           if all(iou(b["bbox"], a["bbox"]) < iou_thr for a in kept_a)]


def to_pdf_box(bbox_image: list[float], W: float, H: float, page_width: float, page_height: float) -> list[float]:
    """One detector box (image px, top-left origin) in PDF user space (points, bottom-left origin)."""
    sx, sy = page_width / W, page_height / H
    return [bbox_image[0] * sx, page_height - bbox_image[3] * sy,
            bbox_image[2] * sx, page_height - bbox_image[1] * sy]


#: R-3: MEASUREMENT = PRODUCT. Box caches for a go/no-go are produced on CPU fp32 with R-1's
#: renderer. A GPU job (training, or a GPU box cache for speed) may stand in only after one check
#: on PARITY_PAGES pages from IDENTICAL PNGs: per class, the p95 of |CPU score - GPU score| over
#: same-class pairs matched greedily at PARITY_IOU must be under PARITY_TOL, else its boxes are
#: refused. Pure arithmetic, no torch: `--check-parity` runs it on two JSONL caches, one
#: `{"page": id, "boxes": [{"label", "score", "bbox"}]}` object per line, both bboxes in the same
#: image space.
PARITY_PAGES = 100
PARITY_TOL = 0.01
PARITY_IOU = 0.5


def _p95(values: list[float]) -> float:
    """The 95th percentile, nearest-rank: the value itself for a single pair."""
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]


def _match_page(cpu_boxes: list[dict], gpu_boxes: list[dict],
               iou_thr: float) -> tuple[list[tuple[str, float]], collections.Counter]:
    """Same-class pairs at `iou_thr` or more, greedy best-IoU-first, with their |score delta|.

    Returns the `(label, delta)` pairs and, per class, the boxes left without a pair on either
    side. Deterministic: ties break on the index, so one pair of caches always reads one way.
    """
    cands = []
    for i, a in enumerate(cpu_boxes):
        for j, b in enumerate(gpu_boxes):
            if a["label"] != b["label"]:
                continue
            v = iou(a["bbox"], b["bbox"])
            if v >= iou_thr:
                cands.append((v, i, j))
    cands.sort(reverse=True)
    used_a, used_b, deltas = set(), set(), []
    for _, i, j in cands:
        if i in used_a or j in used_b:
            continue
        used_a.add(i)
        used_b.add(j)
        deltas.append((cpu_boxes[i]["label"],
                       abs(float(cpu_boxes[i]["score"]) - float(gpu_boxes[j]["score"]))))
    lone: collections.Counter = collections.Counter()
    for i, a in enumerate(cpu_boxes):
        if i not in used_a:
            lone[a["label"]] += 1
    for j, b in enumerate(gpu_boxes):
        if j not in used_b:
            lone[b["label"]] += 1
    return deltas, lone


def box_cache_parity(cpu_pages: list[dict], gpu_pages: list[dict], *, tol: float = PARITY_TOL,
                     min_pages: int = PARITY_PAGES,
                     iou_thr: float = PARITY_IOU) -> tuple[bool, dict]:
    """Whether the GPU box cache may stand in for the CPU one (R-3).

    Pages pair by their `page` id (by line number where it is absent); the two caches must hold
    the same pages, at least `min_pages` of them. Returns `(ok, report)`, where `report` maps
    each class seen on either side to its p95 |score delta| -- or None where the class has boxes
    but no matched pair anywhere. Refused (ok False) when the pages differ, when there are too
    few of them, when a class has no pair, or when a p95 reaches `tol`.
    """
    def keyed(pages):
        return {p.get("page", f"line-{i}"): p.get("boxes") or []
                for i, p in enumerate(pages)}

    cpu, gpu = keyed(cpu_pages), keyed(gpu_pages)
    if set(cpu) != set(gpu):
        return False, {"refused": "the two caches hold different pages",
                       "cpu_only": sorted(set(cpu) - set(gpu))[:5],
                       "gpu_only": sorted(set(gpu) - set(cpu))[:5]}
    if len(cpu) < min_pages:
        return False, {"refused": f"only {len(cpu)} page(s), {min_pages} required"}
    deltas: dict[str, list[float]] = collections.defaultdict(list)
    lone: collections.Counter = collections.Counter()
    for key in cpu:
        pairs, left = _match_page(cpu[key], gpu[key], iou_thr)
        for label, delta in pairs:
            deltas[label].append(delta)
        lone.update(left)
    report: dict = {}
    ok = True
    for label in sorted(set(deltas) | set(lone)):
        if label not in deltas:
            report[label] = None
            ok = False
        else:
            report[label] = _p95(deltas[label])
            if report[label] >= tol:
                ok = False
    return ok, report


def check_parity_files(cpu_path: str, gpu_path: str, tol: float = PARITY_TOL,
                       min_pages: int = PARITY_PAGES) -> int:
    """`--check-parity` on two JSONL box caches: per-class p95 on stdout, 0 or 1."""
    def read(path):
        with open(path, encoding="utf-8") as handle:
            return [json.loads(line) for line in handle.read().splitlines() if line.strip()]

    ok, report = box_cache_parity(read(cpu_path), read(gpu_path), tol=tol, min_pages=min_pages)
    if "refused" in report:
        print(f"REFUSED {report['refused']}")
        return 1
    for label in sorted(report):
        p95 = report[label]
        print(f"{label}: p95 {'no matched pair' if p95 is None else f'{p95:.4f}'} (tol {tol})")
    print("OK the GPU cache may stand in" if ok else "REFUSED a class is over tolerance")
    return 0 if ok else 1


def lines_module(repo):
    sys.path.insert(0, str(repo / "scripts" / "extraction" / "learned"))
    import align_labels
    return align_labels


#: Words whose own characters are Markdown or HTML syntax stay unmarked, as in the engine's
#: writer: marking them would need escapes the rest of this adapter does not write.
_SYNTAX_CHARS = frozenset("*_<>`[]\\~")


def _marked(run: str, bold: bool, italic: bool) -> str:
    """One style run with the engine's markers (`**`, `*`, `***`); plain where it must stay readable."""
    if not run.strip() or not (bold or italic) or any(c in _SYNTAX_CHARS for c in run):
        return run
    mark = "***" if bold and italic else "**" if bold else "*"
    return mark + run + mark


def styled_line(piece: list[dict]) -> str:
    """One line's words with consecutive bold/italic runs marked, as the engine's writer marks them.

    Sup/sub and underline are left out: `feature_dump` words carry `bold`/`italic` flags and no
    raised, lowered or underline flag, and the adapter will not guess those from geometry.
    """
    parts, run, setting = [], [], (False, False)
    for w in piece + [None]:
        key = (bool(w.get("bold")), bool(w.get("italic"))) if w is not None else None
        if key != setting:
            parts.append(_marked(" ".join(run), *setting))
            run, setting = [], key
        if w is not None:
            run.append(w["text"])
    return " ".join(p for p in parts if p)


_STYLE_TAG = re.compile(r"</?(sup|sub|u)>", re.I)
_MD_TABLE = re.compile(r"<table.*?</table>", re.I | re.S)


def engine_style_tokens(engine_markdown: str) -> list[tuple[str, list[tuple]]]:
    """The engine markdown's words outside tables, each with a per-character style.

    (bold, italic, sup, sub, u), read from its own markers (`***`/`**`/`*`, `<sup>`, `<sub>`,
    `<u>`). A heading line's bold/italic read as None: the `## ` carries the weight there, so the
    engine's heading bold must never decide a word's.
    """
    text = _MD_TABLE.sub("\n", engine_markdown or "")
    toks: list[tuple[str, list[tuple]]] = []
    for line in text.split("\n"):
        st = line.strip()
        if st.startswith("|"):
            continue
        heading = st.startswith("#")
        st = re.sub(r"^(#+ |- |\d+\. )", "", st)
        bold = italic = False
        tags = {"sup": False, "sub": False, "u": False}
        cur, sty = [], []
        i = 0

        def flush():
            if cur:
                toks.append(("".join(cur), sty[:]))
                cur.clear()
                sty.clear()

        while i < len(st):
            m = _STYLE_TAG.match(st, i)
            if m:
                tags[m.group(1).lower()] = not m.group(0).startswith("</")
                i = m.end()
                continue
            if st[i] == "<" and re.match(r"</?br\s*/?>", st[i:], re.I):
                flush()
                i = st.index(">", i) + 1
                continue
            if st[i] == "*":
                j = i
                while j < len(st) and st[j] == "*":
                    j += 1
                n = j - i
                if n >= 3:
                    bold, italic = not bold, not italic
                elif n == 2:
                    bold = not bold
                else:
                    italic = not italic
                i = j
                continue
            if st[i].isspace():
                flush()
                i += 1
                continue
            cur.append(st[i])
            sty.append((None if heading else bold, None if heading else italic,
                        tags["sup"], tags["sub"], tags["u"]))
            i += 1
        flush()
    return toks


def engine_styles_for(words: list[dict], engine_markdown: str) -> dict:
    """id(word) -> per-character style, for every feature_dump word aligned to an engine word.

    Aligned by difflib on the word texts (autojunk off), same text 1:1; where consecutive
    feature_dump words concatenate to ONE engine token they take that token's characters in order,
    and the later word is glued to the earlier (no space) unless it is wholly raised or lowered.
    """
    eng = engine_style_tokens(engine_markdown)
    if not eng or not words:
        return {}
    a = [w["text"] for w in words]
    b = [t for t, _ in eng]
    out = {}
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                if len(eng[j1 + k][1]) == len(a[i1 + k]):
                    out[id(words[i1 + k])] = eng[j1 + k][1]
        elif tag == "replace":
            # A raised or lowered mark is often its own feature_dump word ("documents." "2") where
            # the engine writes one token ("documents.<sup>2</sup>"): consecutive words whose
            # concatenation is one engine token take that token's characters in order.
            i, j = i1, j1
            while i < i2 and j < j2:
                tok, sty = eng[j]
                k, acc = i, ""
                while k < i2 and len(acc) < len(tok) and tok.startswith(acc + a[k]):
                    acc += a[k]
                    k += 1
                if acc == tok and k > i:
                    at = 0
                    for m in range(i, k):
                        out[id(words[m])] = sty[at:at + len(a[m])]
                        at += len(a[m])
                        raised = all(x[2] or x[3] for x in sty[at - len(a[m]):at])
                        if m > i and not raised:
                            out[("glue", id(words[m]))] = True
                    i, j = k, j + 1
                elif len(a[i]) < len(tok) and tok.startswith(a[i]):
                    i += 1
                else:
                    j += 1
    return out


def _tagged(text: str, styles: list[tuple]) -> str:
    """One word with its sup/sub/u character runs wrapped in tags."""
    parts, run, key = [], [], None
    for ch, st in list(zip(text, styles)) + [(None, None)]:
        k = st[2:] if st is not None else None
        if k != key and run:
            body = "".join(run)
            sup, sub_, u = key
            if sup:
                body = f"<sup>{body}</sup>"
            elif sub_:
                body = f"<sub>{body}</sub>"
            if u:
                body = f"<u>{body}</u>"
            parts.append(body)
            run = []
        key = k
        if ch is not None:
            run.append(ch)
    return "".join(parts)


def styled_line_engine(piece: list[dict], styles: dict) -> str:
    """`styled_line` where a word the engine wrote takes the engine's sup/sub/u per character.

    Bold and italic stay feature_dump's (taking the engine's lost bold on lines the engine writes
    as headings); other words keep their flags untouched. A glued word joins the run's last word
    with no space, across a style boundary where it must (`**Withdrawn**:`).
    """
    parts, run, plain_run, setting = [], [], [], (False, False)

    def close():
        plain = " ".join(plain_run)
        tagged = " ".join(run)
        if not plain.strip():
            return
        if not any(setting) or any(c in _SYNTAX_CHARS for c in plain):
            parts.append(tagged)
        else:
            mark = "***" if all(setting) else "**" if setting[0] else "*"
            parts.append(mark + tagged + mark)

    for w in piece + [None]:
        key = (bool(w.get("bold")), bool(w.get("italic"))) if w is not None else None
        if key != setting:
            close()
            run, plain_run, setting = [], [], key
        if w is not None:
            st = styles.get(id(w))
            piece_text = (_tagged(w["text"], st) if st is not None and not any(c in _SYNTAX_CHARS for c in w["text"])
                          else w["text"])
            if styles.get(("glue", id(w))) and run:
                run[-1] += piece_text
                plain_run[-1] += w["text"]
            elif styles.get(("glue", id(w))) and parts:
                run.append("\x00" + piece_text)
                plain_run.append(w["text"])
            else:
                run.append(piece_text)
                plain_run.append(w["text"])
    return re.sub(r" ?\x00", "", " ".join(p for p in parts if p))


_RUN_SEAM = re.compile(r"(?<=[^\s*])(\*{1,3}) \1(?=[^\s*])")


def join_runs(text: str, enabled: bool = True) -> str:
    """One style run across a line break: where two lines of one block carry the same marker.

    `**a** **b**` becomes `**a b**`, as the engine writes a wrapped bold sentence; likewise
    `</u> <u>` becomes one space. Off returns the text unchanged.
    """
    if not enabled:
        return text
    return _RUN_SEAM.sub(" ", text).replace("</u> <u>", " ")


#: A list item's own bullet glyph: lists.rs BULLET_CHARS, which the `- ` replaces.
_BULLETS = "\u2022\u2023\u2043\u2219\u00b7\u25aa\u25ab\u25a0\u25a1\u25cf\u25cb\u25e6\u25c6\u25c7\u2756\u2713\u2714-\u2013\u2014"
_LIGATURES = frozenset(chr(c) for c in list(range(0xFB00, 0xFB07)) + list(range(0xFB13, 0xFB18)))


def fold_ligatures(text: str, enabled: bool = True) -> str:
    """Ligature glyphs (U+FB00-FB06, Armenian U+FB13-FB17) as their letters, NFKC as the engine."""
    if not enabled or not any(ch in _LIGATURES for ch in text):
        return text
    return "".join(unicodedata.normalize("NFKC", ch) if ch in _LIGATURES else ch for ch in text)


def hyphen_wrap(prev: str, nxt: str) -> bool:
    """The engine's `is_hyphen_wrap_candidate` (text.rs): >= 3 letters, `-`, next line >= 3 ASCII lowercase."""
    if not prev.endswith("-") or len(prev) < 2 or not prev[-2].isalpha():
        return False
    n = 0
    for ch in reversed(prev[:-1]):
        if not ch.isalpha():
            break
        n += 1
    if n < 3:
        return False
    m = 0
    for ch in nxt.lstrip():
        if not ("a" <= ch <= "z"):
            break
        m += 1
    return m >= 3


def join_lines(lines: list[tuple[str, str]], enabled: bool = True) -> str:
    """A block's lines (plain, shown) joined by a space -- or, across an end-of-line hyphen wrap.

    Whose shown text ends in the hyphen and whose next line opens in lowercase, the hyphen is
    dropped. Off joins by a space.
    """
    out = ""
    prev_plain = None
    for plain, shown in lines:
        if prev_plain is not None and enabled and hyphen_wrap(prev_plain, plain) and out.endswith("-") \
                and shown[:1].islower():
            out = out[:-1] + shown
        else:
            out = shown if prev_plain is None else out + " " + shown
        prev_plain = plain
    return out


def strip_bullet(text: str, enabled: bool = True) -> str:
    """A list item's own bullet glyph (lists.rs BULLET_CHARS) is the `- `, not part of its text."""
    t = text.lstrip()
    if enabled and len(t) > 1 and t[0] in _BULLETS and t[1] == " ":
        return t[2:].lstrip()
    return text


def _at_page_edge(bbox: list[float], height: float, band: float) -> bool:
    """Whether the box's centre lies in the outer band (top or bottom `band` of the page height)."""
    if height <= 0.0:
        return True
    cy = (bbox[1] + bbox[3]) / 2.0
    return cy <= band * height or cy >= height - band * height


#: R6b: the detector-A labels duelling over a furniture-claimed line.
_DUEL_FURNITURE = frozenset(("page_header", "page_footer"))
_DUEL_HEADING = frozenset(("section_header", "title"))


def _furniture_wins(piece: list[dict], ink: float, boxes: list[dict], al) -> bool:
    """Whether detector A's best furniture score beats its best heading score on this line.

    Both sides range over A's boxes claiming the line (>= 50% of its ink); a side no box claims
    scores 0.0, so furniture drops the line only by scoring strictly higher -- position plays
    no part.
    """
    furn = head = 0.0
    for b in boxes:
        if b["label"] not in _DUEL_FURNITURE and b["label"] not in _DUEL_HEADING:
            continue
        if sum(al.overlap(w["bbox"], b["bbox"]) for w in piece) / ink < 0.5:
            continue
        if b["label"] in _DUEL_FURNITURE:
            furn = max(furn, float(b["score"]))
        else:
            head = max(head, float(b["score"]))
    return furn > head


def _norm(word: str) -> str:
    return word.strip(string.punctuation).lower()


def _grid_words(grid: str) -> set[str]:
    """The grid's words, tags gone, for the leftover test: a region line the grid holds stays in it."""
    return {_norm(w) for w in re.sub(r"<[^>]*>", " ", grid).split() if _norm(w)}


def _safe_para(text: str) -> str:
    """A leftover line as a paragraph: a bare rule line is escaped so it stays words, not structure."""
    collapsed = " ".join(text.split())
    if re.fullmatch(r"(---+|\*+)", collapsed):
        return "\\" + collapsed
    return collapsed


def _first_row_cells(grid: str) -> int:
    """The grid's column count, read off its first row; 0 where the grid does not parse."""
    found = re.search(r"<tr[^>]*>(.*?)</tr>", grid, re.S | re.I)
    return len(re.findall(r"<t[dh][\s>/]", found.group(1), re.I)) if found else 0


def _with_title_band(grid: str, lines: list[str]) -> str | None:
    """The grid with one full-width spanning first row per line, or None where it does not parse."""
    cols = _first_row_cells(grid)
    if cols <= 0 or "<table" not in grid.lower():
        return None
    band = "".join(f"<tr><td colspan=\"{cols}\">{html.escape(l, quote=False)}</td></tr>" for l in lines)
    return re.sub(r"<table[^>]*>", lambda m: m.group(0) + band, grid, count=1, flags=re.I)


def leftover_split(lines, grid: str, as_rows: bool = False):
    """A taken grid's leftover lines: the region's lines the grid does not hold, in reading order.

    A line is held when every one of its words is among the grid's (case- and punctuation-blind);
    the rest become paragraphs before the table (lines above the held ones) or after it (lines
    below, lines overlapping the held span, and every line where none is held). With `as_rows`,
    the lines above join the table as full-width spanning first rows (a title band inside it)
    and only the lines below stay paragraphs. Returns the before/after paragraphs and the grid.
    """
    vocab = _grid_words(grid)

    def held(line) -> bool:
        words = [_norm(w) for w in line[1].split()]
        words = [w for w in words if w]
        return bool(words) and all(w in vocab for w in words)

    flags = [held(line) for line in lines]
    left = [line for line, is_held in zip(lines, flags) if not is_held]
    if not left:
        return [], [], grid
    kept = [line for line, is_held in zip(lines, flags) if is_held]
    if kept:
        top = max(line[3][3] for line in kept)
        before = [line for line in left if line[3][1] >= top]
        after = [line for line in left if line[3][1] < top]
    else:
        before, after = [], left
    if as_rows and before:
        banded = _with_title_band(grid, [" ".join(line[1].split()) for line in before])
        if banded is not None:
            return [], [_safe_para(line[2]) for line in after], banded
    return [_safe_para(line[2]) for line in before], [_safe_para(line[2]) for line in after], grid


def markdown(page, regions, al=None, table_html=None, table_grid=None, region_table=None,
             engine_markdown=None, leftover_rows=False, short_regions=None, furniture_band=None,
             furniture_duel=False, duel_boxes=None, orphan_tables=False, engine_tags=False,
             merge_style_runs=False, engine_text=False):
    """Our lines onto the regions (tightest region holding >= 50% of a line's ink), as markdown.

    ORDER IS OURS. A block -- a region's lines, or the lines no region claims -- is placed where its first
    line falls in OUR reading order (feature_dump's blocks, line by line), not by a geometric rule of its
    own: the detector says WHAT a run of lines is, the engine says in which order a reader meets it. (A
    half-page column sort cost the annotated order 0.776 -> 0.763 on the rig, round 5.)

    TABLES GET A GRID. `table_html` maps a region's index to table HTML a structure model made for it;
    `table_grid(text, bbox)` may return HTML for a table region from OUR engine's own table in the same
    place (geometry filters, text ranks). Where neither matches, `region_table(bbox)` may return the grid
    the engine builds inside the region; where that too returns nothing the region's lines are paragraphs,
    not one cell -- a one-cell table scores TEDS 0 by construction and hides the text from the text axes.
    Without a region entry the old one-cell fallback stands (a missing engine binary).

    A PAGE THE TEACHER CANNOT READ IS THE ENGINE'S PAGE. Where `feature_dump` gives the page no words
    (no text layer -- an OCR scan), there are no lines to place and the adapter returns `engine_markdown`
    (the engine's own markdown for the page, already run) verbatim; without it such a page reads empty.

    INLINE STYLE IS CARRIED. A run of consecutive words in one line with `bold`/`italic` set is
    written with the engine's markers (`**`, `*`, `***`); headings stay plain (the `## ` already
    carries the weight) and table cells are untouched (an engine grid styles itself).

    LEFTOVER LINES ARE KEPT. When a table region takes an engine grid (matched or region-built),
    the region's lines the grid does not hold -- a label line, a unit line above the grid -- are
    written, in reading order, as paragraphs before the table (lines above the grid) or after it
    (lines below), instead of being dropped. With `leftover_rows`, the lines above join the table
    as full-width spanning first rows and only the lines below stay paragraphs. A structure-model
    grid and the one-cell fallback keep their lines as before.

    SHORT HEADINGS AT A LOWER BAR. `short_regions` are detector-A section_header/title boxes below
    A's class threshold but at/above T_SHORT. A line of at most SHORT_HEADING_WORDS words that one
    claims becomes a heading -- unless a kept box claims it more tightly (tightest wins, as above).
    Longer lines never see them.

    FURNITURE ONLY AT THE PAGE EDGE. With `furniture_band`, a furniture region drops its lines only
    when its centre lies in the outer band of the page (within BAND of the top or bottom edge, as a
    fraction of the page height); elsewhere it is ignored -- its lines take their next-tightest box,
    else stay paragraphs.

    THE DUEL (R6b). With `furniture_duel`, a line whose tightest box is furniture is dropped only
    where detector A's best page_header/page_footer score on that line beats its best
    section_header/title score (`duel_boxes`, A's boxes in PDF points); otherwise the line takes
    its next-tightest non-furniture box, else stays a paragraph. Position plays no part.

    ORPHAN ENGINE TABLES (R7). With `orphan_tables`, each engine table no region took (cell
    geometry from `structured_markdown --json`, as `grid_matcher` reads it; no geometry, no
    orphans) is written as the engine's table at the reading position of the first line it
    covers. Covered is >= 50% of a line's ink inside the table's cell-union box padded by 2 pt;
    covered lines are removed from their blocks, except heading and furniture lines (a heading
    stays a heading; furniture stays dropped). The table is skipped where any covered line
    belongs to a table region that has a grid, where at least half the covered lines sit in
    heading or list_item regions, or where any covered line sits in a list_item region.

    ENGINE TAGS (R8). With `engine_tags`, the engine markdown's words outside tables are aligned
    to feature_dump's words (difflib on the word texts, autojunk off) and an aligned word of the
    same text takes the engine's `<sup>`, `<sub>` and `<u>` per character; where consecutive words
    concatenate to one engine token they take its characters in order, the later glued (no space)
    unless wholly raised or lowered. Bold and italic stay feature_dump's, and words with
    Markdown/HTML syntax characters stay unmarked.

    ONE RUN ACROSS A LINE BREAK (R9). With `merge_style_runs`, the seam where one line ends and
    the next starts with the same marker (`**`, `*`, `***`) is one space, and likewise `</u> <u>`;
    headings stay plain.

    TEXT AS THE ENGINE WRITES IT (R10). With `engine_text`, ligature code points U+FB00-FB06 and
    U+FB13-FB17 are folded by NFKC; within a block, a line ending in a hyphen wrap joins the next
    line without the hyphen (the engine's `is_hyphen_wrap_candidate`); and a list item's leading
    bullet glyph is dropped (the `- ` is the bullet).
    """
    al = al or lines_module(REPO)
    table_html = table_html or {}
    words = page.get("words") or []
    if engine_markdown is not None and not words:
        return engine_markdown
    # Ignored, not removed: dropping out-of-band furniture here would shift every later region's
    # index, and `table_html` keys the regions as the caller numbered them.
    pool, n_kept = list(regions) + list(short_regions or []), len(regions)
    styles = engine_styles_for(words, engine_markdown) if (engine_tags and engine_markdown) else None
    if furniture_band is not None:
        height = page.get("height") or 0.0
        ignored = {ri for ri, r in enumerate(regions)
                   if r["kind"] == "furniture" and not _at_page_edge(r["bbox"], height, furniture_band)}
    else:
        ignored = set()
    items, loose = collections.defaultdict(list), []
    seq = 0
    for b in page["blocks"]:
        for piece in al.lines_of(b, words):
            if not piece:
                continue
            ink = sum(al.area(w["bbox"]) for w in piece) or 1.0
            cands = sorted((al.area(r["bbox"]), -r["score"], ri) for ri, r in enumerate(pool)
                           if sum(al.overlap(w["bbox"], r["bbox"]) for w in piece) / ink >= 0.5
                           and ri not in ignored
                           and (ri < n_kept or len(piece) <= SHORT_HEADING_WORDS))
            pick = cands[0][2] if cands else None
            if pick is not None and furniture_duel and pool[pick]["kind"] == "furniture" \
                    and not _furniture_wins(piece, ink, duel_boxes or [], al):
                rest = [c for c in cands if pool[c[2]]["kind"] != "furniture"]
                pick = rest[0][2] if rest else None
            plain = " ".join(w["text"] for w in piece)
            box = [min(w["bbox"][0] for w in piece), min(w["bbox"][1] for w in piece),
                   max(w["bbox"][2] for w in piece), max(w["bbox"][3] for w in piece)]
            (items[pick] if pick is not None else loose).append(
                (seq, plain, styled_line_engine(piece, styles) if styles is not None else styled_line(piece),
                 box, b["i"]))
            seq += 1
    blocks = []
    decided = {}
    for ri, ls in items.items():
        text = " ".join(l[1] for l in ls)
        kind = pool[ri]["kind"]
        grid = table_html.get(ri)
        engine_grid = False
        if grid is None and kind == "table" and table_grid is not None:
            grid = table_grid(text, pool[ri]["bbox"])
            engine_grid = grid is not None
        if grid is None and kind == "table" and region_table is not None:
            built = region_table(pool[ri]["bbox"])
            grid = built if built and built.strip() else None
            engine_grid = grid is not None
            if grid is None:
                kind = "paragraph"
        decided[ri] = (kind, grid, engine_grid)
    if orphan_tables and table_grid is not None and hasattr(table_grid, "orphans"):
        for tbl_html, tb in table_grid.orphans():
            pad = [tb[0] - 2, tb[1] - 2, tb[2] + 2, tb[3] + 2]

            def inside(box):
                area = _area(box)
                return area > 0.0 and _overlap(box, pad) / area >= 0.5

            hits = [(ri, l) for ri, ls in items.items() for l in ls if inside(l[3])]
            if any(decided[ri][0] == "table" and decided[ri][1] is not None for ri, _ in hits):
                continue
            lhits = [l for l in loose if inside(l[3])]
            kinds = collections.Counter(decided[ri][0] for ri, _ in hits)
            kinds["loose"] = len(lhits)
            n_all = sum(kinds.values())
            if 2 * (kinds.get("heading", 0) + kinds.get("list_item", 0)) >= n_all:
                continue
            if kinds.get("list_item", 0) > 0:
                continue
            hits = [(ri, l) for ri, l in hits if decided[ri][0] not in ("heading", "furniture")]
            if not hits and not lhits:
                continue
            at = min([l[0] for _, l in hits] + [l[0] for l in lhits])
            gone = {id(l) for _, l in hits} | {id(l) for l in lhits}
            for ri in list(items):
                items[ri] = [l for l in items[ri] if id(l) not in gone]
                if not items[ri]:
                    del items[ri]
            loose[:] = [l for l in loose if id(l) not in gone]
            blocks.append((at, "table", "table", tbl_html, [], []))
    for ri, ls in items.items():
        kind, grid, engine_grid = decided[ri]
        shown = (join_lines([(l[1], l[1]) for l in ls], engine_text) if kind == "heading"
                 else join_runs(join_lines([(l[1], l[2]) for l in ls], engine_text), merge_style_runs))
        if kind == "list_item":
            shown = strip_bullet(shown, engine_text)
        shown = fold_ligatures(shown, engine_text)
        befores, afters = [], []
        if kind == "table" and grid is not None and engine_grid:
            befores, afters, grid = leftover_split(ls, grid, leftover_rows)
        blocks.append((min(l[0] for l in ls), kind, shown, grid, befores, afters))
    by_block = collections.defaultdict(list)
    for at, plain_l, styled, _, bi in loose:
        by_block[bi].append((at, styled, plain_l))
    for ls in by_block.values():
        blocks.append((min(l[0] for l in ls), "paragraph",
                       fold_ligatures(join_runs(join_lines([(l[2], l[1]) for l in ls], engine_text),
                                                merge_style_runs), engine_text), None, [], []))
    out = []
    for _, k, t, grid, befores, afters in sorted(blocks, key=lambda b: b[0]):
        for para in befores:
            para = " ".join(para.split())
            if para:
                out.append(para)
        t = " ".join(t.split())
        if t and k != "furniture":
            out.append("## " + t if k == "heading" else "- " + t if k == "list_item" else
                       (grid or f"<table><tr><td>{t}</td></tr></table>") if k == "table" else t)
        for para in afters:
            para = " ".join(para.split())
            if para:
                out.append(para)
    return "\n\n".join(out)


def bench_module(repo):
    sys.path.insert(0, str(repo / "scripts" / "ci" / "loop"))
    sys.path.insert(0, str(repo / "scripts" / "extraction"))
    import structured_axes, structured_bench
    return structured_bench, structured_axes


#: How alike a teacher table's text and an engine table's text must be before the engine's grid is used:
#: below it the two found different tables, and a grid for other cells would be a wrong structure.
GRID_FLOOR = 0.5

#: Match by position before matching by text: an engine table is the region's when at least
#: POSITION_TABLE of its cells lie inside, a cell counting when POSITION_CELLS of its own area does.
#: (m124b: the engine often HAS the table while the text scores under the floor -- a swallowed label
#: line, drifting cell order -- so the page falls back to one cell and TEDS collapses.)
POSITION_CELLS = 0.5
POSITION_TABLE = 0.5

#: The page the region entry reads, in the flag's own counting: the adapter reads the first page
#: (render_page_png's page 0; feature_dump's first line) and `structured_markdown --region --page`
#: counts from zero, so the flag gets 0 today. Passed explicitly rather than relying on the flag's default.
REGION_PAGE = 0


def _area(box: list[float]) -> float:
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def _overlap(one: list[float], other: list[float]) -> float:
    return (max(0.0, min(one[2], other[2]) - max(one[0], other[0]))
            * max(0.0, min(one[3], other[3]) - max(one[1], other[1])))


def engine_tables_of(engine_json: str | dict | None, page: int = 0) -> list[list[list[float]]]:
    """The engine's tables as their cells' boxes, from `structured_markdown --json` on one page.

    A table is a run of adjacent `tableCell` blocks in row-major order on one page -- the writer's own
    `table_run` rule, so the runs line up with the markdown's tables in order. Unparseable input is no
    geometry (the text matcher still gets its say); only the adapter's page counts.
    """
    if not engine_json:
        return []
    try:
        payload = json.loads(engine_json) if isinstance(engine_json, str) else engine_json
        blocks = payload.get("blocks") or []
    except (ValueError, AttributeError):
        return []
    tables: list[list[list[float]]] = []
    cells: list[list[float]] = []
    last: tuple[int, int] | None = None

    def flush() -> None:
        if cells:
            tables.append(cells[:])
            cells.clear()

    for b in blocks:
        if not isinstance(b, dict) or b.get("kind") != "tableCell" or b.get("page", 0) != page:
            flush()
            last = None
            continue
        try:
            at = (int(b["row"]), int(b["column"]))
            box = [float(v) for v in b["bbox"]]
            if len(box) != 4:
                raise ValueError("bbox")
        except (KeyError, TypeError, ValueError):
            flush()
            last = None
            continue
        if last is None or at <= last:
            flush()
        cells.append(box)
        last = at
    flush()
    return tables


def grid_matcher(engine_markdown: str, repo=REPO, engine_json: str | dict | None = None):
    """`table_grid(text, bbox)` over OUR engine's tables on the page: position filters, text ranks.

    The HTML of the engine table most like `text` among those whose cells lie inside `bbox`, each
    table given out once -- and only where no table overlaps that way, the HTML of the table whose
    text is most like `text` (at least GRID_FLOOR). None when nothing matches. Without `engine_json`,
    or where its runs do not line up with the markdown's tables in order, the geometry half is off
    and every match is by text. The returned callable exposes the tables no region took as
    `orphans()`, each as (html, union bbox of its cells), for R7.
    """
    sb, sa = bench_module(pathlib.Path(repo))
    tables = [b for b in sb.blocks_of(engine_markdown) if b["kind"] == "table" and b.get("html")]
    geos = engine_tables_of(engine_json)
    if len(geos) != len(tables):
        geos = []
    used = set()

    def table_grid(text, bbox=None):
        if bbox is not None and geos:
            eligible = []
            for i, cells in enumerate(geos):
                if i in used or not cells:
                    continue
                inside = sum(1 for c in cells
                             if _area(c) > 0.0 and _overlap(c, bbox) / _area(c) >= POSITION_CELLS)
                score = inside / len(cells)
                if score < POSITION_TABLE:
                    continue
                eligible.append((sa.text_similarity(text, tables[i]["text"]), score, inside, i))
            if eligible:
                # POSITION FILTERS, TEXT RANKS: several engine tables can lie inside one region (a
                # numbers fragment beside the text grid); the most similar text wins, position only
                # breaking ties. A single candidate is unchanged.
                pick = max(eligible, key=lambda e: e[:3])[3]
                used.add(pick)
                return " ".join(tables[pick]["html"].split())
        best = max(((sa.text_similarity(text, t["text"]), i) for i, t in enumerate(tables) if i not in used),
                   default=None)
        if best is None or best[0] < GRID_FLOOR:
            return None
        used.add(best[1])
        return " ".join(tables[best[1]]["html"].split())

    def orphans():
        """The engine tables no region took, as (html, union bbox of their cells)."""
        out = []
        for i, cells in enumerate(geos):
            if i in used or not cells:
                continue
            box = [min(c[0] for c in cells), min(c[1] for c in cells),
                   max(c[2] for c in cells), max(c[3] for c in cells)]
            out.append((" ".join(tables[i]["html"].split()), box))
        return out
    table_grid.orphans = orphans
    return table_grid


def region_caller(binary: str, pdf: str, page: int = REGION_PAGE):
    """`region_table(bbox)` through `structured_markdown --region`: the engine's grid inside one box.

    The box goes as the adapter holds it (PDF points, y up, NO padding -- the entry pads by half a line
    height itself) with the page the adapter read. Empty output (fewer than two lines in the box, or no
    grid from the decoder) and any failure read as None: the region's lines become paragraphs.
    """
    def region_table(bbox) -> str | None:
        spec = ",".join(repr(float(v)) for v in bbox)
        try:
            out = subprocess.run([binary, "--region", spec, "--page", str(page), pdf],
                                 check=True, capture_output=True, text=True).stdout
        except (subprocess.CalledProcessError, OSError):
            return None
        return out if out.strip() else None
    return region_table


class Teacher:
    """The model loaded once; `markdown_of(pdf)` for one page, as extract_structured.py calls it.

    `detector`, when given, is the detector as an injectable callable (page image -> boxes, each
    `{"label", "score", "bbox"}` in image px); the self-test's fakes use it and no weights load.
    `render_page_png` is the render binary's path (`render_binary_path` resolves None).
    """

    def __init__(self, model_dir: str | None = None, feature_dump: str | None = None, thr: float = 0.3,
                 device: str = "cpu", repo=REPO, structured_markdown=None, detector=None,
                 leftover_rows: bool = False, furniture_band: float | None = None,
                 render_page_png: str | None = None):
        self.repo = pathlib.Path(repo)
        self.structured_markdown = structured_markdown
        self.render_page_png = render_page_png
        self.thr, self.device, self.feature_dump = thr, device, feature_dump
        self.leftover_rows = leftover_rows
        self.furniture_band = furniture_band
        self.al = lines_module(pathlib.Path(repo))
        self.detector = detector
        if detector is not None:
            self.torch = self.proc = self.model = self.lab = None
            return
        import torch
        from transformers import AutoImageProcessor, RTDetrV2ForObjectDetection
        self.torch = torch
        self.proc = AutoImageProcessor.from_pretrained(model_dir)
        self.model = RTDetrV2ForObjectDetection.from_pretrained(model_dir).to(device).eval()
        self.lab = self.model.config.id2label

    def markdown_of_page(self, page: dict, image=None, table_grid=None, region_table=None,
                         engine_markdown=None, leftover_rows=None, furniture_band=None) -> str:
        """The page's markdown from one detector's boxes: `markdown_of` without the rendering."""
        if leftover_rows is None:
            leftover_rows = self.leftover_rows
        if furniture_band is None:
            furniture_band = self.furniture_band
        W, H = (image.size if image is not None else (page["width"], page["height"]))
        if self.detector is not None:
            boxes = self.detector(image)
            regions = [{"kind": KIND.get(b["label"], "paragraph"), "score": float(b["score"]),
                        "bbox": to_pdf_box(b["bbox"], W, H, page["width"], page["height"])}
                       for b in boxes if float(b["score"]) >= self.thr]
            return markdown(page, regions, self.al, table_grid=table_grid, region_table=region_table,
                            engine_markdown=engine_markdown, leftover_rows=leftover_rows,
                            furniture_band=furniture_band)
        with self.torch.no_grad():
            o = self.model(pixel_values=self.proc(images=image, return_tensors="pt")["pixel_values"].to(self.device))
            o.logits = o.logits.float().cpu(); o.pred_boxes = o.pred_boxes.float().cpu()
            r = self.proc.post_process_object_detection(o, target_sizes=[(H, W)], threshold=self.thr)[0]
        regions = [{"kind": KIND.get(self.lab[int(l)], "paragraph"), "score": float(s),
                    "bbox": to_pdf_box(b, W, H, page["width"], page["height"])}
                   for s, l, b in zip(r["scores"], r["labels"], r["boxes"].tolist())]
        return markdown(page, regions, self.al, table_grid=table_grid, region_table=region_table,
                        engine_markdown=engine_markdown, leftover_rows=leftover_rows,
                        furniture_band=furniture_band)

    def markdown_of(self, pdf) -> str:
        from PIL import Image
        pdf = pathlib.Path(pdf)
        render_bin = render_binary_path(self.render_page_png, self.repo)
        with tempfile.TemporaryDirectory() as tmp:
            png = pathlib.Path(tmp) / "p.png"
            subprocess.run(render_argv(render_bin, pdf, png), check=True, capture_output=True)
            im = Image.open(png).convert("RGB")
            dump = subprocess.run([self.feature_dump, str(pdf), "--page-id", pdf.name], check=True,
                                  capture_output=True, text=True).stdout
            page = json.loads(dump.strip().split("\n")[0])
        table_grid = region_table = engine_markdown = None
        if self.structured_markdown:
            ours = subprocess.run([self.structured_markdown, str(pdf)], check=True, capture_output=True,
                                  text=True).stdout
            try:
                dumped = subprocess.run([self.structured_markdown, "--json", str(pdf)], check=True,
                                        capture_output=True, text=True).stdout
            except (subprocess.CalledProcessError, OSError):
                dumped = None
            table_grid = grid_matcher(ours, self.repo, dumped)
            region_table = region_caller(self.structured_markdown, str(pdf))
            engine_markdown = ours
        return self.markdown_of_page(page, im, table_grid, region_table, engine_markdown,
                                     self.leftover_rows)


class EnsembleTeacher:
    """The strongest teacher: Heron-ft ep03 (A) plus Egret-ft ep00 (B), one processor, union at 0.5.

    Both detectors take the same input (96-dpi render, 640x640, /255, no normalisation -- Heron's
    `preprocessor_config.json` serves both) and load with `AutoModelForObjectDetection`. Each detector's
    boxes are post-processed at ENSEMBLE_POST, kept per class at ENSEMBLE_THRESHOLDS, then unioned; all
    after that is today's adapter unchanged. `detectors`, when given, is the pair as injectable
    callables (page image -> boxes); the self-test's fakes use it and no weights load.
    `short_threshold` (R5, off when None): A's section_header/title boxes scoring at/above it but
    below A's class threshold still promote the short lines they claim to headings.
    `furniture_band` (R6, off when None): a furniture box drops its lines only when its centre is
    within BAND of the page's top or bottom edge, as a fraction of the page height.
    `furniture_duel` (R6b, off when False): furniture drops a line only where A's best furniture
    score on it beats A's best heading score; the duel is measured beside the band.
    `orphan_tables` (R7, off when False): an engine table no region took is written as the
    engine's table at the reading position of the lines it covers.
    `engine_tags` (R8, off when False): aligned words take the engine's sup/sub/underline tags.
    `merge_style_runs` (R9, off when False): one style run across a line break.
    `engine_text` (R10, off when False): ligatures folded, hyphen wraps joined, bullets dropped.
    `render_page_png` is the render binary's path (`render_binary_path` resolves None).
    """

    def __init__(self, model_a: str | None = None, model_b: str | None = None, feature_dump: str | None = None,
                 device: str = "cpu", repo=REPO, structured_markdown=None, detectors=None,
                 leftover_rows: bool = False, short_threshold: float | None = None,
                 furniture_band: float | None = None, furniture_duel: bool = False,
                 orphan_tables: bool = False, engine_tags: bool = False, merge_style_runs: bool = False,
                 engine_text: bool = False, render_page_png: str | None = None):
        self.repo = pathlib.Path(repo)
        self.structured_markdown = structured_markdown
        self.render_page_png = render_page_png
        self.device, self.feature_dump = device, feature_dump
        self.leftover_rows = leftover_rows
        self.short_threshold = short_threshold
        self.furniture_band = furniture_band
        self.furniture_duel = furniture_duel
        self.orphan_tables = orphan_tables
        self.engine_tags = engine_tags
        self.merge_style_runs = merge_style_runs
        self.engine_text = engine_text
        self.al = lines_module(pathlib.Path(repo))
        if detectors is not None:
            self.det_a, self.det_b = detectors
            self.torch = self.proc = self.model_a = self.model_b = self.lab_a = self.lab_b = None
            return
        import torch
        from transformers import AutoImageProcessor, AutoModelForObjectDetection
        self.torch = torch
        self.det_a = self.det_b = None
        self.proc = AutoImageProcessor.from_pretrained(model_a)
        self.model_a = AutoModelForObjectDetection.from_pretrained(model_a).to(device).eval()
        self.model_b = AutoModelForObjectDetection.from_pretrained(model_b).to(device).eval()
        self.lab_a, self.lab_b = self.model_a.config.id2label, self.model_b.config.id2label

    def _real_boxes(self, model, lab, image, W: float, H: float) -> list[dict]:
        with self.torch.no_grad():
            o = model(pixel_values=self.proc(images=image, return_tensors="pt")["pixel_values"].to(self.device))
            o.logits = o.logits.float().cpu(); o.pred_boxes = o.pred_boxes.float().cpu()
            r = self.proc.post_process_object_detection(o, target_sizes=[(H, W)], threshold=ENSEMBLE_POST)[0]
        return [{"label": lab[int(l)], "score": float(s), "bbox": b}
                for s, l, b in zip(r["scores"], r["labels"], r["boxes"].tolist())]

    def markdown_of_page(self, page: dict, image=None, table_grid=None, region_table=None,
                         engine_markdown=None, leftover_rows=None, short_threshold=None,
                         furniture_band=None, furniture_duel=None, orphan_tables=None,
                         engine_tags=None, merge_style_runs=None, engine_text=None) -> str:
        """The page's markdown from the union: `markdown_of` without the rendering."""
        if leftover_rows is None:
            leftover_rows = self.leftover_rows
        if short_threshold is None:
            short_threshold = self.short_threshold
        if furniture_band is None:
            furniture_band = self.furniture_band
        if furniture_duel is None:
            furniture_duel = self.furniture_duel
        if orphan_tables is None:
            orphan_tables = self.orphan_tables
        if engine_tags is None:
            engine_tags = self.engine_tags
        if merge_style_runs is None:
            merge_style_runs = self.merge_style_runs
        if engine_text is None:
            engine_text = self.engine_text
        W, H = (image.size if image is not None else (page["width"], page["height"]))
        if self.det_a is not None:
            boxes_a, boxes_b = self.det_a(image), self.det_b(image)
        else:
            boxes_a = self._real_boxes(self.model_a, self.lab_a, image, W, H)
            boxes_b = self._real_boxes(self.model_b, self.lab_b, image, W, H)
        kept_a = [{"kind": KIND.get(b["label"], "paragraph"), "score": float(b["score"]),
                   "bbox": to_pdf_box(b["bbox"], W, H, page["width"], page["height"])}
                  for b in boxes_a if float(b["score"]) >= ensemble_threshold("A", b["label"])]
        kept_b = [{"kind": KIND.get(b["label"], "paragraph"), "score": float(b["score"]),
                   "bbox": to_pdf_box(b["bbox"], W, H, page["width"], page["height"])}
                  for b in boxes_b if float(b["score"]) >= ensemble_threshold("B", b["label"])]
        short_a = [{"kind": "heading", "score": float(b["score"]),
                    "bbox": to_pdf_box(b["bbox"], W, H, page["width"], page["height"])}
                   for b in boxes_a
                   if short_threshold is not None and b["label"] in ("section_header", "title")
                   and short_threshold <= float(b["score"]) < ensemble_threshold("A", b["label"])]
        duel_a = [{"label": b["label"], "score": float(b["score"]),
                   "bbox": to_pdf_box(b["bbox"], W, H, page["width"], page["height"])}
                  for b in boxes_a] if furniture_duel else []
        return markdown(page, union_ensemble(kept_a, kept_b), self.al, table_grid=table_grid,
                        region_table=region_table, engine_markdown=engine_markdown,
                        leftover_rows=leftover_rows, short_regions=short_a,
                        furniture_band=furniture_band, furniture_duel=furniture_duel,
                        duel_boxes=duel_a, orphan_tables=orphan_tables, engine_tags=engine_tags,
                        merge_style_runs=merge_style_runs, engine_text=engine_text)

    def markdown_of(self, pdf) -> str:
        from PIL import Image
        pdf = pathlib.Path(pdf)
        render_bin = render_binary_path(self.render_page_png, self.repo)
        with tempfile.TemporaryDirectory() as tmp:
            png = pathlib.Path(tmp) / "p.png"
            subprocess.run(render_argv(render_bin, pdf, png), check=True, capture_output=True)
            im = Image.open(png).convert("RGB")
            dump = subprocess.run([self.feature_dump, str(pdf), "--page-id", pdf.name], check=True,
                                  capture_output=True, text=True).stdout
            page = json.loads(dump.strip().split("\n")[0])
        table_grid = region_table = engine_markdown = None
        if self.structured_markdown:
            ours = subprocess.run([self.structured_markdown, str(pdf)], check=True, capture_output=True,
                                  text=True).stdout
            try:
                dumped = subprocess.run([self.structured_markdown, "--json", str(pdf)], check=True,
                                        capture_output=True, text=True).stdout
            except (subprocess.CalledProcessError, OSError):
                dumped = None
            table_grid = grid_matcher(ours, self.repo, dumped)
            region_table = region_caller(self.structured_markdown, str(pdf))
            engine_markdown = ours
        return self.markdown_of_page(page, im, table_grid, region_table, engine_markdown,
                                     self.leftover_rows)


def _png_stub_module():
    """A stand-in `PIL` module for the no-mutool check where pillow is not installed.

    The fake detector ignores the image, so decoding is all the stub skips: `Image.open(path)`
    reads the PNG's own IHDR for its size and `.convert("RGB")` is itself. What the check proves
    -- which subprocesses `markdown_of` spawns -- is unchanged; decoding stays pillow's business,
    and where pillow IS installed the check uses it, so our PNG's readability is covered there.
    """
    import types
    mod = types.ModuleType("PIL")

    class _Image:
        def __init__(self, size):
            self.size = size

        def convert(self, mode):
            assert mode == "RGB"
            return self

    class Image:
        @staticmethod
        def open(path):
            with open(path, "rb") as handle:
                head = handle.read(26)
            if head[:8] != b"\x89PNG\r\n\x1a\n" or head[12:16] != b"IHDR":
                raise ValueError(f"not a PNG file: {path}")
            return _Image((int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big")))

    mod.Image = Image
    return mod


def self_test(require_render: bool = False) -> int:
    """The page reading, without a model: regions in, markdown out.

    With `require_render` (the landing gate's `--require-render`), the R-1/R-4 render checks must
    run: no built `render_page_png` fails instead of skipping. The fast lane may not reach a
    compiler, so there the skip is the honest answer -- said loudly, never passed quietly.
    """
    al = lines_module(REPO)
    def word(t, x0, y0, x1, y1, block, bold=False, italic=False):
        return {"text": t, "bbox": [x0, y0, x1, y1], "block": block, "size": 10, "bold": bold, "italic": italic}
    words = [word("Results", 50, 700, 110, 712, 0),
             word("first", 60, 660, 100, 670, 1), word("item", 105, 660, 140, 670, 1),
             word("second", 60, 645, 110, 655, 1), word("item", 115, 645, 150, 655, 1),
             word("Page", 280, 20, 310, 30, 2), word("7", 315, 20, 320, 30, 2),
             word("loose", 50, 400, 90, 410, 3), word("text", 95, 400, 125, 410, 3)]
    blocks = [{"i": i, "bbox": [0, 0, 600, 800], "kind": "paragraph", "text": ""} for i in range(4)]
    page = {"width": 600, "height": 800, "words": words, "blocks": blocks}
    regions = [{"kind": "heading", "score": .9, "bbox": [45, 695, 120, 715]},
               {"kind": "paragraph", "score": .6, "bbox": [40, 640, 200, 675]},     # a loose box round both items
               {"kind": "list_item", "score": .9, "bbox": [55, 657, 160, 673]},
               {"kind": "list_item", "score": .9, "bbox": [55, 642, 160, 658]},
               {"kind": "furniture", "score": .9, "bbox": [270, 15, 330, 35]}]
    got = markdown(page, regions, al)
    want = "## Results\n\n- first item\n\n- second item\n\nloose text"
    if got != want:
        print(f"FAIL teacher page reading:\n{got!r}\n  wanted\n{want!r}")
        return 1
    grid = "<table><tr><td>a</td><td>b</td></tr></table>"
    regions.append({"kind": "table", "score": .9, "bbox": [45, 395, 130, 415]})
    got = markdown(page, regions, table_html={len(regions) - 1: grid})
    if grid not in got or "loose text" in got:
        print(f"FAIL a structure model's table HTML must replace the one-cell table: {got!r}")
        return 1
    # THE ORDER IS THE ENGINE'S: a block feature_dump reads first comes first, wherever it sits on the page.
    # Here the engine reads the right-hand column's block before the left one's; a half-page sort would not.
    two = {"width": 600, "height": 800, "blocks": [{"i": 0, "bbox": [0, 0, 600, 800]}, {"i": 1, "bbox": [0, 0, 600, 800]}],
           "words": [word("right", 400, 700, 440, 710, 0), word("left", 50, 300, 80, 310, 1)]}
    got = markdown(two, [{"kind": "heading", "score": .9, "bbox": [395, 695, 450, 715]}], al)
    if got != "## right\n\nleft":
        print(f"FAIL the order must be the engine's line order, not a column sort: {got!r}")
        return 1
    # TEDS ON A FIXTURE TABLE: a table region takes the grid of OUR engine's table with the same text, and
    # that grid is what TEDS reads. The one-cell fallback is the round-5 PubTables number (0.038).
    sb, sa = bench_module(REPO)
    truth = "<table><tr><th>Year</th><th>Cost</th></tr><tr><td>2024</td><td>10</td></tr><tr><td>2025</td><td>12</td></tr></table>"
    ours = "| Year | Cost |\n|---|---|\n| 2024 | 10 |\n| 2025 | 12 |\n"
    cells = [("Year", 60, 500), ("Cost", 200, 500), ("2024", 60, 480), ("10", 200, 480), ("2025", 60, 460), ("12", 200, 460)]
    tab = {"width": 600, "height": 800, "blocks": [{"i": 0, "bbox": [0, 0, 600, 800]}],
           "words": [word(t, x, y, x + 30, y + 10, 0) for t, x, y in cells]}
    region = [{"kind": "table", "score": .9, "bbox": [50, 450, 260, 515]}]
    def teds_of(md):
        found = [b["html"] for b in sb.blocks_of(md) if b["kind"] == "table"]
        return sa.teds(truth, found[0]) if found else 0.0
    with_grid = teds_of(markdown(tab, region, al, table_grid=grid_matcher(ours)))
    one_cell = teds_of(markdown(tab, region, al))
    if with_grid < 0.99 or one_cell > 0.5:
        print(f"FAIL TEDS on the fixture table: engine grid {with_grid:.3f} (want 1.0), one cell {one_cell:.3f}")
        return 1
    if markdown(tab, region, al, table_grid=grid_matcher("| other | words |\n|---|---|\n| x | y |\n")) != \
            markdown(tab, region, al):
        print("FAIL an engine table with other text must not lend its grid")
        return 1
    # MATCH BY POSITION, NOT ONLY BY TEXT: a fake engine table whose text differs (similarity < 0.5)
    # but whose cells lie inside the region lends its grid.
    other_md = "| alpha | beta |\n|---|---|\n| x | y |\n"
    dumped = json.dumps({"blocks": [
        {"index": i, "kind": "tableCell", "row": r, "column": c, "page": 0,
         "bbox": b, "text": t, "continuesPrevious": False}
        for i, (r, c, b, t) in enumerate([(0, 0, [60, 500, 120, 510], "alpha"),
                                          (0, 1, [200, 500, 260, 510], "beta"),
                                          (1, 0, [60, 480, 120, 490], "x"),
                                          (1, 1, [200, 480, 260, 490], "y")])]})
    if sa.text_similarity("Year 2024 2025 Cost 10 12", "alpha beta x y") >= GRID_FLOOR:
        print("FAIL the position fixture's texts must differ (similarity < 0.5)")
        return 1
    got = markdown(tab, region, al, table_grid=grid_matcher(other_md, REPO, dumped))
    if "alpha" not in got or "Year 2024 2025 Cost 10 12" in got:
        print(f"FAIL an engine table inside the region must lend its grid by position: {got!r}")
        return 1
    # POSITION FILTERS, TEXT RANKS: two engine tables both inside the region; the larger one's text
    # is unlike the region's, so the smaller, similar one lends its grid (more cells used to win).
    big_md = "| alpha | beta | gamma |\n|---|---|---|\n| a1 | b1 | c1 |\n| a2 | b2 | c2 |\n"
    pair_md = big_md + "\n" + ours
    seen: dict = {}
    markdown(tab, region, al, table_grid=lambda t, b: seen.setdefault("text", t))
    pair_texts = [b["text"] for b in sb.blocks_of(pair_md) if b["kind"] == "table"]
    if len(pair_texts) != 2 or not (sa.text_similarity(seen["text"], pair_texts[0])
                                    < sa.text_similarity(seen["text"], pair_texts[1])):
        print("FAIL the ranking fixture needs two engine tables, the larger unlike the region")
        return 1
    pair_dump = json.dumps({"blocks": [
        {"index": i, "kind": "tableCell", "row": r, "column": c, "page": 0,
         "bbox": b, "text": t, "continuesPrevious": False}
        for i, (r, c, b, t) in enumerate([(0, 0, [60, 494, 110, 504], "alpha"),
                                          (0, 1, [120, 494, 170, 504], "beta"),
                                          (0, 2, [180, 494, 230, 504], "gamma"),
                                          (1, 0, [60, 482, 110, 492], "a1"),
                                          (1, 1, [120, 482, 170, 492], "b1"),
                                          (1, 2, [180, 482, 230, 492], "c1"),
                                          (2, 0, [60, 470, 110, 480], "a2"),
                                          (2, 1, [120, 470, 170, 480], "b2"),
                                          (2, 2, [180, 470, 230, 480], "c2"),
                                          (0, 0, [60, 458, 110, 468], "Year"),
                                          (0, 1, [120, 458, 170, 468], "Cost"),
                                          (1, 0, [60, 450, 110, 458], "2024"),
                                          (1, 1, [120, 450, 170, 458], "10")])]})
    got = markdown(tab, region, al, table_grid=grid_matcher(pair_md, REPO, pair_dump))
    if "Year" not in got or "alpha" in got:
        print(f"FAIL of two engine tables inside the region, the similar one must lend its grid: {got!r}")
        return 1
    # THE REGION ENTRY, faked (no binary): a 2x2 built table is written, called once with the unpadded box.
    # The fake grid holds none of the region's lines, so they follow it as paragraphs (leftover lines).
    calls: list = []
    two = "<table><tr><td>r1c1</td><td>r1c2</td></tr><tr><td>r2c1</td><td>r2c2</td></tr></table>"
    got = markdown(tab, region, al, region_table=lambda b: (calls.append(list(b)), two)[1])
    if got != two + "\n\nYear\n\n2024\n\n2025\n\nCost\n\n10\n\n12" or calls != [[50, 450, 260, 515]]:
        print(f"FAIL an unmatched table region must write the region entry's table once, unpadded: {got!r} {calls!r}")
        return 1
    # THE ENTRY RETURNING NOTHING writes paragraphs, no `<table>`.
    got = markdown(tab, region, al, region_table=lambda b: None)
    if "<table>" in got or "Year 2024 2025 Cost 10 12" not in got:
        print(f"FAIL no region grid must write the region's lines as paragraphs: {got!r}")
        return 1
    if "<table>" in markdown(tab, region, al, region_table=lambda b: "  \n "):
        print("FAIL blank region output is no grid either")
        return 1
    # A MATCHED REGION never calls the entry: neither by text nor by position.
    calls = []
    got = markdown(tab, region, al, table_grid=grid_matcher(ours),
                   region_table=lambda b: (calls.append(b), two)[1])
    if calls or "Year" not in got or two in got:
        print(f"FAIL a text-matched region must not call the region entry: {got!r} {calls!r}")
        return 1
    calls = []
    got = markdown(tab, region, al, table_grid=grid_matcher(other_md, REPO, dumped),
                   region_table=lambda b: (calls.append(b), two)[1])
    if calls or "alpha" not in got:
        print(f"FAIL a position-matched region must not call the region entry: {got!r} {calls!r}")
        return 1
    # A PAGE THE TEACHER CANNOT READ IS THE ENGINE'S PAGE: with no words there are no lines to
    # place, so the engine markdown passed in is returned verbatim; a page with words is unchanged,
    # and without engine markdown an empty page stays empty.
    blank = {"width": 600, "height": 800, "blocks": [], "words": []}
    if markdown(blank, [], al, engine_markdown="<!-- image -->\n") != "<!-- image -->\n":
        print("FAIL a page with no words must return the engine markdown passed in")
        return 1
    if markdown(blank, [], al) != "":
        print("FAIL a page with no words and no engine markdown must stay empty")
        return 1
    with_words = markdown(tab, region, al, engine_markdown="<!-- image -->\n")
    if with_words == "<!-- image -->\n" or with_words != markdown(tab, region, al):
        print(f"FAIL a page with words must ignore the engine markdown passed in: {with_words!r}")
        return 1
    if Teacher(thr=0.3, detector=lambda image: []).markdown_of_page(
            blank, None, engine_markdown="<!-- image -->\n") != "<!-- image -->\n":
        print("FAIL markdown_of_page must pass the engine markdown through on a wordless page")
        return 1
    # INLINE STYLE IS CARRIED: a paragraph line with a bold word, an italic run and a bold-italic
    # word writes the markers around exactly those words; a heading line stays plain.
    style_page = {"width": 600, "height": 800, "blocks": [{"i": 0, "bbox": [0, 0, 600, 800]}],
                  "words": [word("Pay", 50, 700, 80, 710, 0),
                            word("now", 85, 700, 115, 710, 0, bold=True),
                            word("within", 120, 700, 165, 710, 0, italic=True),
                            word("days", 170, 700, 205, 710, 0, italic=True),
                            word("truly", 210, 700, 250, 710, 0, bold=True, italic=True),
                            word("please", 255, 700, 300, 710, 0)]}
    style_box = [{"kind": "paragraph", "score": .9, "bbox": [45, 695, 310, 715]}]
    if markdown(style_page, style_box, al) != "Pay **now** *within days* ***truly*** please":
        print(f"FAIL bold and italic runs must carry the engine's markers: "
              f"{markdown(style_page, style_box, al)!r}")
        return 1
    head_box = [{"kind": "heading", "score": .9, "bbox": [45, 695, 310, 715]}]
    if markdown(style_page, head_box, al) != "## Pay now within days truly please":
        print(f"FAIL a heading line must stay plain: {markdown(style_page, head_box, al)!r}")
        return 1
    # LEFTOVER LINES ARE KEPT: a region with a unit line above a 2x2 engine grid and a source line
    # below it keeps both around the table; with leftover_rows the line above joins the table as a
    # full-width first row and only the line below stays a paragraph.
    grid4 = "<table><tr><td>a</td><td>b</td></tr><tr><td>c</td><td>d</td></tr></table>"
    keep_page = {"width": 600, "height": 800, "blocks": [{"i": 0, "bbox": [0, 0, 600, 800]}],
                 "words": [word("in", 60, 500, 75, 510, 0), word("millions", 80, 500, 140, 510, 0),
                           word("a", 60, 480, 70, 490, 0), word("b", 120, 480, 130, 490, 0),
                           word("c", 60, 460, 70, 470, 0), word("d", 120, 460, 130, 470, 0),
                           word("Source:", 60, 440, 115, 450, 0), word("x", 120, 440, 130, 450, 0)]}
    keep_region = [{"kind": "table", "score": .9, "bbox": [50, 435, 260, 515]}]
    keep_grid = lambda t, b: grid4
    if markdown(keep_page, keep_region, al, table_grid=keep_grid) != "in millions\n\n" + grid4 + "\n\nSource: x":
        print(f"FAIL a unit line above the grid must stay a paragraph before it: "
              f"{markdown(keep_page, keep_region, al, table_grid=keep_grid)!r}")
        return 1
    banded = ("<table><tr><td colspan=\"2\">in millions</td></tr>"
              "<tr><td>a</td><td>b</td></tr><tr><td>c</td><td>d</td></tr></table>")
    if markdown(keep_page, keep_region, al, table_grid=keep_grid, leftover_rows=True) != banded + "\n\nSource: x":
        print(f"FAIL with leftover_rows the line above must be the table's first row: "
              f"{markdown(keep_page, keep_region, al, table_grid=keep_grid, leftover_rows=True)!r}")
        return 1
    # THE ENSEMBLE, through `markdown_of_page` with fake detectors and no weights. The fakes return fixed
    # boxes in image px (the page is 600x800 and the image is taken as the same size, so the conversion is
    # a y-flip); every check below reads the markdown, never the boxes.
    H_PAGE = 800
    def img(pdf_box):
        return [pdf_box[0], H_PAGE - pdf_box[3], pdf_box[2], H_PAGE - pdf_box[1]]
    def fake(boxes):
        return lambda image: [{"label": lab, "score": s, "bbox": img(b)} for lab, s, b in boxes]
    none = lambda image: []
    # (a) A HEADING ONLY B SEES appears as `## `: A misses it, B fills the miss.
    head_page = {"width": 600, "height": 800, "blocks": [{"i": 0, "bbox": [0, 0, 600, 800]}],
                 "words": [word("Only", 50, 700, 90, 712, 0), word("B", 95, 700, 110, 712, 0)]}
    head_box = [45, 695, 120, 715]
    ens = EnsembleTeacher(detectors=(none, fake([("section_header", 0.60, head_box)])))
    if ens.markdown_of_page(head_page, None) != "## Only B":
        print(f"FAIL a heading only B sees must appear as `## `: {ens.markdown_of_page(head_page, None)!r}")
        return 1
    # (b) WHERE THEY OVERLAP, A WINS: B's overlapping box of another class does not override A's class.
    # B's box is the tighter one, so without the union it would win the line and the markdown would be a list.
    tight_box = [50, 697, 115, 713]
    ens = EnsembleTeacher(detectors=(fake([("section_header", 0.90, head_box)]),
                                     fake([("list_item", 0.90, tight_box)])))
    got = ens.markdown_of_page(head_page, None)
    if got != "## Only B":
        print(f"FAIL a B box overlapping an A box at IoU >= 0.5 must not override A's class: {got!r}")
        return 1
    # (c) THE TABLE THRESHOLD IS PER DETECTOR: A's 0.70 is dropped (below 0.75), B's 0.70 is kept (>= 0.65).
    tab_page = {"width": 600, "height": 800, "blocks": [{"i": 0, "bbox": [0, 0, 600, 800]}],
                "words": [word("Alpha", 60, 500, 100, 510, 0), word("Beta", 105, 500, 135, 510, 0)]}
    tab_box = [50, 495, 150, 515]
    got_a = EnsembleTeacher(detectors=(fake([("table", 0.70, tab_box)]), none)).markdown_of_page(tab_page, None)
    got_b = EnsembleTeacher(detectors=(none, fake([("table", 0.70, tab_box)]))).markdown_of_page(tab_page, None)
    if "<table>" in got_a or got_a != "Alpha Beta":
        print(f"FAIL A's table at 0.70 must be dropped (below 0.75): {got_a!r}")
        return 1
    if "<table>" not in got_b:
        print(f"FAIL B's table at 0.70 must be kept (>= 0.65): {got_b!r}")
        return 1
    # (d) ONE DETECTOR ALONE IS TODAY'S SINGLE CHECKPOINT: with the other returning nothing, and scores
    # high enough that the per-class thresholds and 0.3 agree, the union is the single output exactly.
    both_page = {"width": 600, "height": 800,
                 "blocks": [{"i": 0, "bbox": [0, 0, 600, 800]}, {"i": 1, "bbox": [0, 0, 600, 800]}],
                 "words": [word("Head", 50, 700, 90, 712, 0),
                           word("Item", 60, 660, 95, 670, 1), word("one", 100, 660, 125, 670, 1)]}
    both_boxes = [("section_header", 0.9, [45, 695, 100, 715]), ("list_item", 0.9, [55, 655, 135, 673])]
    single = Teacher(thr=0.3, detector=fake(both_boxes)).markdown_of_page(both_page, None)
    alone_a = EnsembleTeacher(detectors=(fake(both_boxes), none)).markdown_of_page(both_page, None)
    alone_b = EnsembleTeacher(detectors=(none, fake(both_boxes))).markdown_of_page(both_page, None)
    if alone_a != single or alone_b != single:
        print(f"FAIL one detector alone must reproduce the single output exactly:\n"
              f"  single {single!r}\n  A alone {alone_a!r}\n  B alone {alone_b!r}")
        return 1
    # R5 SHORT HEADINGS AT A LOWER BAR: a 3-word all-caps line under an A heading box at 0.15 is
    # `## ` with T_SHORT 0.15 and a paragraph with T_SHORT 0.20 (or off); a 13-word line under the
    # same box stays a paragraph at 0.15; a kept box claiming the line more tightly still wins.
    short_page = {"width": 600, "height": 800, "blocks": [{"i": 0, "bbox": [0, 0, 600, 800]}],
                  "words": [word("NOTES", 50, 700, 100, 712, 0), word("TO", 105, 700, 125, 712, 0),
                            word("ACCOUNTS", 130, 700, 200, 712, 0)]}
    short_box = [45, 695, 210, 715]
    det15 = fake([("section_header", 0.15, short_box)])
    if EnsembleTeacher(detectors=(det15, none)).markdown_of_page(short_page, None, short_threshold=0.15) != \
            "## NOTES TO ACCOUNTS":
        print("FAIL a short line under an A heading box at T_SHORT must become a heading")
        return 1
    if EnsembleTeacher(detectors=(det15, none)).markdown_of_page(short_page, None, short_threshold=0.20) != \
            "NOTES TO ACCOUNTS":
        print("FAIL the same box below T_SHORT must leave the line a paragraph")
        return 1
    if EnsembleTeacher(detectors=(det15, none)).markdown_of_page(short_page, None) != "NOTES TO ACCOUNTS":
        print("FAIL with R5 off the weak heading box must leave the line a paragraph")
        return 1
    long_page = {"width": 600, "height": 800, "blocks": [{"i": 0, "bbox": [0, 0, 600, 800]}],
                 "words": [word(f"w{i}", 50 + i * 10, 600, 58 + i * 10, 610, 0) for i in range(13)]}
    long_box = [45, 595, 200, 615]
    if EnsembleTeacher(detectors=(fake([("section_header", 0.15, long_box)]), none)).markdown_of_page(
            long_page, None, short_threshold=0.15) != " ".join(f"w{i}" for i in range(13)):
        print("FAIL a 13-word line must stay a paragraph under a weak heading box")
        return 1
    tight = fake([("section_header", 0.15, short_box), ("list_item", 0.9, [50, 697, 205, 713])])
    if EnsembleTeacher(detectors=(tight, none)).markdown_of_page(short_page, None, short_threshold=0.15) != \
            "- NOTES TO ACCOUNTS":
        print("FAIL a kept box claiming the line more tightly must beat the weak heading box")
        return 1
    # R6 FURNITURE ONLY AT THE PAGE EDGE: with BAND 0.15 on an 800-high page, a furniture box at
    # mid-page over a heading line is ignored (the heading stands), while furniture in the top and
    # bottom bands still drops its lines. Without the band the mid-page box drops the heading too.
    furn_page = {"width": 600, "height": 800, "blocks": [{"i": 0, "bbox": [0, 0, 600, 800]}],
                 "words": [word("Middle", 50, 395, 110, 405, 0), word("Head", 115, 395, 155, 405, 0),
                           word("Top", 50, 748, 90, 758, 0), word("Note", 95, 748, 135, 758, 0),
                           word("Bottom", 50, 38, 110, 48, 0), word("Note", 115, 38, 155, 48, 0)]}
    furn_a = fake([("section_header", 0.9, [45, 390, 210, 410]),
                   ("page_header", 0.9, [48, 392, 208, 408]),
                   ("page_header", 0.9, [40, 745, 210, 765]),
                   ("page_footer", 0.9, [40, 35, 210, 55])])
    if EnsembleTeacher(detectors=(furn_a, none)).markdown_of_page(furn_page, None, furniture_band=0.15) != \
            "## Middle Head":
        print("FAIL a mid-page furniture box must not drop its line, edge furniture still must")
        return 1
    if EnsembleTeacher(detectors=(furn_a, none)).markdown_of_page(furn_page, None) != "":
        print("FAIL without the band the tighter mid-page furniture must still drop the heading")
        return 1
    # The band must not shift region indices: a table region after an ignored furniture box still
    # gets its own table_html grid, not the one-cell fallback.
    idx_page = {"width": 600, "height": 800, "blocks": [{"i": 0, "bbox": [0, 0, 600, 800]}],
                "words": [word("Cell", 60, 400, 100, 410, 0)]}
    idx_regions = [{"kind": "furniture", "score": 0.9, "bbox": [50, 395, 120, 415]},
                   {"kind": "table", "score": 0.9, "bbox": [50, 390, 130, 420]}]
    idx_grid = "<table><tr><td>Cell</td><td>more</td></tr></table>"
    if markdown(idx_page, idx_regions, al, table_html={1: idx_grid}, furniture_band=0.15) != idx_grid:
        print("FAIL the band must leave region indices (table_html keys) alone")
        return 1
    # R6b THE DUEL: a line under a page_header box at 0.6 and a section_header box at 0.7 is kept
    # (as the heading), at 0.6 vs 0.3 it is dropped; without the duel both lines are dropped.
    duel_page = {"width": 600, "height": 800, "blocks": [{"i": 0, "bbox": [0, 0, 600, 800]}],
                 "words": [word("Kept", 50, 600, 100, 612, 0), word("Head", 105, 600, 150, 612, 0),
                           word("Dropped", 50, 500, 110, 512, 0), word("Head", 115, 500, 160, 512, 0)]}
    duel_a = fake([("page_header", 0.6, [45, 595, 160, 615]),
                   ("section_header", 0.7, [40, 590, 170, 620]),
                   ("page_header", 0.6, [45, 495, 170, 515]),
                   ("section_header", 0.3, [40, 490, 180, 520])])
    if EnsembleTeacher(detectors=(duel_a, none)).markdown_of_page(duel_page, None, furniture_duel=True) != \
            "## Kept Head":
        print("FAIL the duel must keep the line its heading score wins and drop the one it loses")
        return 1
    if EnsembleTeacher(detectors=(duel_a, none)).markdown_of_page(duel_page, None) != "":
        print("FAIL without the duel both furniture-covered lines must be dropped")
        return 1
    # R7 ORPHAN ENGINE TABLES: paragraph regions over a fixture engine table (tableCell JSON) write
    # the engine's grid, its lines not repeated as prose; a heading line inside stays a heading
    # beside the table; a list_item line inside vetoes it; a table region that took a grid stays one.
    orph_page = {"width": 600, "height": 800, "blocks": [{"i": 0, "bbox": [0, 0, 600, 800]}],
                 "words": [word("Alpha", 60, 500, 110, 510, 0), word("Beta", 120, 500, 160, 510, 0),
                           word("Gamma", 60, 480, 110, 490, 0), word("Delta", 120, 480, 170, 490, 0),
                           word("Epsilon", 60, 460, 120, 470, 0), word("Zeta", 125, 460, 160, 470, 0)]}
    orph_md = "| Alpha | Beta |\n|---|---|\n| Gamma | Delta |\n| Epsilon | Zeta |\n"
    orph_dump = json.dumps({"blocks": [
        {"index": i, "kind": "tableCell", "row": r, "column": c, "page": 0,
         "bbox": b, "text": t, "continuesPrevious": False}
        for i, (r, c, b, t) in enumerate([(0, 0, [60, 500, 110, 510], "Alpha"),
                                          (0, 1, [120, 500, 160, 510], "Beta"),
                                          (1, 0, [60, 480, 110, 490], "Gamma"),
                                          (1, 1, [120, 480, 170, 490], "Delta"),
                                          (2, 0, [60, 460, 120, 470], "Epsilon"),
                                          (2, 1, [125, 460, 160, 470], "Zeta")])]})
    orph_para = [{"kind": "paragraph", "score": .9, "bbox": [50, 455, 180, 515]}]
    grid_only = markdown(orph_page, orph_para, al,
                         table_grid=grid_matcher(orph_md, REPO, orph_dump), orphan_tables=True)
    if "<table>" not in grid_only or "Alpha" not in grid_only \
            or "Alpha Beta" in grid_only or "Gamma Delta" in grid_only or "Epsilon Zeta" in grid_only:
        print(f"FAIL an engine table no region took must be written as the engine's table: {grid_only!r}")
        return 1
    orph_head = [{"kind": "heading", "score": .9, "bbox": [55, 497, 165, 513]},
                 {"kind": "paragraph", "score": .9, "bbox": [50, 455, 180, 515]}]
    if markdown(orph_page, orph_head, al,
                table_grid=grid_matcher(orph_md, REPO, orph_dump), orphan_tables=True) != \
            "## Alpha Beta\n\n" + grid_only:
        print("FAIL a heading line inside an orphan table must stay a heading beside it")
        return 1
    orph_heads = [{"kind": "heading", "score": .9, "bbox": [55, 497, 165, 513]},
                  {"kind": "heading", "score": .9, "bbox": [55, 477, 175, 493]},
                  {"kind": "paragraph", "score": .9, "bbox": [50, 455, 180, 515]}]
    if "<table>" in markdown(orph_page, orph_heads, al,
                             table_grid=grid_matcher(orph_md, REPO, orph_dump), orphan_tables=True):
        print("FAIL an orphan table under mostly headings must stay prose")
        return 1
    orph_list = [{"kind": "list_item", "score": .9, "bbox": [55, 477, 175, 493]},
                 {"kind": "paragraph", "score": .9, "bbox": [50, 455, 180, 515]}]
    got = markdown(orph_page, orph_list, al,
                   table_grid=grid_matcher(orph_md, REPO, orph_dump), orphan_tables=True)
    if "<table>" in got or "- Gamma Delta" not in got:
        print(f"FAIL a list_item line inside an orphan table must veto it: {got!r}")
        return 1
    orph_tab = [{"kind": "table", "score": .9, "bbox": [50, 455, 180, 515]}]
    got = markdown(orph_page, orph_tab, al,
                   table_grid=grid_matcher(orph_md, REPO, orph_dump), orphan_tables=True)
    if got.count("<table>") != 1 or got != grid_only:
        print(f"FAIL a table region that took a grid must stay one table: {got!r}")
        return 1
    # The constructor flag reaches `markdown`: with mocked detectors seeing nothing, the loose lines
    # take the orphan grid with R7 on and stay prose with it off.
    ens7 = EnsembleTeacher(detectors=(none, none), orphan_tables=True)
    if ens7.markdown_of_page(orph_page, None, table_grid=grid_matcher(orph_md, REPO, orph_dump)) != grid_only:
        print("FAIL orphan_tables=True must write the grid through markdown_of_page")
        return 1
    if "<table>" in EnsembleTeacher(detectors=(none, none)).markdown_of_page(
            orph_page, None, table_grid=grid_matcher(orph_md, REPO, orph_dump)):
        print("FAIL with R7 off the orphan table's lines must stay prose")
        return 1
    # R8 ENGINE TAGS: an aligned raised mark takes the engine's `<sup>` (unglued: it is wholly
    # raised); an aligned `<u>` word takes the underline; `Withdrawn` `:` glued across the bold
    # boundary is `**Withdrawn**:`; an engine heading line never changes a word's bold.
    sup_page = {"width": 600, "height": 800, "blocks": [{"i": 0, "bbox": [0, 0, 600, 800]}],
                "words": [word("the", 50, 700, 75, 710, 0), word("requested", 80, 700, 150, 710, 0),
                          word("documents.", 155, 700, 235, 710, 0), word("2", 240, 700, 250, 710, 0)]}
    sup_box = [{"kind": "paragraph", "score": .9, "bbox": [45, 695, 260, 715]}]
    got = markdown(sup_page, sup_box, al, engine_markdown="the requested documents.<sup>2</sup>\n",
                   engine_tags=True)
    if got != "the requested documents. <sup>2</sup>":
        print(f"FAIL an aligned raised mark must take the engine's sup, unglued: {got!r}")
        return 1
    und_page = {"width": 600, "height": 800, "blocks": [{"i": 0, "bbox": [0, 0, 600, 800]}],
                "words": [word("Please", 50, 700, 100, 710, 0), word("sign", 105, 700, 135, 710, 0),
                          word("here", 140, 700, 175, 710, 0)]}
    und_box = [{"kind": "paragraph", "score": .9, "bbox": [45, 695, 185, 715]}]
    if markdown(und_page, und_box, al, engine_markdown="Please <u>sign</u> here\n", engine_tags=True) != \
            "Please <u>sign</u> here":
        print("FAIL an aligned underlined word must take the engine's u")
        return 1
    glue_page = {"width": 600, "height": 800, "blocks": [{"i": 0, "bbox": [0, 0, 600, 800]}],
                 "words": [word("Filing", 50, 700, 100, 710, 0, bold=True),
                           word("Withdrawn", 105, 700, 185, 710, 0, bold=True),
                           word(":", 190, 700, 195, 710, 0)]}
    glue_box = [{"kind": "paragraph", "score": .9, "bbox": [45, 695, 205, 715]}]
    if markdown(glue_page, glue_box, al, engine_markdown="**Filing Withdrawn**: \n", engine_tags=True) != \
            "**Filing Withdrawn**:":
        print("FAIL a glued word across the bold boundary must join with no space")
        return 1
    bold_page = {"width": 600, "height": 800, "blocks": [{"i": 0, "bbox": [0, 0, 600, 800]}],
                 "words": [word("Decree", 50, 700, 110, 710, 0, bold=True),
                           word("text", 115, 700, 150, 710, 0)]}
    bold_box = [{"kind": "paragraph", "score": .9, "bbox": [45, 695, 160, 715]}]
    if markdown(bold_page, bold_box, al, engine_markdown="# Decree text\n", engine_tags=True) != \
            "**Decree** text":
        print("FAIL an engine heading line must not change a word's bold")
        return 1
    ens8 = EnsembleTeacher(detectors=(none, none), engine_tags=True)
    if ens8.markdown_of_page(sup_page, None,
                             engine_markdown="the requested documents.<sup>2</sup>\n") != \
            "the requested documents. <sup>2</sup>":
        print("FAIL engine_tags=True must write the sup through markdown_of_page")
        return 1
    # R9 ONE RUN ACROSS A LINE BREAK: a two-line bold paragraph is one `**...**` span; mixed
    # markers and `2 * 3 * 4` are untouched; headings stay plain; `</u> <u>` joins too.
    seam_page = {"width": 600, "height": 800, "blocks": [{"i": 0, "bbox": [0, 0, 600, 800]}],
                 "words": [word("Alpha", 60, 700, 110, 710, 0, bold=True),
                           word("Beta", 115, 700, 155, 710, 0, bold=True),
                           word("Gamma", 60, 680, 115, 690, 0, bold=True),
                           word("Delta", 120, 680, 165, 690, 0, bold=True)]}
    seam_box = [{"kind": "paragraph", "score": .9, "bbox": [50, 675, 175, 715]}]
    if markdown(seam_page, seam_box, al, merge_style_runs=True) != "**Alpha Beta Gamma Delta**":
        print("FAIL a two-line bold paragraph must be one span")
        return 1
    mix_page = {"width": 600, "height": 800, "blocks": [{"i": 0, "bbox": [0, 0, 600, 800]}],
                "words": [word("a", 60, 700, 70, 710, 0, bold=True),
                          word("b", 75, 700, 85, 710, 0, italic=True)]}
    mix_box = [{"kind": "paragraph", "score": .9, "bbox": [50, 695, 95, 715]}]
    if markdown(mix_page, mix_box, al, merge_style_runs=True) != "**a** *b*":
        print("FAIL mixed markers must not join")
        return 1
    arith_page = {"width": 600, "height": 800, "blocks": [{"i": 0, "bbox": [0, 0, 600, 800]}],
                  "words": [word(t, 60 + i * 15, 700, 70 + i * 15, 710, 0) for i, t in enumerate("2 * 3 * 4".split())]}
    arith_box = [{"kind": "paragraph", "score": .9, "bbox": [50, 695, 150, 715]}]
    if markdown(arith_page, arith_box, al, merge_style_runs=True) != "2 * 3 * 4":
        print("FAIL arithmetic stars must not join")
        return 1
    seam_head = [{"kind": "heading", "score": .9, "bbox": [50, 675, 175, 715]}]
    if markdown(seam_page, seam_head, al, merge_style_runs=True) != "## Alpha Beta Gamma Delta":
        print("FAIL a heading must stay plain")
        return 1
    useam_page = {"width": 600, "height": 800, "blocks": [{"i": 0, "bbox": [0, 0, 600, 800]}],
                  "words": [word("a", 60, 700, 70, 710, 0), word("b", 60, 680, 70, 690, 0)]}
    useam_box = [{"kind": "paragraph", "score": .9, "bbox": [50, 675, 80, 715]}]
    if markdown(useam_page, useam_box, al, engine_markdown="<u>a</u>\n<u>b</u>\n",
                engine_tags=True, merge_style_runs=True) != "<u>a b</u>":
        print("FAIL an underline seam across a line break must join")
        return 1
    ens9 = EnsembleTeacher(detectors=(none, none), merge_style_runs=True)
    if ens9.markdown_of_page(seam_page, None) != "**Alpha Beta Gamma Delta**":
        print("FAIL merge_style_runs=True must join the span through markdown_of_page")
        return 1
    # R10 TEXT AS THE ENGINE WRITES IT: a ligature folds to its letters; an end-of-line hyphen
    # wrap joins without the hyphen, but not before an uppercase line; a list item's own bullet
    # glyph is dropped (the `- ` is the bullet).
    lig_page = {"width": 600, "height": 800, "blocks": [{"i": 0, "bbox": [0, 0, 600, 800]}],
                "words": [word("\ufb01nest", 60, 700, 110, 710, 0)]}
    lig_box = [{"kind": "paragraph", "score": .9, "bbox": [50, 695, 120, 715]}]
    if markdown(lig_page, lig_box, al, engine_text=True) != "finest":
        print("FAIL a ligature must fold to its letters")
        return 1
    hyph_page = {"width": 600, "height": 800, "blocks": [{"i": 0, "bbox": [0, 0, 600, 800]}],
                 "words": [word("the", 50, 700, 75, 710, 0), word("risk", 80, 700, 115, 710, 0),
                           word("pro-", 120, 700, 155, 710, 0), word("vision", 60, 680, 115, 690, 0)]}
    hyph_box = [{"kind": "paragraph", "score": .9, "bbox": [45, 675, 165, 715]}]
    if markdown(hyph_page, hyph_box, al, engine_text=True) != "the risk provision":
        print("FAIL an end-of-line hyphen wrap must join without the hyphen")
        return 1
    real_page = {"width": 600, "height": 800, "blocks": [{"i": 0, "bbox": [0, 0, 600, 800]}],
                 "words": [word("real-", 60, 700, 105, 710, 0), word("Time", 60, 680, 105, 690, 0)]}
    real_box = [{"kind": "paragraph", "score": .9, "bbox": [50, 675, 115, 715]}]
    if markdown(real_page, real_box, al, engine_text=True) != "real- Time":
        print("FAIL a hyphen before an uppercase line must stay")
        return 1
    bul_page = {"width": 600, "height": 800, "blocks": [{"i": 0, "bbox": [0, 0, 600, 800]}],
                "words": [word("\u2022", 60, 700, 70, 710, 0), word("Filing", 75, 700, 125, 710, 0)]}
    bul_box = [{"kind": "list_item", "score": .9, "bbox": [50, 695, 135, 715]}]
    if markdown(bul_page, bul_box, al, engine_text=True) != "- Filing":
        print("FAIL a list item's own bullet glyph must be dropped")
        return 1
    ens10 = EnsembleTeacher(detectors=(none, none), engine_text=True)
    if ens10.markdown_of_page(hyph_page, None) != "the risk provision":
        print("FAIL engine_text=True must join the wrap through markdown_of_page")
        return 1
    # R-2 TORCH PINNED, CPU FP32: the best-tier requirements name exact versions, and the round
    # installs the teacher venv from that one file -- no inline pin may drift beside it.
    req_file = REPO / "scripts" / "extraction" / "adapters" / "requirements-best.txt"
    try:
        req = [line.split("#", 1)[0].strip() for line in req_file.read_text().splitlines()]
    except OSError:
        print(f"FAIL {req_file} is missing: the best tier installs from that file")
        return 1
    req = [line for line in req if line and not line.startswith("-")]
    torch_lines = [line for line in req if line.startswith("torch==")]
    torch_base = [line.split(";", 1)[0].split("==", 1)[1].split("+", 1)[0].strip()
                  for line in torch_lines]
    if not torch_lines or any(base != "2.5.1" for base in torch_base):
        print(f"FAIL every torch line in requirements-best.txt must pin the same "
              f"release 2.5.1: {req!r}")
        return 1
    if sorted(line.split(";", 1)[1].strip() if ";" in line else "" for line in torch_lines) != [
            'sys_platform != "darwin"', 'sys_platform == "darwin"']:
        print(f"FAIL requirements-best.txt must pin torch per platform "
              f"(+cpu off darwin, plain on darwin): {req!r}")
        return 1
    if [line for line in req if line.startswith("transformers==")] != ["transformers==4.57.6"]:
        print(f"FAIL requirements-best.txt must pin transformers exactly: {req!r}")
        return 1
    bench_sh = (REPO / "scripts" / "extraction" / "structured_bench_remote.sh").read_text()
    if "requirements-best.txt" not in bench_sh:
        print("FAIL structured_bench_remote.sh must install the teacher venv from requirements-best.txt")
        return 1
    if "torch==" in bench_sh or "transformers>" in bench_sh or "transformers<" in bench_sh:
        print("FAIL the round must carry no torch/transformers pin beside requirements-best.txt")
        return 1
    if "render_page_png" not in bench_sh:
        print("FAIL structured_bench_remote.sh must build the render_page_png example")
        return 1
    # R-3 MEASUREMENT = PRODUCT: the CPU-vs-GPU check, on synthetic caches (no torch, no pages).
    # Scores are exact binary fractions, so each p95 reads exactly.
    def cache_box(label, score, box=None):
        return {"label": label, "score": score, "bbox": box or [0, 0, 10, 10]}

    def cache_page(pid, boxes):
        return {"page": pid, "boxes": boxes}

    head, tab = [0, 0, 100, 20], [0, 40, 100, 120]
    cpu_cache = [cache_page(f"p{i}", [cache_box("section_header", 0.75, head),
                                      cache_box("table", 0.875, tab)]) for i in range(3)]
    gpu_cache = [cache_page(f"p{i}", [cache_box("section_header", 0.7578125, head),
                                      cache_box("table", 0.8828125, tab)]) for i in range(3)]
    ok, report = box_cache_parity(cpu_cache, gpu_cache, min_pages=3)
    if not ok or report != {"section_header": 0.0078125, "table": 0.0078125}:
        print(f"FAIL agreeing caches must stand in with their p95: {ok!r} {report!r}")
        return 1
    hot = [cache_page(f"p{i}", [cache_box("section_header", 0.765625, head)]) for i in range(3)]
    cold = [cache_page(f"p{i}", [cache_box("section_header", 0.75, head)]) for i in range(3)]
    ok, report = box_cache_parity(cold, hot, min_pages=3)
    if ok or report != {"section_header": 0.015625}:
        print(f"FAIL a p95 of 0.015625 must refuse the cache: {ok!r} {report!r}")
        return 1
    ok, report = box_cache_parity(cold, [cache_page(f"p{i}", []) for i in range(3)], min_pages=3)
    if ok or report != {"section_header": None}:
        print(f"FAIL a class with boxes but no pair must refuse: {ok!r} {report!r}")
        return 1
    ok, report = box_cache_parity(cpu_cache, gpu_cache)
    if ok or "only 3 page(s)" not in report.get("refused", ""):
        print(f"FAIL three pages must not satisfy the hundred-page check: {ok!r} {report!r}")
        return 1
    ok, report = box_cache_parity(cpu_cache, [cache_page(f"q{i}", []) for i in range(3)],
                                  min_pages=3)
    if ok or report.get("refused") != "the two caches hold different pages":
        print(f"FAIL caches over different pages must refuse: {ok!r} {report!r}")
        return 1
    with tempfile.TemporaryDirectory() as tmp:
        cpu_f = pathlib.Path(tmp) / "cpu.jsonl"
        gpu_f = pathlib.Path(tmp) / "gpu.jsonl"
        cpu_f.write_text("\n".join(json.dumps(p) for p in cpu_cache) + "\n")
        gpu_f.write_text("\n".join(json.dumps(p) for p in gpu_cache) + "\n")
        with contextlib.redirect_stdout(io.StringIO()):
            agree = check_parity_files(str(cpu_f), str(gpu_f), min_pages=3)
            few = check_parity_files(str(cpu_f), str(gpu_f))
        if agree != 0:
            print("FAIL --check-parity must accept agreeing caches")
            return 1
        if few != 1:
            print("FAIL --check-parity must refuse three pages at the default hundred")
            return 1
    # R-4 PIXEL-HASH SELF-TEST and R-1 NO MUTOOL IN THE BEST TIER, through the real binary.
    # Without a built render_page_png there is nothing to look at -- said loudly, not passed
    # quietly; --require-render (the landing gate) turns the skip into a failure.
    render_bin = render_binary_path(repo=REPO)
    pin_pdf = REPO / RENDER_PIN_PAGE
    if not pathlib.Path(render_bin).is_file():
        render_note = "render-pin SKIPPED (not a pass): no render_page_png built"
        if require_render:
            print(f"FAIL {render_note}")
            return 1
        print(render_note)
    else:
        with tempfile.TemporaryDirectory() as tmp:
            out = pathlib.Path(tmp) / "pin.png"
            proc = subprocess.run(render_argv(render_bin, pin_pdf, out),
                                  capture_output=True, text=True)
            if proc.returncode != 0:
                print(f"FAIL render_page_png {pin_pdf.name}: {proc.stderr.strip()}")
                return 1
            digest = hashlib.sha256(out.read_bytes()).hexdigest()
            if digest != RENDER_PIN_SHA256:
                print(f"FAIL the renderer drifted: {RENDER_PIN_PAGE} at 96 dpi hashes {digest}, "
                      f"the pin is {RENDER_PIN_SHA256}")
                return 1
        # R-1: `markdown_of` never invokes mutool -- with PATH emptied of it, both entries
        # (Teacher, EnsembleTeacher) still read the page through the binary above. The feature
        # dump is a stub (canned words, not the unit under test); the detector is fake; the
        # render is real. Pillow decodes where it is installed, else the PNG stub above.
        page_json = json.dumps({"width": 612, "height": 792,
                                "words": [word("Safety", 72, 680, 120, 692, 0),
                                          word("rules", 125, 680, 160, 692, 0),
                                          word("Wear", 86, 656, 120, 666, 1),
                                          word("boots", 125, 656, 160, 666, 1)],
                                "blocks": [{"i": 0, "bbox": [0, 0, 612, 792],
                                            "kind": "paragraph", "text": ""},
                                           {"i": 1, "bbox": [0, 0, 612, 792],
                                            "kind": "paragraph", "text": ""}]})
        with tempfile.TemporaryDirectory() as tmp:
            stub = pathlib.Path(tmp) / "feature_dump"
            # The running interpreter, absolute: PATH is emptied below, so `env` would find nothing.
            stub.write_text(f"#!{sys.executable}\nprint({page_json!r})\n")
            stub.chmod(0o755)
            if "PIL" not in sys.modules:
                try:
                    import PIL  # noqa: F401 -- the page path decodes through pillow when it is there
                    pil_note = "pillow"
                except ImportError:
                    sys.modules["PIL"] = _png_stub_module()
                    pil_note = "a PNG stub (pillow is not installed)"
            else:
                pil_note = "pillow"
            old_path, os.environ["PATH"] = os.environ.get("PATH", ""), tmp
            try:
                if shutil.which("mutool") is not None:
                    print("FAIL the no-mutool check needs PATH without mutool")
                    return 1
                got_teacher = Teacher(feature_dump=str(stub), detector=lambda image: [],
                                      repo=REPO, render_page_png=render_bin).markdown_of(pin_pdf)
                got_ens = EnsembleTeacher(
                    feature_dump=str(stub), detectors=(lambda image: [], lambda image: []),
                    repo=REPO, render_page_png=render_bin).markdown_of(pin_pdf)
            finally:
                os.environ["PATH"] = old_path
            want = "Safety rules\n\nWear boots"
            if got_teacher != want or got_ens != want:
                print(f"FAIL without mutool on PATH both entries must still read the page: "
                      f"{got_teacher!r} {got_ens!r}")
                return 1
        render_note = (f"render-pin {digest[:12]} checked, no-mutool page path checked "
                       f"(decoded through {pil_note})")
    print(f"OK teacher_page: tightest box wins, furniture dropped, unclaimed lines stay paragraphs, table HTML used, "
          f"engine order kept, fixture TEDS {with_grid:.2f} with the engine grid vs {one_cell:.2f} as one cell, "
          f"position grid, region tables vs paragraphs, matched regions skip the entry, "
          f"ensemble B-fills-misses, A-wins-overlap, per-class tables, single-parity, short-headings, "
          f"edge-furniture, furniture-duel, orphan-tables, engine-tags, one-run, engine-text, "
          f"torch pinned, cpu-gpu parity, {render_note}")
    return 0


def main():
    if "--self-test" in sys.argv:
        sys.exit(self_test(require_render="--require-render" in sys.argv))
    if "--check-parity" in sys.argv:
        args = sys.argv[sys.argv.index("--check-parity") + 1:]
        tol, min_pages, paths = PARITY_TOL, PARITY_PAGES, []
        it = iter(range(len(args)))
        for i in it:
            if args[i] == "--parity-tol":
                tol = float(args[next(it)])
            elif args[i] == "--parity-min-pages":
                min_pages = int(args[next(it)])
            else:
                paths.append(args[i])
        if len(paths) != 2:
            print("usage: teacher_page.py --check-parity CPU.jsonl GPU.jsonl "
                  "[--parity-tol 0.01] [--parity-min-pages 100]", file=sys.stderr)
            sys.exit(2)
        sys.exit(check_parity_files(paths[0], paths[1], tol, min_pages))
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True); ap.add_argument("--feature-dump", required=True)
    ap.add_argument("--batch", required=True); ap.add_argument("--out-dir", required=True)
    ap.add_argument("--thr", type=float, default=0.3); ap.add_argument("--device", default="cpu")
    ap.add_argument("--repo", default=str(REPO))
    ap.add_argument("--leftover-rows", action="store_true",
                    help="leftover lines above a grid join the table as spanning first rows")
    ap.add_argument("--furniture-band", type=float, default=None,
                    help="furniture drops its lines only within BAND of the page edge (R6)")
    a = ap.parse_args()
    teacher = Teacher(a.model, a.feature_dump, a.thr, a.device, a.repo,
                      leftover_rows=a.leftover_rows, furniture_band=a.furniture_band)
    out = pathlib.Path(a.out_dir); out.mkdir(parents=True, exist_ok=True)
    clock = (out / "timings.tsv").open("w")
    for pdf in [pathlib.Path(p) for p in pathlib.Path(a.batch).read_text().split("\n") if p.strip()]:
        t = time.perf_counter(); outcome = "ok"
        try:
            (out / f"{pdf.name}.md").write_text(teacher.markdown_of(pdf))
        except Exception as e:  # one bad page must not end the batch; unanswered is the harness's verdict
            outcome = "crash"; print(f"[teacher] {pdf.name}: {e}", file=sys.stderr)
        clock.write(f"{pdf.name}\t{int(1000 * (time.perf_counter() - t))}\t{outcome}\n"); clock.flush()


if __name__ == "__main__":
    main()
