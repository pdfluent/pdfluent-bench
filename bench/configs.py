# Copyright (c) 2026 Innovation Trigger B.V. All rights reserved.
# Licence pending owner decision: see LICENSE-TBD.md.
"""The configuration matrix, read from configs/*.yaml and checked."""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
CONFIGS = ROOT / "configs"
NOT_CONFIGS = {"machines", "sets"}
DATASET_SETS = ("fintabnet", "doclaynet", "pubtables-1m", "parsebench")

REQUIRED = {"name": str, "description": str, "tool": str, "package": str, "version": str,
            "vision": bool, "models": list, "image": str, "gpu": dict, "reproducible": str}


class ConfigError(SystemExit):
    pass


def names() -> list[str]:
    return sorted(p.stem for p in CONFIGS.glob("*.yaml") if p.stem not in NOT_CONFIGS)


def load(name: str) -> dict:
    path = CONFIGS / f"{name}.yaml"
    if not path.is_file():
        raise ConfigError(f"[bench] FATAL: no configuration `{name}` (configs/{name}.yaml); "
                          f"known: {', '.join(names())}")
    config = yaml.safe_load(path.read_text())
    problems = [f"`{key}` must be {kind.__name__}" for key, kind in REQUIRED.items()
                if not isinstance(config.get(key), kind)]
    if config.get("name") != name:
        problems.append(f"`name` is {config.get('name')!r}, the file is {name}.yaml")
    if config.get("reproducible") not in ("public", "internal"):
        problems.append("`reproducible` must be public or internal")
    gpu = config.get("gpu") or {}
    if not isinstance(gpu.get("route"), bool) or not gpu.get("why"):
        problems.append("`gpu` needs `route` (true/false) and `why`")
    if problems:
        raise ConfigError(f"[bench] FATAL: configs/{name}.yaml: " + "; ".join(problems))
    return config


def resolve(spec: str) -> list[dict]:
    wanted = names() if spec in ("all", "") else [s.strip() for s in spec.split(",") if s.strip()]
    return [load(name) for name in wanted]


def resolve_sets(spec: str) -> list[str]:
    if spec in ("all", ""):
        return list(DATASET_SETS)
    wanted = [s.strip() for s in spec.split(",") if s.strip()]
    for name in wanted:
        if name not in DATASET_SETS and name != "fixtures":
            raise ConfigError(f"[bench] FATAL: unknown set `{name}`; known: "
                              f"{', '.join(DATASET_SETS)}, fixtures")
    return wanted


def machines() -> dict:
    return yaml.safe_load((CONFIGS / "machines.yaml").read_text())


def machine(name: str) -> dict:
    table = machines()["machines"]
    if name not in table:
        raise ConfigError(f"[bench] FATAL: unknown machine `{name}`; known: {', '.join(table)}")
    return dict(table[name], name=name)


def sets() -> dict:
    return yaml.safe_load((CONFIGS / "sets.yaml").read_text())


def pages_for(set_name: str, profile: str, kind: str = "pages") -> int:
    """Pages to sample per split (or for speed), under a profile."""
    table = sets()
    if set_name == "fixtures":
        return 0  # every fixture page
    base = table["sets"][set_name][kind]
    prof = table["profiles"].get(profile)
    if prof is None:
        raise ConfigError(f"[bench] FATAL: unknown profile `{profile}`; known: "
                          f"{', '.join(table['profiles'])}")
    if "scale" in prof:
        return max(1, round(base * prof["scale"]))
    if kind == "speed_pages":
        return prof["speed_pages"]
    if set_name == "parsebench":
        return prof["parsebench_pages"]
    return prof["pages"]


def tool_python(config: dict) -> str:
    """The interpreter that runs this column: <TOOL>_PYTHON, the image's venv, or ours."""
    tool = config["tool"]
    explicit = os.environ.get(f"{tool.upper()}_PYTHON")
    if explicit:
        return explicit
    in_image = pathlib.Path(f"/opt/ex/{tool}/bin/python")
    if os.environ.get("BENCH_IN_CONTAINER") and in_image.exists():
        return str(in_image)
    return sys.executable


def installed_version(python: str, package: str) -> str | None:
    done = subprocess.run(
        [python, "-c", "import sys\nfrom importlib.metadata import version\n"
                       f"print(version({package!r}))"],
        capture_output=True, text=True, timeout=120, check=False)
    return done.stdout.strip() if done.returncode == 0 and done.stdout.strip() else None


def unavailable(config: dict, python: str) -> str | None:
    """Why this column cannot run here, or None. Never guesses past a pin."""
    installed = installed_version(python, config["package"])
    if installed is None:
        return f"`{config['package']}` is not installed for {python}"
    if installed != config["version"] and not os.environ.get("BENCH_ALLOW_VERSION_MISMATCH"):
        return (f"`{config['package']}` {installed} is installed, the configuration pins "
                f"{config['version']} (set BENCH_ALLOW_VERSION_MISMATCH=1 to measure anyway; "
                "the run file then says so)")
    missing = [name for name in config.get("required_env") or [] if not os.environ.get(name)]
    if missing:
        return "needs " + ", ".join(missing)
    return None


def version_record(config: dict, python: str) -> dict:
    installed = installed_version(python, config["package"])
    return {"package": config["package"], "pinned": config["version"], "installed": installed,
            "matches_pin": installed == config["version"], "python": python}
