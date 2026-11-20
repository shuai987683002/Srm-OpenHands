"""Grain synthesis: overall indicators -> a concrete grain geometry (M4 front end
of the forward design chain, FR-2).

The textbook sizing route is

1. propellant mass from the impulse requirement ``m_p = I_t / (I_sp g0)`` (or
   ``F t_b / (I_sp g0)``);
2. a design chamber pressure ``p_d`` (the user limit, derated);
3. mean burning rate ``r_bar = a p_d^n``;
4. mean burning area ``A_bar_b = m_p / (rho_p r_bar t_b)``;
5. family parameters chosen so that the *burn-back* of the generated grain has
   that mean area and that propellant volume.

Step 5 is solved with a small least-squares problem over the family design
space, evaluated with the *same* burn-back engine used everywhere else, so the
synthesised grain is consistent with the forward chain by construction.
"""

from __future__ import annotations

import math

import numpy as np

from ..burnback.geometric import burnback_geometric
from ..constants import G0
from ..inverse.design import default_design_space
from ..inverse.optimize import minimize
from ..models.grain import GrainFamily, GrainGeometry
from ..models.propellant import PropellantModel
from ..models.requirements import RequirementSet
from ..thermo.backend import evaluate as evaluate_thermo


def required_propellant_mass(req: RequirementSet, isp: float | None = None) -> float | None:
    """Propellant mass implied by the impulse/impulse-per-mass requirements."""
    isp = isp if isp is not None else req.specific_impulse
    if req.total_impulse and isp:
        return req.total_impulse / (isp * G0)
    if req.thrust and req.burn_time and isp:
        return req.thrust * req.burn_time / (isp * G0)
    if req.propellant_mass:
        return req.propellant_mass
    return None


def recommend_families(req: RequirementSet) -> list[tuple[GrainFamily, str]]:
    """Rank grain families for the requested thrust profile / geometry."""
    recs: list[tuple[GrainFamily, str]] = []
    ld = req.length_ratio
    if ld is None and req.outer_diameter and req.chamber_length:
        ld = req.chamber_length / req.outer_diameter
    progressive = req.thrust_profile == "progressive"
    regressive = req.thrust_profile == "regressive"
    neutral = req.thrust_profile in (None, "neutral")

    if progressive:
        recs += [(GrainFamily.TUBE, "increasing burn area gives progressive thrust"),
                 (GrainFamily.CONICAL, "conical port gives progressive thrust"),
                 (GrainFamily.SLOTTED, "slotted port progressive with high loading")]
    if regressive:
        recs += [(GrainFamily.STAR, "star regresses to a smaller burn area"),
                 (GrainFamily.WAGON_WHEEL, "wagon wheel regressive")]
    if neutral:
        recs += [(GrainFamily.STAR, "star designed near neutral (progressive-regressive balance)"),
                 (GrainFamily.WAGON_WHEEL, "wagon wheel near neutral"),
                 (GrainFamily.AIRFOIL, "airfoil blades keep A_b near neutral with low stress concentration"),
                 (GrainFamily.WEDGE, "wedge sectors give a broad near-neutral plateau"),
                 (GrainFamily.MULTI_PERFORATED, "multi-perf near neutral")]
    if ld is not None and ld > 8:
        recs.append((GrainFamily.MULTI_PERFORATED, "large L/D suits multi-perforation"))
    if req.throat_diameter is None and ld is not None and ld < 3:
        recs.append((GrainFamily.FINOCYL, "finocyl for high loading in short grains"))
    if progressive and req.max_chamber_pressure is not None:
        recs.append((GrainFamily.EMBEDDED_WIRE,
                     "embedded wires add area as the web regresses (progressive by design)"))
    # de-duplicate, keep order
    seen: set[GrainFamily] = set()
    out: list[tuple[GrainFamily, str]] = []
    for fam, why in recs:
        if fam not in seen:
            seen.add(fam)
            out.append((fam, why))
    return out or [(GrainFamily.TUBE, "default simple internal-burning tube")]


def synthesize_grain(req: RequirementSet, propellant: PropellantModel,
                     family: GrainFamily = GrainFamily.TUBE,
                     length: float | None = None,
                     design_pressure: float | None = None,
                     web_pressure: float | None = None,
                     method: str = "nelder_mead",
                     max_iter: int = 200) -> tuple[GrainGeometry, dict]:
    """Generate a grain that meets the mass, mean-area and burn-time requirements.

    ``design_pressure`` sets the burning rate used for the mean-area target (and
    is the pressure the nozzle is later sized against).  ``web_pressure`` sets
    the burning rate used for the web (burn-time) target; it defaults to
    ``design_pressure``.  Separating the two lets the caller correct the burn
    time from the *realized* mean pressure without disturbing the throat sizing.
    """
    p_d = design_pressure or (0.9 * req.max_chamber_pressure if req.max_chamber_pressure else 7e6)
    p_w = web_pressure or p_d
    thermo = evaluate_thermo(propellant, p_d)
    isp = req.specific_impulse or (thermo.c_star * 0.95 * 1.0 / G0)  # rough fallback
    m_p = required_propellant_mass(req, req.specific_impulse)
    notes: list[str] = []
    if m_p is None:
        # fall back to a geometric estimate so a grain is still produced
        d_o = req.outer_diameter or 0.16
        length = length or req.chamber_length or 5.0 * d_o
        m_p = 0.6 * propellant.density * math.pi / 4.0 * d_o ** 2 * length
        notes.append("propellant mass not determinable from indicators; "
                     "used a 60% loading estimate")

    tb = req.burn_time or 3.0
    r_bar = propellant.burn_rate.a * p_d ** propellant.burn_rate.n
    r_web = propellant.burn_rate.a * p_w ** propellant.burn_rate.n
    a_bar = m_p / (propellant.density * r_bar * tb)

    d_o = req.outer_diameter or 0.16
    if length is None:
        ld = req.length_ratio
        if ld is None and req.chamber_length:
            ld = req.chamber_length / d_o
        length = (ld or 5.0) * d_o

    space = default_design_space(family, d_o, (max(0.05, 0.5 * length), 2.0 * length))
    # initial guess: set the length parameter to the requested length
    x0 = np.array([0.5 * (p.low + p.high) for p in space.parameters])
    for i, p in enumerate(space.parameters):
        if p.name == "length":
            x0[i] = length

    target_vol = m_p / propellant.density
    target_web = r_web * tb  # web that burns out in the requested burn time
    vol_scale = max(target_vol, 1e-9)
    area_scale = max(a_bar, 1e-9)
    web_scale = max(target_web, 1e-9)

    def objective(x):
        geom = space.to_geometry(x)
        # The synthesis objective only needs volume, mean area and web; a coarse
        # sweep is ample and several times cheaper inside the optimiser loop.
        curve = burnback_geometric(geom, n_steps=24, quad_segs=16)
        vol = curve.propellant_volume
        a_mean = float(np.mean(curve.burn_area)) if len(curve.burn_area) else 0.0
        web = curve.web_thickness
        if vol <= 0 or a_mean <= 0 or web <= 0:
            return 1e6
        e_vol = (vol - target_vol) / vol_scale
        e_area = (a_mean - a_bar) / area_scale
        e_web = (web - target_web) / web_scale
        return float(e_vol ** 2 + e_area ** 2 + e_web ** 2)

    res = minimize(objective, x0, bounds=space.bounds(), method=method, max_iter=max_iter)
    geom = space.to_geometry(res.x)
    curve = burnback_geometric(geom, n_steps=120)
    info = {
        "design_pressure": p_d,
        "c_star": thermo.c_star,
        "assumed_specific_impulse": isp,
        "target_propellant_mass": m_p,
        "target_mean_burn_area": a_bar,
        "achieved_propellant_mass": curve.propellant_volume * propellant.density,
        "achieved_mean_burn_area": float(np.mean(curve.burn_area)),
        "objective": float(res.fun),
        "length": length,
        "notes": notes,
    }
    return geom, info
