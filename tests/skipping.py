# Copyright (c) 2026 Innovation Trigger B.V. All rights reserved.
# Licence pending owner decision: see LICENSE-TBD.md.
"""Skip with a reason; conftest.py prints `SKIPPED (not a pass): <reason>` on stderr."""
import pytest


def skip(reason: str) -> None:
    pytest.skip(reason)
