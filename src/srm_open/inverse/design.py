"""Inverse design: recover grain parameters from geometry, burn-area data or
ballistic requirements.

Three entry points
------------------
* :func:`analyze_existing_grain` -- *reverse engineering* of an existing grain:
  from the geometry (or a user polygon) recover the standard design parameters
  (port area, initial burning area, web, sliver, L/D, loading) that a designer
  would otherwise read off a drawing.
* :func:`fit_burn_area` -- given a measured/target burning-area curve ``A_b(e)``,
  recover the family parameters that reproduce it.
* :func:`inverse_design` -- given ballistic requirements (thrust / impulse /
  burn time / impulse / Isp / peak pressure), recover the grain parameters and
  the throat diameter, then re-run the forward pipeline on the recovered design
  (the closed loop).

All optimisers operate on the *same* forward model, so a recovered design is
guaranteed to be self-consistent with the forward chain.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from ..burnback.geometric import burnback_geometric
from ..geometry.analysis import port_polygon
from ..models.design_result import DesignResult
from ..models.grain import BurnMode, GrainFamily, GrainGeometry, GrainSegment
from ..models.propellant import PropellantModel
from ..models.requirements import RequirementSet
from ..orchestration.forward import forward_design
from .optimize import minimize


# ---------------------------------------------------------------------------
# design space: continuous vector <-> GrainSegment
# ---------------------------------------------------------------------------
@dataclass
class Parameter:
    name: str
    low: float
    high: float
    scale: str = "linear"  # linear | log
    integer: bool = False  # count parameters (n_points, n_slots, ...)

    def clamp(self, value: float) -> float:
        v = min(max(value, self.low), self.high)
        return float(round(v)) if self.integer else float(v)


@dataclass
class GrainDesignSpace:
    """Maps a continuous parameter vector to a :class:`GrainGeometry`."""

    family: GrainFamily
    parameters: list[Parameter]
    outer_diameter: float
    burn_mode: BurnMode = BurnMode.INTERNAL
    fixed: dict[str, float] = field(default_factory=dict)

    def to_vector(self, params: dict[str, float], length: float) -> np.ndarray:
        vec = []
        for p in self.parameters:
            if p.name == "length":
                vec.append(length)
            else:
                vec.append(params.get(p.name, 0.5 * (p.low + p.high)))
        return np.array(vec, dtype=float)

    def to_geometry(self, x: np.ndarray) -> GrainGeometry:
        x = np.asarray(x, dtype=float)
        params: dict[str, float] = dict(self.fixed)
        length = 0.0
        for p, val in zip(self.parameters, x):
            v = p.clamp(float(val))
            if p.name == "length":
                length = v
            else:
                params[p.name] = v
        return GrainGeometry(segments=[GrainSegment(
            family=self.family, length=length, outer_diameter=self.outer_diameter,
            params=params, burn_mode=self.burn_mode)])

    def to_params(self, x: np.ndarray) -> dict[str, float]:
        return {p.name: p.clamp(float(v)) for p, v in zip(self.parameters, x)}

    def bounds(self) -> list[tuple[float, float]]:
        return [(p.low, p.high) for p in self.parameters]


def default_design_space(family: GrainFamily, outer_diameter: float,
                         length_range: tuple[float, float],
                         burn_mode: BurnMode = BurnMode.INTERNAL) -> GrainDesignSpace:
    """Reasonable, documented bounds for each family (all lengths in metres)."""
    r = outer_diameter / 2.0
    L = Parameter("length", length_range[0], length_range[1])
    common = [
        Parameter("inner_diameter", 0.02 * outer_diameter, 0.9 * outer_diameter),
    ]
    if family in (GrainFamily.TUBE, GrainFamily.TUBE_INHIBITED):
        params = common + [L]
    elif family == GrainFamily.STAR:
        params = [
            Parameter("inner_diameter", 0.05 * outer_diameter, 0.6 * outer_diameter),
            Parameter("point_diameter", 0.2 * outer_diameter, 0.98 * outer_diameter),
            Parameter("point_half_angle", 8.0, 45.0),
            Parameter("n_points", 4.0, 12.0, integer=True),
            L,
        ]
    elif family == GrainFamily.WAGON_WHEEL:
        params = [
            Parameter("inner_diameter", 0.05 * outer_diameter, 0.5 * outer_diameter),
            Parameter("spoke_width", 0.02 * outer_diameter, 0.3 * outer_diameter),
            Parameter("spoke_length", 0.05 * outer_diameter, 0.9 * r),
            Parameter("n_spokes", 3.0, 12.0, integer=True),
            L,
        ]
    elif family == GrainFamily.SLOTTED:
        params = [
            Parameter("inner_diameter", 0.05 * outer_diameter, 0.6 * outer_diameter),
            Parameter("slot_width", 0.01 * outer_diameter, 0.2 * outer_diameter),
            Parameter("slot_depth", 0.02 * outer_diameter, 0.9 * r),
            Parameter("n_slots", 2.0, 12.0, integer=True),
            L,
        ]
    elif family == GrainFamily.FINOCYL:
        params = [
            Parameter("inner_diameter", 0.05 * outer_diameter, 0.6 * outer_diameter),
            Parameter("fin_width", 0.01 * outer_diameter, 0.2 * outer_diameter),
            Parameter("fin_length", 0.02 * outer_diameter, 0.9 * r),
            Parameter("n_fins", 2.0, 12.0, integer=True),
            L,
        ]
    elif family == GrainFamily.MULTI_PERFORATED:
        params = [
            Parameter("perf_diameter", 0.01 * outer_diameter, 0.15 * outer_diameter),
            Parameter("n_perforations", 7.0, 7.0, integer=True),
            L,
        ]
    elif family == GrainFamily.SPHERICAL:
        params = [Parameter("cavity_diameter", 0.05 * outer_diameter, 0.98 * outer_diameter), L]
    elif family == GrainFamily.CONICAL:
        params = [
            Parameter("base_diameter", 0.05 * outer_diameter, 0.98 * outer_diameter),
            Parameter("apex_diameter", 0.0, 0.5 * outer_diameter),
            L,
        ]
    else:
        params = common + [L]
    return GrainDesignSpace(family=family, parameters=params,
                            outer_diameter=outer_diameter, burn_mode=burn_mode)


# ---------------------------------------------------------------------------
# inverse: existing grain -> design parameters
# ---------------------------------------------------------------------------
def analyze_existing_grain(grain: GrainGeometry, propellant_density: float = 0.0,
                           sliver_threshold: float = 0.10) -> dict:
    """Recover standard design parameters from an existing grain geometry."""
    curve = burnback_geometric(grain, n_steps=200)
    a_b0 = float(curve.burn_area[0]) if len(curve.burn_area) else 0.0
    a_bmax = float(np.max(curve.burn_area)) if len(curve.burn_area) else 0.0
    idx_max = int(np.argmax(curve.burn_area)) if len(curve.burn_area) else 0
    web = curve.web_thickness
    port_area = 0.0
    for seg in grain.segments:
        port_area += port_polygon(seg).area
    vol = curve.propellant_volume
    out = {
        "family": [s.family.value for s in grain.segments],
        "total_length": grain.total_length,
        "outer_diameter": grain.max_outer_diameter,
        "length_to_diameter": grain.total_length / grain.max_outer_diameter
        if grain.max_outer_diameter else 0.0,
        "port_area": port_area,
        "initial_burning_area": a_b0,
        "max_burning_area": a_bmax,
        "burn_distance_at_max_area": float(curve.burn_distance[idx_max]) if web > 0 else 0.0,
        "web_thickness": web,
        "propellant_volume": vol,
        "port_volume": curve.port_volume,
        "loading_fraction": vol / (vol + curve.port_volume) if (vol + curve.port_volume) > 0 else 0.0,
        "burn_area_curve": list(map(float, curve.burn_area)),
        "burn_distance_curve": list(map(float, curve.burn_distance)),
    }
    if propellant_density > 0:
        out["propellant_mass"] = vol * propellant_density
    return out


# ---------------------------------------------------------------------------
# inverse: burn-area curve -> family parameters
# ---------------------------------------------------------------------------
def fit_burn_area(space: GrainDesignSpace, target_e: list[float], target_ab: list[float],
                  x0: np.ndarray | None = None, method: str = "cma",
                  max_iter: int = 300) -> dict:
    """Fit grain parameters so that A_b(e) matches a target curve."""
    te = np.asarray(target_e, dtype=float)
    ta = np.asarray(target_ab, dtype=float)
    scale = max(float(np.max(ta)), 1e-12)
    x0 = x0 if x0 is not None else np.array([0.5 * (p.low + p.high) for p in space.parameters])

    def objective(x):
        g = space.to_geometry(x)
        curve = burnback_geometric(g, n_steps=80)
        pred = np.interp(te, curve.burn_distance, curve.burn_area, left=curve.burn_area[0], right=0.0)
        return float(np.mean(((pred - ta) / scale) ** 2))

    res = minimize(objective, x0, bounds=space.bounds(), method=method, max_iter=max_iter)
    geom = space.to_geometry(res.x)
    return {
        "geometry": geom,
        "params": space.to_params(res.x),
        "rms_relative_error": math.sqrt(res.fun),
        "success": res.success,
        "iterations": res.nit,
    }


# ---------------------------------------------------------------------------
# inverse: ballistic requirements -> grain + throat (closed loop)
# ---------------------------------------------------------------------------
def inverse_design(req: RequirementSet, propellant: PropellantModel,
                   space: GrainDesignSpace, x0: np.ndarray | None = None,
                   method: str = "cma", max_iter: int = 300,
                   weights: dict[str, float] | None = None,
                   ballistics_model: str = "equilibrium") -> DesignResult:
    """Recover a grain + nozzle that meets the ballistic requirements.

    The objective is the weighted relative error of the achieved performance
    versus the requirements; the throat is re-sized analytically inside the
    forward model at every evaluation, so the loop is closed on the *forward*
    pipeline.
    """
    weights = weights or {}
    w_f = weights.get("thrust", 1.0)
    w_tb = weights.get("burn_time", 1.0)
    w_it = weights.get("total_impulse", 1.0)
    w_isp = weights.get("specific_impulse", 0.5)
    w_p = weights.get("peak_pressure", 1.0)

    x0 = x0 if x0 is not None else np.array([0.5 * (p.low + p.high) for p in space.parameters])

    def objective(x):
        geom = space.to_geometry(x)
        try:
            result = forward_design(req, geom, propellant, ballistics_model=ballistics_model)
        except Exception:
            return 1e6
        perf = result.performance
        if perf is None or perf.total_impulse <= 0:
            return 1e6
        err = 0.0
        n = 0.0
        if req.thrust:
            err += w_f * ((perf.average_thrust - req.thrust) / req.thrust) ** 2
            n += w_f
        if req.burn_time:
            err += w_tb * ((perf.burn_time - req.burn_time) / req.burn_time) ** 2
            n += w_tb
        if req.total_impulse:
            err += w_it * ((perf.total_impulse - req.total_impulse) / req.total_impulse) ** 2
            n += w_it
        if req.specific_impulse:
            err += w_isp * ((perf.specific_impulse - req.specific_impulse) / req.specific_impulse) ** 2
            n += w_isp
        if req.max_chamber_pressure and result.ballistics:
            pk = result.ballistics.peak_pressure
            if pk > req.max_chamber_pressure:
                err += w_p * ((pk - req.max_chamber_pressure) / req.max_chamber_pressure) ** 2
                n += w_p
        return err / max(n, 1e-9)

    res = minimize(objective, x0, bounds=space.bounds(), method=method, max_iter=max_iter)
    geom = space.to_geometry(res.x)
    result = forward_design(req, geom, propellant, ballistics_model=ballistics_model)
    result.mode = "inverse"
    # Judge convergence by the *achieved* residual, not the optimiser's internal
    # `success` flag (Nelder-Mead sets it False on simplex shrink even when the
    # physical targets are met).
    achieved = float(objective(res.x))
    result.verification.checks.append({
        "name": "inverse_convergence", "passed": achieved < 1e-3,
        "value": achieved, "tol": 1e-3,
        "note": f"{method}, {res.nit} iterations, optimiser_fun={res.fun:.3e}",
    })
    result.provenance.model_choices["inverse_optimizer"] = method
    result.provenance.model_choices["inverse_objective"] = f"{achieved:.3e}"
    result.provenance.random_seed = 12345
    return result


def inverse_burn_rate(pressures: list[float], rates: list[float],
                      T_ref: float = 293.15) -> dict:
    """Recover Saint-Robert coefficients (a, n) from measured r(p) data."""
    p = np.asarray(pressures, dtype=float)
    r = np.asarray(rates, dtype=float)
    mask = (p > 0) & (r > 0)
    if mask.sum() < 2:
        raise ValueError("need at least two positive (p, r) points")
    lnp = np.log(p[mask])
    lnr = np.log(r[mask])
    n, ln_a = np.polyfit(lnp, lnr, 1)
    a = math.exp(ln_a)
    pred = a * p[mask] ** n
    rel = np.abs(pred - r[mask]) / r[mask]
    return {"a": a, "n": float(n), "reference_temperature": T_ref,
            "max_relative_error": float(np.max(rel)),
            "mean_relative_error": float(np.mean(rel))}
