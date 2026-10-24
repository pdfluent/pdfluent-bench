# Copyright (c) 2026 Innovation Trigger B.V.
#
# PDFluent is available under two licences, at your option: the GNU AGPLv3, or
# the PDFluent Commercial Licence. See the LICENSE file in this repository --
# that file travels with the copy you received, which a URL does not.
"""#971 DoD: planted leaks (renamed, re-rendered at another DPI, other container) are found; clean pages are not.

The fixtures are generated PDFs, so nothing here depends on a dataset. Each mutation test breaks one part of
the check and demands that the planted-leak assertion then fails, i.e. that the fixture really tests it.
"""
import io
import json
import pathlib
import shutil

import pytest

from bench import registry as R
from pdfmaker import page_pdf, raster_pdf, rasterise
from skipping import skip

WORDS = ("alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima mike november oscar papa "
         "quebec romeo sierra tango uniform victor whiskey xray yankee zulu").split()


def lines(seed: int, n: int = 30) -> list[str]:
    return [" ".join(WORDS[(seed * 7 + i * 3 + j * 5) % len(WORDS)] for j in range(9)) + f" {seed}-{i}" for i in range(n)]


def speckle(pgm: bytes) -> bytes:
    """A dirty scan: every 40th pixel pure black, ~2.5% of the page, over a 1-pixel horizontal shift."""
    width, height = map(int, pgm.split(None, 4)[1:3])
    data = bytearray(pgm[-(width * height):])
    shifted = bytearray(data[1:] + data[:1])
    for i in range(0, len(shifted), 40):
        shifted[i] = 0
    return pgm[:-(width * height)] + bytes(shifted)


HOLDOUT_BOXES = [(50, 100, 200, 60), (300, 120, 220, 40), (60, 300, 400, 25), (100, 400, 60, 150)]
CLEAN_BOXES = [(400, 80, 100, 200), (60, 600, 300, 30)]


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    d = tmp_path_factory.mktemp("leaks")
    holdout = d / "holdout.pdf"
    holdout.write_bytes(page_pdf(lines(1), HOLDOUT_BOXES))
    files = {"holdout": holdout}
    files["renamed"] = d / "something_else.pdf"
    shutil.copy(holdout, files["renamed"])
    for dpi in (110, 200):
        files[f"dpi{dpi}"] = d / f"scan{dpi}.pdf"
        files[f"dpi{dpi}"].write_bytes(raster_pdf(rasterise(holdout, dpi, d / f"r{dpi}")))
    files["noisy"] = d / "noisy.pdf"
    files["noisy"].write_bytes(raster_pdf(speckle(rasterise(holdout, 150, d / "r150"))))
    files["container"] = d / "container.pdf"
    files["container"].write_bytes(page_pdf(lines(1), HOLDOUT_BOXES, producer="Another Tool 9.1", compress=True))
    files["clean"] = d / "clean.pdf"
    files["clean"].write_bytes(page_pdf(lines(2), CLEAN_BOXES))
    files["clean_same_template"] = d / "clean2.pdf"
    files["clean_same_template"].write_bytes(page_pdf(lines(3), HOLDOUT_BOXES))
    return files


def entry(path, **extra):
    return R.fingerprint_pdf(path, file=path.name, **extra)[0]


def registry_with(world, extra_holdouts=None):
    held = [entry(world["holdout"], holdout="fixture-holdout", doc="fixture:source-1", half="holdout")]
    meta = {"fixture-holdout": {"entries": held, "status": "complete"}}
    meta.update(extra_holdouts or {})
    return {"holdouts": meta}


def layer(name, layer_name, pages, **extra):
    return {"name": name, "layer": layer_name, "pages": pages, **extra}


def assert_planted_leaks_found(result, expected=("renamed", "dpi110", "dpi200", "noisy", "container")):
    """The fixture assertion the mutation tests break."""
    found = {v["layer_page"].split("#")[0] for v in result["violations"] if v["holdout"].startswith("fixture-holdout")}
    names = {"renamed": "something_else.pdf", "dpi110": "scan110.pdf", "dpi200": "scan200.pdf",
             "noisy": "noisy.pdf", "container": "container.pdf"}
    assert {names[k] for k in expected} <= found, f"not found: {sorted({names[k] for k in expected} - found)}"
    assert "clean.pdf" not in found and "clean2.pdf" not in found


def planted_manifest(world):
    return [layer("planted", "dev", [entry(world[k], doc=f"planted:{k}") for k in
                                    ("renamed", "dpi110", "dpi200", "noisy", "container", "clean", "clean_same_template")])]


# ---------------------------------------------------------------- the planted leaks

def test_renamed_rerendered_and_repackaged_copies_are_found_and_clean_pages_are_not(world):
    result = R.check(registry_with(world), planted_manifest(world))
    assert_planted_leaks_found(result)


def test_each_copy_is_found_by_the_rule_that_can_see_it(world):
    by_rule = {}
    for v in R.check(registry_with(world), planted_manifest(world))["violations"]:
        by_rule.setdefault(v["layer_page"].split("#")[0], set()).add(v["rule"])
    assert by_rule["something_else.pdf"] == {"sha", "text"}                # same bytes under another name
    assert by_rule["scan110.pdf"] == {"render"}                            # no text layer, other bytes
    assert by_rule["scan200.pdf"] == {"render"} and by_rule["noisy.pdf"] == {"render"}
    assert by_rule["container.pdf"] == {"text"}                            # other bytes, same text


def test_a_violation_names_the_page_hash_the_holdout_and_the_layer(world):
    v = next(v for v in R.check(registry_with(world), planted_manifest(world))["violations"]
             if v["layer_page"].startswith("something_else.pdf") and v["rule"] == "sha")
    assert v["page_hash"] == entry(world["holdout"])["file_sha256"]
    assert v["holdout"] == "fixture-holdout"
    assert v["layer"] == "dev" and v["dataset"] == "planted"


def test_a_clean_page_is_not_flagged_even_on_the_same_template(world):
    # clean2.pdf has the holdout's layout and other words: when both pages have a text layer, the text decides.
    clean = [layer("clean", "validation", [entry(world["clean"], doc="x:1"), entry(world["clean_same_template"], doc="x:2")])]
    assert R.check(registry_with(world), clean)["violations"] == []


def test_the_report_names_hash_holdout_and_layer(world):
    out, err = io.StringIO(), io.StringIO()
    result = R.check(registry_with(world), planted_manifest(world))
    assert R.report(result, [], out, err) == 1
    line = next(l for l in out.getvalue().splitlines() if l.startswith("LEAK (sha)"))
    assert entry(world["holdout"])["file_sha256"] in line and "fixture-holdout" in line and "layer dev" in line


# ---------------------------------------------------------------- documents

def test_a_document_id_matches_across_versions_and_namespaces_are_kept_apart():
    assert R.canonical_doc("arxiv:2502.15977v3") == "arxiv:2502.15977"
    assert R.canonical_doc("pmc:5120491") == "pmc:PMC5120491"
    assert R.canonical_doc("x.pdf", "doclaynet") == "doclaynet:x.pdf"
    with pytest.raises(ValueError):
        R.canonical_doc("no-namespace")


def test_a_holdout_document_may_not_appear_in_any_layer():
    held = {"holdouts": {"olmocr-bench": {"entries": [{"file": "arxiv_math/2502.15977_pg21.pdf", "page": 1,
                                                       "doc": "arxiv:2502.15977", "file_sha256": "a" * 64}]}}}
    for layer_name in R.LAYER_NAMES:
        got = R.check(held, [layer("harvest", layer_name, [{"file": "p.pdf", "page": 7, "doc": "arxiv:2502.15977v2"}])])
        assert [(v["rule"], v["layer"], v["holdout"]) for v in got["violations"]] == [("doc", layer_name, "olmocr-bench")]


def test_pages_of_one_document_may_not_sit_on_both_sides_of_the_validation_boundary():
    page = lambda n, doc: {"file": f"{n}.pdf", "page": 1, "doc": doc}
    dev = layer("d", "dev", [page("a", "arxiv:2401.00001"), page("b", "arxiv:2401.00002")])
    val = layer("v", "validation", [page("c", "arxiv:2401.00001v2")])
    got = R.check({"holdouts": {}}, [dev, val])["violations"]
    assert [(v["rule"], v["layer"], v["holdout"]) for v in got] == [("doc", "validation", "dev:d")]
    # dev and training are one side: training is drawn from dev
    train = layer("t", "training", [page("e", "arxiv:2401.00001")])
    assert R.check({"holdouts": {}}, [dev, train])["violations"] == []


def test_a_page_in_both_dev_and_validation_is_a_violation(world):
    dev = layer("d", "dev", [entry(world["holdout"], doc="a:1")])
    val = layer("v", "validation", [entry(world["renamed"], doc="a:2")])
    assert {v["rule"] for v in R.check({"holdouts": {}}, [dev, val])["violations"]} == {"sha", "text"}


# ---------------------------------------------------------------- registration and coverage

def write_layers(root, registered, files):
    (root / "reg").mkdir(parents=True)
    (root / "reg" / "layers.json").write_text(json.dumps({"datasets": registered}))
    for rel, doc in files.items():
        p = root / "layers" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(doc))
    return root / "layers", root / "reg"


def test_adding_a_dataset_to_a_layer_without_registering_it_fails(tmp_path):
    layers, reg = write_layers(tmp_path, [], {"dev/sneaky.json": {"pages": []}})
    _, problems = R.load_layers(layers, reg)
    assert problems == ["dataset not registered: dev/sneaky.json is in layer 'dev' but not in layers.json"]
    out = io.StringIO()
    assert R.report({"violations": [], "missing": [], "partial": []}, problems, out, io.StringIO()) == 1


def test_a_registered_dataset_loads_and_a_missing_manifest_is_a_problem(tmp_path):
    decl = {"name": "arxiv-dev", "layer": "dev", "manifest": "dev/arxiv.json"}
    layers, reg = write_layers(tmp_path, [decl, {**decl, "name": "gone", "manifest": "dev/gone.json"}],
                               {"dev/arxiv.json": {"pages": [{"file": "a", "page": 1, "doc": "arxiv:1"}]}})
    manifests, problems = R.load_layers(layers, reg)
    assert [m["name"] for m in manifests] == ["arxiv-dev"]
    assert problems == ["gone: registered manifest dev/gone.json does not exist"]


def test_a_repack_without_hashes_is_refused(tmp_path):
    decl = {"name": "dln-mirror", "layer": "training", "manifest": "training/dln.json", "repack_of": "doclaynet"}
    layers, reg = write_layers(tmp_path, [decl], {"training/dln.json": {"pages": []}})
    _, problems = R.load_layers(layers, reg)
    assert problems == ["dln-mirror: a repack of doclaynet needs a page-hash manifest before use"]


def test_a_manifest_in_the_wrong_layer_directory_is_a_problem(tmp_path):
    decl = {"name": "x", "layer": "validation", "manifest": "dev/x.json"}
    layers, reg = write_layers(tmp_path, [decl], {"dev/x.json": {"pages": []}})
    assert any("not in the validation directory" in p for p in R.load_layers(layers, reg)[1])


def test_every_required_holdout_missing_is_reported_by_name():
    result = R.check({"holdouts": {}}, [])
    assert [m["holdout"] for m in result["missing"]] == list(R.REQUIRED)
    err = io.StringIO()
    assert R.report(result, [], io.StringIO(), err) == 3
    assert "SKIPPED (not a pass): holdout olmocr-bench" in err.getvalue()


def test_a_partial_holdout_is_reported_and_is_not_a_pass():
    held = {n: {"entries": [{"file": "f", "page": 1, "doc": "x:1"}], "status": "complete"} for n in R.REQUIRED}
    held["pubtables-1m-test"]["status"] = "partial"
    held["pubtables-1m-test"]["reason"] = "mutool 1.23.10 seal"
    result = R.check({"holdouts": held}, [])
    assert [p["holdout"] for p in result["partial"]] == ["pubtables-1m-test"]
    err = io.StringIO()
    assert R.report(result, [], io.StringIO(), err) == 3 and "mutool 1.23.10 seal" in err.getvalue()


# ---------------------------------------------------------------- mutations: the fixture must notice

@pytest.mark.parametrize("rules", [("sha", "doc"), ("sha", "text", "doc"), ("doc",)])
def test_disabling_the_fingerprint_match_makes_the_planted_leak_fixture_fail(world, rules):
    mutated = R.check(registry_with(world), planted_manifest(world), rules=rules)
    with pytest.raises(AssertionError):
        assert_planted_leaks_found(mutated)


def test_the_renamed_copy_survives_losing_the_byte_hash_because_text_and_render_see_it(world):
    mutated = R.check(registry_with(world), planted_manifest(world), rules=("text", "render", "doc"))
    assert_planted_leaks_found(mutated)


def test_a_zero_distance_threshold_loses_the_dirty_scan(world):
    with pytest.raises(AssertionError):
        assert_planted_leaks_found(R.check(registry_with(world), planted_manifest(world), max_bits=0))


def test_removing_the_holdout_from_the_registry_makes_the_fixture_fail(world):
    with pytest.raises(AssertionError):
        assert_planted_leaks_found(R.check({"holdouts": {}}, planted_manifest(world)))


def test_removing_the_document_rule_loses_the_document_leak():
    held = {"holdouts": {"olmocr-bench": {"entries": [{"file": "f", "page": 1, "doc": "arxiv:2502.15977"}]}}}
    page = layer("h", "dev", [{"file": "p.pdf", "page": 7, "doc": "arxiv:2502.15977"}])
    assert R.check(held, [page])["violations"]
    assert R.check(held, [page], rules=("sha", "text", "render"))["violations"] == []


# ---------------------------------------------------------------- fingerprints themselves

def test_a_blank_page_has_no_render_hash_and_a_short_text_has_no_text_hash(tmp_path):
    blank = tmp_path / "blank.pdf"
    blank.write_bytes(page_pdf([], []))
    fp = R.page_fingerprints(blank)
    assert fp == [{"page": 1, "text": None, "render": None}]


def test_text_fingerprint_survives_case_spacing_and_unicode_form():
    a = "Alpha  Bravo " * 40
    b = "alpha bravo\n" * 40
    assert R.text_fingerprint(a) == R.text_fingerprint(b) is not None
    assert R.text_fingerprint("short") is None


def test_the_threshold_separates_a_re_render_from_a_different_page(world):
    base = entry(world["holdout"])["render"]
    assert max(R.hamming(base, entry(world[k])["render"]) for k in ("dpi110", "dpi200", "noisy", "container")) <= R.HAMMING
    assert R.hamming(base, entry(world["clean"])["render"]) > R.HAMMING
    # same layout, other words: the render hash cannot tell them apart, which is why a render match is
    # only used when one of the two pages has no text layer
    assert R.hamming(base, entry(world["clean_same_template"])["render"]) <= R.HAMMING


# ---------------------------------------------------------------- the committed registry

REGISTRY = pathlib.Path(__file__).resolve().parents[1] / "datasets" / "registry"
LAYERS = pathlib.Path(__file__).resolve().parents[1] / "datasets" / "layers"


def test_the_committed_registry_covers_every_holdout_and_no_layer_page_leaks():
    """Runs on every push. A holdout that is only partly fingerprinted is a SKIPPED line, never a pass."""
    reg = R.load_registry(REGISTRY)
    manifests, problems = R.load_layers(LAYERS, REGISTRY)
    assert problems == []
    result = R.check(reg, manifests)
    assert result["violations"] == []
    names = {m["holdout"] for m in result["missing"]}
    assert names <= {"docling-dpbench"}, f"holdouts missing from the registry: {sorted(names)}"
    gaps = [f"{m['holdout']}: {m['reason']}" for m in result["missing"] + result["partial"]]
    if gaps:
        skip("registry incomplete: " + " | ".join(gaps))


def test_committed_holdout_files_match_their_seals_and_hold_no_content():
    reg = R.load_registry(REGISTRY)        # raises on a seal mismatch
    allowed = {"holdout", "source", "file", "half", "doc", "licence", "page", "file_sha256", "text", "render"}
    for name, meta in reg["holdouts"].items():
        for e in meta["entries"]:
            assert set(e) <= allowed, (name, set(e) - allowed)


def test_text_less_holdout_pages_of_different_documents_rarely_look_alike():
    """Measured on the committed registry. A render match is only used when a page has no text layer, so the
    pages that matter are the scans: how many of them have a lookalike from another document within HAMMING?
    Dense scanned text looks alike at 16 x 16 cells; the share must stay small enough for a hit to be worth a look."""
    reg = R.load_registry(REGISTRY)
    held = [(n, e) for n, m in reg["holdouts"].items() for e in m["entries"] if e.get("render") and not e.get("text")]
    if len(held) < 100:
        skip(f"only {len(held)} text-less pages in the registry; the measurement needs the full one")
    index = R.Index([{**e, "_h": n} for n, e in held])
    close = [(n, e["file"]) for n, e in held
             if any(h is not e and h["doc"] != e["doc"] for _, h in index.match({"render": e["render"]}))]
    assert len(close) <= 0.03 * len(held), f"{len(close)} of {len(held)} text-less pages have a lookalike, e.g. {close[:3]}"
