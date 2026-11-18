# Copyright (c) 2026 Innovation Trigger B.V.
#
# PDFluent is available under two licences, at your option: the GNU AGPLv3, or
# the PDFluent Commercial Licence. See the LICENSE file in this repository --
# that file travels with the copy you received, which a URL does not.
"""arXiv harvest with a licence filter (#972): metadata through OAI-PMH, content behind a gate.

    python -m bench.arxiv harvest  --data <dir>               metadata of every record, resumable
    python -m bench.arxiv report   --data <dir>               licence counts, eligible pool, reject reasons
    python -m bench.arxiv sample   --data <dir> -n 2000       licence-eligible sample, spread over categories
    python -m bench.arxiv fetch    --data <dir>               PDF + HTML of the sample (GATED, see below)

Routes: the OAI-PMH endpoint (oaipmh.arxiv.org/oai, metadata prefix arXivRaw), which arXiv's Terms of
Use for the APIs cover: at most one request per three seconds, one connection at a time. The requester-pays
S3 bulk bucket is not used (it costs money). No scraping of the site. The terms as read, with the date, are
in datasets/arxiv/EVIDENCE.md.

Licence filter: a paper is kept when its metadata `license` is exactly one of the CC0 1.0, CC BY or
CC BY-SA URLs in ALLOWED (any version arXiv offers). Everything else is rejected and counted by value.
CC BY-SA papers carry `share_alike: true` so a later release of derived data can leave them out.

Content (`fetch`) is gated. arXiv's OAI data policy says full-content harvesting is not permitted except by
special arrangement, and its robots.txt disallows /e-print (the source tarball) and asks for a 15 s crawl
delay. `fetch` therefore refuses to run unless ARXIV_CONTENT_ARRANGEMENT names the arrangement with arXiv,
reads robots.txt before every host, sleeps at least the larger of 15 s and its Crawl-delay between requests,
and never requests a path robots.txt disallows.
"""
from __future__ import annotations

import argparse
import datetime
import gzip
import hashlib
import json
import os
import pathlib
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
import xml.etree.ElementTree as ET

OAI_URL = "https://oaipmh.arxiv.org/oai"
USER_AGENT = "pdfluent-bench-data/0.1 (hello@pdfluent.com; metadata harvest, 1 request per 3 s)"
API_INTERVAL = 3.0          # arXiv API Terms of Use: no more than one request every three seconds
CONTENT_INTERVAL = 15.0     # arxiv.org robots.txt Crawl-delay
OAI_NS = "{http://www.openarchives.org/OAI/2.0/}"
RAW_NS = "{http://arxiv.org/OAI/arXivRaw/}"

# Exact licence URLs, as arXiv emits them. Nothing else is kept.
_VERSIONS = ("1.0", "2.0", "2.5", "3.0", "4.0")
ALLOWED: dict[str, str] = {"http://creativecommons.org/publicdomain/zero/1.0/": "CC0-1.0"}
for _v in _VERSIONS:
    ALLOWED[f"http://creativecommons.org/licenses/by/{_v}/"] = f"CC-BY-{_v}"
    ALLOWED[f"http://creativecommons.org/licenses/by-sa/{_v}/"] = f"CC-BY-SA-{_v}"

MATH_HEAVY = ("math.",)
MATH_HEAVY_EXACT = {"math-ph", "stat.TH", "cs.IT", "cs.LO", "cs.DM", "cs.CC", "cs.FL", "quant-ph", "hep-th", "gr-qc"}


# ---------------------------------------------------------------- licence filter

def licence_verdict(url: str | None) -> tuple[bool, str]:
    """(kept, reason). The reason names the value, so the reject report can count it."""
    if url is None or not url.strip():
        return False, "missing"
    if url in ALLOWED:
        return True, ALLOWED[url]
    return False, "licence:" + url


def filter_records(records: list[dict], excluded_ids: dict[str, str] | None = None,
                   allowed: dict[str, str] | None = None) -> tuple[list[dict], dict[str, int]]:
    """Keep eligible records; count every rejection by reason. `excluded_ids` maps arXiv id -> holdout name."""
    excluded_ids = excluded_ids or {}
    allowed = ALLOWED if allowed is None else allowed
    kept, rejected = [], {}
    for r in records:
        url = r.get("license")
        if r["id"] in excluded_ids:
            reason = "sealed-holdout:" + excluded_ids[r["id"]]
        elif url is None or not url.strip():
            reason = "missing"
        elif url not in allowed:
            reason = "licence:" + url
        else:
            kept.append({**r, "licence_name": allowed[url], "share_alike": "-SA-" in allowed[url]})
            continue
        rejected[reason] = rejected.get(reason, 0) + 1
    return kept, rejected


# ---------------------------------------------------------------- OAI-PMH parsing

def parse_oai(xml: bytes) -> dict:
    """{"records": [...], "token": str|None, "size": int|None, "error": (code, text)|None}."""
    root = ET.fromstring(xml)
    err = root.find(OAI_NS + "error")
    if err is not None:
        return {"records": [], "token": None, "size": None, "error": (err.get("code"), (err.text or "").strip())}
    out = []
    listing = root.find(OAI_NS + "ListRecords")
    for rec in (listing if listing is not None else []):
        if rec.tag != OAI_NS + "record":
            continue
        header = rec.find(OAI_NS + "header")
        if header is not None and header.get("status") == "deleted":
            continue
        raw = rec.find(f"{OAI_NS}metadata/{RAW_NS}arXivRaw")
        if raw is None:
            continue

        def text(tag):
            el = raw.find(RAW_NS + tag)
            return " ".join((el.text or "").split()) if el is not None and el.text else None

        versions = raw.findall(RAW_NS + "version")
        cats = (text("categories") or "").split()
        out.append({"id": text("id"), "v": len(versions) or None,
                    "created": _iso(versions[0].findtext(RAW_NS + "date")) if versions else None,
                    "datestamp": header.findtext(OAI_NS + "datestamp") if header is not None else None,
                    "title": text("title"), "authors": text("authors"), "categories": cats,
                    "license": text("license"), "doi": text("doi")})
    tok = listing.find(OAI_NS + "resumptionToken") if listing is not None else None
    token = (tok.text or "").strip() if tok is not None else None
    size = None
    if tok is not None and (tok.get("completeListSize") or "").isdigit():
        size = int(tok.get("completeListSize"))
    return {"records": out, "token": token or None, "size": size, "error": None}


def _iso(rfc822: str | None) -> str | None:
    try:
        return datetime.datetime.strptime(rfc822, "%a, %d %b %Y %H:%M:%S %Z").date().isoformat()
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- polite HTTP

class Http:
    """One connection at a time, at least `interval` seconds between the starts of two requests."""

    def __init__(self, interval: float, clock=time.monotonic, sleep=time.sleep, opener=None):
        self.interval, self.clock, self.sleep = interval, clock, sleep
        self.last: float | None = None
        self.opener = opener or self._urlopen
        self.requests = 0

    @staticmethod
    def _urlopen(url: str, timeout: float) -> tuple[int, dict, bytes]:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    def get(self, url: str, timeout: float = 120.0) -> tuple[int, dict, bytes]:
        if self.last is not None:
            wait = self.interval - (self.clock() - self.last)
            if wait > 0:
                self.sleep(wait)
        self.last = self.clock()
        self.requests += 1
        return self.opener(url, timeout)


def get_retrying(http: Http, url: str, tries: int = 6) -> tuple[int, dict, bytes]:
    """503 with Retry-After is honoured; other failures back off. Every attempt goes through the rate limit."""
    for attempt in range(tries):
        try:
            status, headers, body = http.get(url)
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            print(f"[arxiv] {type(e).__name__} on attempt {attempt + 1}: {e}", file=sys.stderr)
            http.sleep(min(600, 30 * 2 ** attempt))
            continue
        if status in (429, 503):
            retry = next((v for k, v in headers.items() if k.lower() == "retry-after"), None)
            http.sleep(min(3600, float(retry)) if retry and retry.replace(".", "").isdigit() else 60 * (attempt + 1))
            continue
        return status, headers, body
    raise RuntimeError(f"giving up on {url} after {tries} attempts")


# ---------------------------------------------------------------- the metadata harvest

def _atomic_write(path: pathlib.Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def harvest(data: pathlib.Path, http: Http | None = None, max_pages: int | None = None,
            name: str = "all", from_: str | None = None, until: str | None = None) -> dict:
    """ListRecords through resumption tokens; one file per OAI page; the state file makes it resumable.

    `name` keeps windows apart (the recent papers first, then the backfill); "all" is the unbounded run.
    If arXiv no longer honours the stored token, the harvest restarts from the last datestamp seen
    (inclusive); duplicates are removed when the parts are read.
    """
    http = http or Http(API_INTERVAL)
    meta = data / "meta"
    tag = "" if name == "all" else f"{name}-"
    state_path = data / ("harvest-state.json" if name == "all" else f"harvest-state-{name}.json")
    state = json.loads(state_path.read_text()) if state_path.exists() else {
        "next_part": 0, "token": None, "last_datestamp": None, "records": 0, "complete_list_size": None, "done": False}
    pages = 0
    while not state["done"] and (max_pages is None or pages < max_pages):
        if state["token"]:
            url = f"{OAI_URL}?verb=ListRecords&resumptionToken={urllib.parse.quote(state['token'])}"
        else:
            url = f"{OAI_URL}?verb=ListRecords&metadataPrefix=arXivRaw"
            start = max(filter(None, [state["last_datestamp"], from_]), default=None)
            if start:
                url += f"&from={start}"
            if until:
                url += f"&until={until}"
        status, _, body = get_retrying(http, url)
        if status != 200:
            raise RuntimeError(f"OAI-PMH answered HTTP {status} for {url}")
        page = parse_oai(body)
        if page["error"]:
            code, msg = page["error"]
            if code == "noRecordsMatch":
                state["done"] = True
            elif code == "badResumptionToken" and state["token"]:
                print(f"[arxiv] token refused ({msg}); restarting from {state['last_datestamp']}", file=sys.stderr)
                state["token"] = None
            else:
                raise RuntimeError(f"OAI-PMH error {code}: {msg}")
            _atomic_write(state_path, json.dumps(state).encode())
            continue
        lines = "".join(json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n" for r in page["records"])
        _atomic_write(meta / f"oai-{tag}{state['next_part']:06d}.jsonl.gz", gzip.compress(lines.encode(), 6))
        state["next_part"] += 1
        state["records"] += len(page["records"])
        stamps = [r["datestamp"] for r in page["records"] if r.get("datestamp")]
        if stamps:
            state["last_datestamp"] = max(stamps)
        state["token"] = page["token"]
        state["complete_list_size"] = page["size"] or state["complete_list_size"]
        state["done"] = page["token"] is None
        _atomic_write(state_path, json.dumps(state).encode())
        pages += 1
    return state


def read_records(data: pathlib.Path, slim: bool = False):
    """Every harvested record, once (later parts win over earlier ones).

    slim drops title, authors and DOI of records whose licence is not allowed: the full harvest is ~3 million
    records and only the eligible ones are ever needed with their attribution fields.
    """
    latest: dict[str, dict] = {}
    for part in sorted((data / "meta").glob("oai-*.jsonl.gz")):
        with gzip.open(part, "rt", encoding="utf-8") as f:
            for line in f:
                r = json.loads(line)
                if slim and r.get("license") not in ALLOWED:
                    r = {k: r.get(k) for k in ("id", "license", "categories", "v", "created", "datestamp")}
                latest[r["id"]] = r
    return latest.values()


# ---------------------------------------------------------------- exclusions, report, sample

def holdout_arxiv_ids(registry_dir: pathlib.Path) -> dict[str, str]:
    """arXiv ids that any sealed holdout draws on (olmOCR-bench among them), id -> holdout name."""
    from bench import registry
    ids: dict[str, str] = {}
    for name, meta in registry.load_registry(registry_dir)["holdouts"].items():
        for e in meta["entries"]:
            if e["doc"].startswith("arxiv:"):
                ids.setdefault(e["doc"].split(":", 1)[1], name)
    return ids


def primary(rec: dict) -> str:
    return rec["categories"][0] if rec.get("categories") else "unknown"


def math_heavy(rec: dict) -> bool:
    cats = rec.get("categories") or []
    p = cats[0] if cats else ""
    return p in MATH_HEAVY_EXACT or p.startswith(MATH_HEAVY)


def licence_report(data: pathlib.Path, excluded: dict[str, str]) -> dict:
    counts: dict[str, int] = {}
    total = 0
    records = []
    for r in read_records(data, slim=True):
        total += 1
        counts[r.get("license") or "(missing)"] = counts.get(r.get("license") or "(missing)", 0) + 1
        records.append(r)
    kept, rejected = filter_records(records, excluded)
    by_licence: dict[str, int] = {}
    for k in kept:
        by_licence[k["licence_name"]] = by_licence.get(k["licence_name"], 0) + 1
    return {"records": total, "per_licence_value": dict(sorted(counts.items(), key=lambda kv: -kv[1])),
            "eligible": len(kept), "eligible_per_licence": dict(sorted(by_licence.items())),
            "eligible_share_alike": sum(1 for k in kept if k["share_alike"]),
            "eligible_math_heavy": sum(1 for k in kept if math_heavy(k)),
            "eligible_with_html_era_id": sum(1 for k in kept if re.match(r"^\d{4}\.", k["id"]) and k["id"] >= "2401."),
            "eligible_by_primary_category": dict(sorted(
                ((c, n) for c, n in _count(primary(k) for k in kept).items()), key=lambda kv: -kv[1])[:40]),
            "rejected": dict(sorted(rejected.items(), key=lambda kv: -kv[1])), "kept": kept}


def _count(items) -> dict[str, int]:
    out: dict[str, int] = {}
    for i in items:
        out[i] = out.get(i, 0) + 1
    return out


def draw_sample(kept: list[dict], n: int, seed: str, min_id: str | None = None) -> list[dict]:
    """n papers, alternating math-heavy and other, round-robin over primary categories inside each group.

    Any prefix of the result is as spread as the whole, so a fetch that is stopped early is still balanced.
    min_id ('2401.00000') limits the draw to papers that can have an arxiv.org/html page (since Dec 2023).
    """
    def key(r):
        return hashlib.sha256(f"{seed}|{r['id']}".encode()).hexdigest()
    pool = [r for r in kept if not min_id or (re.match(r"^\d{4}\.", r["id"]) and r["id"] >= min_id)]
    groups: dict[bool, dict[str, list[dict]]] = {True: {}, False: {}}
    for r in sorted(pool, key=key):
        groups[math_heavy(r)].setdefault(primary(r), []).append(r)

    def round_robin(by_cat):
        queues = [by_cat[c] for c in sorted(by_cat)]
        i = 0
        while any(queues):
            for q in queues:
                if q:
                    yield q.pop(0)
            i += 1
    gens = [round_robin(groups[True]), round_robin(groups[False])]
    out, turn = [], 0
    while len(out) < n and gens:
        try:
            out.append(next(gens[turn % len(gens)]))
            turn += 1
        except StopIteration:
            gens.pop(turn % len(gens))
    return out


def manifest_record(r: dict, retrieved: str) -> dict:
    return {"arxiv_id": r["id"], "version": f"v{r['v']}" if r.get("v") else None, "licence_url": r["license"],
            "licence": r["licence_name"], "share_alike": r["share_alike"], "authors": r["authors"],
            "title": r["title"], "categories": r["categories"], "created": r["created"], "doi": r.get("doi"),
            "math_heavy": math_heavy(r), "retrieved": retrieved, "source_route": "oai-pmh:arXivRaw"}


# ---------------------------------------------------------------- content fetch (gated)

class Refused(Exception):
    pass


def content_gate(env=os.environ) -> str:
    arrangement = env.get("ARXIV_CONTENT_ARRANGEMENT", "").strip()
    if not arrangement:
        raise Refused("REFUSED: arXiv's OAI data policy does not permit full-content harvesting except by special "
                      "arrangement, and no arrangement is named. Set ARXIV_CONTENT_ARRANGEMENT to the reference "
                      "of the permission (for example the correspondence with arXiv) to fetch PDF and HTML.")
    return arrangement


def robots_for(http: Http, host: str = "https://arxiv.org") -> urllib.robotparser.RobotFileParser:
    status, _, body = get_retrying(http, f"{host}/robots.txt")
    if status != 200:
        raise Refused(f"REFUSED: {host}/robots.txt answered HTTP {status}; without it nothing is fetched")
    rp = urllib.robotparser.RobotFileParser()
    rp.parse(body.decode("utf-8", "replace").splitlines())
    return rp


def content_urls(rec: dict) -> dict[str, str]:
    ident = rec["arxiv_id"] + (rec["version"] or "")
    return {"pdf": f"https://arxiv.org/pdf/{ident}", "html": f"https://arxiv.org/html/{ident}",
            "source": f"https://arxiv.org/e-print/{rec['arxiv_id']}"}


def fetch_content(data: pathlib.Path, sample: list[dict], http: Http, arrangement: str, limit: int | None = None) -> dict:
    """PDF and HTML per paper, resumable by the ledger. The source tarball is requested only if robots.txt allows."""
    rp = robots_for(http)
    delay = max(CONTENT_INTERVAL, float(rp.crawl_delay(USER_AGENT) or 0))
    http.interval = delay
    ledger_path = data / "papers" / "ledger.jsonl"
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    done = set()
    if ledger_path.exists():
        done = {(j["arxiv_id"], j["kind"]) for j in map(json.loads, ledger_path.read_text().splitlines() if ledger_path.stat().st_size else [])}
    counts = {"fetched": 0, "skipped_done": 0, "disallowed": 0, "absent": 0}
    for rec in sample:
        for kind, url in content_urls(rec).items():
            if (rec["arxiv_id"], kind) in done:
                counts["skipped_done"] += 1
                continue
            if limit is not None and counts["fetched"] >= limit:
                return counts
            row = {"arxiv_id": rec["arxiv_id"], "kind": kind, "url": url, "arrangement": arrangement,
                   "at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")}
            if not rp.can_fetch(USER_AGENT, url):
                row["status"] = "disallowed-by-robots"
                counts["disallowed"] += 1
            else:
                status, _, body = get_retrying(http, url)
                row["http"] = status
                if status == 200:
                    ext = {"pdf": "pdf", "html": "html", "source": "tar.gz"}[kind]
                    _atomic_write(data / "papers" / rec["arxiv_id"].replace("/", "_") / f"{kind}.{ext}", body)
                    row.update(status="ok", bytes=len(body), sha256=hashlib.sha256(body).hexdigest())
                    counts["fetched"] += 1
                else:
                    row["status"] = "absent"
                    counts["absent"] += 1
            with open(ledger_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, sort_keys=True) + "\n")
    return counts


# ---------------------------------------------------------------- CLI

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="bench.arxiv")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("harvest", "report", "sample", "fetch"):
        p = sub.add_parser(name)
        p.add_argument("--data", type=pathlib.Path, required=True)
        p.add_argument("--registry", type=pathlib.Path, default=None, help="registry dir with the holdout entries")
        if name == "harvest":
            p.add_argument("--max-pages", type=int)
            p.add_argument("--name", default="all", help="window name; 'all' is the unbounded harvest")
            p.add_argument("--from", dest="from_", help="OAI datestamp lower bound, YYYY-MM-DD")
            p.add_argument("--until", help="OAI datestamp upper bound, YYYY-MM-DD")
        if name == "sample":
            p.add_argument("-n", type=int, default=2000)
            p.add_argument("--seed", default="pdfluent-arxiv-sample-1")
            p.add_argument("--min-id", default="2401.00000")
        if name == "fetch":
            p.add_argument("--limit", type=int)
    args = ap.parse_args(argv)
    from bench import registry as reg
    excluded = holdout_arxiv_ids(args.registry or reg.REGISTRY) if args.cmd in ("report", "sample") else {}
    if args.cmd == "harvest":
        state = harvest(args.data, max_pages=args.max_pages, name=args.name, from_=args.from_, until=args.until)
        print(json.dumps({k: state[k] for k in ("records", "complete_list_size", "done", "next_part")}))
        return 0
    if args.cmd == "report":
        rep = licence_report(args.data, excluded)
        rep.pop("kept")
        (args.data / "reject-report.json").write_text(json.dumps(rep, indent=1) + "\n")
        print(json.dumps(rep, indent=1))
        return 0
    if args.cmd == "sample":
        rep = licence_report(args.data, excluded)
        today = datetime.date.today().isoformat()
        picked = draw_sample(rep["kept"], args.n, args.seed, args.min_id)
        lines = "".join(json.dumps(manifest_record(r, today), sort_keys=True) + "\n" for r in picked)
        _atomic_write(args.data / "sample.jsonl", lines.encode())
        print(f"{len(picked)} papers in {args.data / 'sample.jsonl'}")
        return 0
    try:
        arrangement = content_gate()
    except Refused as e:
        print(e, file=sys.stderr)
        return 2
    sample = [json.loads(line) for line in (args.data / "sample.jsonl").read_text().splitlines()]
    print(json.dumps(fetch_content(args.data, sample, Http(CONTENT_INTERVAL), arrangement, args.limit)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
