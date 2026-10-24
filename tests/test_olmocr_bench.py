# Copyright (c) 2026 Innovation Trigger B.V.
#
# PDFluent is available under two licences, at your option: the GNU AGPLv3, or
# the PDFluent Commercial Licence. See the LICENSE file in this repository --
# that file travels with the copy you received, which a URL does not.
"""olmocr-bench: the gate, the manifest and pin, the blindness rule, the loud skip.

No olmOCR-bench data is needed: everything runs on synthetic documents."""
import json
import pathlib
import types

import pytest

from bench import odl, olmocr

ROOT = pathlib.Path(__file__).resolve().parents[1]
DOCS = ["pdfs/arxiv_math/2301_12345_pg3.pdf", "pdfs/old_scans/zebra7.pdf", "pdfs/tables/quarterly_filing.pdf"]

SUMMARY = """
Candidate: pdfluent-fast
  [FAIL] Test 2301_12345_pg3_math_07 on arxiv_math/2301_12345_pg3 page 1 average pass ratio: 0.000 (0/1 repeats passed). Ex: nope
  [FAIL] Test zebra7_order_2 on old_scans/zebra7 page 1 average pass ratio: 0.000 (0/1 repeats passed). Ex: nope
  Average Score: 71.2% (95% CI: [70.0%, 72.1%]) over 7010 tests.
============================================================
Final Summary with 95% Confidence Intervals:
pdfluent-fast        : Average Score: 71.2% ± 1.1% (average of per-JSONL scores)
    absent  : 90.0% average pass rate over 823 tests

    Results by JSONL file:
        arxiv_math.jsonl              : 50.0% (1500/3000 tests)
        baseline.jsonl                : 99.0% (990/1000 tests)
        headers_footers.jsonl         : 80.0% (800/1000 tests)
"""


def result_of(overall=0.7, categories=None, recipe="fast", mhash="m1"):
    categories = categories or {"arxiv_math": 0.5, "baseline": 0.99, "headers_footers": 0.8}
    return {"recipe": recipe, "manifest_hash": mhash, "scores": {"overall": overall, "categories": categories}}


def baseline_of(result, tol=0.005):
    return olmocr.make_baseline(result, tol)


# --- the scorer's output ---------------------------------------------------

def test_parse_summary_takes_aggregates_and_drops_the_per_test_lines():
    parsed = olmocr.parse_summary(SUMMARY)
    assert parsed["categories"] == {"arxiv_math": 0.5, "baseline": 0.99, "headers_footers": 0.8}
    assert parsed["overall"] == pytest.approx((0.5 + 0.99 + 0.8) / 3)
    text = json.dumps(parsed)
    assert "2301_12345" not in text and "zebra7" not in text


def test_parse_summary_refuses_what_it_cannot_read():
    with pytest.raises(odl.Unjudged):
        olmocr.parse_summary("nothing here")
    with pytest.raises(odl.Unjudged, match="errors"):
        olmocr.parse_summary("Final Summary\npdfluent-fast : FAILED (errors)\n Results by JSONL file:\n")


# --- the threshold logic ---------------------------------------------------

def test_same_scores_pass():
    r = result_of()
    assert olmocr.check(r, baseline_of(r)) == []


def test_a_drop_inside_the_tolerance_passes_and_one_outside_fails():
    base = baseline_of(result_of())
    inside = result_of(categories={"arxiv_math": 0.4955, "baseline": 0.99, "headers_footers": 0.8})
    outside = result_of(categories={"arxiv_math": 0.4940, "baseline": 0.99, "headers_footers": 0.8})
    assert olmocr.check(inside, base) == []
    failures = olmocr.check(outside, base)
    assert failures == ["recipe=fast scope=arxiv_math: dropped more than the tolerance 0.005 below the baseline"]


def test_exactly_the_tolerance_is_not_a_failure_and_a_gain_never_fails():
    base = baseline_of(result_of())
    assert olmocr.check(result_of(categories={"arxiv_math": 0.495, "baseline": 0.99, "headers_footers": 0.8}), base) == []
    assert olmocr.check(result_of(overall=0.9, categories={"arxiv_math": 0.9, "baseline": 1.0, "headers_footers": 0.9}),
                        base) == []


def test_the_overall_score_is_gated_on_its_own():
    base = baseline_of(result_of())
    failures = olmocr.check(result_of(overall=0.69), base)
    assert any("scope=overall" in f for f in failures)


def test_a_category_missing_from_the_run_fails():
    base = baseline_of(result_of())
    failures = olmocr.check(result_of(categories={"arxiv_math": 0.5, "baseline": 0.99}), base)
    assert any("scope=headers_footers" in f and "absent" in f for f in failures)


def test_another_recipe_or_another_manifest_cannot_be_judged_against_a_baseline():
    base = baseline_of(result_of())
    assert "baseline is for recipe" in olmocr.check(result_of(recipe="balanced+tatr"), base)[0]
    assert "another manifest" in olmocr.check(result_of(mhash="m2"), base)[0]


def test_numbers_appear_only_when_asked_for():
    base = baseline_of(result_of())
    worse = result_of(overall=0.6)
    assert "0.6000" not in " ".join(olmocr.check(worse, base))
    assert "0.6000" in " ".join(olmocr.check(worse, base, show=True))


def test_mutation_the_gate_bites():
    """Break the comparison in a copy of the module: the drop test must then fail to see the drop."""
    source = (ROOT / "bench" / "olmocr.py").read_text(encoding="utf-8")
    assert "got < floor - tol - EPS" in source
    mutated = types.ModuleType("olmocr_mutated")
    mutated.__dict__["__file__"] = str(ROOT / "bench" / "olmocr.py")
    exec(compile(source.replace("got < floor - tol - EPS", "got < floor - 1.0"), "olmocr_mutated", "exec"),
         mutated.__dict__)
    base = baseline_of(result_of())
    drop = result_of(overall=0.6, categories={"arxiv_math": 0.3, "baseline": 0.99, "headers_footers": 0.8})
    assert olmocr.check(drop, base) != []
    assert mutated.check(drop, base) == []   # the mutant is blind, so the real check is what catches it


# --- manifest and pin ------------------------------------------------------

def make_data(tmp_path):
    data = tmp_path / "data" / "bench_data"
    for rel in DOCS:
        (data / rel).parent.mkdir(parents=True, exist_ok=True)
        (data / rel).write_bytes(b"%PDF-1.4 " + rel.encode())
    (data / "old_scans.jsonl").write_text("{}\n", encoding="utf-8")
    return data


def test_the_manifest_of_a_clean_copy_is_clean_and_a_change_is_named(tmp_path):
    data = make_data(tmp_path)
    manifest = olmocr.build_manifest(data)
    assert olmocr.manifest_problems(data, manifest) == []
    (data / DOCS[1]).write_bytes(b"changed")
    (data / DOCS[2]).unlink()
    (data / "pdfs" / "extra.pdf").write_bytes(b"x")
    problems = olmocr.manifest_problems(data, manifest)
    assert any(DOCS[1] in p and "sha256" in p for p in problems)
    assert any(DOCS[2] in p and "missing" in p for p in problems)
    assert any("extra.pdf" in p and "not in the manifest" in p for p in problems)


def test_the_committed_manifest_matches_the_pins():
    manifest = olmocr.load_manifest()
    assert manifest["hf_revision"] == olmocr.HF_REVISION == "54a96a6fb6a2bd3b297e59869491db4d3625b711"
    assert manifest["scorer_commit"] == olmocr.SCORER_PIN
    assert sum(1 for n in manifest["files"] if n.endswith(".pdf")) == 1403
    assert len(manifest["files"]) == 1403 + 7


def test_a_manifest_for_another_revision_or_with_a_wrong_hash_is_refused(tmp_path):
    good = json.loads(olmocr.MANIFEST.read_text(encoding="utf-8"))
    for change in ({"hf_revision": "0" * 40}, {"scorer_commit": "0" * 40}, {"manifest_hash": "0" * 64}):
        path = tmp_path / "m.json"
        path.write_text(json.dumps({**good, **change}), encoding="utf-8")
        with pytest.raises(odl.Unjudged):
            olmocr.load_manifest(path)


def test_the_data_root_may_not_be_under_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / "x").mkdir(parents=True)
    with pytest.raises(odl.Unjudged, match="home"):
        olmocr.resolve_root(str(home / "x"), home=home)
    monkeypatch.delenv("OLMOCR_BENCH_ROOT", raising=False)
    with pytest.raises(odl.Unjudged, match="no data root"):
        olmocr.resolve_root(None)
    assert olmocr.resolve_root(str(tmp_path / "ext"), home=home) == (tmp_path / "ext").resolve()


# --- a whole run on stand-ins ----------------------------------------------

@pytest.fixture
def run_env(tmp_path, monkeypatch):
    data = make_data(tmp_path)
    manifest = olmocr.build_manifest(data)
    monkeypatch.setattr(olmocr, "load_manifest", lambda *a, **k: manifest)
    monkeypatch.setattr(olmocr, "ensure_scorer", lambda root, fetch: tmp_path / "scorer")
    monkeypatch.setattr(olmocr, "scorer_problem", lambda *a, **k: None)
    monkeypatch.setattr(olmocr, "extract", lambda *a, **k: (0.5, 0))
    monkeypatch.setattr(odl, "installed_pdfluent", lambda wheel: {"version": "1.2.0", "wheel_sha256": "ab" * 32})
    monkeypatch.setattr(olmocr, "score", lambda *a, **k: {"overall": 0.7, "categories": {"arxiv_math": 0.5, "baseline": 0.99},
                                                          "test_counts": {"arxiv_math": 10, "baseline": 3}})
    monkeypatch.setenv("OLMOCR_BENCH_ROOT", str(tmp_path))
    monkeypatch.setenv("PDFLUENT_MODEL_DIR", str(tmp_path))
    return types.SimpleNamespace(tmp=tmp_path, manifest=manifest, monkeypatch=monkeypatch)


def run_cli(env, *extra, recipe="fast"):
    out = env.tmp / "out"
    code = olmocr.main(["run", "--recipe", recipe, "--out", str(out), *extra])
    return code, out


def test_a_run_writes_aggregates_only_and_no_file_names_a_document(run_env, capsys):
    code, out = run_cli(run_env, "--no-gate", "--show-scores")
    assert code == 0
    names = [n for n in run_env.manifest["files"] if n.endswith(".pdf")]
    assert olmocr.blindness_violations(out, names) == []
    captured = capsys.readouterr()
    for name in names:
        assert name.rsplit("/", 1)[-1].removesuffix(".pdf") not in captured.out + captured.err
    run = json.loads((out / "run.json").read_text(encoding="utf-8"))
    assert "documents" not in run and run["scores"]["overall"] == 0.7
    assert "ODC-BY-1.0" in run["attribution"] and "Allen Institute for AI" in run["attribution"]
    assert run["recipe_config"]["extract_markdown_kwargs"] == {"quality": "fast"}


def test_blindness_the_check_catches_a_per_document_file(run_env):
    """Mutation: plant a per-document score in the output and the rule must fire."""
    _, out = run_cli(run_env, "--no-gate")
    names = [n for n in run_env.manifest["files"] if n.endswith(".pdf")]
    assert olmocr.blindness_violations(out, names) == []
    (out / "per-doc.json").write_text(json.dumps({"zebra7": 0.0}), encoding="utf-8")
    assert olmocr.blindness_violations(out, names)


def test_a_scorer_result_with_per_document_data_is_never_written(run_env):
    run_env.monkeypatch.setattr(olmocr, "score", lambda *a, **k: {
        "overall": 0.7, "categories": {"arxiv_math": 0.5}, "documents": {"zebra7": 0.0}})
    code, out = run_cli(run_env, "--no-gate")
    assert code == odl.EXIT_UNJUDGED
    assert not (out / "run.json").exists()


def test_the_gate_end_to_end_passes_then_fails_on_a_drop(run_env, tmp_path):
    base = baseline_of(result_of(overall=0.7, categories={"arxiv_math": 0.5, "baseline": 0.99}, mhash=run_env.manifest["manifest_hash"]))
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps(base), encoding="utf-8")
    assert run_cli(run_env, "--baseline", str(path))[0] == odl.EXIT_OK
    run_env.monkeypatch.setattr(olmocr, "score", lambda *a, **k: {
        "overall": 0.7, "categories": {"arxiv_math": 0.49, "baseline": 0.99}, "test_counts": {}})
    assert run_cli(run_env, "--baseline", str(path))[0] == odl.EXIT_FAIL


def test_extraction_errors_fail_the_run_by_count_not_by_name(run_env, capsys):
    run_env.monkeypatch.setattr(olmocr, "extract", lambda *a, **k: (0.5, 2))
    code, out = run_cli(run_env, "--no-gate")
    assert code == odl.EXIT_FAIL
    assert json.loads((out / "run.json").read_text(encoding="utf-8"))["extraction_errors"] == 2


def test_no_baseline_cannot_be_judged(run_env):
    assert run_cli(run_env)[0] == odl.EXIT_UNJUDGED


def test_missing_models_skip_loudly_with_exit_3(run_env, capsys):
    run_env.monkeypatch.delenv("PDFLUENT_MODEL_DIR")
    code, _ = run_cli(run_env, "--no-gate", recipe="balanced+tatr")
    assert code == odl.EXIT_SKIPPED
    assert "SKIPPED (not a pass): " in capsys.readouterr().err
    assert run_cli(run_env, "--no-gate", "--allow-skip", recipe="balanced+tatr")[0] == odl.EXIT_OK


def test_a_baseline_holds_aggregates_only():
    base = olmocr.make_baseline(result_of())
    assert set(base) >= {"scores", "recipe", "manifest_hash", "hf_revision", "scorer_commit", "attribution"}
    olmocr.assert_aggregates_only(base)
    with pytest.raises(odl.Unjudged):
        olmocr.assert_aggregates_only({"scores": {"documents": {}}})
