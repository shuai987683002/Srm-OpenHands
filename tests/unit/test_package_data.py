"""Package data tests.

``data/`` exists twice: the repository root copy that scripts and CI regenerate,
and the copy inside the package so a plain ``pip install`` is usable outside a
checkout. Two copies invite drift, so every shipped file is compared byte for
byte here. Regenerate with ``python scripts/sync_package_data.py``.
"""

from __future__ import annotations

import filecmp
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
REPO_DATA = ROOT / "data"
PKG_DATA = ROOT / "src" / "srm_open" / "data"

SHIPPED = [
    "propellants/propellants.yaml",
    "templates/templates.yaml",
    "thermo/thermo.yaml",
    "schema/grain.schema.json",
    "schema/nozzle.schema.json",
    "schema/project.schema.json",
    "schema/propellant.schema.json",
    "schema/requirement.schema.json",
]


@pytest.mark.parametrize("rel", SHIPPED)
def test_package_copy_matches_repo_copy(rel: str):
    repo = REPO_DATA / rel
    pkg = PKG_DATA / rel
    assert repo.is_file(), f"missing in repo data: {rel}"
    assert pkg.is_file(), (
        f"missing in package data: {rel}; run `python scripts/sync_package_data.py`"
    )
    assert filecmp.cmp(repo, pkg, shallow=False), (
        f"package copy is stale: {rel}; run `python scripts/sync_package_data.py`"
    )


def test_no_extra_files_in_package_copy():
    repo_files = {p.relative_to(REPO_DATA).as_posix() for p in REPO_DATA.rglob("*") if p.is_file()}
    pkg_files = {p.relative_to(PKG_DATA).as_posix() for p in PKG_DATA.rglob("*") if p.is_file()}
    assert pkg_files == repo_files, (
        f"package data drifted from repo data; extra={pkg_files - repo_files}, "
        f"missing={repo_files - pkg_files}"
    )
