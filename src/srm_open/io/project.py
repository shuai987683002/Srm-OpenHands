"""Project I/O: save/load a whole design (requirement + grain + propellant +
nozzle) to a single YAML project file, and export results to JSON.

A project file is self-contained: loading it reproduces the exact design inputs,
and re-running the forward pipeline regenerates every result.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from ..models.design_result import DesignResult
from ..models.grain import GrainGeometry
from ..models.nozzle import NozzleModel
from ..models.propellant import PropellantModel
from ..models.requirements import RequirementSet


def save_project(path: str | Path, requirement: RequirementSet,
                 grain: GrainGeometry, propellant: PropellantModel,
                 nozzle: NozzleModel | None = None, meta: dict | None = None) -> Path:
    path = Path(path)
    payload = {
        "format": "srm-open-project",
        "version": 1,
        "meta": meta or {},
        "requirement": requirement.model_dump(mode="json"),
        "propellant": propellant.model_dump(mode="json"),
        "grain": grain.model_dump(mode="json"),
    }
    if nozzle is not None:
        payload["nozzle"] = nozzle.model_dump(mode="json")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        yaml.safe_dump(payload, fh, allow_unicode=True, sort_keys=False)
    return path


def load_project(path: str | Path) -> dict:
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if data.get("format") != "srm-open-project":
        raise ValueError(f"{path} is not a srm-open project file")
    out = {
        "requirement": RequirementSet(**data["requirement"]),
        "grain": GrainGeometry(**data["grain"]),
        "propellant": PropellantModel(**data["propellant"]),
        "meta": data.get("meta", {}),
    }
    if "nozzle" in data:
        out["nozzle"] = NozzleModel(**data["nozzle"])
    return out


def save_result_json(result: DesignResult, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(result.model_dump_json(indent=2))
    return path
