"""Mirror ``data/`` into the package so wheels ship the databases.

``data/`` lives at the repository root, but setuptools' ``package-data`` is
relative to the package directory, so the root copy alone is not packaged. This
script copies it into ``src/srm_open/data``; ``tests/unit/test_package_data.py``
fails if the two ever differ.
"""

from __future__ import annotations

import filecmp
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO_DATA = ROOT / "data"
PKG_DATA = ROOT / "src" / "srm_open" / "data"


def sync() -> list[str]:
    changed: list[str] = []
    for src in sorted(REPO_DATA.rglob("*")):
        if not src.is_file():
            continue
        rel = src.relative_to(REPO_DATA)
        dst = PKG_DATA / rel
        if dst.is_file() and filecmp.cmp(src, dst, shallow=False):
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        changed.append(rel.as_posix())

    for dst in sorted(PKG_DATA.rglob("*")):
        if dst.is_file() and not (REPO_DATA / dst.relative_to(PKG_DATA)).is_file():
            dst.unlink()
            changed.append(f"removed {dst.relative_to(PKG_DATA).as_posix()}")
    return changed


if __name__ == "__main__":
    files = sync()
    if files:
        print("synced:", *files, sep="\n  ")
    else:
        print("package data already in sync")
