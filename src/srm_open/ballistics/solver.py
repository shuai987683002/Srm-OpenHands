"""Internal ballistics: chamber pressure history.

Two models
----------
* **equilibrium** (default, teaching): at each instant the chamber pressure
  satisfies the mass balance between gas generation and choked nozzle discharge

      rho_p * A_b(e) * a * p^n = p * A_t / c*          (mdot_gen = mdot_out)

  which rearranges to the closed form

      p = ( rho_p * c* * a * A_b / A_t )^(1/(1-n))

  The burn distance advances as ``de/dt = r`` so ``e`` and ``t`` are linked by
  the same burning-rate law.

* **transient** (optional): integrates the filling equation

      d(p V / (R T_c)) / dt = rho_p A_b r - p A_t / c*

  with an ODE solver (``scipy.integrate.solve_ivp``), which captures ignition
  transients and the pressure tail.

Both models use :func:`srm_open.burnback.burn_area_at`, so they share the exact
same geometry channel as the burn-back engine (forward/inverse consistency).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..burnback.geometric import burnback_geometric
from ..constants import R_UNIVERSAL
from ..models.grain import GrainGeometry
from ..models.nozzle import NozzleModel
from ..models.propellant import PropellantModel
from ..models.results import InternalBallisticsResult
from ..propellant.burn_rate import build_burn_rate


@dataclass
class BallisticsInputs:
    grain: GrainGeometry
    propellant: PropellantModel
    nozzle: NozzleModel
    c_star: float
    gamma: float
    chamber_temperature: float
    molecular_weight: float
    free_volume0: float
    ambient_pressure: float = 101325.0
    pressure_floor: float = 1.0e4


def equilibrium_pressure(a_b: float, rho_p: float, c_star: float, a: float, n: float,
                         a_t: float, floor: float = 0.0) -> float:
    """Closed-form equilibrium chamber pressure."""
    if a_b <= 0 or a_t <= 0:
        return 0.0
    if abs(1.0 - n) < 1e-12:
        # degenerate: linear in A_b, no equilibrium unless exactly matched
        return max(a_b * rho_p * c_star * a / a_t, floor)
    p = (rho_p * c_star * a * a_b / a_t) ** (1.0 / (1.0 - n))
    return max(p, floor)


def solve_equilibrium(inputs: BallisticsInputs, n_steps: int | None = None) -> InternalBallisticsResult:
    """Quasi-steady equilibrium internal ballistics."""
    grain = inputs.grain
    prop = inputs.propellant
    nozzle = inputs.nozzle
    r_fn = build_burn_rate(prop.burn_rate)
    a_coef, n_exp = prop.burn_rate.a, prop.burn_rate.n
    rho_p = prop.density

    curve = burnback_geometric(grain, n_steps=max(n_steps or 200, 50))
    e_max = curve.web_thickness
    # A_b(e) is tabulated by the sweep above; interpolating it avoids repeating a
    # full shapely burn-back evaluation at every march step.
    e_grid = np.asarray(curve.burn_distance)
    ab_grid = np.asarray(curve.burn_area)

    def area_at(e: float) -> float:
        return float(np.interp(e, e_grid, ab_grid))

    times: list[float] = []
    presses: list[float] = []
    ab_list: list[float] = []
    mgen: list[float] = []
    mout: list[float] = []
    kn_list: list[float] = []

    a_t = nozzle.throat_area
    e = 0.0
    t = 0.0
    guard = 0
    while e < e_max - 1e-12 and guard < 2_000_000:
        guard += 1
        a_b = area_at(e)
        p = equilibrium_pressure(a_b, rho_p, inputs.c_star, a_coef, n_exp, a_t,
                                 inputs.pressure_floor)
        r = r_fn(max(p, 1.0))
        times.append(t)
        presses.append(p)
        ab_list.append(a_b)
        mgen.append(rho_p * a_b * r)
        mout.append(p * a_t / inputs.c_star)
        kn_list.append(a_b / a_t if a_t > 0 else 0.0)
        if r <= 0:
            break
        de = min(max(0.01 * e_max, 2e-4), 2e-3, e_max - e)
        if de <= 0:
            break
        e += de
        t += de / r

    p_arr = np.array(presses)
    time_arr = np.array(times)
    peak = float(np.max(p_arr)) if len(p_arr) else 0.0
    t_peak = float(time_arr[int(np.argmax(p_arr))]) if len(p_arr) else 0.0
    return InternalBallisticsResult(
        model="equilibrium",
        time=list(map(float, time_arr)),
        chamber_pressure=list(map(float, p_arr)),
        mass_generated=list(map(float, mgen)),
        mass_discharged=list(map(float, mout)),
        burn_area=list(map(float, ab_list)),
        kn=list(map(float, kn_list)),
        peak_pressure=peak,
        time_at_peak=t_peak,
        free_volume_initial=inputs.free_volume0,
        converged=True,
    )


def solve_transient(inputs: BallisticsInputs, n_steps: int | None = None,
                    rtol: float = 1e-6, atol: float = 1e-3) -> InternalBallisticsResult:
    """Transient filling equation integrated with an ODE solver."""
    from scipy.integrate import solve_ivp

    grain = inputs.grain
    prop = inputs.propellant
    nozzle = inputs.nozzle
    r_fn = build_burn_rate(prop.burn_rate)
    rho_p = prop.density
    curve = burnback_geometric(grain, n_steps=max(n_steps or 200, 50))
    e_max = curve.web_thickness
    e_grid = np.asarray(curve.burn_distance)
    ab_grid = np.asarray(curve.burn_area)

    def area_at(e: float) -> float:
        return float(np.interp(e, e_grid, ab_grid))

    a_t = nozzle.throat_area
    Rt = R_UNIVERSAL * inputs.chamber_temperature / inputs.molecular_weight

    # chamber free volume grows as propellant is consumed: V = V0 + A_burned_volume
    # approximate burned volume by the volume swept: integrate A_b de
    from scipy.integrate import cumulative_trapezoid

    vol_curve = cumulative_trapezoid(curve.burn_area, curve.burn_distance, initial=0.0)

    def burned_volume(e: float) -> float:
        return float(np.interp(e, curve.burn_distance, vol_curve))

    def rhs(t, y):
        e, p = y
        p = max(p, 1.0)
        a_b = area_at(e)
        r = r_fn(p)
        V = inputs.free_volume0 + burned_volume(e)
        # d(pV)/(dt) / (R T) = rho_p A_b r - p A_t/c*
        mdot_gen = rho_p * a_b * r
        mdot_out = p * a_t / inputs.c_star
        dp = (mdot_gen - mdot_out) * Rt / max(V, 1e-9)
        de = r
        return [de, dp]

    t_end_est = e_max / max(r_fn(max(inputs.pressure_floor, 1.0)), 1e-9)
    t_end_est = min(max(t_end_est * 1.5, 1e-3), 1e4)
    t_eval = np.linspace(0.0, t_end_est, max(n_steps or 400, 100))
    sol = solve_ivp(rhs, [0.0, t_end_est], [0.0, inputs.ambient_pressure],
                    t_eval=t_eval, rtol=rtol, atol=atol, method="LSODA", max_step=t_end_est / 50)
    e_arr = np.clip(sol.y[0], 0.0, e_max)
    p_arr = np.maximum(sol.y[1], 0.0)
    a_b_arr = np.array([area_at(float(ee)) for ee in e_arr])
    mgen = rho_p * a_b_arr * np.array([r_fn(max(pp, 1.0)) for pp in p_arr])
    mout = p_arr * a_t / inputs.c_star
    peak = float(np.max(p_arr)) if len(p_arr) else 0.0
    t_peak = float(sol.t[int(np.argmax(p_arr))]) if len(p_arr) else 0.0
    return InternalBallisticsResult(
        model="transient",
        time=list(map(float, sol.t)),
        chamber_pressure=list(map(float, p_arr)),
        mass_generated=list(map(float, mgen)),
        mass_discharged=list(map(float, mout)),
        burn_area=list(map(float, a_b_arr)),
        kn=list(map(float, a_b_arr / a_t if a_t > 0 else np.zeros_like(a_b_arr))),
        peak_pressure=peak,
        time_at_peak=t_peak,
        free_volume_initial=inputs.free_volume0,
        converged=bool(sol.success),
    )


def solve_internal_ballistics(inputs: BallisticsInputs, model: str = "equilibrium",
                              n_steps: int | None = None) -> InternalBallisticsResult:
    if model == "transient":
        return solve_transient(inputs, n_steps=n_steps)
    return solve_equilibrium(inputs, n_steps=n_steps)
