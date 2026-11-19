"""Reproducibility metadata (NFR-2).

A result is reproducible when the same input, the same software version and the
same dependency versions give the same numbers.  This module records the two
things that were previously only declared in the data model:

* the runtime versions of every package the numeric core actually imports, and
* a SHA-256 over the canonical JSON of the design inputs, so two reports can be
  compared without diffing the whole file.
"""

from __future__ import annotations

import hashlib
import json
from importlib import metadata

# Only what changes a number.  Tooling (pytest, ruff, matplotlib) is excluded on
# purpose: it cannot alter a design result, and including it would make hashes
# differ between a laptop and CI for no reason.
_NUMERIC_PACKAGES = ("numpy", "scipy", "shapely", "pydantic", "PyYAML")


def dependency_versions() -> dict[str, str]:
    """Version of each numeric dependency, or ``"absent"`` when not installed."""
    out: dict[str, str] = {}
    for name in _NUMERIC_PACKAGES:
        try:
            out[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            out[name] = "absent"
    return out


def _canonical(obj) -> str:
    """Stable JSON: sorted keys, no whitespace drift, floats at full precision."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def input_hash(*parts) -> str:
    """SHA-256 of the canonical form of the design inputs.

    ``parts`` may be pydantic models, dicts or scalars; anything pydantic is
    dumped first so the hash tracks the *data*, not the class.
    """
    payload = []
    for p in parts:
        if p is None:
            payload.append(None)
        elif hasattr(p, "model_dump"):
            payload.append(p.model_dump(mode="json"))
        else:
            payload.append(p)
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()
