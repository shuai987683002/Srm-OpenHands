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
    if family == GrainFamily.CUSTOM_POLYGON:
        # a free-form port is data, not a parametric family: there is no theta to
        # optimise, so an inverse design space is meaningless here
        raise ValueError(
            "custom_polygon has no parametric design space; supply the outline "
            "directly (GrainSegment.polygon) or fit it with fit_outline")
    if family == GrainFamily.CYLINDRICAL:
        # solid grain: no port, so only the length is a design variable and the
        # family must burn from the ends (an INTERNAL mode here yields A_b = 0)
        params = [L]
        burn_mode = BurnMode.END
    elif family in (GrainFamily.TUBE, GrainFamily.TUBE_INHIBITED):
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
            # The hub bore is the web-defining coordinate here, so its range must
            # reach far enough towards the outer radius for short burn times.
            Parameter("inner_diameter", 0.05 * outer_diameter, 0.6 * outer_diameter),
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
            # A seven-perforation port spans three perforation radii from the
            # axis, so the holes still leave a web at a diameter near a quarter
            # of the outer diameter; the cap has to allow that much or the burn
            # time cannot be shortened.
            Parameter("perf_diameter", 0.01 * outer_diameter, 0.28 * outer_diameter),
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
    elif family == GrainFamily.WEDGE:
        params = [
            Parameter("inner_diameter", 0.05 * outer_diameter, 0.6 * outer_diameter),
            Parameter("tip_diameter", 0.2 * outer_diameter, 0.98 * outer_diameter),
            # above 360/n the wedges would merge into an annulus; the builder
            # clamps there, so the bound keeps the optimiser in the realisable range
            Parameter("wedge_angle", 4.0, 40.0),
            Parameter("n_wedges", 3.0, 12.0, integer=True),
            L,
        ]
    elif family == GrainFamily.AIRFOIL:
        params = [
            Parameter("inner_diameter", 0.05 * outer_diameter, 0.6 * outer_diameter),
            Parameter("blade_chord", 0.05 * outer_diameter, 0.9 * r),
            Parameter("thickness_ratio", 0.04, 0.30),
            Parameter("n_blades", 3.0, 12.0, integer=True),
            L,
        ]
    elif family == GrainFamily.EMBEDDED_WIRE:
        params = [
            Parameter("inner_diameter", 0.05 * outer_diameter, 0.7 * outer_diameter),
            # wire diameter changes the *slope* of A_b(e), not its level, so the
            # optimiser can trade it against the bore to hold thrust and still
            # reshape the thrust curve -- keep a wide range for that reason
            Parameter("wire_diameter", 0.2e-3, 4.0e-3),
            Parameter("n_wires", 4.0, 16.0, integer=True),
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
# inverse: identifiability / conditioning of the parameter set
# ---------------------------------------------------------------------------
def _burn_area_on_grid(space: GrainDesignSpace, x: np.ndarray,
                       e_grid: np.ndarray, n_steps: int = 80) -> np.ndarray:
    curve = burnback_geometric(space.to_geometry(x), n_steps=n_steps)
    return np.interp(e_grid, curve.burn_distance, curve.burn_area,
                     left=curve.burn_area[0] if len(curve.burn_area) else 0.0, right=0.0)


def sensitivity_matrix(space: GrainDesignSpace, x: np.ndarray,
                       n_points: int = 40, rel_step: float = 0.02,
                       n_steps: int = 80) -> dict:
    """Finite-difference sensitivity of ``A_b(e)`` to each design parameter.

    Columns are normalised to a *relative* parameter change, so the entries are
    ``dA_b/d(theta_i/theta_i)`` and are directly comparable between a diameter
    and a star-point count.  Integer parameters (``n_points``, ``n_slots``, ...)
    cannot be perturbed continuously, so they are held fixed and reported as
    frozen -- they are structurally unidentifiable from a smooth ``A_b`` fit.
    """
    x = np.asarray(x, dtype=float)
    curve = burnback_geometric(space.to_geometry(x), n_steps=n_steps)
    e_max = float(curve.burn_distance[-1]) if len(curve.burn_distance) else 0.0
    if e_max <= 0:
        raise ValueError("grain has no burn distance; cannot build a sensitivity matrix")
    e_grid = np.linspace(0.0, e_max, n_points)

    base = _burn_area_on_grid(space, x, e_grid, n_steps)
    scale = max(float(np.max(np.abs(base))), 1e-12)

    names: list[str] = []
    frozen: list[str] = []
    columns: list[np.ndarray] = []
    for i, p in enumerate(space.parameters):
        if p.integer:
            frozen.append(p.name)
            continue
        span = p.high - p.low
        h = rel_step * span
        if h <= 0:
            frozen.append(p.name)
            continue
        lo, hi = x.copy(), x.copy()
        lo[i] = max(p.low, x[i] - h)
        hi[i] = min(p.high, x[i] + h)
        if hi[i] <= lo[i]:
            frozen.append(p.name)
            continue
        d = (_burn_area_on_grid(space, hi, e_grid, n_steps)
             - _burn_area_on_grid(space, lo, e_grid, n_steps)) / (hi[i] - lo[i])
        # relative-to-parameter-change and relative-to-area scaling
        columns.append(d * span / scale)
        names.append(p.name)

    if not columns:
        return {"parameters": [], "frozen": frozen, "e_grid": e_grid.tolist(),
                "matrix": [], "condition_number": None, "singular_values": [],
                "per_parameter": [], "notes": "no continuous parameters to perturb"}

    matrix = np.column_stack(columns)
    col_norm = np.linalg.norm(matrix, axis=0)
    col_max = float(np.max(col_norm)) if col_norm.size else 0.0

    # A zero column means the parameter has no effect at this operating point --
    # usually because the family clamps it (e.g. an infeasible star point angle
    # collapses to the symmetric default).  Such parameters are inactive, not
    # merely ill-conditioned, so they are dropped before the SVD; otherwise one
    # dead parameter would report the whole set as singular.
    active = [i for i, cn in enumerate(col_norm) if cn > 1e-12 * max(col_max, 1e-30)]
    inactive_idx = [i for i in range(len(names)) if i not in active]
    inactive = [names[i] for i in inactive_idx]
    if active:
        active_matrix = matrix[:, active]
        _, s, vt = np.linalg.svd(active_matrix, full_matrices=False)
        s_max = float(s[0]) if s.size else 0.0
        s_min = float(s[-1]) if s.size else 0.0
        cond = float("inf") if s_min <= 1e-12 else s_max / s_min
        # Variance inflation factors from the column-normalised design matrix:
        # VIF_i = [(M^T M)^-1]_ii.  VIF = 1 means the parameter is orthogonal to
        # the others (perfectly separable); VIF > 10 is the usual rule-of-thumb
        # for serious collinearity.  This is the standard collinearity measure,
        # unlike an arbitrary null-vector weight which flags one of any two
        # parameters even when they are cleanly separable.
        unit = active_matrix / np.maximum(np.linalg.norm(active_matrix, axis=0), 1e-30)
        try:
            vif = np.diag(np.linalg.inv(unit.T @ unit))
        except np.linalg.LinAlgError:
            vif = np.full(len(active), np.inf)
    else:
        s, cond, vif = np.array([]), float("inf"), np.array([])

    per_param = []
    for i, name in enumerate(names):
        if i in inactive_idx:
            per_param.append({"name": name, "relative_sensitivity": 0.0, "vif": None,
                              "verdict": "inactive: no effect at this operating point"})
            continue
        j = active.index(i)
        own = float(col_norm[i] / col_max) if col_max > 0 else 0.0
        v = float(vif[j]) if j < vif.size else float("inf")
        if own < 0.05:
            verdict = "weak: burn area barely responds"
        elif v > 10.0 and len(active) > 1:
            verdict = "confounded: trades off against another parameter"
        else:
            verdict = "identifiable"
        per_param.append({"name": name, "relative_sensitivity": round(own, 6),
                          "vif": None if math.isinf(v) else round(v, 4),
                          "verdict": verdict})

    identifiable = [p["name"] for p in per_param if p["verdict"] == "identifiable"]
    return {
        "parameters": names,
        "frozen": frozen,
        "inactive": inactive,
        "e_grid": e_grid.tolist(),
        "matrix": matrix.tolist(),
        "singular_values": [float(v) for v in s],
        "condition_number": None if math.isinf(cond) else round(cond, 6),
        "conditioning": "well" if cond < 10 else ("moderate" if cond < 100 else "ill"),
        "per_parameter": per_param,
        "identifiable": identifiable,
        "notes": (f"{len(identifiable)}/{len(names)} continuous parameters identifiable; "
                  f"condition number {cond:.3g}"
                  + (f"; inactive: {', '.join(inactive)}" if inactive else "")),
    }


def identifiability(space: GrainDesignSpace, x: np.ndarray,
                    target_e: list[float] | None = None,
                    target_ab: list[float] | None = None, **kwargs) -> dict:
    """Assess whether a parameter set can be recovered from burning-area data.

    When ``target_e``/``target_ab`` are given, the sensitivity is also evaluated
    against the *measured* window only, so parameters whose effect lies outside
    the available data are flagged as not recoverable.
    """
    info = sensitivity_matrix(space, x, **kwargs)
    info["params"] = space.to_params(np.asarray(x, dtype=float))
    if target_e is None or target_ab is None:
        return info

    te = np.asarray(target_e, dtype=float)
    if te.size < 2:
        info.setdefault("warnings", []).append("fewer than two data points; nothing to fit")
        return info
    span = float(np.max(te) - np.min(te))
    e_max = float(info["e_grid"][-1]) if info["e_grid"] else 0.0
    covered = span / e_max if e_max > 0 else 0.0
    info["data_window"] = {"e_min": float(np.min(te)) if te.size else 0.0,
                           "e_max": float(np.max(te)) if te.size else 0.0,
                           "web_fraction_covered": round(covered, 6)}
    if covered < 0.5:
        info.setdefault("warnings", []).append(
            f"burn-area data covers only {covered:.0%} of the web; "
            "parameters that act late in the burn cannot be recovered")
    return info


# ---------------------------------------------------------------------------
# inverse: existing cross-section outline -> parametric family parameters
# ---------------------------------------------------------------------------
def _angular_signature(poly) -> tuple[int, float, np.ndarray]:
    """Dominant angular harmonic of a port outline.

    Samples the radius ``r(theta)`` about the centroid and takes its FFT; the
    strongest harmonic ``k >= 2`` is the number of lobes (star points, slots,
    fins, spokes).  Area, perimeter and second moment alone cannot tell a
    6-slot port from an 8-slot one, so this count is what makes the fit
    well-posed.  Returns ``(k, relative_amplitude, r_theta)``.
    """
    n = 720
    theta = np.linspace(0.0, 2.0 * math.pi, n, endpoint=False)
    c = poly.centroid
    from shapely.geometry import LineString

    boundaries = ([g.exterior for g in poly.geoms] if poly.geom_type == "MultiPolygon"
                  else [poly.exterior])
    reach = 10.0 * max(poly.bounds[2] - poly.bounds[0],
                       poly.bounds[3] - poly.bounds[1], 1e-9)

    def _points(geom):
        if geom.is_empty:
            return []
        if geom.geom_type == "Point":
            return [geom]
        if geom.geom_type in ("LineString", "LinearRing"):
            # a radial flank of a wedge or slot can be *collinear* with the ray,
            # so the intersection is a segment, not a point: take its far end
            return [geom.interpolate(1.0, normalized=True)]
        if hasattr(geom, "geoms"):
            return [p for g in geom.geoms for p in _points(g)]
        return []

    r = np.empty(n)
    for i, th in enumerate(theta):
        ray = LineString([(c.x, c.y),
                          (c.x + reach * math.cos(th), c.y + reach * math.sin(th))])
        pts = [p for b in boundaries for p in _points(b.intersection(ray))]
        r[i] = (max(math.hypot(p.x - c.x, p.y - c.y) for p in pts) if pts else 0.0)
    spec = np.abs(np.fft.rfft(r - r.mean()))
    if spec.size <= 2:
        return 0, 0.0, r
    k = int(np.argmax(spec[2:]) + 2)
    amp = float(spec[k] / max(r.mean(), 1e-12))
    return k, amp, r


def fit_outline(points: list[tuple[float, float]], family: GrainFamily,
                outer_diameter: float, length: float,
                x0: np.ndarray | None = None, method: str = "cma",
                max_iter: int = 400) -> dict:
    """Fit family parameters to a measured 2-D port outline (plan §5.2 path A).

    The outline is a closed polygon (a digitised drawing, a CAD section, or the
    convex hull of a point cloud) in metres, centred on the grain axis.  The fit
    matches three scale-free descriptors that a designer reads off a section:
    port **area**, port **perimeter** and the **radial second moment**, so it
    works without any point-to-point correspondence between the curves.

    ``length`` is the segment length the section came from.  It does not affect
    the cross-section, so it is held fixed rather than fitted: a single section
    carries no information about axial length.
    """
    from shapely.geometry import Polygon

    if family == GrainFamily.CYLINDRICAL:
        raise ValueError(
            "cylindrical is an end-burning solid grain and has no port outline "
            "to fit; use a ported family or analyse the grain directly")

    pts = np.asarray(points, dtype=float)
    if pts.ndim != 2 or pts.shape[1] != 2 or pts.shape[0] < 3:
        raise ValueError("outline needs >= 3 (x, y) points")
    target = Polygon(pts)
    if not target.is_valid:
        target = target.buffer(0)
    if target.is_empty:
        raise ValueError("outline is empty after repair")

    t_area = float(target.area)
    t_perim = float(target.length)
    c = target.centroid
    parts = target.geoms if target.geom_type == "MultiPolygon" else [target]
    coords = np.vstack([np.asarray(g.exterior.coords) for g in parts])
    t_rm2 = float(np.mean((coords[:, 0] - c.x) ** 2 + (coords[:, 1] - c.y) ** 2))

    space = default_design_space(family, outer_diameter, (length, length))
    x0 = x0 if x0 is not None else np.array([0.5 * (p.low + p.high) for p in space.parameters])

    def descriptors(x):
        poly = port_polygon(space.to_geometry(x).segments[0])
        if poly.is_empty:
            return None
        cc = poly.centroid
        gs = poly.geoms if poly.geom_type == "MultiPolygon" else [poly]
        pc = np.vstack([np.asarray(g.exterior.coords) for g in gs])
        rm2 = float(np.mean((pc[:, 0] - cc.x) ** 2 + (pc[:, 1] - cc.y) ** 2))
        return float(poly.area), float(poly.length), rm2

    # Radius profile of the target, sampled on a fixed angular grid.  Comparing
    # profiles (not just area/perimeter/moment) is what distinguishes a 6-lobe
    # from a 7-lobe port: those three scalars are nearly degenerate in the lobe
    # count, so an integer chosen on them alone lands on a neighbour.
    _, _, r_target = _angular_signature(target)
    r_target = r_target / max(float(r_target.mean()), 1e-12)

    def profile_error(x) -> float:
        poly = port_polygon(space.to_geometry(x).segments[0])
        if poly.is_empty:
            return 1e6
        _, _, r = _angular_signature(poly)
        r = r / max(float(r.mean()), 1e-12)
        return float(np.mean((r - r_target) ** 2))

    def objective(x):
        d = descriptors(x)
        if d is None:
            return 1e6
        a, per, rm2 = d
        scalars = (0.5 * ((a - t_area) / max(t_area, 1e-12)) ** 2
                   + 0.3 * ((per - t_perim) / max(t_perim, 1e-12)) ** 2
                   + 0.2 * ((rm2 - t_rm2) / max(t_rm2, 1e-12)) ** 2)
        return scalars + 2.0 * profile_error(x)

    # Integer counts (n_points, n_slots, ...) make the objective piecewise
    # constant, so a simplex search stalls on them.  The dominant angular
    # harmonic of the target section gives the lobe count directly, so seed the
    # counts from it and only re-optimise the continuous parameters; a short
    # ±1 search covers digitisation noise in the harmonic estimate.
    k_lobes, lobe_amp, _ = _angular_signature(target)
    int_idx = [i for i, p in enumerate(space.parameters) if p.integer]
    candidates: list[np.ndarray] = [x0.copy()]
    if int_idx:
        candidates = []
        seeds = [k_lobes] if k_lobes else []
        for base in seeds:
            for offset in (0, -1, 1):
                v = x0.copy()
                ok = True
                for i in int_idx:
                    p = space.parameters[i]
                    val = base + offset
                    if val < p.low or val > p.high:
                        ok = False
                        break
                    v[i] = val
                if ok:
                    candidates.append(v)
        if not candidates:
            candidates = [x0.copy()]

    best: tuple[float, np.ndarray, int] | None = None
    for start in candidates:
        res = minimize(objective, start, bounds=space.bounds(), method=method,
                       max_iter=max_iter)
        if best is None or res.fun < best[0]:
            best = (float(res.fun), np.asarray(res.x, dtype=float), res.nit)
    fun, x_best, nit = best  # type: ignore[misc]
    geom = space.to_geometry(x_best)
    d = descriptors(x_best) or (0.0, 0.0, 0.0)
    return {
        "geometry": geom,
        "params": space.to_params(x_best),
        "target_descriptors": {"area": t_area, "perimeter": t_perim, "radial_second_moment": t_rm2},
        "fitted_descriptors": {"area": d[0], "perimeter": d[1], "radial_second_moment": d[2]},
        "relative_errors": {
            "area": abs(d[0] - t_area) / max(t_area, 1e-12),
            "perimeter": abs(d[1] - t_perim) / max(t_perim, 1e-12),
            "radial_second_moment": abs(d[2] - t_rm2) / max(t_rm2, 1e-12),
        },
        "objective": fun,
        "success": bool(fun < 1e-4),
        "iterations": nit,
        "notes": ("matched on area/perimeter/radial second moment; the outline's "
                  "absolute position is ignored (centroid is subtracted)"),
    }


# ---------------------------------------------------------------------------
# inverse: ballistic requirements -> grain + throat (closed loop)
# ---------------------------------------------------------------------------
def inverse_design(req: RequirementSet, propellant: PropellantModel,
                   space: GrainDesignSpace, x0: np.ndarray | None = None,
                   method: str = "cma", max_iter: int = 25,
                   weights: dict[str, float] | None = None,
                   ballistics_model: str = "equilibrium",
                   polish: bool = True,
                   polish_max_iter: int = 60) -> DesignResult:
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

    def residual(result) -> float:
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

    def objective(x):
        # The optimiser only compares candidates, so the cheap screening model is
        # enough; whatever it returns is re-evaluated at full resolution below.
        try:
            return residual(forward_design(req, space.to_geometry(x), propellant,
                                           ballistics_model=ballistics_model, fast=True))
        except Exception:
            return 1e6

    def full_objective(x):
        # Candidate *selection* has to run at full resolution.  The screening
        # model smooths over features that only appear at fine sampling -- on a
        # multi-perforated grain the burn-area step at hole merge is straddled,
        # so a polished design that reads 5000 N / 3.00 s on the full model
        # scores worse than an unpolished one on the coarse model and would be
        # discarded.  Only the population search may use the cheap model.
        try:
            return residual(forward_design(req, space.to_geometry(x), propellant,
                                           ballistics_model=ballistics_model, fast=False))
        except Exception:
            return 1e6

    res = minimize(objective, x0, bounds=space.bounds(), method=method, max_iter=max_iter)
    x_best = res.x
    achieved = full_objective(x_best)

    # The global search only has to land in the right basin: its population cost
    # grows steeply with the parameter count, so a coarse pass followed by the
    # same coordinate-wise polish the forward design uses is far cheaper than
    # driving the population to a tight tolerance.  Both candidates are scored
    # with this module's weighted residual, not with the polish's own metric.
    if polish and achieved > 1e-6:
        from ..api.facade import _refine_synthesis

        geom = space.to_geometry(x_best)
        polished, _ = _refine_synthesis(
            req, propellant, geom.segments[0].family, geom, polish_max_iter)
        seg = polished.segments[0]
        x_pol = np.asarray(space.to_vector(seg.params, seg.length))
        achieved_pol = full_objective(x_pol)
        if achieved_pol < achieved:
            x_best, achieved = x_pol, achieved_pol

    result = forward_design(req, space.to_geometry(x_best), propellant,
                            ballistics_model=ballistics_model)
    # The check must describe the reported design, so score the full-resolution
    # result rather than the screening one the optimiser saw.
    achieved = residual(result)
    result.mode = "inverse"
    # Judge convergence by the *achieved* residual, not the optimiser's internal
    # `success` flag (Nelder-Mead sets it False on simplex shrink even when the
    # physical targets are met).
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
