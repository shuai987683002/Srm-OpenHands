"""Performance analysis (M7).

Converts the chamber-pressure history into thrust, impulse and integral metrics.
Uses the same nozzle model as the ballistics solver so the chain is consistent.
"""

from __future__ import annotations

import numpy as np

from ..constants import G0
from ..models.nozzle import NozzleModel
from ..models.results import InternalBallisticsResult, PerformanceResult
from ..nozzle.model import Nozzle


def analyze_performance(ballistics: InternalBallisticsResult, nozzle: NozzleModel,
                        gamma: float, chamber_temperature: float,
                        molecular_weight: float, propellant_mass: float,
                        propellant_volume: float, chamber_volume: float,
                        burn_time: float | None = None,
                        sliver_mass: float = 0.0,
                        ambient_pressure: float | None = None) -> PerformanceResult:
    nz = Nozzle(nozzle)
    t = np.array(ballistics.time)
    p = np.array(ballistics.chamber_pressure)
    if len(t) < 2:
        return PerformanceResult(notes="insufficient ballistics points")

    cf = np.array([nz.thrust_coefficient(max(pp, 1.0), gamma, ambient_pressure) for pp in p])
    a_t = nozzle.throat_area
    f = cf * a_t * p

    it = float(np.trapezoid(f, t)) if hasattr(np, "trapezoid") else float(np.trapz(f, t))
    # effective burn time: propellant consumed up to sliver
    tb = burn_time if burn_time is not None else float(t[-1])
    avg_f = it / tb if tb > 0 else 0.0
    isp = it / (propellant_mass * G0) if propellant_mass > 0 else 0.0
    loading = propellant_volume / chamber_volume if chamber_volume > 0 else 0.0
    sliver_fraction = sliver_mass / propellant_mass if propellant_mass > 0 else 0.0
    return PerformanceResult(
        time=list(map(float, t)),
        thrust=list(map(float, f)),
        thrust_coefficient=list(map(float, cf)),
        total_impulse=it,
        specific_impulse=isp,
        average_thrust=avg_f,
        burn_time=tb,
        propellant_mass=propellant_mass,
        loading_fraction=loading,
        sliver_fraction=sliver_fraction,
        mass_ratio=(propellant_mass + sliver_mass) / propellant_mass if propellant_mass else 0.0,
        peak_thrust=float(np.max(f)) if len(f) else 0.0,
        peak_pressure=float(np.max(p)) if len(p) else 0.0,
    )
