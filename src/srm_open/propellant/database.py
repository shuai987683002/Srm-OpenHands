"""Propellant database: load, query, save and extend propellant definitions.

The database is a YAML file so it is human editable and diff friendly.  Users
can add their own propellants, either by editing the YAML or via the API
(:func:`PropellantDB.add`).  Nothing here is proprietary: shipped entries are
generic, literature-documented classes and are marked ``source``.
"""

from __future__ import annotations

import copy
from pathlib import Path

import yaml

from ..models.propellant import (
    BurnRateLaw,
    BurnRateModel,
    PropellantModel,
    ThermalChem,
)


class PropellantDB:
    """In-memory propellant database backed by a YAML file."""

    def __init__(self, records: dict[str, dict] | None = None, path: Path | None = None) -> None:
        self._records: dict[str, dict] = records or {}
        self.path = path

    # -- construction -------------------------------------------------------
    @classmethod
    def from_file(cls, path: str | Path) -> PropellantDB:
        path = Path(path)
        if not path.exists():
            return cls({}, path)
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        return cls(data.get("propellants", {}), path)

    @classmethod
    def load_default(cls) -> PropellantDB:
        from ..config import get_config

        return cls.from_file(get_config().resolve_path("paths.propellant_db"))

    # -- queries ------------------------------------------------------------
    def ids(self) -> list[str]:
        return sorted(self._records)

    def names(self) -> dict[str, str]:
        return {k: v.get("name", k) for k, v in self._records.items()}

    def __contains__(self, pid: str) -> bool:
        return pid in self._records

    def get(self, pid: str) -> PropellantModel:
        if pid not in self._records:
            raise KeyError(f"propellant '{pid}' not in database; available: {self.ids()}")
        return self._to_model(pid, self._records[pid])

    def all(self) -> list[PropellantModel]:
        return [self.get(pid) for pid in self.ids()]

    # -- mutation -----------------------------------------------------------
    def add(self, propellant: PropellantModel) -> None:
        self._records[propellant.id] = self._from_model(propellant)

    def remove(self, pid: str) -> None:
        self._records.pop(pid, None)

    def save(self, path: str | Path | None = None) -> Path:
        path = Path(path) if path else self.path
        if path is None:
            raise ValueError("no path to save propellant database")
        payload = {"propellants": self._records}
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            yaml.safe_dump(payload, fh, allow_unicode=True, sort_keys=True)
        self.path = path
        return path

    # -- (de)serialisation --------------------------------------------------
    @staticmethod
    def _to_model(pid: str, rec: dict) -> PropellantModel:
        rec = copy.deepcopy(rec)
        br = rec.pop("burn_rate", {})
        th = rec.pop("thermo", {})
        return PropellantModel(
            id=pid,
            burn_rate=BurnRateModel(**br),
            thermo=ThermalChem(**th),
            **rec,
        )

    @staticmethod
    def _from_model(p: PropellantModel) -> dict:
        d = p.model_dump()
        d.pop("id", None)
        # enums -> value
        d["burn_rate"]["law"] = BurnRateLaw(d["burn_rate"]["law"]).value
        return d
