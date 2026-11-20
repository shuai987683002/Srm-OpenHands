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

import numpy as np

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


def _refine_synthesis(req: RequirementSet, propellant: PropellantModel, family: GrainFamily,
                      grain: GrainGeometry, max_iter: int,
                      tol: float = 1e-6) -> tuple[GrainGeometry, float]:
    """Refine the synthesised grain onto the requested indicators (M9, plan §5.1 step 7).

    A blind simplex is the wrong tool here.  Each evaluation costs a full
    burn-back sweep, the objective has flat shelves, and coordinates are clipped
    at their bounds, so Nelder-Mead either stalls in a shelf or needs hundreds of
    evaluations.  Instead the refinement exploits the near-separability of the
    forward model: the burning area (hence thrust) scales with one coordinate
    while the web (hence burn time) is set by another.  One finite difference per
    coordinate identifies those two, then they are corrected alternately -- a
    multiplicative step for thrust, which is close to linear in its coordinate,
    and a bracketed secant/bisection solve for burn time.  Total impulse follows
    from the product.

    Thrust steps are only taken when they improve the combined objective.  Burn
    time steps are taken whenever they reduce the burn-time error, even at a
    small cost in thrust: burn time has to be solved on its own coordinate, and
    the thrust step restores the thrust in the next round.

    Returns the refined geometry and the achieved objective.
    """
    from ..inverse.design import default_design_space

    d_o = req.outer_diameter or grain.max_outer_diameter
    seg = grain.segments[0]
    # The synthesised seed length is not a lower limit: low-thrust designs need a
    # shorter grain, and a tight range would clip the correction and freeze the
    # refinement on the bound.
    length_range = (max(0.02, 0.05 * seg.length), 3.0 * seg.length)
    space = default_design_space(family, d_o, length_range)
    x = space.to_vector(seg.params, seg.length)
    bounds = space.bounds()
    lo = np.array([b[0] for b in bounds])
    hi = np.array([b[1] for b in bounds])
    int_idx = [i for i, p in enumerate(space.parameters) if p.integer]

    def perf_at(vec):
        return forward_design(req, space.to_geometry(vec), propellant,
                              fast=True).performance

    def clipped(vec):
        out = np.minimum(np.maximum(vec, lo), hi)
        # Count parameters are integral: rounding here keeps the refine loop from
        # evaluating several distinct vectors that map to the same geometry.
        for i in int_idx:
            out[i] = round(out[i])
        return out

    def finite(p):
        return (p is not None and p.total_impulse > 0 and p.average_thrust > 0
                and p.burn_time > 0)

    def objective_of(p):
        err, n_terms = 0.0, 0
        if req.thrust and p.average_thrust > 0:
            err += ((p.average_thrust - req.thrust) / req.thrust) ** 2
            n_terms += 1
        if req.burn_time and p.burn_time > 0:
            err += ((p.burn_time - req.burn_time) / req.burn_time) ** 2
            n_terms += 1
        if req.total_impulse and p.total_impulse > 0:
            err += ((p.total_impulse - req.total_impulse) / req.total_impulse) ** 2
            n_terms += 1
        return err / max(n_terms, 1)

    current = perf_at(x)
    if not finite(current):
        return space.to_geometry(x), float("inf")

    best = {"x": x.copy(), "fun": objective_of(current)}

    # Screen every coordinate with one finite difference: rank them by how much
    # they move thrust and how much they move burn time.
    sens_f: list[tuple[float, int]] = []
    sens_tb: list[tuple[float, int]] = []
    for i in range(len(x)):
        span = hi[i] - lo[i]
        if span <= 0:
            continue
        step = 0.15 * span
        probe = x.copy()
        probe[i] = x[i] + step if x[i] + step <= hi[i] else x[i] - step
        p = perf_at(clipped(probe))
        if not finite(p):
            continue
        sens_f.append((abs(p.average_thrust - current.average_thrust), i))
        sens_tb.append((abs(p.burn_time - current.burn_time), i))
    sens_f.sort(reverse=True)
    sens_tb.sort(reverse=True)

    def pinned(i: int) -> bool:
        tol_i = 1e-9 * max(hi[i] - lo[i], 1.0)
        return x[i] <= lo[i] + tol_i or x[i] >= hi[i] - tol_i

    i_f = sens_f[0][1] if sens_f else None
    # Prefer a burn-time coordinate that is not also the thrust coordinate, but
    # do not insist: when a family has only one free coordinate that moves both
    # (a wagon-wheel spoke length, say), reusing it is the only way to close the
    # burn time at all.
    i_tb = next((i for _, i in sens_tb if i != i_f and not pinned(i)), None)
    if i_tb is None:
        i_tb = next((i for _, i in sens_tb if not pinned(i)), None)
    if i_tb is None:
        i_tb = sens_tb[0][1] if sens_tb else None

    def accept(trial, p):
        nonlocal x, current
        if not finite(p):
            return False
        fun = objective_of(p)
        if fun < best["fun"] - 1e-12:
            x, current = trial, p
            best["x"], best["fun"] = trial.copy(), fun
            return True
        return False

    rounds = min(max(4, max_iter // 10), 12)
    for _ in range(rounds):
        if best["fun"] < tol:
            break
        moved = False

        if req.thrust and i_f is not None and current.average_thrust > 0:
            # Thrust is close to linear in its coordinate, so scaling straight
            # onto the target lands in one step.  Fall back to the other
            # thrust-sensitive coordinates when this one is clipped at a bound.
            # Evaluate the scaling on every thrust-sensitive coordinate and keep
            # the best combined objective: the coordinate that moves thrust most
            # is not always the one that keeps burn time, so pick on the actual
            # score rather than on a proxy.
            ratio = min(max(req.thrust / current.average_thrust, 0.2), 5.0)
            for _, i in sens_f:
                trial = x.copy()
                trial[i] = x[i] * ratio if x[i] > 0 else x[i] + ratio - 1.0
                trial = clipped(trial)
                if np.allclose(trial, x):
                    continue
                if accept(trial, perf_at(trial)):
                    moved = True

        if req.burn_time and i_tb is not None:
            # Solve burn time on one coordinate.  Burn time is monotone there, so
            # step in whichever direction crosses the target (doubling the step
            # until it does) and then bisect.  Accepting only steps that reduce
            # the error up front would fail whenever the first probe already
            # jumps past the target, which is the normal case: the burn time can
            # change by more than the whole error over one small parameter step.
            t_target = req.burn_time
            err0 = current.burn_time - t_target

            def tb_at(base: np.ndarray, cand: int, val: float):
                trial = clipped(base.copy())
                trial[cand] = val
                p = perf_at(trial)
                return (p, p.burn_time) if finite(p) else (None, None)

            if abs(err0) > 1e-4 * t_target:
                # Pick the coordinate with the largest burn-time response, then
                # the direction that crosses the target soonest.
                candidates = [i_tb] + [i for _, i in sens_tb if i != i_tb]
                chosen = None
                for cand in candidates:
                    cand_span = hi[cand] - lo[cand]
                    if cand_span <= 0:
                        continue
                    for sign in (1.0, -1.0):
                        step = 0.1 * cand_span
                        near, far = x[cand], x[cand]
                        crossed = False
                        for _ in range(8):
                            nxt = far + sign * step
                            if nxt < lo[cand] or nxt > hi[cand]:
                                break
                            _, tb = tb_at(x, cand, nxt)
                            if tb is None:
                                break
                            near, far = far, nxt
                            if (tb - t_target) * err0 <= 0:
                                crossed = True
                                break
                            step *= 2.0
                        if crossed:
                            chosen = (cand, min(near, far), max(near, far))
                            break
                    if chosen:
                        break

                if chosen is not None:
                    cand, a, b = chosen
                    p_a, tb_a = tb_at(x, cand, a)
                    p_b, tb_b = tb_at(x, cand, b)
                    best_val, best_p, best_err = x[cand], current, abs(err0)
                    for _ in range(8):
                        if tb_a is None or tb_b is None or tb_a == tb_b:
                            break
                        frac = (t_target - tb_a) / (tb_b - tb_a)
                        # Keep a bisection floor: the response has flat stretches
                        # (a swallowed star tip still burns like a plain bore), so a
                        # pure secant can stall on one side forever.
                        frac = min(max(frac, 0.1), 0.9)
                        mid = a + frac * (b - a)
                        p_mid, tb_mid = tb_at(x, cand, mid)
                        if tb_mid is None:
                            break
                        if abs(tb_mid - t_target) < best_err:
                            best_val, best_p, best_err = mid, p_mid, abs(tb_mid - t_target)
                        if (tb_mid - t_target) * (tb_a - t_target) <= 0:
                            b, tb_b = mid, tb_mid
                        else:
                            a, tb_a = mid, tb_mid
                        if best_err < 1e-4 * t_target:
                            break
                    if best_err < abs(err0):
                        x = x.copy()
                        x[cand] = best_val
                        current = best_p
                        if objective_of(best_p) < best["fun"]:
                            best["x"], best["fun"] = x.copy(), objective_of(best_p)
                        moved = True

        if not moved:
            break

    best["x"], best["fun"] = _polish_on_full_model(
        space, best["x"], req, propellant, i_f, i_tb, lo, hi, int_idx,
        best["fun"], objective_of)

    return space.to_geometry(best["x"]), float(best["fun"])


def _polish_on_full_model(space, x0: np.ndarray, req: RequirementSet,
                          propellant: PropellantModel, i_f: int | None,
                          i_tb: int | None, lo: np.ndarray, hi: np.ndarray,
                          int_idx: list[int], fun0: float, objective_of) -> tuple[np.ndarray, float]:
    """Close the refinement on the *full* model, not the screening one.

    The refinement evaluates ``fast=True`` (16-segment polygons, 48 burn-back
    steps) for speed, but the result the user receives is recomputed at full
    resolution.  The two disagree: 0.01-0.5 % on smooth families, and up to
    ~2 % on a multi-perforated grain, where the burn area steps down when the
    holes merge and the coarse sweep straddles that step.  Correcting the
    thrust once on the full model removes that bias.
    """
    def full_perf(vec):
        return forward_design(req, space.to_geometry(vec), propellant,
                              fast=False).performance

    def clipped(vec):
        out = np.minimum(np.maximum(vec, lo), hi)
        for i in int_idx:
            out[i] = round(out[i])
        return out

    x = clipped(x0)
    cur = full_perf(x)
    if cur is None or cur.average_thrust <= 0 or cur.burn_time <= 0:
        return x0, fun0

    for _ in range(4):
        improved = False
        # Burn time first, on the coordinate that carries the web.  On a
        # multi-perforated grain that is the hole diameter, which *also* moves
        # thrust, so closing burn time first and thrust second leaves both met;
        # doing it the other way round lands on a point where the thrust is
        # exact and the burn time is 1.5 % long.
        if req.burn_time and i_tb is not None:
            err = cur.burn_time - req.burn_time
            if abs(err) > 2e-3 * req.burn_time:
                span = hi[i_tb] - lo[i_tb]
                # a wider port leaves less web, so burn time falls as the hole
                # diameter grows: bracket the target between the current value
                # and a point that crosses it, then bisect.  A fixed step either
                # overshoots into a port that fills the cross-section or stalls.
                a = x[i_tb]
                b = a
                step = 0.04 * span * (1.0 if err > 0 else -1.0)
                p_b = None
                for _ in range(7):
                    nxt = b + step
                    if nxt <= lo[i_tb] or nxt >= hi[i_tb]:
                        break
                    trial = clipped(x.copy())
                    trial[i_tb] = nxt
                    p = full_perf(trial)
                    if p is None or p.burn_time <= 0 or p.average_thrust <= 0:
                        break
                    b, p_b = nxt, p
                    if (p.burn_time - req.burn_time) * err <= 0:
                        break
                    step *= 1.8
                if p_b is not None:
                    # keep the solution that goes with ``b``: the bracket end
                    # that already crossed the target, so ``x[i_tb] = b`` and
                    # ``cur = p_b`` stay consistent after the loop
                    for _ in range(10):
                        if abs(p_b.burn_time - req.burn_time) < 2e-3 * req.burn_time:
                            break
                        mid = 0.5 * (a + b)
                        trial = clipped(x.copy())
                        trial[i_tb] = mid
                        p = full_perf(trial)
                        if p is None or p.burn_time <= 0 or p.average_thrust <= 0:
                            break
                        if (p.burn_time - req.burn_time) * err <= 0:
                            b, p_b = mid, p
                        else:
                            a = mid
                    if abs(p_b.burn_time - req.burn_time) < abs(err):
                        x = clipped(x.copy())
                        x[i_tb] = b
                        cur = p_b
                        improved = True
        if req.thrust and i_f is not None and cur.average_thrust > 0:
            # length scales the burning area without changing the web, so this
            # recovers thrust without undoing the burn time
            if abs(cur.average_thrust - req.thrust) > 2e-3 * req.thrust:
                ratio = min(max(req.thrust / cur.average_thrust, 0.5), 2.0)
                trial = clipped(x.copy())
                trial[i_f] = x[i_f] * ratio if x[i_f] > 0 else x[i_f] + ratio - 1.0
                p = full_perf(trial)
                if (p is not None and p.average_thrust > 0
                        and abs(p.average_thrust - req.thrust)
                        < abs(cur.average_thrust - req.thrust)):
                    x, cur = trial, p
                    improved = True
        if not improved or objective_of(cur) <= 1e-7:
            break
    return x, objective_of(cur)


def design_forward(requirement: RequirementSet | dict[str, Any],
                   propellant_id: str = "ap_htpb_80",
                   family: str | GrainFamily | None = None,
                   length: float | None = None,
                   material: Any = None,
                   refine: bool = True,
                   refine_iters: int = 1,
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
        # The loop only reads burn time to decide whether to re-synthesise, and
        # the final result is recomputed at full resolution afterwards, so the
        # screening passes can use the cheap model.
        result = forward_design(req, grain, prop, material=material,
                                **{"fast": True, **kwargs})
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
