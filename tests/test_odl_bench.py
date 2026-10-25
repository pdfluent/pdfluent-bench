# Copyright (c) 2026 Innovation Trigger B.V.
#
# PDFluent is available under two licences, at your option: the GNU AGPLv3, or
# the PDFluent Commercial Licence. See the LICENSE file in this repository --
# that file travels with the copy you received, which a URL does not.
"""odl-bench: the gate, the manifest, the labels (#969) and the loud skip."""
import collections
import copy
import hashlib
import json
import os
import pathlib
import types

import pytest

from bench import odl

ROOT = pathlib.Path(__file__).resolve().parents[1]
IDS = [f"0103000000{n:04d}" for n in range(1, 201)]


def make_result(recipe="fast+footers", overall=0.83, mhash="m1", docs=None):
    docs = docs or {d: {"overall": overall, "nid": overall, "teds": None, "mhs": overall} for d in IDS}
    split = {d: ("tune" if int(d) % 2 == 0 else "holdout") for d in IDS}
    return {"recipe": recipe, "manifest_hash": mhash, "documents": docs, "split": split,
            "scores": odl.scores_by_scope(docs, split)}


def baseline_of(result, tol=0.005):
    return odl.make_baseline({**result, "bench_commit": odl.PIN}, tol)


# --- the threshold logic ---------------------------------------------------

def test_same_scores_pass():
    r = make_result()
    assert odl.check(r, baseline_of(r)) == []


def test_a_drop_inside_the_tolerance_passes_and_one_outside_fails():
    base = baseline_of(make_result(overall=0.83))
    assert odl.check(make_result(overall=0.8255), base) == []          # -0.0045
    failures = odl.check(make_result(overall=0.8240), base)             # -0.0060
    assert failures and all("recipe=fast+footers" in f for f in failures)
    assert any("scope=all metric=overall" in f for f in failures)
    assert any("scope=holdout" in f for f in failures) and any("scope=tune" in f for f in failures)


def test_exactly_the_tolerance_is_not_a_failure():
    base = baseline_of(make_result(overall=0.83))
    assert odl.check(make_result(overall=0.825), base) == []


def test_a_gain_never_fails():
    base = baseline_of(make_result(overall=0.83))
    assert odl.check(make_result(overall=0.90), base) == []


def test_the_failure_names_the_documents_that_lost_most():
    result = make_result()
    base = baseline_of(result)
    docs = copy.deepcopy(result["documents"])
    docs[IDS[4]] = {"overall": 0.0, "nid": 0.0, "teds": None, "mhs": 0.0}
    failures = odl.check(make_result(docs=docs), base)
    assert any(IDS[4] in f and "metric=overall" in f for f in failures)


def test_a_canary_below_its_floor_fails_even_if_the_mean_holds():
    result = make_result()
    base = baseline_of(result)
    docs = copy.deepcopy(result["documents"])
    docs[odl.CANARIES[0]]["overall"] = 0.5          # one document of 200: the mean moves 0.0016
    docs[odl.CANARIES[0]]["nid"] = 0.5
    failures = odl.check(make_result(docs=docs), base)
    assert any(f"canary document={odl.CANARIES[0]}" in f for f in failures)
    assert not any("scope=all" in f for f in failures)


def test_a_missing_score_is_a_failure_not_a_pass():
    result = make_result()
    base = baseline_of(result)
    docs = copy.deepcopy(result["documents"])
    for d in docs.values():
        d["mhs"] = None
    assert any("metric=mhs: no score" in f for f in odl.check(make_result(docs=docs), base))


def test_a_baseline_for_another_recipe_or_manifest_refuses_to_judge():
    r = make_result()
    assert "baseline is for recipe" in odl.check(make_result(recipe="fast"), baseline_of(r))[0]
    assert "manifest" in odl.check(make_result(mhash="other"), baseline_of(r))[0]


def test_a_baseline_scope_the_run_lacks_fails():
    r = make_result()
    r["scores"].pop("tune")
    base = baseline_of(make_result())
    assert any("scope=tune" in f for f in odl.check(r, base))


def test_mutation_the_gate_bites():
    """Break the comparison in a copy of the module: the scenario that must fail then passes."""
    source = (ROOT / "bench" / "odl.py").read_text()
    assert "got < floor - tol - EPS" in source
    mutated = types.ModuleType("odl_mutated")
    mutated.__dict__["__file__"] = str(ROOT / "bench" / "odl.py")
    exec(compile(source.replace("got < floor - tol - EPS", "False"), "odl_mutated", "exec"), mutated.__dict__)
    base = baseline_of(make_result(overall=0.83))
    bad = make_result(overall=0.80)
    assert odl.check(bad, base)                      # the real gate is red
    assert not any("scope=" in f for f in mutated.check(bad, base))   # the broken one lets the drop through


# --- recipes, models, the loud skip ----------------------------------------

def test_recipes_cover_the_story_and_pass_options_through():
    recipes = odl.load_recipes()
    for name in ("fast", "fast+footers", "balanced", "balanced+kf", "balanced+tatr+kf"):
        assert name in recipes
    assert odl.recipe_kwargs(recipes["fast+footers"], None) == {
        "quality": "fast", "include_headers": True, "include_footers": True}
    kw = odl.recipe_kwargs(recipes["balanced+tatr+kf"], "/m")
    assert kw == {"quality": "balanced", "keep_furniture": True, "model_dir": "/m", "table_model_dir": "/m"}


def test_balanced_without_models_is_skipped_loudly_and_never_zero(tmp_path, capsys):
    recipe = odl.load_recipes()["balanced"]
    assert "PDFLUENT_MODEL_DIR is not set" in odl.model_problem(recipe, {})
    assert "ORT_DYLIB_PATH is not set" in odl.model_problem(recipe, {"PDFLUENT_MODEL_DIR": str(tmp_path)})
    assert odl.model_problem(recipe, {"PDFLUENT_MODEL_DIR": str(tmp_path), "ORT_DYLIB_PATH": str(tmp_path / "x")})
    lib = tmp_path / "lib.dylib"
    lib.write_text("")
    assert odl.model_problem(recipe, {"PDFLUENT_MODEL_DIR": str(tmp_path), "ORT_DYLIB_PATH": str(lib)}) is None
    assert odl.model_problem(odl.load_recipes()["fast"], {}) is None


def test_main_exits_3_on_a_skip_and_prints_the_line(tmp_path, monkeypatch, capsys):
    clone = tmp_path / "clone"
    monkeypatch.setattr(odl, "ensure_clone", lambda *a: None)
    monkeypatch.setattr(odl, "load_manifest", lambda *a: json.loads((ROOT / "odl-bench" / "manifest.json").read_text()))
    monkeypatch.setattr(odl, "manifest_problems", lambda *a: [])
    base = tmp_path / "b.json"
    base.write_text("{}")
    monkeypatch.delenv("PDFLUENT_MODEL_DIR", raising=False)
    argv = ["run", "--recipe", "balanced", "--clone", str(clone), "--baseline", str(base),
            "--no-split", "--out", str(tmp_path / "out")]
    assert odl.main(argv) == odl.EXIT_SKIPPED
    assert "SKIPPED (not a pass): " in capsys.readouterr().err
    assert odl.main(argv + ["--allow-skip"]) == odl.EXIT_OK
    assert "SKIPPED (not a pass): " in capsys.readouterr().err


def test_a_missing_baseline_or_split_file_cannot_pass(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(odl, "ensure_clone", lambda *a: None)
    monkeypatch.setattr(odl, "load_manifest", lambda *a: json.loads((ROOT / "odl-bench" / "manifest.json").read_text()))
    monkeypatch.setattr(odl, "manifest_problems", lambda *a: [])
    argv = ["run", "--recipe", "fast", "--clone", str(tmp_path), "--out", str(tmp_path / "o")]
    assert odl.main(argv + ["--baseline", str(tmp_path / "none.json")]) == odl.EXIT_UNJUDGED
    assert "baseline not found" in capsys.readouterr().err
    (tmp_path / "b.json").write_text("{}")
    code = odl.main(argv + ["--baseline", str(tmp_path / "b.json"), "--split-file", str(tmp_path / "gone.json")])
    assert code == odl.EXIT_UNJUDGED and "split not found" in capsys.readouterr().err


# --- manifest --------------------------------------------------------------

def _corpus(root: pathlib.Path):
    (root / odl.PDF_DIR).mkdir(parents=True)
    (root / odl.GT_DIR).mkdir(parents=True)
    for n in range(3):
        (root / odl.PDF_DIR / f"d{n}.pdf").write_bytes(b"pdf%d" % n)
        (root / odl.GT_DIR / f"d{n}.md").write_text(f"gt {n}")


def test_a_changed_pdf_or_ground_truth_file_is_named(tmp_path):
    _corpus(tmp_path)
    manifest = odl.build_manifest(tmp_path)
    assert odl.manifest_problems(tmp_path, manifest) == []
    (tmp_path / odl.PDF_DIR / "d1.pdf").write_bytes(b"changed")
    (tmp_path / odl.GT_DIR / "d2.md").write_text("changed")
    (tmp_path / odl.GT_DIR / "d9.md").write_text("new")
    problems = odl.manifest_problems(tmp_path, manifest)
    assert any("pdfs/d1.pdf" in p and "sha256" in p for p in problems)
    assert any("ground-truth/markdown/d2.md" in p for p in problems)
    assert any("d9.md" in p and "not in the manifest" in p for p in problems)
    (tmp_path / odl.PDF_DIR / "d0.pdf").unlink()
    assert any("d0.pdf: missing" in p for p in odl.manifest_problems(tmp_path, manifest))


def test_combined_hash_is_sha256sum_format_over_sorted_names():
    files = {"b": "2" * 64, "a": "1" * 64}
    assert odl.combined_hash(files) == hashlib.sha256(f"{'1' * 64}  a\n{'2' * 64}  b\n".encode()).hexdigest()


def test_the_committed_manifest_is_the_one_in_the_story():
    m = json.loads((ROOT / "odl-bench" / "manifest.json").read_text())
    assert m["bench_commit"] == odl.PIN
    assert len(m["pdfs"]) == len(m["ground_truth"]) == 200
    assert m["pdfs_combined"].startswith("80fcf955e57157e4")
    assert m["ground_truth_combined"].startswith("b029b4f2260aee21")
    assert odl.combined_hash(m["pdfs"]) == m["pdfs_combined"]
    assert odl.combined_hash(m["ground_truth"]) == m["ground_truth_combined"]
    assert odl.labels_hash(m) == m["labels_manifest_sha256"]


def test_the_pinned_corpus_matches_the_manifest_when_a_clone_is_given():
    clone = os.environ.get("ODL_CLONE")
    if not clone or not pathlib.Path(clone).is_dir():
        pytest.skip("ODL_CLONE is not set: the corpus hashes are checked by `python -m bench.odl verify`")
    assert odl.manifest_problems(pathlib.Path(clone), odl.load_manifest()) == []


# --- the split (public, scoreless) and the labels of #969 (private) --------

SPLIT_FILE = ROOT / "odl-bench" / "split-v1.json"
EXPECT_COUNTS = {"A": 5, "T1": 4, "B": 20, "C": 47, "T2": 14, "D": 13, "E": 97}
EXPECT_LOSS = {"A": 0.0076, "T1": 0.0085, "B": 0.0166, "C": 0.0120, "T2": 0.0062, "D": 0.0064, "E": 0.0054}


def _split():
    return json.loads(SPLIT_FILE.read_text(encoding="utf-8"))


def test_the_split_file_is_scoreless_even_tune_odd_holdout_and_hash_checked():
    doc = _split()
    assert doc["schema"] == odl.SPLIT_SCHEMA and doc["bench_commit"] == odl.PIN
    assert set(doc) == {"schema", "bench_commit", "manifest_sha256", "split_rule", "split_sha256", "documents"}
    assert list(doc["documents"]) == IDS
    for d, label in doc["documents"].items():
        assert label == ("tune" if int(d) % 2 == 0 else "holdout"), d
    assert doc["split_sha256"] == odl.split_digest(doc["documents"])
    assert collections.Counter(doc["documents"].values()) == {"tune": 100, "holdout": 100}


def test_the_public_tree_holds_no_per_document_scores():
    for path in (ROOT / "odl-bench").iterdir():
        if path.suffix in (".json", ".csv") and path.name != "manifest.json":
            text = path.read_text(encoding="utf-8")
            assert '"overall"' not in text and '"scores"' not in text and "loss_vs" not in text, path.name
    assert not (ROOT / "odl-bench" / "labels-v1.json").exists()


def test_the_split_is_built_on_the_committed_manifest_and_the_pin():
    m = json.loads((ROOT / "odl-bench" / "manifest.json").read_text())
    assert _split()["manifest_sha256"] == m["labels_manifest_sha256"]


def test_the_split_loader_reads_the_split_and_refuses_a_different_manifest():
    doc = _split()
    good = odl.load_split(SPLIT_FILE, IDS, doc["manifest_sha256"])
    assert sum(v == "tune" for v in good.values()) == 100
    with pytest.raises(odl.Unjudged, match="built on manifest"):
        odl.load_split(SPLIT_FILE, IDS, "0" * 64)
    with pytest.raises(odl.Unjudged, match="not found"):
        odl.load_split(SPLIT_FILE.with_name("none.json"), IDS, "x")
    with pytest.raises(odl.Unjudged, match="no tune/holdout split"):
        odl.load_split(None, IDS, "x")


def test_the_split_loader_refuses_broken_split_files(tmp_path):
    doc = _split()
    lh = doc["manifest_sha256"]

    def write(d):
        p = tmp_path / "s.json"
        p.write_text(json.dumps(d))
        return p

    def fixed(d):  # a consistent hash, so the check under test is the one that fires
        d["split_sha256"] = odl.split_digest(d["documents"])
        return d

    hole = copy.deepcopy(doc); hole["documents"].pop(IDS[-1]); fixed(hole)
    with pytest.raises(odl.Unjudged, match="does not cover"):
        odl.load_split(write(hole), IDS, lh)
    odd = copy.deepcopy(doc); odd["documents"][IDS[0]] = "both"; fixed(odd)
    with pytest.raises(odl.Unjudged, match="other than tune/holdout"):
        odl.load_split(write(odd), IDS, lh)
    moved = copy.deepcopy(doc); moved["documents"][IDS[0]] = "tune"       # hash left stale
    with pytest.raises(odl.Unjudged, match="split_sha256"):
        odl.load_split(write(moved), IDS, lh)
    other = copy.deepcopy(doc); other["bench_commit"] = "0" * 40
    with pytest.raises(odl.Unjudged, match="bench commit"):
        odl.load_split(write(other), IDS, lh)
    with pytest.raises(odl.Unjudged, match="schema"):
        odl.load_split(write({**doc, "schema": "x"}), IDS, lh)


def test_the_private_labels_file_is_accepted_and_its_split_matches_the_public_one(tmp_path):
    synthetic = {"schema": odl.LABELS_SCHEMA, "bench_commit": odl.PIN, "manifest_sha256": _split()["manifest_sha256"],
                 "documents": [{"doc_id": d, "split": s} for d, s in _split()["documents"].items()]}
    p = tmp_path / "labels.json"
    p.write_text(json.dumps(synthetic))
    assert odl.load_split(p, IDS, synthetic["manifest_sha256"]) == _split()["documents"]
    synthetic["documents"].append(dict(synthetic["documents"][0]))
    p.write_text(json.dumps(synthetic))
    with pytest.raises(odl.Unjudged, match="twice"):
        odl.load_split(p, IDS, synthetic["manifest_sha256"])


def test_split_subcommand_writes_a_scoreless_file(tmp_path):
    labels = {"schema": odl.LABELS_SCHEMA, "bench_commit": odl.PIN, "manifest_sha256": "m", "split_rule": "r",
              "documents": [{"doc_id": d, "split": s, "scores": {"x": {"overall": 0.5}}, "primary_class": "E"}
                            for d, s in _split()["documents"].items()]}
    src, out = tmp_path / "l.json", tmp_path / "s.json"
    src.write_text(json.dumps(labels))
    assert odl.main(["split", "--labels", str(src), "--out", str(out)]) == odl.EXIT_OK
    written = json.loads(out.read_text())
    assert "scores" not in out.read_text() and written["documents"] == _split()["documents"]


def test_private_labels_class_totals_when_the_file_is_given():
    """#969: class totals re-derived from the stored scores. The labels file is private (it carries the
    other engines' scores), so the engine CI passes it with ODL_LABELS; public CI skips loudly."""
    path = os.environ.get("ODL_LABELS")
    if not path or not pathlib.Path(path).is_file():
        pytest.skip("ODL_LABELS is not set: the private labels file (#969) is checked in the engine CI")
    doc = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    assert [x["doc_id"] for x in doc["documents"]] == IDS
    assert dict(collections.Counter(x["primary_class"] for x in doc["documents"])) == EXPECT_COUNTS
    assert {x["doc_id"]: x["split"] for x in doc["documents"]} == _split()["documents"]
    assert doc["manifest_sha256"] == _split()["manifest_sha256"] and doc["bench_commit"] == odl.PIN
    loss = collections.defaultdict(float)
    for x in doc["documents"]:
        s = x["scores"]
        loss[x["primary_class"]] += max(0.0, (s["opendataloader_hybrid"]["overall"]
                                              - s["pdfluent_balanced_tatr"]["overall"]) / 200)
    for c, v in EXPECT_LOSS.items():
        assert round(loss[c], 4) == pytest.approx(v, abs=1e-9), c


def test_ci_verifies_the_corpus_hashes_and_runs_these_tests_with_the_clone():
    text = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    assert "bench.odl verify --fetch" in text and "ODL_CLONE" in text


def test_every_result_names_its_recipe_version_and_configuration():
    info = odl.recipes_version()
    assert isinstance(info["version"], int) and len(info["sha256"]) == 64
    src = (ROOT / "bench" / "odl.py").read_text()
    assert src.count('"recipe_config": recipe_info') == 2   # run.json and scores.json
