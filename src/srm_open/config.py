"""Configuration system.

Layered configuration: built-in defaults -> package ``configs/default.yaml``
-> user file (``SRM_OPEN_CONFIG`` env var) -> explicit overrides.

The point is *adaptability*: numerical settings (tolerances, grid sizes,
solver choice), data paths and plugin choices are all configurable without
touching code.
"""

from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

import yaml

_DEFAULTS: dict[str, Any] = {
    "paths": {
        "data_dir": "data",
        "propellant_db": "data/propellants/propellants.yaml",
        "template_dir": "data/templates",
        "thermo_db": "data/thermo/thermo.yaml",
        "output_dir": "output",
    },
    "ballistics": {
        "model": "equilibrium",  # equilibrium | transient
        "pressure_floor": 1.0e4,  # Pa, guard against r->0
        "time_steps": 400,
        "ode_rtol": 1.0e-6,
        "ode_atol": 1.0e-3,
    },
    "burnback": {
        "method": "analytic",  # analytic | levelset
        "n_steps": 200,
        "levelset": {
            "nx": 300,
            "ny": 300,
            "cfl": 0.5,
            "reinit_iters": 20,
        },
    },
    "geometry": {
        "backend": "auto",  # auto | cadquery | pure
        "min_feature_size": 1.0e-4,  # m, guard for degenerate geometry
    },
    "thermo": {
        "backend": "auto",  # auto | gibbs | rocketcea | cantera
        "species": "default",
    },
    "integrity": {
        "method": "lame",  # lame | fem
        "fem": {"nr": 60, "nz": 60},
    },
    "inverse": {
        "optimizer": "cma",  # cma | de | nelder_mead
        "max_iter": 400,
        "seed": 12345,
    },
    "report": {
        "format": "markdown",  # markdown | html
        "include_plots": True,
    },
}


def _coerce(node: Any) -> Any:
    """Best-effort numeric coercion for values YAML left as strings.

    YAML 1.1 requires a sign in exponents, so ``1.0e4`` is read as the *string*
    ``"1.0e4"``.  We normalise such strings to floats on read.
    """
    if isinstance(node, str):
        try:
            return float(node)
        except ValueError:
            return node
    return node


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def repo_root() -> Path:
    """Best-effort repository root (parent of the ``src`` tree)."""
    env = os.environ.get("SRM_OPEN_ROOT")
    if env:
        return Path(env)
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "pyproject.toml").exists():
            return parent
    return Path.cwd()


class Config:
    """Dict-like configuration object with dotted access."""

    def __init__(self, data: dict[str, Any] | None = None) -> None:
        self._data = _deep_merge(_DEFAULTS, data or {})

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self._data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return _coerce(node)

    def set(self, dotted: str, value: Any) -> None:
        parts = dotted.split(".")
        node = self._data
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value

    def resolve_path(self, dotted: str) -> Path:
        p = Path(self.get(dotted, ""))
        return p if p.is_absolute() else repo_root() / p

    def as_dict(self) -> dict[str, Any]:
        return copy.deepcopy(self._data)

    def __getitem__(self, key: str) -> Any:
        return self._data[key]


def load_config(path: str | os.PathLike | None = None) -> Config:
    """Load configuration with the documented precedence."""
    data: dict[str, Any] = {}
    # package default file (optional)
    pkg_default = Path(__file__).resolve().parent.parent.parent / "configs" / "default.yaml"
    candidates = []
    if pkg_default.exists():
        candidates.append(pkg_default)
    env_path = path or os.environ.get("SRM_OPEN_CONFIG")
    if env_path:
        candidates.append(Path(env_path))
    for c in candidates:
        if c and Path(c).exists():
            with open(c, encoding="utf-8") as fh:
                data = _deep_merge(data, yaml.safe_load(fh) or {})
    return Config(data)


_default_config: Config | None = None


def get_config() -> Config:
    global _default_config
    if _default_config is None:
        _default_config = load_config()
    return _default_config


def set_config(cfg: Config) -> None:
    global _default_config
    _default_config = cfg
