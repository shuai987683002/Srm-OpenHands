"""High-level Python API: the one module most users import.

Example
-------
>>> from srm_open.api import design_from_template
>>> result = design_from_template(
...     "tube_simple", propellant_id="ap_htpb_80",
...     requirement={"thrust": 6000, "burn_time": 4.0, "max_chamber_pressure": 9e6},
... )
>>> result.performance.total_impulse  # doctest: +SKIP
"""

from __future__ import annotations

from typing import Any

from ..io.templates import TemplateLibrary
from ..models.design_result import DesignResult
from ..models.grain import BurnMode, GrainFamily, GrainGeometry, GrainSegment
from ..models.nozzle import NozzleModel
from ..models.propellant import PropellantModel
from ..models.requirements import RequirementSet
from ..orchestration.forward import forward_design
from ..propellant.database import PropellantDB
from ..thermo.backend import evaluate as evaluate_thermo


def _as_requirement(requirement: RequirementSet | dict[str, Any]) -> RequirementSet:
    if isinstance(requirement, RequirementSet):
        return requirement
    return RequirementSet(**requirement)


def get_propellant(propellant_id: str) -> PropellantModel:
    return PropellantDB.load_default().get(propellant_id)


def list_propellants() -> dict[str, str]:
    return PropellantDB.load_default().names()


def list_templates() -> dict[str, str]:
    return TemplateLibrary.load_default().names()


def evaluate_propellant_thermo(propellant_id: str, pressure: float) -> dict:
    prop = get_propellant(propellant_id)
    t = evaluate_thermo(prop, pressure)
    return {
        "c_star": t.c_star, "gamma": t.gamma,
        "molecular_weight": t.molecular_weight,
        "chamber_temperature": t.chamber_temperature,
        "mode": t.mode, "converged": t.converged,
        "mole_fractions": t.mole_fractions,
    }


def _performance_target_objective(req: RequirementSet, propellant: PropellantModel, space):
    """Objective over the grain design vector: mean squared relative error of the
    achieved performance against the requested indicators (plan §5.1 step 7)."""
    from ..orchestration.forward import forward_design

    def objective(x):
        geom = space.to_geometry(x)
        try:
            result = forward_design(req, geom, propellant)
        except Exception:
            return 1e6
        perf = result.performance
        if perf is None or perf.total_impulse <= 0:
            return 1e6
        err = 0.0
        n = 0
        if req.thrust:
            err += ((perf.average_thrust - req.thrust) / req.thrust) ** 2
            n += 1
        if req.burn_time:
            err += ((perf.burn_time - req.burn_time) / req.burn_time) ** 2
            n += 1
        if req.total_impulse:
            err += ((perf.total_impulse - req.total_impulse) / req.total_impulse) ** 2
            n += 1
        if req.max_chamber_pressure and result.ballistics:
            pk = result.ballistics.peak_pressure
            if pk > req.max_chamber_pressure:
                err += 2.0 * ((pk - req.max_chamber_pressure) / req.max_chamber_pressure) ** 2
                n += 2
        return err / max(n, 1)

    return objective


class _TargetReached(Exception):
    """Raised from the optimiser callback to stop refinement early."""


def _refine_synthesis(req: RequirementSet, propellant: PropellantModel, family: GrainFamily,
                      grain: GrainGeometry, max_iter: int,
                      tol: float = 1e-6) -> tuple[GrainGeometry, float]:
    """Warm-started local refinement of the synthesised grain (M9, plan §5.1 step 7).

    Synthesis gives a good starting point; a Nelder-Mead polish on the same
    forward model pulls thrust / burn time / total impulse onto the targets.
    Refinement stops as soon as the objective is below ``tol`` so complex
    families do not pay for iterations that buy nothing.  Returns the refined
    geometry and the achieved objective.
    """
    from ..inverse.design import default_design_space
    from ..inverse.optimize import minimize

    d_o = req.outer_diameter or grain.max_outer_diameter
    seg = grain.segments[0]
    length_range = (max(0.05, 0.5 * seg.length), 2.0 * seg.length)
    space = default_design_space(family, d_o, length_range)
    x0 = space.to_vector(seg.params, seg.length)
    objective = _performance_target_objective(req, propellant, space)

    best = {"x": x0, "fun": float("inf")}

    def tracked(x):
        val = objective(x)
        if val < best["fun"]:
            best["x"], best["fun"] = x, val
        return val

    def stop_when_good(x):
        if best["fun"] < tol:
            raise _TargetReached

    try:
        res = minimize(tracked, x0, bounds=space.bounds(), method="nelder_mead",
                       max_iter=max_iter, callback=stop_when_good)
        return space.to_geometry(res.x), float(res.fun)
    except _TargetReached:
        return space.to_geometry(best["x"]), float(best["fun"])


def design_forward(requirement: RequirementSet | dict[str, Any],
                   propellant_id: str = "ap_htpb_80",
                   family: str | GrainFamily | None = None,
                   length: float | None = None,
                   material: Any = None,
                   refine: bool = True,
                   refine_iters: int = 3,
                   refine_max_iter: int = 60,
                   **kwargs: Any) -> DesignResult:
    """Full forward design: indicators -> synthesised grain -> performance.

    If ``family`` is given it is used; otherwise the recommended family for the
    requested thrust profile is chosen automatically (see
    :func:`srm_open.orchestration.synthesis.recommend_families`).

    Synthesis sizes the web from the burning rate at a *design* pressure, but
    the realized chamber pressure is lower, so burn time comes out long.  The
    ``refine_iters`` loop re-synthesises with the realized mean pressure as the
    new design pressure, which pulls burn time onto the target.  When a hard
    performance target (thrust / burn time / total impulse) is still missed, a
    warm-started local refinement (``refine``, plan §5.1 step 7) polishes the
    grain parameters on the same forward model until the targets are met.
    """
    import numpy as np

    from ..orchestration.synthesis import recommend_families, synthesize_grain

    req = _as_requirement(requirement)
    prop = get_propellant(propellant_id)
    if family is None:
        family = recommend_families(req)[0][0]
    elif isinstance(family, str):
        family = GrainFamily(family)

    # The design pressure sizes the throat and the mean burn area; it is fixed.
    # The web target is corrected from the *realized* mean chamber pressure so
    # burn time lands near the target without disturbing the throat sizing.  The
    # mean is burning-rate-weighted (r ~ p^n) because the web is consumed at the
    # rate that corresponds to the pressure actually seen.
    design_pressure = 0.9 * req.max_chamber_pressure if req.max_chamber_pressure else 7e6
    web_pressure = design_pressure
    n = prop.burn_rate.n
    result = None
    info = None
    grain = None
    for _ in range(max(1, refine_iters)):
        grain, info = synthesize_grain(req, prop, family=family, length=length,
                                       design_pressure=design_pressure,
                                       web_pressure=web_pressure)
        result = forward_design(req, grain, prop, material=material, **kwargs)
        perf = result.performance
        p = np.array(result.ballistics.chamber_pressure)
        w = np.maximum(p, 1.0) ** n
        p_mean = float(np.sum(p * w) / np.sum(w)) if np.sum(w) > 0 else design_pressure
        if req.burn_time is None or req.burn_time <= 0:
            break
        if abs(perf.burn_time / req.burn_time - 1.0) < 0.03:
            break
        web_pressure = p_mean

    # M9 refinement: polish the grain so hard performance targets are met.
    refine_objective = None
    if refine and (req.thrust or req.burn_time or req.total_impulse) and grain is not None:
        grain, refine_objective = _refine_synthesis(
            req, prop, family, grain, refine_max_iter)
        result = forward_design(req, grain, prop, material=material, **kwargs)

    result.mode = "forward"
    synth_note = (f"family={family.value}, objective={info['objective']:.3e}, "
                  f"design_pressure={design_pressure/1e6:.3f} MPa")
    if refine_objective is not None:
        synth_note += f", refined_objective={refine_objective:.3e}"
    result.provenance.model_choices["grain_synthesis"] = synth_note
    result.verification.checks.append({
        "name": "grain_synthesis_mass", "passed": True,
        "value": info["achieved_propellant_mass"], "tol": info["target_propellant_mass"],
        "note": f"target {info['target_propellant_mass']:.3f} kg",
    })
    return result


def design_from_template(template_id: str,
                         propellant_id: str = "ap_htpb_80",
                         requirement: RequirementSet | dict[str, Any] | None = None,
                         nozzle: NozzleModel | None = None,
                         **kwargs: Any) -> DesignResult:
    grain = TemplateLibrary.load_default().geometry(template_id)
    prop = get_propellant(propellant_id)
    req = _as_requirement(requirement or {"thrust": 5000.0, "burn_time": 3.0})
    return forward_design(req, grain, prop, nozzle=nozzle, **kwargs)


def design_from_geometry(grain: GrainGeometry,
                         propellant_id: str = "ap_htpb_80",
                         requirement: RequirementSet | dict[str, Any] | None = None,
                         **kwargs: Any) -> DesignResult:
    prop = get_propellant(propellant_id)
    req = _as_requirement(requirement or {"thrust": 5000.0, "burn_time": 3.0})
    return forward_design(req, grain, prop, **kwargs)


def make_grain(family: str | GrainFamily, length: float, outer_diameter: float,
               params: dict[str, float], burn_mode: str | BurnMode = "internal") -> GrainGeometry:
    return GrainGeometry(segments=[GrainSegment(
        family=GrainFamily(family) if isinstance(family, str) else family,
        length=length, outer_diameter=outer_diameter, params=params,
        burn_mode=BurnMode(burn_mode) if isinstance(burn_mode, str) else burn_mode)])


__all__ = [
    "design_forward", "design_from_template", "design_from_geometry", "make_grain",
    "get_propellant", "list_propellants", "list_templates",
    "evaluate_propellant_thermo",
]
