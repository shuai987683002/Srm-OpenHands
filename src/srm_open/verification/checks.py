"""Verification checks (M9) applied to a design.

Each check produces a named record with a pass/fail flag and the numbers behind
it, so the report can show *why* a design passes or fails.  Checks are the
trip-wires described in the design document: ballistic, performance, loading,
integrity and numerical consistency.
"""

from __future__ import annotations

from ..models.design_result import DesignResult
from ..models.requirements import RequirementSet
from ..models.results import IntegrityResult, InternalBallisticsResult, PerformanceResult


def _close(a: float | None, b: float | None, rel: float) -> tuple[bool, float | None]:
    if a is None or b is None or b == 0:
        return True, None
    dev = abs(a - b) / abs(b)
    return dev <= rel, dev


def run_checks(result: DesignResult, req: RequirementSet | None = None,
               tol: float = 0.05) -> DesignResult:
    rep = result.verification
    req = req or result.requirement
    perf: PerformanceResult | None = result.performance
    ball: InternalBallisticsResult | None = result.ballistics

    # mass-flow conservation: mdot_gen ~ mdot_out at each step (equilibrium model)
    if ball is not None and ball.mass_generated and ball.mass_discharged:
        n = min(len(ball.mass_generated), len(ball.mass_discharged))
        devs = []
        for i in range(n):
            mo = ball.mass_discharged[i]
            if mo > 0:
                devs.append(abs(ball.mass_generated[i] - mo) / mo)
        max_dev = max(devs) if devs else 0.0
        rep.add("mass_flow_balance", max_dev < 1e-3, max_dev, 1e-3,
                "mdot_gen vs mdot_out at equilibrium")

    # thrust vs requirement
    if perf is not None and req is not None:
        if req.thrust is not None:
            ok, dev = _close(perf.average_thrust, req.thrust, tol)
            rep.add("average_thrust_vs_requirement", ok, perf.average_thrust, req.thrust,
                    f"dev={dev if dev is None else round(dev,4)}")
        if req.total_impulse is not None:
            ok, dev = _close(perf.total_impulse, req.total_impulse, tol)
            rep.add("total_impulse_vs_requirement", ok, perf.total_impulse, req.total_impulse,
                    f"dev={dev if dev is None else round(dev,4)}")
        if req.burn_time is not None:
            ok, dev = _close(perf.burn_time, req.burn_time, max(tol, 0.15))
            rep.add("burn_time_vs_requirement", ok, perf.burn_time, req.burn_time,
                    f"dev={dev if dev is None else round(dev,4)}")
        if req.specific_impulse is not None:
            ok, dev = _close(perf.specific_impulse, req.specific_impulse, max(tol, 0.10))
            rep.add("specific_impulse_vs_requirement", ok, perf.specific_impulse,
                    req.specific_impulse, f"dev={dev if dev is None else round(dev,4)}")
        if req.max_chamber_pressure is not None and ball is not None:
            ok = ball.peak_pressure <= req.max_chamber_pressure * 1.0001
            rep.add("peak_pressure_below_limit", ok, ball.peak_pressure,
                    req.max_chamber_pressure)
        if req.min_load_fraction is not None and perf is not None:
            rep.add("loading_fraction_min", perf.loading_fraction >= req.min_load_fraction,
                    perf.loading_fraction, req.min_load_fraction)
        if req.max_sliver_fraction is not None and perf is not None:
            rep.add("sliver_fraction_max", perf.sliver_fraction <= req.max_sliver_fraction,
                    perf.sliver_fraction, req.max_sliver_fraction)

    # integrity
    integ: IntegrityResult | None = result.integrity
    if integ is not None and req is not None and req.min_safety_factor is not None:
        rep.add("structural_safety_factor", integ.safety_factor >= req.min_safety_factor,
                integ.safety_factor, req.min_safety_factor)

    # geometry sanity
    if result.grain is not None:
        segs = result.grain.segments
        ok = all(s.outer_diameter > 0 and s.length > 0 for s in segs)
        rep.add("geometry_positive_dimensions", ok, len(segs), None)
        if result.burnback is not None:
            ok = result.burnback.web_thickness > 0
            rep.add("positive_web_thickness", ok, result.burnback.web_thickness, 0.0)

    return result
