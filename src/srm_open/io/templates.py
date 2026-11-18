"""Grain template library (built-in and user-extendable).

Templates are complete :class:`~srm_open.models.grain.GrainGeometry` definitions
stored in ``data/templates/templates.yaml``.  Users can add their own by editing
the YAML or calling :func:`save_template`.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from ..config import get_config
from ..models.grain import GrainGeometry, GrainSegment


class TemplateLibrary:
    def __init__(self, records: dict[str, dict] | None = None, path: Path | None = None) -> None:
        self._records = records or {}
        self.path = path

    @classmethod
    def load_default(cls) -> TemplateLibrary:
        return cls.from_file(get_config().resolve_path("paths.template_dir") / "templates.yaml")

    @classmethod
    def from_file(cls, path: str | Path) -> TemplateLibrary:
        path = Path(path)
        if not path.exists():
            return cls({}, path)
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        return cls(data.get("templates", {}), path)

    def ids(self) -> list[str]:
        return sorted(self._records)

    def names(self) -> dict[str, str]:
        return {k: v.get("name", k) for k, v in self._records.items()}

    def get(self, tid: str) -> GrainGeometry:
        if tid not in self._records:
            raise KeyError(f"template '{tid}' not found; available: {self.ids()}")
        return self._to_geometry(self._records[tid])

    def geometry(self, tid: str) -> GrainGeometry:
        return self.get(tid)

    @staticmethod
    def _to_geometry(rec: dict) -> GrainGeometry:
        segments = [GrainSegment(**seg) for seg in rec.get("segments", [])]
        return GrainGeometry(segments=segments, source=rec.get("name", "template"))

    def save_template(self, tid: str, name: str, grain: GrainGeometry,
                      path: str | Path | None = None) -> Path:
        self._records[tid] = {
            "name": name,
            "segments": [seg.model_dump(mode="json") for seg in grain.segments],
        }
        return self.save(path)

    def save(self, path: str | Path | None = None) -> Path:
        path = Path(path) if path else self.path
        if path is None:
            raise ValueError("no path to save templates")
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            yaml.safe_dump({"templates": self._records}, fh, allow_unicode=True, sort_keys=True)
        self.path = path
        return path
