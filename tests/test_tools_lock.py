# Copyright (c) 2026 Innovation Trigger B.V.
#
# PDFluent is available under two licences, at your option: the GNU AGPLv3, or
# the PDFluent Commercial Licence. See the LICENSE file in this repository --
# that file travels with the copy you received, which a URL does not.
"""The native tool pins and the configurations never drift apart."""
import pathlib
import re

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]


def pins(name: str) -> dict[str, str]:
    out = {}
    for line in (ROOT / "tools" / f"{name}.txt").read_text().splitlines():
        if line.strip() and not line.startswith("#"):
            package, _, version = line.partition("==")
            assert version and re.fullmatch(r"[0-9][0-9A-Za-z.]*", version), line
            out[package.strip()] = version.strip()
    return out


def config_pin(config: str) -> tuple[str, str]:
    data = yaml.safe_load((ROOT / "configs" / f"{config}.yaml").read_text())
    return data["package"], str(data["version"])


def test_pymupdf4llm_pin_equals_its_config():
    package, version = config_pin("pymupdf4llm")
    assert pins("pymupdf4llm") == {package: version}


def test_every_pdfluent_config_uses_the_pinned_wheel():
    wheel = pins("pdfluent")
    for config in ("pdfluent-fast", "pdfluent-balanced", "pdfluent-balanced-tatr"):
        package, version = config_pin(config)
        assert wheel == {package: version}, config


def test_every_dockerfile_pins_the_same_wheel_and_scorer():
    text = (ROOT / "docker" / "pdfluent" / "Dockerfile").read_text()
    (package, version), = pins("pdfluent").items()
    assert f"{package}=={version}" in text
    (scorer, scorer_version), = pins("parsebench").items()
    assert f"{scorer}=={scorer_version}" in text


def test_every_package_in_the_repo_requirement_files_is_pinned_exactly():
    for path in [ROOT / "requirements-dev.txt", ROOT / "docker" / "requirements-harness.txt",
                 *(ROOT / "tools").glob("*.txt")]:
        for line in path.read_text().splitlines():
            if line.strip() and not line.startswith("#"):
                assert re.fullmatch(r"[A-Za-z0-9_.-]+==[0-9][0-9A-Za-z.]*", line.strip()), \
                    f"{path.name}: {line!r} is not an exact pin"


def test_dev_and_container_requirements_agree_on_shared_packages():
    def read(path):
        return dict(line.split("==") for line in path.read_text().splitlines()
                    if line.strip() and not line.startswith("#"))
    dev = read(ROOT / "requirements-dev.txt")
    box = read(ROOT / "docker" / "requirements-harness.txt")
    shared = set(dev) & set(box)
    assert {"pyyaml", "tiktoken", "huggingface_hub", "pyarrow"} <= shared
    assert all(dev[name] == box[name] for name in shared)
