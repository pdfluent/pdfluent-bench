# Copyright (c) 2026 Innovation Trigger B.V.
#
# PDFluent is available under two licences, at your option: the GNU AGPLv3, or
# the PDFluent Commercial Licence. See the LICENSE file in this repository --
# that file travels with the copy you received, which a URL does not.
"""#972 DoD: one paper per licence value, exactly CC0 / CC BY / CC BY-SA pass; an olmOCR-bench id is rejected.

Recorded OAI-PMH responses are built from a template, so no test touches the network.
"""
import json

import pytest

from bench import arxiv as A
from bench import registry as R

CC = "http://creativecommons.org/"
LICENCES = {
    "0001.00001": CC + "publicdomain/zero/1.0/",
    "0001.00002": CC + "licenses/by/4.0/",
    "0001.00003": CC + "licenses/by-sa/4.0/",
    "0001.00004": CC + "licenses/by-nc/4.0/",
    "0001.00005": CC + "licenses/by-nd/4.0/",
    "0001.00006": "http://arxiv.org/licenses/nonexclusive-distrib/1.0/",
    "0001.00007": None,
}
PASSING = ["0001.00001", "0001.00002", "0001.00003"]


def record(ident, licence, cats="math.AG math.NT", versions=2, status=None):
    lic = f"<license>{licence}</license>" if licence else ""
    vers = "".join(f'<version version="v{i}"><date>Mon, 0{i} Jan 2024 10:00:00 GMT</date><size>9kb</size></version>'
                   for i in range(1, versions + 1))
    if status:
        return f'<record><header status="deleted"><identifier>oai:arXiv.org:{ident}</identifier><datestamp>2024-02-01</datestamp></header></record>'
    return (f"<record><header><identifier>oai:arXiv.org:{ident}</identifier><datestamp>2024-02-01</datestamp></header>"
            f'<metadata><arXivRaw xmlns="http://arxiv.org/OAI/arXivRaw/"><id>{ident}</id>{vers}'
            f"<title>Title of {ident}</title><authors>A. Author and B. Writer</authors><categories>{cats}</categories>"
            f"{lic}<abstract>x</abstract></arXivRaw></metadata></record>")


def page(records, token=None, size=None):
    tok = f'<resumptionToken completeListSize="{size}">{token}</resumptionToken>' if token else "<resumptionToken/>"
    return ('<?xml version="1.0"?><OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/"><ListRecords>'
            + "".join(records) + tok + "</ListRecords></OAI-PMH>").encode()


def sample_records():
    return A.parse_oai(page([record(i, lic) for i, lic in LICENCES.items()]))["records"]


# ---------------------------------------------------------------- the licence filter

def assert_exactly_the_three_pass(records, **kw):
    kept, rejected = A.filter_records(records, **kw)
    assert [k["id"] for k in kept] == PASSING
    assert sum(rejected.values()) == 4
    return kept, rejected


def test_exactly_cc0_cc_by_and_cc_by_sa_pass_and_every_reject_is_counted_by_value():
    kept, rejected = assert_exactly_the_three_pass(sample_records())
    assert rejected == {"licence:" + CC + "licenses/by-nc/4.0/": 1, "licence:" + CC + "licenses/by-nd/4.0/": 1,
                        "licence:http://arxiv.org/licenses/nonexclusive-distrib/1.0/": 1, "missing": 1}
    assert [(k["licence_name"], k["share_alike"]) for k in kept] == [("CC0-1.0", False), ("CC-BY-4.0", False),
                                                                    ("CC-BY-SA-4.0", True)]


@pytest.mark.parametrize("url", [
    CC + "licenses/by/4.0", "https://creativecommons.org/licenses/by/4.0/", CC + "licenses/by/9.0/",
    CC + "licenses/by-nc-sa/4.0/", CC + "licenses/by-nc-nd/4.0/", CC + "licenses/by-sa/4.0/legalcode",
    CC + "publicdomain/mark/1.0/", " ", ""])
def test_near_misses_and_other_licences_are_rejected(url):
    kept, _ = A.filter_records([{"id": "0002.00001", "license": url}])
    assert kept == []


@pytest.mark.parametrize("version", ["1.0", "2.0", "2.5", "3.0", "4.0"])
def test_any_version_of_cc_by_and_cc_by_sa_is_kept(version):
    for kind in ("by", "by-sa"):
        kept, _ = A.filter_records([{"id": "0002.00002", "license": f"{CC}licenses/{kind}/{version}/"}])
        assert len(kept) == 1


def test_widening_the_allow_list_by_one_value_makes_the_fixture_fail():
    widened = {**A.ALLOWED, CC + "licenses/by-nc/4.0/": "CC-BY-NC-4.0"}
    with pytest.raises(AssertionError):
        kept, _ = A.filter_records(sample_records(), allowed=widened)
        assert [k["id"] for k in kept] == PASSING


def test_narrowing_the_allow_list_by_one_value_makes_the_fixture_fail():
    narrowed = {k: v for k, v in A.ALLOWED.items() if "by-sa/4.0" not in k}
    with pytest.raises(AssertionError):
        kept, _ = A.filter_records(sample_records(), allowed=narrowed)
        assert [k["id"] for k in kept] == PASSING


def test_the_allow_list_is_exactly_what_the_issue_names():
    versions = ("1.0", "2.0", "2.5", "3.0", "4.0")
    want = {CC + "publicdomain/zero/1.0/": "CC0-1.0"}
    want.update({f"{CC}licenses/by/{v}/": f"CC-BY-{v}" for v in versions})
    want.update({f"{CC}licenses/by-sa/{v}/": f"CC-BY-SA-{v}" for v in versions})
    assert A.ALLOWED == want


# ---------------------------------------------------------------- olmOCR-bench exclusion

def write_registry(tmp_path, olm_docs):
    entries = [{"file": f"arxiv_math/{d.split(':')[1]}_pg1.pdf", "page": 1, "doc": d, "file_sha256": "a" * 64,
                "text": None, "render": None} for d in olm_docs]
    R.write_jsonl(tmp_path / "holdouts" / "olmocr-bench.jsonl", entries)
    (tmp_path / "registry.json").write_text(json.dumps({"holdouts": {"olmocr-bench": {
        "file": "holdouts/olmocr-bench.jsonl", "seal": R.seal(entries), "status": "complete"}}}))
    return tmp_path


def test_a_planted_olmocr_bench_arxiv_id_is_rejected_by_id(tmp_path):
    reg = write_registry(tmp_path, ["arxiv:0001.00002", "olmocr-bench:old_scans/x"])
    excluded = A.holdout_arxiv_ids(reg)
    assert excluded == {"0001.00002": "olmocr-bench"}
    kept, rejected = A.filter_records(sample_records(), excluded)
    assert [k["id"] for k in kept] == ["0001.00001", "0001.00003"]
    assert rejected["sealed-holdout:olmocr-bench"] == 1


def test_without_the_exclusion_the_planted_id_would_pass(tmp_path):
    with pytest.raises(AssertionError):
        kept, _ = A.filter_records(sample_records(), {})
        assert "0001.00002" not in [k["id"] for k in kept]


# ---------------------------------------------------------------- OAI-PMH parsing

def test_parse_reads_attribution_fields_token_and_skips_deleted_records():
    got = A.parse_oai(page([record("0001.00002", LICENCES["0001.00002"]), record("0001.00009", None, status="deleted")],
                           token="verb%3DListRecords%26skip%3D1", size=5))
    assert got["token"] == "verb%3DListRecords%26skip%3D1" and got["size"] == 5
    (r,) = got["records"]
    assert (r["id"], r["v"], r["created"], r["authors"], r["title"], r["categories"]) == (
        "0001.00002", 2, "2024-01-01", "A. Author and B. Writer", "Title of 0001.00002", ["math.AG", "math.NT"])
    assert r["license"] == LICENCES["0001.00002"]


def test_parse_reports_an_oai_error():
    xml = b'<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/"><error code="badResumptionToken">gone</error></OAI-PMH>'
    assert A.parse_oai(xml)["error"] == ("badResumptionToken", "gone")


def test_the_manifest_record_carries_licence_url_authors_title_version_date_and_route():
    kept, _ = A.filter_records(sample_records())
    m = A.manifest_record(kept[2], "2026-10-24")
    assert m["licence_url"] == LICENCES["0001.00003"] and m["licence"] == "CC-BY-SA-4.0" and m["share_alike"] is True
    assert (m["arxiv_id"], m["version"], m["authors"], m["title"]) == ("0001.00003", "v2", "A. Author and B. Writer",
                                                                      "Title of 0001.00003")
    assert (m["retrieved"], m["source_route"]) == ("2026-10-24", "oai-pmh:arXivRaw")


# ---------------------------------------------------------------- politeness

class Clock:
    def __init__(self):
        self.t = 1000.0
        self.slept = []

    def now(self):
        return self.t

    def sleep(self, s):
        self.slept.append(s)
        self.t += s


def make_http(responses, interval=A.API_INTERVAL):
    clock = Clock()
    calls = []

    def opener(url, timeout):
        calls.append((clock.t, url))
        clock.t += 0.2                     # a request takes time
        status, headers, body = responses(url) if callable(responses) else responses.pop(0)
        return status, headers, body
    return A.Http(interval, clock=clock.now, sleep=clock.sleep, opener=opener), clock, calls


def test_requests_start_at_least_three_seconds_apart_on_one_connection():
    http, clock, calls = make_http(lambda url: (200, {}, b"x"))
    for _ in range(5):
        http.get("https://example.test/a")
    starts = [t for t, _ in calls]
    assert all(b - a >= 3.0 for a, b in zip(starts, starts[1:]))
    assert A.API_INTERVAL >= 3.0


def test_a_503_with_retry_after_is_waited_out_through_the_rate_limit():
    http, clock, calls = make_http([(503, {"Retry-After": "20"}, b""), (200, {}, b"ok")])
    status, _, body = A.get_retrying(http, "https://example.test/")
    assert (status, body) == (200, b"ok") and 20 in clock.slept
    assert calls[1][0] - calls[0][0] >= 20


def test_the_user_agent_identifies_the_project_and_a_contact():
    assert "hello@pdfluent.com" in A.USER_AGENT and "pdfluent" in A.USER_AGENT


def test_no_requester_pays_bucket_client_is_used():
    src = open(A.__file__, encoding="utf-8").read()
    assert "boto" not in src and "s3://" not in src and "amazonaws" not in src


# ---------------------------------------------------------------- the resumable harvest

def serve(pages):
    def responses(url):
        if "resumptionToken=" in url:
            n = int(url.rsplit("page", 1)[1].split("&")[0].split("%26")[0]) if "page" in url else 0
            return 200, {}, pages[n]
        return 200, {}, pages[0]
    return responses


def test_harvest_resumes_from_the_stored_token_and_loses_nothing(tmp_path):
    pages = [page([record("0001.00001", LICENCES["0001.00001"])], token="page1"),
             page([record("0001.00002", LICENCES["0001.00002"])], token="page2"),
             page([record("0001.00003", LICENCES["0001.00003"])])]
    http, _, calls = make_http(serve(pages))
    state = A.harvest(tmp_path, http, max_pages=1)
    assert state["done"] is False and state["token"] == "page1"
    http2, _, calls2 = make_http(serve(pages))              # a new process
    state = A.harvest(tmp_path, http2)
    assert state["done"] is True and state["records"] == 3
    assert len(calls2) == 2 and "resumptionToken=page1" in calls2[0][1]
    assert sorted(r["id"] for r in A.read_records(tmp_path)) == ["0001.00001", "0001.00002", "0001.00003"]
    http3, _, calls3 = make_http(serve(pages))
    A.harvest(tmp_path, http3)
    assert calls3 == []                                      # nothing left to do, nothing requested


def test_a_refused_token_restarts_from_the_last_datestamp(tmp_path):
    bad = b'<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/"><error code="badResumptionToken">expired</error></OAI-PMH>'
    first = page([record("0001.00001", LICENCES["0001.00001"])], token="stale")
    seq = [(200, {}, first), (200, {}, bad), (200, {}, page([record("0001.00002", LICENCES["0001.00002"])]))]
    http, _, calls = make_http(seq)
    state = A.harvest(tmp_path, http)
    assert state["done"] and "from=2024-02-01" in calls[2][1]
    assert sorted(r["id"] for r in A.read_records(tmp_path)) == ["0001.00001", "0001.00002"]


def test_the_harvest_uses_the_oai_endpoint_only(tmp_path):
    http, _, calls = make_http(lambda url: (200, {}, page([])))
    A.harvest(tmp_path, http)
    assert all(u.startswith("https://oaipmh.arxiv.org/oai?verb=ListRecords") for _, u in calls)


# ---------------------------------------------------------------- the sample

def pool(n_math=60, n_other=60):
    out = []
    cats_m, cats_o = ["math.AG", "math.NT", "math.PR", "hep-th"], ["cs.CV", "cs.CL", "q-bio.NC", "econ.GN", "eess.SP"]
    for i in range(n_math):
        out.append({"id": f"24{i % 12 + 1:02d}.{i:05d}", "categories": [cats_m[i % 4]], "license": CC + "licenses/by/4.0/"})
    for i in range(n_other):
        out.append({"id": f"25{i % 12 + 1:02d}.{i + 500:05d}", "categories": [cats_o[i % 5]], "license": CC + "licenses/by/4.0/"})
    out.append({"id": "2301.00001", "categories": ["cs.CV"], "license": CC + "licenses/by/4.0/"})
    return A.filter_records(out)[0]


def test_the_sample_alternates_math_heavy_and_other_and_spreads_over_categories():
    got = A.draw_sample(pool(), 40, "s", min_id="2401.00000")
    assert len(got) == 40 and "2301.00001" not in {r["id"] for r in got}
    assert sum(A.math_heavy(r) for r in got) == 20
    assert len({A.primary(r) for r in got}) == 9
    first = got[:18]                                          # any prefix is already spread
    assert len({A.primary(r) for r in first}) == 9


def test_the_sample_is_deterministic_and_has_no_duplicates():
    a, b = A.draw_sample(pool(), 50, "seed"), A.draw_sample(pool(), 50, "seed")
    assert [r["id"] for r in a] == [r["id"] for r in b] and len({r["id"] for r in a}) == 50
    assert [r["id"] for r in a] != [r["id"] for r in A.draw_sample(pool(), 50, "other")]


def test_a_sample_larger_than_the_pool_returns_the_whole_pool():
    assert len(A.draw_sample(pool(5, 5), 100, "s")) == 11


# ---------------------------------------------------------------- the gated content fetch

ROBOTS = b"User-agent: *\nCrawl-delay: 15\nAllow: /pdf\nAllow: /html\nDisallow: /e-print\n"
REC = {"arxiv_id": "2401.00001", "version": "v2"}


def content_responses(url):
    if url.endswith("robots.txt"):
        return 200, {}, ROBOTS
    return (404, {}, b"") if "/html/" in url and "2401.00002" in url else (200, {}, b"%PDF-" + url.encode())


def test_content_is_refused_without_a_named_arrangement(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("ARXIV_CONTENT_ARRANGEMENT", raising=False)
    (tmp_path / "sample.jsonl").write_text(json.dumps(REC) + "\n")
    assert A.main(["fetch", "--data", str(tmp_path)]) == 2
    assert "special arrangement" in capsys.readouterr().err
    assert not (tmp_path / "papers").exists()
    with pytest.raises(A.Refused):
        A.content_gate({})
    assert A.content_gate({"ARXIV_CONTENT_ARRANGEMENT": "mail from arXiv, 2026-10-30"})


def test_fetch_never_requests_a_path_robots_disallows_and_waits_the_crawl_delay(tmp_path):
    http, clock, calls = make_http(content_responses, interval=A.CONTENT_INTERVAL)
    counts = A.fetch_content(tmp_path, [REC], http, "ref-1")
    urls = [u for _, u in calls]
    assert not any("/e-print/" in u for u in urls)
    assert urls == ["https://arxiv.org/robots.txt", "https://arxiv.org/pdf/2401.00001v2", "https://arxiv.org/html/2401.00001v2"]
    assert all(b - a >= 15.0 for (a, _), (b, _) in zip(calls, calls[1:]))
    assert counts == {"fetched": 2, "skipped_done": 0, "disallowed": 1, "absent": 0}
    rows = [json.loads(l) for l in (tmp_path / "papers" / "ledger.jsonl").read_text().splitlines()]
    assert {r["kind"]: r["status"] for r in rows} == {"pdf": "ok", "html": "ok", "source": "disallowed-by-robots"}
    assert (tmp_path / "papers" / "2401.00001" / "pdf.pdf").read_bytes().startswith(b"%PDF-")


def test_fetch_resumes_and_requests_nothing_twice(tmp_path):
    other = {"arxiv_id": "2401.00002", "version": "v1"}
    http, _, calls = make_http(content_responses, interval=A.CONTENT_INTERVAL)
    A.fetch_content(tmp_path, [REC, other], http, "ref-1", limit=1)
    http2, _, calls2 = make_http(content_responses, interval=A.CONTENT_INTERVAL)
    counts = A.fetch_content(tmp_path, [REC, other], http2, "ref-1")
    fetched = [u for _, u in calls + calls2 if "robots" not in u]
    assert len(fetched) == len(set(fetched)) == 4            # pdf and html of each paper, once
    assert counts["skipped_done"] == 1
    rows = [json.loads(l) for l in (tmp_path / "papers" / "ledger.jsonl").read_text().splitlines()]
    assert {(r["arxiv_id"], r["kind"]): r["status"] for r in rows}[("2401.00002", "html")] == "absent"


def test_robots_that_cannot_be_read_stops_the_fetch(tmp_path):
    http, _, calls = make_http(lambda url: (500, {}, b""))
    http.sleep = lambda s: None
    with pytest.raises((A.Refused, RuntimeError)):
        A.fetch_content(tmp_path, [REC], http, "ref-1")
    assert not any("/pdf/" in u for _, u in calls)


# ---------------------------------------------------------------- report and sample end to end

def harvested(tmp_path):
    pages = [page([record(i, lic) for i, lic in list(LICENCES.items())[:4]], token="page1"),
             page([record(i, lic) for i, lic in list(LICENCES.items())[4:]])]
    http, _, _ = make_http(serve(pages))
    A.harvest(tmp_path, http)
    return tmp_path


def test_the_report_counts_every_licence_value_and_the_eligible_pool(tmp_path):
    rep = A.licence_report(harvested(tmp_path), {"0001.00002": "olmocr-bench"})
    assert rep["records"] == 7 and rep["eligible"] == 2
    assert rep["eligible_per_licence"] == {"CC-BY-SA-4.0": 1, "CC0-1.0": 1} and rep["eligible_share_alike"] == 1
    assert rep["per_licence_value"][CC + "licenses/by/4.0/"] == 1 and rep["per_licence_value"]["(missing)"] == 1
    assert rep["rejected"]["sealed-holdout:olmocr-bench"] == 1 and rep["rejected"]["missing"] == 1
    assert sum(rep["rejected"].values()) + rep["eligible"] == rep["records"]


def test_the_sample_file_holds_the_attribution_fields_and_only_eligible_papers(tmp_path):
    data = harvested(tmp_path)
    reg = write_registry(tmp_path / "reg", ["arxiv:0001.00002"])
    assert A.main(["sample", "--data", str(data), "--registry", str(reg), "-n", "10", "--min-id", "0000.00000"]) == 0
    rows = [json.loads(line) for line in (data / "sample.jsonl").read_text().splitlines()]
    assert sorted(r["arxiv_id"] for r in rows) == ["0001.00001", "0001.00003"]
    for r in rows:
        assert r["licence_url"] in A.ALLOWED and r["authors"] and r["title"] and r["version"] and r["retrieved"]
