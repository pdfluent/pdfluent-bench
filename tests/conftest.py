# Copyright (c) 2026 Innovation Trigger B.V. All rights reserved.
# Licence pending owner decision: see LICENSE-TBD.md.
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

_SKIPPED: list[str] = []


def pytest_runtest_logreport(report):
    if report.skipped and report.when in ("setup", "call"):
        reason = report.longrepr[2] if isinstance(report.longrepr, tuple) else str(report.longrepr)
        _SKIPPED.append(f"{report.nodeid}: {reason.removeprefix('Skipped: ')}")


def pytest_terminal_summary(terminalreporter):
    # A skip is never silent: one line per skip on stderr, after capture ends.
    for line in _SKIPPED:
        print(f"SKIPPED (not a pass): {line}", file=sys.stderr)
