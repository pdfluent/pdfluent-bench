#!/usr/bin/env python3
# Copyright (c) 2026 Innovation Trigger B.V.
#
# PDFluent is available under two licences, at your option: the GNU AGPLv3, or
# the PDFluent Commercial Licence. See the LICENSE file in this repository --
# that file travels with the copy you received, which a URL does not.
"""Which side of the FinTabNet set a company is on (#574).

The set is split by COMPANY, not by page: two pages of one annual report share
a template, a font programme and a table style, so they are not independent
draws. A page of an eval company in the training dump would be tuning on the
rig, and nothing would report it.

THE RULE IS ONE FUNCTION, and this file is it: the structured lane takes only
the eval side, and the training dump (a later spec) takes only the train side
and refuses an eval company through `rig_data_stays_on_the_rig`. Two copies of
this rule would be two answers to which pages may be trained on.

`int(sha256(company) % 10) == 0` is eval: 41 of the 428 companies, 10,034 of
the 89,646 pages -- the same rule as
`gs://pdfluent-datasets/fintabnet/1.0.0/fintabnet_split_by_company.json`.
`scripts/ci/test_fintabnet_split.py` freezes the 41, so a "simplification" of
this rule goes red rather than silently moving the split.

THE TRAIN SIDE HAS A LANE OF ITS OWN for the table-structure dump (track A):
`lane()` below holds `% 10 == 1` out as the dev companies, and the dump's
train manifest takes only the rest. Same one-home rule: the dump and its test
read this function, not a second spelling of the modulus.

AND TEN PAGES ARE OUT BY NAME, whatever lane their company is on. Pages of
an eval set that sit on the train side are invisible to the company rule,
so `corpus/structured/EXCLUDE_EVAL_OVERLAP.json` names their sha256 --
hashes only, never the names -- and `page_excluded()` below is the one
check every FinTabNet train/tuning draw and fetch reads. Scoring a named
page is not a draw and does not read it.
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib


def side(company: str) -> str:
    """`"eval"` or `"train"`, from the company name alone.

    The name is the ticker exactly as the archive spells it (`FLS`, `BRK` --
    plain capitals, no dots or dashes in this release), so the answer depends
    on nothing but the string it is handed: no year, no page, no state.
    """
    digest = hashlib.sha256(company.encode()).hexdigest()
    return "eval" if int(digest, 16) % 10 == 0 else "train"


def lane(company: str) -> str:
    """`"eval"`, `"dev"` or `"train"`, from the company name alone.

    `side()` decides eval; what is left splits again on the same modulus:
    `% 10 == 1` is the dev companies the table-structure dump tunes on, and
    everything else is train. A dev company in the train manifest would be
    tuning on the training set, so the dump refuses it by this function.
    """
    digest = hashlib.sha256(company.encode()).hexdigest()
    remainder = int(digest, 16) % 10
    if remainder == 0:
        return "eval"
    return "dev" if remainder == 1 else "train"


REPO = pathlib.Path(__file__).resolve().parents[2]

# The pages no train/tuning draw may hold, by sha256 of the page id
# (`<COMPANY>_<YEAR>_page_<N>`, the walk name). Hashes, never names: the
# names would describe an eval set in the tree.
EXCLUSION_FILE = REPO / "corpus" / "structured" / "EXCLUDE_EVAL_OVERLAP.json"

# The test's seam: a scratch exclusion file instead of the committed one,
# so the suite proves the refusal without naming a real page.
EXCLUSION_ENV = "FINTABNET_EXCLUSION_FILE"

_hashes: dict[str, frozenset[str]] = {}


def exclusion_path() -> pathlib.Path:
    """The exclusion file: the override when one is set, else the tree's."""
    override = os.environ.get(EXCLUSION_ENV, "").strip()
    return pathlib.Path(override) if override else EXCLUSION_FILE


def eval_overlap_hashes(path: pathlib.Path | None = None) -> frozenset[str]:
    """The excluded page hashes, read once per path.

    Empty without a readable file -- a reference that is not there cannot
    refuse, the same rule the held-out lists keep for scratch roots.
    """
    key = str(path or exclusion_path())
    if key not in _hashes:
        try:
            data = json.loads(pathlib.Path(key).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        raw = data.get("pages_sha256") or []
        _hashes[key] = frozenset(
            entry for entry in raw if isinstance(entry, str))
    return _hashes[key]


def page_excluded(page_id: str) -> bool:
    """Whether a FinTabNet page id is on the eval-overlap list.

    The id is the walk name (`FLS_2019_page_3`), hashed and compared --
    the only place the comparison is spelled, read by the draw, the
    dump and the dev fetch alike.
    """
    digest = hashlib.sha256(page_id.encode()).hexdigest()
    return digest in eval_overlap_hashes()
