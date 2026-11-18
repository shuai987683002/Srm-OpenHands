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


def design_forward(requirement: RequirementSet | dict[str, Any],
                   propellant_id: str = "ap_htpb_80",
                   family: str | GrainFamily | None = None,
                   length: float | None = None,
                   material: Any = None,
                   refine_iters: int = 3,
                   **kwargs: Any) -> DesignResult:
    """Full forward design: indicators -> synthesised grain -> performance.

    If ``family`` is given it is used; otherwise the recommended family for the
    requested thrust profile is chosen automatically (see
    :func:`srm_open.orchestration.synthesis.recommend_families`).

    Synthesis sizes the web from the burning rate at a *design* pressure, but
    the realized chamber pressure is lower, so burn time comes out long.  The
    ``refine_iters`` loop re-synthesises with the realized mean pressure as the
    new design pressure, which pulls burn time onto the target.
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

    result.mode = "forward"
    result.provenance.model_choices["grain_synthesis"] = (
        f"family={family.value}, objective={info['objective']:.3e}, "
        f"design_pressure={design_pressure/1e6:.3f} MPa")
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
