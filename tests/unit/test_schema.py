"""Dataset JSON Schema tests (``data/schema``).

Two things are checked: the committed schemas still match the models (a stale
schema silently validates the wrong thing), and the shipped example projects
actually satisfy the schema. The second is what proves the schema is usable
rather than decorative.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = ROOT / "data" / "schema"


def _load(name: str) -> dict:
    return json.loads((SCHEMA_DIR / name).read_text(encoding="utf-8"))


def test_all_expected_schemas_exist():
    expected = {
        "project.schema.json",
        "requirement.schema.json",
        "propellant.schema.json",
        "grain.schema.json",
        "nozzle.schema.json",
    }
    assert expected <= {p.name for p in SCHEMA_DIR.glob("*.json")}


def test_committed_schemas_match_the_models(tmp_path):
    """Regenerate into a temp dir and compare: catches an un-rerun build script."""
    sys.path.insert(0, str(ROOT / "src"))
    sys.path.insert(0, str(ROOT / "scripts"))
    import build_schema

    for name, schema in build_schema.build().items():
        committed = _load(name)
        assert committed == schema, (
            f"{name} is stale; run `python scripts/build_schema.py`"
        )


def test_schemas_declare_the_draft():
    # Without $schema a validator cannot pick the spec when loading the file.
    for path in SCHEMA_DIR.glob("*.json"):
        assert "$schema" in _load(path.name), path.name


def test_nested_refs_resolve_within_their_own_file():
    """The project schema must not inline subschemas with dangling #/$defs refs."""
    project = _load("project.schema.json")
    for key in ("requirement", "propellant", "grain", "nozzle"):
        ref = project["properties"][key]
        assert set(ref) == {"$ref"}, f"{key} must be a cross-file ref, got {ref}"
        target = SCHEMA_DIR / ref["$ref"]
        assert target.exists(), ref["$ref"]


@pytest.mark.parametrize("path", sorted((ROOT / "examples" / "projects").glob("*/project.yaml")),
                         ids=lambda p: p.parent.name)
def test_example_projects_validate(path):
    jsonschema = pytest.importorskip("jsonschema")
    from referencing import Registry, Resource

    registry = Registry().with_resources(
        [(p.name, Resource.from_contents(_load(p.name))) for p in SCHEMA_DIR.glob("*.json")]
    )
    validator = jsonschema.Draft202012Validator(_load("project.schema.json"), registry=registry)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    errors = sorted(validator.iter_errors(data), key=lambda e: list(e.path))
    assert not errors, "; ".join(f"{list(e.path)}: {e.message}" for e in errors[:3])


def test_schema_rejects_a_malformed_project():
    """A schema that accepts everything is worthless; prove it rejects."""
    jsonschema = pytest.importorskip("jsonschema")
    from referencing import Registry, Resource

    registry = Registry().with_resources(
        [(p.name, Resource.from_contents(_load(p.name))) for p in SCHEMA_DIR.glob("*.json")]
    )
    validator = jsonschema.Draft202012Validator(_load("project.schema.json"), registry=registry)
    assert list(validator.iter_errors({"format": "srm-open-project", "version": 1}))
    bad_grain = yaml.safe_load(
        (ROOT / "examples/projects/01_tube_sounding/project.yaml").read_text(encoding="utf-8")
    )
    bad_grain["grain"]["segments"][0]["family"] = "not_a_family"
    assert list(validator.iter_errors(bad_grain))


def test_build_script_runs_standalone():
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "build_schema.py")],
        capture_output=True, text=True, cwd=str(ROOT),
    )
    assert result.returncode == 0, result.stderr
