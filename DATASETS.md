# Datasets

No dataset file is stored in this repository. `bench datasets fetch` downloads
each set from its public source at a pinned revision, into `work/`. What is
committed are the sealed manifests under `engine/corpus/structured/`: page
names and SHA-256 digests, no document content.

Licences were checked against the sources on 2026-10-17 (dataset cards and
README files at the pinned revisions) and agree with the engine register
(`engine/benchmarks/structured/comparison/DATASETS.toml`), which holds the
clause, the quote and the reading for each set. That register is what the
harness enforces: a set whose verdict is older than 180 days is refused until
it is read again.

| set | source (pinned revision) | licence | what is downloaded |
|---|---|---|---|
| DocLayNet v1.2, `test` split | `docling-project/DocLayNet-v1.2` @ `0daf9310` | CDLA-Permissive-1.0 (README, "Licensing Information") | six parquet files, about 2.4 GB |
| PubTables-1M | annotations `bsmock/pubtables-1m` @ `35b1c097`; documents from the PMC Open Access bucket | annotations CDLA-Permissive-2.0 (card); documents per article, and only articles licensed CC0 or CC BY are fetched | the annotations archive, about 3.7 GB, then one article PDF at a time, at most 400 pages |
| FinTabNet 1.0.0 with FinTabNet.c | documents `Leon1207/FinTabNet` @ `e30b3df7` (archive.zip); cell structure `bsmock/FinTabNet.c` @ `e5673a90` | documents CDLA-Permissive-1.0 (IBM's LICENSE.txt inside the archive; the mirror's card states none); FinTabNet.c CDLA-Permissive-2.0 (card) | FinTabNet.c annotations, about 255 MB; from the 16.8 GB archive only its central directory and the pinned pages, by HTTP range |
| ParseBench | `llamaindex/ParseBench` @ `2805a1d9` | Apache-2.0 (card), over third-party documents it does not own | the rule files of the measured groups, about 62 MB, and the sampled pages one by one |

Conditions this benchmark keeps, from the register:

- ParseBench: only scores and file names leave the machine; the markdown the
  tools write stays in `work/`.
- PubTables-1M: an article whose licence code is not CC0 or CC BY is never
  fetched. The refusals are counted.
- FinTabNet: the mirror was accepted on measured provenance (its members match
  IBM's release), not on its card. A different mirror can be used with
  `FINTABNET_ARCHIVE_URL`; it must serve the same bytes.
- Results of each set may be published as aggregate numbers, with the set's
  attribution.

## Manifest format

One JSON file per set:

```json
{
 "set": "doclaynet",
 "version": "...", "revision": "<source commit>", "source": "<url>", "licence": "...",
 "drawn": "2026-09-17",
 "seed": "pdfluent-structured-holdout-2026-09-17",
 "rule": "scripts/ci/structured_holdout_rule.py",
 "share": 0.2,
 "counts": {"pages": 4951, "tuning": 3961, "holdout": 990, "documents": 196},
 "seal": {"tuning": "<sha256>", "holdout": "<sha256>"},
 "pages": [{"source": "...", "name": "...", "document": "...", "page_no": 1,
            "category": "...", "sha256": "<sha256 of the page PDF>", "split": "tuning"}]
}
```

The split is by document: every page of a document is on the same side, and
documents are taken into the holdout in the order of
`sha256("<seed>|rank|<source>|<document>")` until the next one would pass 20
percent of the pages. A seal is one SHA-256 over `source`, `name` and `sha256`
of the half's pages in manifest order, so a published seal identifies the
pages without the file. Seeds: DocLayNet
`pdfluent-structured-holdout-2026-09-17`, PubTables-1M
`pdfluent-structured-tables-holdout-2026-09-20`, ParseBench
`pdfluent-structured-parsebench-holdout-2026-09-23`, FinTabNet
`pdfluent-structured-fintabnet-holdout-2026-09-30`.

FinTabNet's committed file is the pinned page list (`FINTABNET_DRAW.json`);
its split is drawn from those pages at fetch time under the seed above and
written to `FINTABNET.json`. The other three manifests are sealed and are not
redrawn: before any tool reads a page, its file is hashed and compared with the
manifest, and a mismatch stops the run.

A run samples about N pages from one half by whole documents, with the set's
seed (`configs/sets.yaml` gives N), so the same N selects the same pages.
