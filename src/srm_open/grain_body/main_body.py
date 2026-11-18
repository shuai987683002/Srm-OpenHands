"""Cylindrical grain main-body calculation (M8).

This module answers the questions engineers ask about the *cylindrical body* of
a grain specifically:

* initial burning area and burning-area evolution of the main body;
* web thickness and the burn distance at which the grain breaks up (sliver);
* propellant volume, chamber volume and volumetric loading fraction;
* sliver (unburned residual) mass and fraction;
* length-to-diameter ratio;
* an engineering structural check (see :mod:`srm_open.integrity`).

"Sliver" is defined as the residual propellant when the burning surface reaches
its minimum before the grain is fully consumed -- here approximated by the
propellant remaining when the burning area has dropped to ``sliver_threshold``
of its initial value (a configurable engineering criterion, documented as an
assumption).
"""

from __future__ import annotations

import math

import numpy as np

from ..burnback.geometric import burnback_geometric
from ..models.grain import GrainGeometry
from ..models.results import GrainBodyResult


def _equivalent_solid_cylinder_volume(grain: GrainGeometry) -> float:
    return sum(
        math.pi * (s.outer_diameter / 2.0) ** 2 * s.length for s in grain.segments
    )


def compute_grain_body(grain: GrainGeometry, propellant_density: float,
                       sliver_threshold: float = 0.10,
                       chamber_diameter: float | None = None) -> GrainBodyResult:
    curve = burnback_geometric(grain, n_steps=200)
    a_b0 = float(curve.burn_area[0]) if len(curve.burn_area) else 0.0
    a_bf = float(curve.burn_area[-1]) if len(curve.burn_area) else 0.0
    prop_vol = curve.propellant_volume
    web = curve.web_thickness

    # chamber volume: grain envelope (cylinder) plus any free volume at the ends
    chamber_vol = _equivalent_solid_cylinder_volume(grain)
    loading = prop_vol / chamber_vol if chamber_vol > 0 else 0.0

    # sliver: residual propellant when A_b falls below the threshold of A_b0
    sliver_mass = 0.0
    if a_b0 > 0 and len(curve.burn_area) > 1:
        below = np.where(curve.burn_area < sliver_threshold * a_b0)[0]
        if len(below):
            idx = int(below[0])
            # residual propellant volume: integrate (port area growth) approx
            # V_residual = prop_vol - integral(A_b de) from 0..sliver_e
            if hasattr(np, "trapezoid"):
                burned = float(np.trapezoid(curve.burn_area[: idx + 1],
                                            curve.burn_distance[: idx + 1]))
            else:  # pragma: no cover
                burned = float(np.trapz(curve.burn_area[: idx + 1],
                                        curve.burn_distance[: idx + 1]))
            residual_vol = max(prop_vol - burned, 0.0)
            sliver_mass = residual_vol * propellant_density

    ld = grain.max_outer_diameter and grain.total_length / grain.max_outer_diameter

    assumptions = [
        "sliver defined as residual propellant when A_b drops below "
        f"{sliver_threshold:.0%} of initial A_b (configurable)",
        "chamber volume approximated by the grain envelope cylinder",
        "structural check is an engineering estimate, not a finite-element result",
    ]
    return GrainBodyResult(
        burn_area_initial=a_b0,
        burn_area_final=a_bf,
        web_thickness=web,
        propellant_volume=prop_vol,
        chamber_volume=chamber_vol,
        loading_fraction=loading,
        sliver_mass=sliver_mass,
        sliver_fraction=sliver_mass / (prop_vol * propellant_density) if prop_vol > 0 else 0.0,
        length_to_diameter=float(ld) if ld else 0.0,
        burn_area_curve=list(map(float, curve.burn_area)),
        burn_distance_curve=list(map(float, curve.burn_distance)),
        assumptions=assumptions,
    )
