# arXiv harvest: routes, terms read, results

Story pdfluent-internal#972, epic #970. Read on 2026-10-24. The pages carry no version number or
revision date, so the date of reading is the version.

## What the terms say

| page | what it says that matters here |
|---|---|
| Terms of Use for arXiv APIs (info.arxiv.org/help/api/tou.html) | OAI-PMH, RSS and the arXiv API: at most one request every three seconds, one connection at a time, across all machines you control. Metadata (title, abstract, authors, identifiers, categories) is CC0 1.0 and may be retrieved, stored, transformed and shared. E-prints stay under their copyright; most use the arXiv non-exclusive licence, some a Creative Commons licence. E-prints may be retrieved for personal or research use; serving them from your own servers needs permission or a licence that allows it. |
| OAI data policy (info.arxiv.org/help/oa/dataPolicy.html, repeated in the `Identify` response) | "Full-content harvesting from arXiv.org is not permitted except by special arrangement." |
| Robots (info.arxiv.org/help/robots.html) and `arxiv.org/robots.txt` | Indiscriminate automated downloads are not permitted. robots.txt: `Crawl-delay: 15`; `Allow: /pdf`, `/html`, `/abs`; `Disallow: /e-print`, `/src`, `/oai2`, `/api`. `export.arxiv.org/robots.txt` disallows everything. |
| Bulk data (info.arxiv.org/help/bulk_data.html) | Metadata: OAI-PMH is "the preferred way". Full text in bulk: the Amazon S3 bucket (requester pays), or Kaggle. Do not download the complete corpus programmatically. |

## Routes used

- Metadata: OAI-PMH at `https://oaipmh.arxiv.org/oai`, `ListRecords`, metadata prefix `arXivRaw` (carries
  the `license` element, the version list, authors, title, categories). One request per three seconds at
  most, one connection, `User-Agent` naming the project and `hello@pdfluent.com`. `503` with `Retry-After`
  is waited out. The harvest is resumable (`bench/arxiv.py harvest`, state file per window).
- Not used: the requester-pays S3 bucket (costs money, owner decision), scraping of listing or search pages,
  `export.arxiv.org` for content (its robots.txt disallows everything), `arxiv.org/e-print` (robots.txt
  disallows it).

## Content is gated

The data policy and robots.txt together mean PDF, HTML and source tarballs may not be fetched in bulk by a
robot without an arrangement with arXiv. `python -m bench.arxiv fetch` therefore refuses unless
`ARXIV_CONTENT_ARRANGEMENT` names the permission. With it, the fetcher still reads robots.txt, waits at
least 15 s between requests (the larger of 15 s and the Crawl-delay), never requests a disallowed path (the
source tarball is therefore recorded as `disallowed-by-robots`), and resumes from a ledger. The PDF and HTML
routes were tested against recorded responses only (`tests/test_arxiv.py`). Options to get the content, for
the owner to choose: ask arXiv (help@arxiv.org) for the special arrangement for a 2,000-paper sample; take
the sample from the requester-pays S3 bucket (small, selective transfer, but it costs money); or use a
Kaggle copy and check its terms.

## Licence filter

A paper is kept when its metadata `license` is exactly one of: `http://creativecommons.org/publicdomain/zero/1.0/`,
`http://creativecommons.org/licenses/by/{1.0,2.0,2.5,3.0,4.0}/`, `http://creativecommons.org/licenses/by-sa/{1.0,2.0,2.5,3.0,4.0}/`.
Everything else is rejected and counted by value: the non-exclusive distribution licence, CC BY-NC, CC BY-NC-SA,
CC BY-NC-ND, CC BY-ND, any other URL, a missing field. A CC BY-SA paper carries `share_alike: true`; a legal
read of ShareAlike for training use is still owed before such a paper enters a training split.
Papers whose arXiv id appears in a sealed holdout (olmOCR-bench draws on arXiv; ids are taken from the
holdout registry of #971) are rejected as `sealed-holdout:<name>`.

## Results

Metadata harvested 2026-10-24 and 25 through OAI-PMH `arXivRaw`: **3,224,546 records** (two windows: datestamps
from 2023-12-01, then everything up to 2023-12-11; together all of arXiv as of 2026-10-23).
Reproduce the counts with `python -m bench.arxiv report --data <dir>`; the machine-readable copy is
`reject-report.json` next to this file.

### Licence values in the metadata

| `license` value | records |
|---|---|
| `http://arxiv.org/licenses/nonexclusive-distrib/1.0/` | 1,937,672 |
| `http://creativecommons.org/licenses/by/4.0/` | 604,366 |
| `(missing)` | 452,704 |
| `http://creativecommons.org/licenses/by-nc-nd/4.0/` | 92,066 |
| `http://creativecommons.org/licenses/by-nc-sa/4.0/` | 67,636 |
| `http://creativecommons.org/licenses/by-sa/4.0/` | 31,402 |
| `http://creativecommons.org/publicdomain/zero/1.0/` | 22,443 |
| `http://creativecommons.org/licenses/by/3.0/` | 7,912 |
| `http://creativecommons.org/licenses/by-nc-sa/3.0/` | 5,871 |
| `http://creativecommons.org/licenses/publicdomain/` | 2,474 |

### Eligible pool

**665,925 papers** pass the filter after the olmOCR-bench exclusion.

| kept as | papers |
|---|---|
| CC-BY-3.0 | 7,912 |
| CC-BY-4.0 | 604,179 |
| CC-BY-SA-4.0 | 31,396 |
| CC0-1.0 | 22,438 |

- CC BY-SA (flagged `share_alike`): 31,396.
- With a primary category that is math-heavy (`math.*`, `math-ph`, `stat.TH`, `cs.IT`, `cs.LO`, `cs.DM`, `cs.CC`,
  `cs.FL`, `quant-ph`, `hep-th`, `gr-qc`): 165,777.
- With an id from 2401 on, so an `arxiv.org/html` page can exist: 382,574.
- The largest primary categories are cs.LG, cs.CV, cs.CL, quant-ph, cs.AI, hep-ph (see `reject-report.json`).

### Reject report (everything not kept, by reason)

| reason | papers |
|---|---|
| `licence:http://arxiv.org/licenses/nonexclusive-distrib/1.0/` | 1,937,403 |
| `missing` | 452,704 |
| `licence:http://creativecommons.org/licenses/by-nc-nd/4.0/` | 92,030 |
| `licence:http://creativecommons.org/licenses/by-nc-sa/4.0/` | 67,617 |
| `licence:http://creativecommons.org/licenses/by-nc-sa/3.0/` | 5,871 |
| `licence:http://creativecommons.org/licenses/publicdomain/` | 2,474 |
| `sealed-holdout:olmocr-bench` | 522 |

A record with no `license` element (452,704, all with an old-style id or an id from 0704 to 0812) is rejected as `missing`.
`sealed-holdout:olmocr-bench` counts the papers whose id appears in the olmOCR-bench list of the registry (522 ids);
they are rejected before the licence is looked at, so some of them would have failed the licence anyway.

### First sample

`sample-2000.jsonl`: 2,000 licence-eligible papers, ids from 2401 on, drawn with seed `pdfluent-arxiv-sample-1`:
1,000 math-heavy and 1,000 other, round-robin over 149 primary categories, so any prefix is as spread as the whole.
1839 CC BY 4.0, 93 CC BY-SA 4.0 (flagged), 68 CC0 1.0.
Each row has the arXiv id, version, licence URL, authors, title, categories, creation date, retrieval date and source route.
**PDF, HTML and source tarballs of this sample have not been fetched**: see "Content is gated".

