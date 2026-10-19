# Copyright (c) 2026 Innovation Trigger B.V. All rights reserved.
# Licence pending owner decision: see LICENSE-TBD.md.
"""The public-surface check is clean on this tree and catches what it exists to catch."""
import base64
import importlib.util
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("check_public_surface",
                                               ROOT / "scripts" / "check_public_surface.py")
cps = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cps)


def _word(encoded: str) -> str:
    return base64.b64decode(encoded).decode()


def test_the_tree_and_the_history_are_clean():
    assert cps.scan_tree() == []
    assert cps.scan_history(None) == []


# The samples are built from pieces so this file does not trip the check itself.
HOME_MAC = "/Us" + "ers/someone/work/run.json"
HOME_LINUX = "/ho" + "me/someone/run.json"
PRIVATE_IP = "192." + "168.1.20"
KEY = "-----BEGIN OPENSSH " + "PRIVATE KEY-----"


def test_a_home_directory_path_is_caught():
    assert cps.scan_text("f", "see " + HOME_MAC)
    assert cps.scan_text("f", "see " + HOME_LINUX)
    assert not cps.scan_text("f", "see /opt/bench/bin/python and /home/runner/work")


def test_a_tool_name_and_an_attribution_trailer_are_caught():
    assert cps.scan_text("f", "# fixed with help from " + _word("Y2xhdWRl"))
    assert cps.scan_text("f", "# review (" + _word("Y29kZXg=") + ", #12)")
    assert cps.scan_text("c", _word("Y28tYXV0aG9yZWQtYnk=") + ": someone")
    assert not cps.scan_text("f", "amused and museum")


def test_a_private_address_and_a_key_are_caught_but_a_version_is_not():
    assert cps.scan_text("f", "host " + PRIVATE_IP)
    assert cps.scan_text("f", KEY)
    assert not cps.scan_text("f", "nvidia-curand==10.4.0.35")
