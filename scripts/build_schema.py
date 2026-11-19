"""Generate ``data/schema/*.json`` from the pydantic models.

The schema files are derived, never hand-written: a hand-maintained schema
drifts from the models the moment a field changes, and then editors validate
against a fiction. Regenerate after touching a model::

    python scripts/build_schema.py

``tests/unit/test_schema.py`` fails if the committed files are stale.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from srm_open.models.grain import GrainGeometry  # noqa: E402
from srm_open.models.nozzle import NozzleModel  # noqa: E402
from srm_open.models.propellant import PropellantModel  # noqa: E402
from srm_open.models.requirements import RequirementSet  # noqa: E402

SCHEMA_DIR = ROOT / "data" / "schema"

# The project file is a plain dict of these four objects (see io/project.py), so
# its schema is assembled here rather than from a dedicated pydantic class.
PROJECT_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "Srm-Open project file",
    "type": "object",
    "required": ["format", "version", "requirement", "propellant", "grain"],
    "properties": {
        "format": {"const": "srm-open-project"},
        "version": {"const": 1},
        "meta": {"type": "object"},
        # Cross-file refs, not inlined subschemas: each model schema keeps its own
        # "$defs", and inlining them would leave "#/$defs/..." pointing at a root
        # that has none.
        "requirement": {"$ref": "requirement.schema.json"},
        "propellant": {"$ref": "propellant.schema.json"},
        "grain": {"$ref": "grain.schema.json"},
        "nozzle": {"$ref": "nozzle.schema.json"},
    },
    "additionalProperties": False,
}


def _model_schema(model) -> dict:
    """pydantic omits ``$schema``; a validator loading these files standalone needs it."""
    schema = model.model_json_schema()
    return {"$schema": "https://json-schema.org/draft/2020-12/schema", **schema}


def build() -> dict[str, dict]:
    return {
        "project.schema.json": PROJECT_SCHEMA,
        "requirement.schema.json": _model_schema(RequirementSet),
        "propellant.schema.json": _model_schema(PropellantModel),
        "grain.schema.json": _model_schema(GrainGeometry),
        "nozzle.schema.json": _model_schema(NozzleModel),
    }


def main() -> int:
    SCHEMA_DIR.mkdir(parents=True, exist_ok=True)
    for name, schema in build().items():
        path = SCHEMA_DIR / name
        path.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"wrote {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
