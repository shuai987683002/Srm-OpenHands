"""Forward design orchestrator (M6/M7).

Flow
----
1. complete/verify requirements (:mod:`srm_open.orchestration.requirements`);
2. evaluate thermochemistry (c*, gamma, M, T_c) at the design chamber pressure;
3. size the nozzle throat from the target thrust (or use the given throat);
4. compute burn-back A_b(e) for the grain;
5. solve internal ballistics -> p_c(t);
6. integrate performance -> F(t), I_t, I_sp, burn time;
7. cylindrical grain-body calculation (sliver, loading);
8. structural integrity check;
9. verification checks -> :class:`~srm_open.models.design_result.DesignResult`.
"""

from __future__ import annotations

import math

import numpy as np

from ..ballistics.solver import BallisticsInputs, solve_internal_ballistics
from ..burnback.engine import compute_burnback
from ..config import get_config
from ..geometry.analysis import analyze_segment
from ..grain_body.main_body import compute_grain_body
from ..integrity.lame import ElasticMaterial, lame_check
from ..models.design_result import DesignResult, Provenance
from ..models.grain import GrainGeometry
from ..models.nozzle import NozzleModel
from ..models.propellant import PropellantModel
from ..models.requirements import RequirementSet
from ..performance.analysis import analyze_performance
from ..provenance import dependency_versions, input_hash
from ..thermo.backend import evaluate as evaluate_thermo
from ..verification.checks import run_checks
from .requirements import check_and_complete


def design_chamber_pressure(req: RequirementSet, propellant: PropellantModel,
                            default: float = 7.0e6) -> float:
    """Design chamber pressure: the user's limit (derated) or a neutral default."""
    if req.max_chamber_pressure:
        return 0.9 * req.max_chamber_pressure
    return default


def size_throat_from_pressure(p_target: float, a_b: float, rho_p: float,
                              c_star: float, a: float, n: float) -> float:
    """Throat area that yields a target equilibrium chamber pressure.

    From ``p_c = (rho_p c* a A_b / A_t)^(1/(1-n))``:
    ``A_t = rho_p c* a A_b / p_c^(1-n)``.
    """
    if p_target <= 0:
        raise ValueError("p_target must be positive")
    return rho_p * c_star * a * a_b / p_target ** (1.0 - n)


def forward_design(req: RequirementSet, grain: GrainGeometry,
                   propellant: PropellantModel, nozzle: NozzleModel | None = None,
                   ballistics_model: str | None = None,
                   material: ElasticMaterial | None = None,
                   fast: bool = False) -> DesignResult:
    """Run the forward chain.  ``fast`` trades sampling resolution for speed and
    is intended for optimiser inner loops, never for reported results."""
    cfg = get_config()
    ballistics_model = ballistics_model or cfg.get("ballistics.model", "equilibrium")

    report = check_and_complete(req)

    p_design = design_chamber_pressure(req, propellant)
    thermo = evaluate_thermo(propellant, p_design, cfg.get("thermo.backend", "auto"))

    from ..burnback.geometric import burn_area_at

    a_b0 = burn_area_at(grain, 0.0)
    quad = 16 if fast else 64
    method = cfg.get("burnback.method", "analytic")
    n_steps = 48 if fast else cfg.get("burnback.n_steps", 200)
    burnback = compute_burnback(grain, method=method, n_steps=n_steps, quad_segs=quad)
    # The throat is sized from the same curve that later gives the propellant
    # volume: sizing it from a differently resolved peak burning area would leave
    # the throat inconsistent with the grain (a small throat means high pressure
    # and a short burn time).
    a_b_max = float(np.max(burnback.burn_area)) if len(burnback.burn_area) else a_b0
    if not a_b_max > 0.0:
        raise ValueError(
            "grain has no burning surface: the port fills the cross-section, so no "
            "throat can be sized; enlarge the web or reduce the port size")

    # nozzle: use the given one, else size the throat so that the *peak* burning
    # area gives the design chamber pressure (peak pressure = design pressure)
    if nozzle is None:
        if req.throat_diameter:
            d_t = req.throat_diameter
        else:
            a_t = size_throat_from_pressure(p_design, a_b_max, propellant.density,
                                            thermo.c_star, propellant.burn_rate.a,
                                            propellant.burn_rate.n)
            d_t = 2.0 * math.sqrt(a_t / math.pi)
        nozzle = NozzleModel(throat_diameter=d_t,
                             expansion_ratio=req.expansion_ratio or 8.0,
                             ambient_pressure=req.ambient_pressure,
                             source="sized from pressure target" if not req.throat_diameter else "user")

    # free volume = initial port volume
    free_vol = sum(analyze_segment(s).port_volume for s in grain.segments)

    inputs = BallisticsInputs(
        grain=grain, propellant=propellant, nozzle=nozzle,
        c_star=thermo.c_star, gamma=thermo.gamma,
        chamber_temperature=thermo.chamber_temperature,
        molecular_weight=thermo.molecular_weight,
        free_volume0=free_vol,
        ambient_pressure=req.ambient_pressure,
        pressure_floor=cfg.get("ballistics.pressure_floor", 1e4),
    )
    ball = solve_internal_ballistics(
        inputs, model=ballistics_model,
        n_steps=120 if fast else cfg.get("ballistics.time_steps", 400))

    prop_mass = burnback.propellant_volume * propellant.density
    body = compute_grain_body(grain, propellant.density, curve=burnback)
    perf = analyze_performance(
        ball, nozzle, thermo.gamma, thermo.chamber_temperature,
        thermo.molecular_weight, prop_mass, burnback.propellant_volume,
        burnback.propellant_volume + burnback.port_volume,
        ambient_pressure=req.ambient_pressure, sliver_mass=body.sliver_mass,
    )
    # if the user gave a burn-time target, report against it; otherwise use computed
    if req.burn_time:
        perf.burn_time = perf.burn_time or req.burn_time

    integrity = None
    if material is not None and grain.segments:
        seg = grain.segments[0]
        a = 0.0
        from ..geometry.analysis import port_polygon

        port = port_polygon(seg)
        a = math.sqrt(port.area / math.pi) if port.area > 0 else seg.outer_diameter * 0.1
        b = seg.outer_diameter / 2.0
        if b > a:
            integrity = lame_check(a, b, ball.peak_pressure, material,
                                   delta_t=0.0)

    result = DesignResult(
        mode="forward", requirement=req, propellant=propellant, nozzle=nozzle,
        grain=grain, burnback=burnback, ballistics=ball, performance=perf,
        grain_body=body, integrity=integrity,
        provenance=Provenance(
            software_version=__import__("srm_open").__version__,
            model_choices={
                "ballistics": ballistics_model,
                "burnback": cfg.get("burnback.method", "analytic"),
                "thermo": thermo.mode,
                "integrity": integrity.method if integrity else "none",
            },
            dependency_versions=dependency_versions(),
            data_sources=["propellant: " + (propellant.source or "user"),
                          "thermo: " + thermo.mode],
            input_hash=input_hash(req, grain, propellant, nozzle),
        ),
    )
    result.verification.checks.append(
        {"name": "requirement_consistency", "passed": not report.conflicts,
         "value": report.filled, "tol": None, "note": "; ".join(report.notes)}
    )
    result = run_checks(result, req)
    return result
