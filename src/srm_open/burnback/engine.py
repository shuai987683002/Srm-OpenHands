"""Burn-back engine: ties geometry (A_b(e)) to the burning rate and provides
the cross-check between the vector and grid channels.

``burn_distance`` is the natural independent variable for the geometry; the
time mapping ``e(t) = integral r(p(t)) dt`` is performed by the internal
ballistics solver, which calls :func:`burn_area_at` at every step.
"""

from __future__ import annotations

import numpy as np

from ..models.grain import GrainGeometry
from ..models.results import BurnbackResult
from .geometric import BurnbackCurve, burn_area_at, burnback_geometric
from .levelset import levelset_grain_burnback


def compute_burnback(grain: GrainGeometry, method: str = "analytic",
                     n_steps: int = 200, nx: int = 240, ny: int = 240) -> BurnbackResult:
    """Return a :class:`BurnbackResult` for the requested method."""
    if method == "levelset":
        res = levelset_grain_burnback(grain, n_snapshots=n_steps, nx=nx, ny=ny)
        return BurnbackResult(
            method="levelset",
            burn_distance=list(res.burn_distance),
            burn_area=list(res.burn_area),
            web_thickness=float(res.burn_distance[-1]),
            notes="; ".join(res.notes),
        )
    curve = burnback_geometric(grain, n_steps=n_steps)
    return BurnbackResult(
        method="analytic",
        burn_distance=list(curve.burn_distance),
        burn_area=list(curve.burn_area),
        web_thickness=curve.web_thickness,
        propellant_volume=curve.propellant_volume,
        port_volume=curve.port_volume,
        notes="; ".join(curve.notes),
    )


def crosscheck(grain: GrainGeometry, n_steps: int = 40, nx: int = 240, ny: int = 240) -> dict:
    """Compare the vector and grid burn-back channels.

    Returns the maximum relative deviation of A_b(e) over the common burn
    distance range, plus the two curves, for the verification report.
    """
    vec = burnback_geometric(grain, n_steps=n_steps)
    ls = levelset_grain_burnback(grain, e_max=vec.web_thickness, n_snapshots=n_steps, nx=nx, ny=ny)
    e = vec.burn_distance
    a_vec = vec.burn_area
    a_ls = np.interp(e, ls.burn_distance, ls.burn_area)
    denom = np.maximum(np.abs(a_vec), 1e-12)
    rel = np.abs(a_vec - a_ls) / denom
    # ignore the last few points where both areas approach zero
    mask = a_vec > 0.05 * a_vec.max() if a_vec.max() > 0 else np.ones_like(a_vec, dtype=bool)
    max_rel = float(np.max(rel[mask])) if mask.any() else float(np.max(rel))
    return {
        "burn_distance": list(map(float, e)),
        "area_geometric": list(map(float, a_vec)),
        "area_levelset": list(map(float, a_ls)),
        "max_relative_deviation": max_rel,
        "mean_relative_deviation": float(np.mean(rel[mask])) if mask.any() else float(np.mean(rel)),
    }


__all__ = [
    "BurnbackCurve", "burnback_geometric", "burn_area_at",
    "compute_burnback", "crosscheck", "levelset_grain_burnback",
]
