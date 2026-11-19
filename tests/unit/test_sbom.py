"""The SBOM must stay in step with pyproject.toml.

NFR-8 promises an auditable inventory. Without a guard the file rots the first
time someone adds a dependency, and the inventory silently under-reports.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import tomllib

ROOT = Path(__file__).resolve().parents[2]
SBOM = ROOT / "data_registry" / "sbom.md"

# Package names in pyproject differ from the import/distribution names we list.
ALIASES = {"pyyaml": "pyyaml", "pyyaml>=": "pyyaml"}


def _declared() -> set[str]:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text())
    specs = list(data["project"]["dependencies"])
    for extra in data["project"]["optional-dependencies"].values():
        specs.extend(extra)
    names = set()
    for spec in specs:
        name = spec.split(";")[0].strip()
        for sep in ("[", ">", "<", "=", "!", "~", " "):
            name = name.split(sep)[0]
        names.add(name.strip().lower())
    return names


def _documented() -> set[str]:
    names = set()
    for line in SBOM.read_text().splitlines():
        if not line.startswith("| "):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 4 or cells[0] in ("Package", "") or set(cells[0]) <= {"-"}:
            continue
        names.add(cells[0].lower())
    return names


def test_sbom_lists_every_declared_dependency():
    missing = _declared() - _documented()
    assert not missing, f"declared in pyproject.toml but absent from sbom.md: {sorted(missing)}"


def test_sbom_has_no_stale_entries():
    # The reverse direction catches renamed/removed deps that nobody cleaned up.
    extra = _documented() - _declared()
    assert not extra, f"listed in sbom.md but not declared anywhere: {sorted(extra)}"


def test_every_sbom_row_records_a_license():
    rows = 0
    for line in SBOM.read_text().splitlines():
        if not line.startswith("| "):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 4 or cells[0] in ("Package", "") or set(cells[0]) <= {"-"}:
            continue
        rows += 1
        assert cells[2], f"no license recorded for {cells[0]}"
    assert rows > 10, "SBOM table looks truncated"


@pytest.mark.parametrize("name", ["fastapi", "jsonschema", "uvicorn"])
def test_recently_added_optional_deps_are_inventoried(name):
    assert name in _documented()
