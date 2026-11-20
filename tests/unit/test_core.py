"""Unit tests: geometry families, burn-back, ballistics, nozzle, thermo."""

import math

import numpy as np
import pytest

from srm_open.burnback.engine import crosscheck
from srm_open.burnback.geometric import burn_area_at, burnback_geometric
from srm_open.geometry.analysis import analyze_segment, port_polygon, web_thickness
from srm_open.models.grain import BurnMode, GrainFamily, GrainSegment
from srm_open.models.nozzle import NozzleModel
from srm_open.nozzle.model import Nozzle, exit_mach
from srm_open.thermo import GibbsEquilibrium, default_db
from srm_open.thermo.backend import c_star_from


# --------------------------------------------------------------------------
# geometry: analytical checks
# --------------------------------------------------------------------------
def test_tube_port_area_is_analytic():
    seg = GrainSegment(family=GrainFamily.TUBE, length=0.5, outer_diameter=0.16,
                       params={"inner_diameter": 0.06})
    a = analyze_segment(seg)
    exact = math.pi / 4.0 * 0.06 ** 2
    assert a.port_area == pytest.approx(exact, rel=2e-3)
    assert a.port_volume == pytest.approx(exact * 0.5, rel=2e-3)


def test_tube_web_thickness_is_analytic():
    seg = GrainSegment(family=GrainFamily.TUBE, length=0.5, outer_diameter=0.16,
                       params={"inner_diameter": 0.06})
    assert web_thickness(seg) == pytest.approx((0.16 - 0.06) / 2.0, rel=1e-6)


def test_web_thickness_is_the_gap_at_the_farthest_port_point():
    """The web is set by the *farthest* port point, not the nearest.

    Vectorising this loop once turned ``min(r_out - r)`` into
    ``r_out - max(r)``, which is the same thing, but a plain transcription slip
    to ``r_out - min(r)`` reports the deepest web instead of the shallowest and
    inflates the burn time.  Cross-check against the brute-force definition.
    """
    import math

    seg = GrainSegment(family=GrainFamily.MULTI_PERFORATED, length=0.5,
                       outer_diameter=0.18,
                       params={"perf_diameter": 0.03, "n_perforations": 7})
    port = port_polygon(seg)
    parts = [port] if port.geom_type == "Polygon" else list(port.geoms)
    r_out = seg.outer_diameter / 2.0
    brute = min(r_out - math.hypot(x, y)
                for g in parts for x, y in g.exterior.coords)
    assert web_thickness(seg) == pytest.approx(brute, rel=1e-9)


def test_tube_burning_area_is_analytic():
    # internal burning tube: A_b = pi * D_i * L, constant until the port opens out
    seg = GrainSegment(family=GrainFamily.TUBE, length=0.5, outer_diameter=0.16,
                       params={"inner_diameter": 0.06}, burn_mode=BurnMode.INTERNAL)
    a0 = burn_area_at(_grain(seg), 0.0)
    assert a0 == pytest.approx(math.pi * 0.06 * 0.5, rel=3e-3)


def test_star_port_area_is_positive_and_bounded():
    seg = GrainSegment(family=GrainFamily.STAR, length=0.5, outer_diameter=0.16,
                       params={"n_points": 6, "inner_diameter": 0.04,
                               "point_diameter": 0.12, "point_half_angle": 30})
    port = port_polygon(seg)
    assert 0 < port.area < math.pi / 4 * 0.16 ** 2
    assert port.is_valid


@pytest.mark.parametrize("family,params", [
    (GrainFamily.STAR, {"n_points": 6, "inner_diameter": 0.04,
                        "point_diameter": 0.12, "point_half_angle": 30}),
    (GrainFamily.WAGON_WHEEL, {"n_spokes": 6, "inner_diameter": 0.04,
                               "spoke_width": 0.02, "spoke_length": 0.05}),
    (GrainFamily.SLOTTED, {"inner_diameter": 0.05, "n_slots": 6,
                           "slot_width": 0.012, "slot_depth": 0.04}),
    (GrainFamily.FINOCYL, {"inner_diameter": 0.05, "n_fins": 6,
                           "fin_width": 0.012, "fin_length": 0.05}),
    (GrainFamily.MULTI_PERFORATED, {"n_perforations": 7, "perf_diameter": 0.012}),
    (GrainFamily.SPHERICAL, {"cavity_diameter": 0.10}),
    (GrainFamily.CONICAL, {"base_diameter": 0.12, "apex_diameter": 0.0}),
    (GrainFamily.WEDGE, {"n_wedges": 6, "inner_diameter": 0.04,
                         "tip_diameter": 0.14, "wedge_angle": 22}),
    (GrainFamily.AIRFOIL, {"n_blades": 6, "inner_diameter": 0.04,
                           "blade_chord": 0.06, "thickness_ratio": 0.15}),
    (GrainFamily.EMBEDDED_WIRE, {"inner_diameter": 0.06, "n_wires": 8,
                                 "wire_diameter": 0.001}),
])
def test_all_families_produce_valid_port(family, params):
    seg = GrainSegment(family=family, length=0.4, outer_diameter=0.18, params=params)
    port = port_polygon(seg)
    assert not port.is_empty
    assert port.is_valid
    assert port.area > 0
    assert port.area < math.pi / 4 * 0.18 ** 2


def test_slotted_finocyl_wagon_wheel_are_distinct():
    """Guards against the three multi-cavity families degenerating into one
    shape: they must differ geometrically at equal nominal parameters."""
    common = {"inner_diameter": 0.05}
    specs = {
        "wagon_wheel": (GrainFamily.WAGON_WHEEL,
                        {"n_spokes": 6, "spoke_width": 0.02, "spoke_length": 0.05}),
        "slotted": (GrainFamily.SLOTTED,
                    {"n_slots": 6, "slot_width": 0.012, "slot_depth": 0.04}),
        "finocyl": (GrainFamily.FINOCYL,
                    {"n_fins": 6, "fin_width": 0.012, "fin_length": 0.05}),
    }
    ports = {}
    for name, (family, params) in specs.items():
        seg = GrainSegment(family=family, length=0.4, outer_diameter=0.18,
                           params={**common, **params})
        port = port_polygon(seg)
        assert port.is_valid
        assert port.geom_type == "Polygon", f"{name} port is disconnected"
        ports[name] = port
    names = list(ports)
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a, b = ports[names[i]], ports[names[j]]
            assert a.symmetric_difference(b).area > 1e-4, \
                f"{names[i]} and {names[j]} are the same geometry"


def test_airfoil_keeps_every_blade():
    """The blade root sits *inside* the hub bore.

    A root that only touched the hub circle met it at a single point, so the
    union came out disconnected and the repair path kept one blade plus the hub.
    """
    def exposed(n_blades):
        seg = GrainSegment(family=GrainFamily.AIRFOIL, length=0.4,
                           outer_diameter=0.18,
                           params={"n_blades": n_blades, "inner_diameter": 0.04,
                                   "blade_chord": 0.06, "thickness_ratio": 0.15})
        port = port_polygon(seg)
        assert port.is_valid
        return port.area - math.pi / 4 * 0.04 ** 2

    # each blade contributes the same exposed area, so the total must scale with
    # the count; a dropped blade shows up as a ratio far from 6/5
    assert exposed(6) / exposed(5) == pytest.approx(6 / 5, rel=0.05)
    assert exposed(6) > 0.0


def test_wedge_sector_area_matches_the_analytic_sector():
    """One wedge is a sector of the hub/tip annulus: A = n * (a/2) * (r_t**2 - r_h**2)."""
    n, d_hub, d_tip, angle = 6, 0.04, 0.14, 22.0
    seg = GrainSegment(family=GrainFamily.WEDGE, length=0.4, outer_diameter=0.18,
                       params={"n_wedges": n, "inner_diameter": d_hub,
                               "tip_diameter": d_tip, "wedge_angle": angle})
    port = port_polygon(seg)
    a = math.radians(angle)
    expected = math.pi / 4 * d_hub ** 2 + n * 0.5 * a * ((d_tip / 2) ** 2 - (d_hub / 2) ** 2)
    assert port.area == pytest.approx(expected, rel=0.02)


def test_wedge_angle_is_clamped_to_the_pitch():
    """Beyond 360/n the cavities would merge into an annulus, so the builder
    clamps: two different over-wide angles must give the same port."""
    def port_at(angle):
        seg = GrainSegment(family=GrainFamily.WEDGE, length=0.4, outer_diameter=0.18,
                           params={"n_wedges": 4, "inner_diameter": 0.04,
                                   "tip_diameter": 0.14, "wedge_angle": angle})
        return port_polygon(seg)

    assert port_at(120.0).area == pytest.approx(port_at(150.0).area, rel=1e-6)


def test_embedded_wire_bore_is_the_plain_bore():
    """The wires are not voids, so the port is exactly the bore circle."""
    seg = GrainSegment(family=GrainFamily.EMBEDDED_WIRE, length=0.4, outer_diameter=0.18,
                       params={"inner_diameter": 0.06, "n_wires": 8,
                               "wire_diameter": 0.001})
    port = port_polygon(seg)
    assert port.area == pytest.approx(math.pi / 4 * 0.06 ** 2, rel=0.01)


def test_wedge_airfoil_star_differ_at_equal_lobe_count():
    """Same six lobes, three different cavity shapes: the burn-area *shape* is
    what distinguishes them, so the ports must not coincide."""
    od = 0.18
    specs = {
        "wedge": (GrainFamily.WEDGE, {"n_wedges": 6, "inner_diameter": 0.04,
                                      "tip_diameter": 0.14, "wedge_angle": 22}),
        "airfoil": (GrainFamily.AIRFOIL, {"n_blades": 6, "inner_diameter": 0.04,
                                          "blade_chord": 0.06, "thickness_ratio": 0.15}),
        "star": (GrainFamily.STAR, {"n_points": 6, "inner_diameter": 0.04,
                                    "point_diameter": 0.14, "point_half_angle": 25}),
    }
    ports = {name: port_polygon(GrainSegment(family=fam, length=0.4, outer_diameter=od,
                                             params=par))
             for name, (fam, par) in specs.items()}
    names = list(ports)
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a, b = ports[names[i]], ports[names[j]]
            assert a.symmetric_difference(b).area > 1e-4, f"{names[i]} == {names[j]}"


def test_multi_perforated_holes_are_separated_by_a_web():
    """The holes must not be tangent.

    A ring radius of exactly 2*rp made the centre hole tangent to all six ring
    holes and each ring hole tangent to its neighbours: zero web, so the port
    fused into a blob after 0.5 mm of burn and the burning perimeter *fell*.
    """
    seg = GrainSegment(family=GrainFamily.MULTI_PERFORATED, length=0.4,
                       outer_diameter=0.18,
                       params={"n_perforations": 7, "perf_diameter": 0.012})
    port = port_polygon(seg)
    assert port.geom_type == "MultiPolygon"
    assert len(port.geoms) == 7
    # the perimeter must grow monotonically through the early burn, i.e. the
    # holes are still separate at a burn depth well below the web
    prev = port.length
    for e in (0.0002, 0.0004, 0.0006):
        cur = port.buffer(e).length
        assert cur > prev, f"perimeter fell at e={e}: {prev:.6f} -> {cur:.6f}"
        prev = cur
    # and the web really is the requested fraction of the hole diameter
    rp = 0.006
    centres = sorted((g.centroid.x, g.centroid.y) for g in port.geoms)
    r_ring = min(math.hypot(x, y) for x, y in centres if math.hypot(x, y) > 1e-9)
    assert r_ring - 2 * rp == pytest.approx(0.15 * 0.012, rel=0.05)


def test_multi_perforated_nineteen_holes_use_two_rings():
    """The 19-hole layout is centre + 6 + 12, all separated by a web."""
    seg = GrainSegment(family=GrainFamily.MULTI_PERFORATED, length=0.5,
                       outer_diameter=0.30,
                       params={"n_perforations": 19, "perf_diameter": 0.012})
    port = port_polygon(seg)
    assert port.geom_type == "MultiPolygon"
    assert len(port.geoms) == 19
    radii = sorted({round(math.hypot(g.centroid.x, g.centroid.y), 6)
                    for g in port.geoms})
    assert len(radii) == 3, radii          # centre, inner ring, outer ring
    assert radii[0] == pytest.approx(0.0, abs=1e-9)
    assert radii[2] - radii[1] == pytest.approx(radii[1], rel=0.02)


def test_multi_perforated_port_stays_disconnected():
    """A 7-perf port must keep all seven voids; collapsing to one component is
    the failure that halved the burning area."""
    seg = GrainSegment(family=GrainFamily.MULTI_PERFORATED, length=0.4,
                       outer_diameter=0.18,
                       params={"n_perforations": 7, "perf_diameter": 0.01})
    port = port_polygon(seg)
    assert port.area == pytest.approx(7 * math.pi * 0.005 ** 2, rel=0.02)


def _grain(seg):
    from srm_open.models.grain import GrainGeometry
    return GrainGeometry(segments=[seg])


# --------------------------------------------------------------------------
# burn-back
# --------------------------------------------------------------------------
def test_tube_burn_area_is_monotonic_increasing(tube_grain):
    curve = burnback_geometric(tube_grain, n_steps=100)
    a = curve.burn_area
    # tube grows monotonically until the sliver
    assert a[0] <= a[len(a) // 2] <= a[-2]
    assert a[-1] == pytest.approx(0.0, abs=1e-9)


def test_burnback_methods_crosscheck_tube(tube_grain):
    res = crosscheck(tube_grain, n_steps=60)
    assert res["max_relative_deviation"] < 0.15, res["max_relative_deviation"]


def test_propellant_volume_matches_annulus(tube_grain):
    curve = burnback_geometric(tube_grain, n_steps=10)
    exact = math.pi / 4 * (0.16 ** 2 - 0.06 ** 2) * 0.5
    assert curve.propellant_volume == pytest.approx(exact, rel=3e-3)


def test_end_burner_area_is_constant():
    seg = GrainSegment(family=GrainFamily.CYLINDRICAL, length=0.3,
                       outer_diameter=0.15, params={}, burn_mode=BurnMode.END)
    curve = burnback_geometric(_grain(seg), n_steps=50)
    a = curve.burn_area[:-1]
    assert np.allclose(a, a[0], rtol=1e-9)


# --------------------------------------------------------------------------
# nozzle
# --------------------------------------------------------------------------
def test_exit_mach_matches_isentropic_relation():
    gamma, eps = 1.2, 10.0
    me = exit_mach(eps, gamma)
    # area ratio identity
    ar = (1 / me) * ((2 / (gamma + 1) * (1 + (gamma - 1) / 2 * me ** 2))
                     ** ((gamma + 1) / (2 * (gamma - 1))))
    assert ar == pytest.approx(eps, rel=1e-6)


def test_thrust_coefficient_positive_and_reasonable():
    nz = Nozzle(NozzleModel(throat_diameter=0.04, expansion_ratio=8.0))
    cf = nz.thrust_coefficient(7e6, 1.2, 101325.0)
    assert 1.0 < cf < 2.2


# --------------------------------------------------------------------------
# thermo: benchmark adiabatic flame temperatures (vs NASA/Cantera)
# --------------------------------------------------------------------------
def test_h2_o2_flame_temperature():
    g = GibbsEquilibrium(default_db())
    r = g.solve_adiabatic(101325.0, {"H": 2.0, "O": 1.0}, 0.0)
    # NASA CEA / Cantera give 3077 K
    assert r.temperature == pytest.approx(3077.0, abs=15.0)
    assert r.gamma == pytest.approx(1.214, abs=0.01)


def test_ch4_o2_flame_temperature():
    g = GibbsEquilibrium(default_db())
    mw = (12.011 + 4 * 1.008 + 4 * 15.999) / 1000
    r = g.solve_adiabatic(101325.0, {"C": 1.0, "H": 4.0, "O": 4.0}, -74870 / mw)
    assert r.temperature == pytest.approx(3052.0, abs=15.0)


def test_c_star_formula_consistency():
    # c* = sqrt(R T) / Gamma  with  Gamma = sqrt(g) (2/(g+1))^((g+1)/(2(g-1)))
    gamma, T, Mw = 1.2, 3000.0, 0.024
    from srm_open.constants import R_UNIVERSAL
    R = R_UNIVERSAL / Mw
    gam = math.sqrt(gamma) * (2 / (gamma + 1)) ** ((gamma + 1) / (2 * (gamma - 1)))
    assert c_star_from(gamma, T, Mw, 7e6) == pytest.approx(math.sqrt(R * T) / gam, rel=1e-9)


# --------------------------------------------------------------------------
# ballistics: equilibrium pressure closed form
# --------------------------------------------------------------------------
def test_equilibrium_pressure_closed_form():
    from srm_open.ballistics.solver import equilibrium_pressure
    p = equilibrium_pressure(a_b=0.1, rho_p=1770.0, c_star=1550.0, a=5e-5, n=0.35,
                             a_t=1e-3)
    expected = (1770.0 * 1550.0 * 5e-5 * 0.1 / 1e-3) ** (1 / (1 - 0.35))
    assert p == pytest.approx(expected, rel=1e-9)


# --------------------------------------------------------------------------
# inverse: sensitivity / identifiability
# --------------------------------------------------------------------------
def _space(family, od=0.16, length=(0.5, 1.0)):
    from srm_open.inverse.design import default_design_space
    return default_design_space(family, od, length)


def test_tube_parameters_are_independent():
    # A tube's inner diameter and length enter A_b = pi*D_i*L multiplicatively,
    # so both are recoverable from the curve -> VIF ~ 1, no confounding.
    from srm_open.inverse.design import sensitivity_matrix
    sp = _space(GrainFamily.TUBE)
    info = sensitivity_matrix(sp, sp.to_vector({"inner_diameter": 0.06}, 0.8))
    assert info["conditioning"] in ("well", "moderate")
    assert all(p["vif"] is not None and p["vif"] < 10 for p in info["per_parameter"])
    assert "inner_diameter" in info["identifiable"]


def test_integer_counts_are_frozen_not_fitted():
    from srm_open.inverse.design import sensitivity_matrix
    sp = _space(GrainFamily.STAR)
    x = sp.to_vector({"inner_diameter": 0.05, "point_diameter": 0.13,
                      "point_half_angle": 25, "n_points": 6}, 0.8)
    info = sensitivity_matrix(sp, x)
    assert "n_points" in info["frozen"]
    assert "n_points" not in info["parameters"]


def test_degenerate_parameter_reported_inactive():
    # a star point angle that the family clamps has zero influence; it must be
    # excluded from the SVD so it does not masquerade as ill-conditioning
    from srm_open.inverse.design import sensitivity_matrix
    sp = _space(GrainFamily.STAR)
    x = sp.to_vector({"inner_diameter": 0.05, "point_diameter": 0.13,
                      "point_half_angle": 25, "n_points": 6}, 0.8)
    info = sensitivity_matrix(sp, x)
    verdicts = {p["name"]: p["verdict"] for p in info["per_parameter"]}
    if "point_half_angle" in verdicts:
        assert verdicts["point_half_angle"].startswith("inactive")


def test_identifiability_flags_short_data_window():
    from srm_open.inverse.design import identifiability
    sp = _space(GrainFamily.TUBE)
    x = sp.to_vector({"inner_diameter": 0.06}, 0.8)
    # data only over the first 20% of the web
    info = identifiability(sp, x, target_e=[0.0, 0.001], target_ab=[0.1, 0.1])
    assert info["data_window"]["web_fraction_covered"] < 0.5
    assert info["warnings"]


# --------------------------------------------------------------------------
# inverse: outline fitting (path A)
# --------------------------------------------------------------------------
@pytest.mark.parametrize("family,params,count_key", [
    # 10 deg is inside the feasible band for these radii; a larger half-angle is
    # clamped by the family (see test_star_angle_beyond_feasible_is_clamped)
    (GrainFamily.STAR, {"inner_diameter": 0.05, "point_diameter": 0.13,
                        "point_half_angle": 10, "n_points": 6}, "n_points"),
    (GrainFamily.SLOTTED, {"inner_diameter": 0.05, "n_slots": 6,
                           "slot_width": 0.012, "slot_depth": 0.04}, "n_slots"),
    (GrainFamily.FINOCYL, {"inner_diameter": 0.05, "n_fins": 6,
                           "fin_width": 0.012, "fin_length": 0.05}, "n_fins"),
    (GrainFamily.WAGON_WHEEL, {"n_spokes": 6, "inner_diameter": 0.04,
                               "spoke_width": 0.02, "spoke_length": 0.05}, "n_spokes"),
])
def test_outline_fit_recovers_family_parameters(family, params, count_key):
    from srm_open.inverse.design import fit_outline
    sp = _space(family, length=(0.6, 0.6))
    poly = port_polygon(sp.to_geometry(sp.to_vector(params, 0.6)).segments[0])
    res = fit_outline(list(poly.exterior.coords), family, 0.16, 0.6,
                      method="nelder_mead", max_iter=400)
    assert res["objective"] < 1e-8
    assert int(res["params"][count_key]) == params[count_key]
    for key, value in params.items():
        if key == count_key:
            continue
        assert res["params"][key] == pytest.approx(value, rel=2e-2)


def test_star_angle_beyond_feasible_is_clamped():
    # documents the physics behind the 10 deg choice above: past ~17 deg (for
    # these radii and 6 points) the requested half-angle cannot be realised, so
    # the outline stops changing and the parameter becomes unidentifiable
    from srm_open.geometry.families import build_star
    a = build_star({"n_points": 6, "inner_diameter": 0.05, "point_diameter": 0.13,
                    "point_half_angle": 20}, 0.08)
    b = build_star({"n_points": 6, "inner_diameter": 0.05, "point_diameter": 0.13,
                    "point_half_angle": 30}, 0.08)
    assert a.area == pytest.approx(b.area, rel=1e-9)


def test_outline_fit_ignores_translation():
    from srm_open.inverse.design import fit_outline
    sp = _space(GrainFamily.TUBE, length=(0.5, 0.5))
    poly = port_polygon(sp.to_geometry(sp.to_vector({"inner_diameter": 0.06}, 0.5)).segments[0])
    shifted = [(x + 0.3, y - 0.2) for x, y in poly.exterior.coords]
    res = fit_outline(shifted, GrainFamily.TUBE, 0.16, 0.5,
                      method="nelder_mead", max_iter=400)
    assert res["params"]["inner_diameter"] == pytest.approx(0.06, rel=2e-2)


def test_outline_fit_rejects_degenerate_input():
    from srm_open.inverse.design import fit_outline
    with pytest.raises(ValueError):
        fit_outline([(0.0, 0.0), (1.0, 1.0)], GrainFamily.TUBE, 0.16, 0.5)


# --------------------------------------------------------------------------
# studies: sweep and Monte-Carlo (FR-12)
# --------------------------------------------------------------------------
def _study_inputs():
    from srm_open.io.templates import TemplateLibrary
    from srm_open.models.requirements import RequirementSet
    from srm_open.propellant.database import PropellantDB
    grain = TemplateLibrary.load_default().get("tube_simple")
    prop = PropellantDB.load_default().get("ap_htpb_80")
    req = RequirementSet(name="study", thrust=6000.0, burn_time=4.0,
                         max_chamber_pressure=9e6)
    return grain, prop, req


def test_sweep_is_monotonic_in_pressure_limit():
    from srm_open.orchestration.study import sweep
    grain, prop, req = _study_inputs()
    study = sweep(req, grain, prop, "max_chamber_pressure", [6e6, 7e6, 8e6])
    assert study.failed == 0
    peaks = study.metric("peak_pressure")
    assert peaks == sorted(peaks)
    assert len(study.metric("average_thrust")) == 3


def test_sweep_rejects_unknown_parameter():
    from srm_open.orchestration.study import sweep
    grain, prop, req = _study_inputs()
    with pytest.raises(ValueError):
        sweep(req, grain, prop, "not_a_field", [1.0])


def test_monte_carlo_is_reproducible_for_a_seed():
    from srm_open.orchestration.study import monte_carlo
    grain, prop, req = _study_inputs()
    vary = {"density": {"sigma": 15.0}, "burn_rate.a": {"sigma": 2e-7}}
    a = monte_carlo(req, grain, prop, vary, n_samples=6, seed=7)
    b = monte_carlo(req, grain, prop, vary, n_samples=6, seed=7)
    c = monte_carlo(req, grain, prop, vary, n_samples=6, seed=8)
    assert a.to_dict()["samples"] == b.to_dict()["samples"]
    assert a.to_dict()["samples"] != c.to_dict()["samples"]


def test_monte_carlo_spread_follows_input_spread():
    from srm_open.orchestration.study import monte_carlo
    grain, prop, req = _study_inputs()
    tight = monte_carlo(req, grain, prop, {"density": {"sigma": 1.0}}, n_samples=12, seed=3)
    loose = monte_carlo(req, grain, prop, {"density": {"sigma": 40.0}}, n_samples=12, seed=3)
    assert loose.summary()["total_impulse"]["std"] > tight.summary()["total_impulse"]["std"]


# --------------------------------------------------------------------------
# coverage: every declared grain family is constructible and invertible
# --------------------------------------------------------------------------
PARAMETRIC_FAMILIES = [f for f in GrainFamily if f is not GrainFamily.CUSTOM_POLYGON]


def test_every_grain_family_has_a_builder_and_a_design_space():
    from srm_open.geometry.families import FAMILY_PARAMS
    from srm_open.inverse.design import default_design_space
    for family in GrainFamily:
        assert family in FAMILY_PARAMS, f"{family.value} has no geometry builder"
    for family in PARAMETRIC_FAMILIES:
        space = default_design_space(family, 0.16, (0.5, 1.0))
        assert space.parameters, f"{family.value} has no inverse design space"


def test_custom_polygon_has_no_design_space():
    from srm_open.inverse.design import default_design_space
    with pytest.raises(ValueError, match="custom_polygon"):
        default_design_space(GrainFamily.CUSTOM_POLYGON, 0.16, (0.5, 1.0))


def test_every_family_builds_a_valid_geometry_with_burn_area():
    from srm_open.inverse.design import default_design_space
    for family in PARAMETRIC_FAMILIES:
        space = default_design_space(family, 0.16, (0.5, 1.0))
        params = {p.name: 0.5 * (p.low + p.high) for p in space.parameters}
        seg = space.to_geometry(space.to_vector(params, 0.8)).segments[0]
        a = analyze_segment(seg)
        assert a.web_thickness > 0, f"{family.value} has no web"
        if seg.burn_mode is BurnMode.END:
            # solid end-burner: no port by construction, burns on the face
            assert a.port_area == 0.0
            assert burn_area_at(_grain(seg), 0.0) > 0
        else:
            poly = port_polygon(seg)
            assert poly.is_valid and poly.area > 0, f"{family.value} port invalid"
            assert burn_area_at(_grain(seg), 0.0) > 0


def test_outline_fit_rejects_solid_end_burner():
    from srm_open.inverse.design import fit_outline
    with pytest.raises(ValueError, match="end-burning"):
        fit_outline([(0.03, 0), (0, -0.03), (-0.03, 0), (0, 0.03)],
                    GrainFamily.CYLINDRICAL, 0.16, 0.5)


# --------------------------------------------------------------------------
# FR-1: requirement consistency checking and completion
# --------------------------------------------------------------------------
def test_requirements_fill_total_impulse_from_thrust_and_time():
    from srm_open.models.requirements import RequirementSet
    from srm_open.orchestration.requirements import check_and_complete

    req = RequirementSet(name="r", thrust=5000.0, burn_time=4.0)
    rep = check_and_complete(req)
    assert req.total_impulse == pytest.approx(20000.0)
    assert rep.filled["total_impulse"] == pytest.approx(20000.0)
    assert not rep.conflicts


def test_requirements_fill_propellant_mass_from_impulse_and_isp():
    from srm_open.constants import G0
    from srm_open.models.requirements import RequirementSet
    from srm_open.orchestration.requirements import check_and_complete

    req = RequirementSet(name="r", total_impulse=20000.0, specific_impulse=200.0)
    check_and_complete(req)
    assert req.propellant_mass == pytest.approx(20000.0 / (200.0 * G0), rel=1e-9)


def test_requirements_flag_impulse_conflict():
    from srm_open.models.requirements import RequirementSet
    from srm_open.orchestration.requirements import check_and_complete

    # F*t_b = 12000, but total_impulse claims 30000: a real contradiction
    req = RequirementSet(name="bad", thrust=4000.0, burn_time=3.0,
                         total_impulse=30000.0)
    rep = check_and_complete(req)
    assert rep.conflicts
    assert "inconsistent" in rep.conflicts[0]
    assert req.total_impulse == 30000.0  # never silently overwritten


def test_requirements_note_under_determined_input():
    from srm_open.models.requirements import RequirementSet
    from srm_open.orchestration.requirements import check_and_complete

    rep = check_and_complete(RequirementSet(name="thin", thrust=1000.0))
    assert rep.degrees_of_freedom == 1
    assert any("under-determined" in n for n in rep.notes)


def test_requirements_note_thrust_vs_average_thrust():
    from srm_open.models.requirements import RequirementSet
    from srm_open.orchestration.requirements import check_and_complete

    rep = check_and_complete(RequirementSet(name="f", thrust=9000.0,
                                            average_thrust=6000.0, burn_time=2.0))
    assert any("average thrust" in n for n in rep.notes)


def test_requirements_reject_unknown_thrust_profile():
    """A typo must not silently fall through to the "no preference" branch."""
    import pytest
    from pydantic import ValidationError

    from srm_open.models.requirements import RequirementSet

    for ok in ("neutral", "progressive", "regressive", None):
        assert RequirementSet(thrust=1000.0, thrust_profile=ok).thrust_profile == ok
    with pytest.raises(ValidationError, match="thrust_profile"):
        RequirementSet(thrust=1000.0, thrust_profile="nutral")


# --------------------------------------------------------------------------
# FR-4: propellant database is data-driven and user-extensible
# --------------------------------------------------------------------------
def test_propellant_db_lists_shipped_entries_with_source():
    from srm_open.propellant.database import PropellantDB

    db = PropellantDB.load_default()
    ids = db.ids()
    assert len(ids) >= 4
    for p in db.all():
        assert p.source, f"{p.id} must declare where its numbers come from"
        assert p.density > 0


def test_propellant_db_round_trip_through_yaml(tmp_path):
    from srm_open.propellant.database import PropellantDB

    db = PropellantDB.load_default()
    out = db.save(tmp_path / "props.yaml")
    again = PropellantDB.from_file(out)
    assert set(again.ids()) == set(db.ids())
    assert again.get(db.ids()[0]).density == pytest.approx(db.get(db.ids()[0]).density)


def test_propellant_db_user_can_add_and_remove(tmp_path):
    from srm_open.propellant.database import PropellantDB

    db = PropellantDB.load_default()
    base = db.get(db.ids()[0])
    custom = base.model_copy(update={"id": "my_prop", "name": "User propellant"})
    db.add(custom)
    assert "my_prop" in db
    assert db.get("my_prop").name == "User propellant"

    db.save(tmp_path / "props.yaml")
    assert "my_prop" in PropellantDB.from_file(tmp_path / "props.yaml")

    db.remove("my_prop")
    assert "my_prop" not in db


def test_propellant_db_unknown_id_raises():
    from srm_open.propellant.database import PropellantDB

    with pytest.raises(KeyError):
        PropellantDB.load_default().get("does_not_exist")


def test_burn_rate_law_is_pluggable():
    from srm_open.models.propellant import BurnRateLaw, BurnRateModel
    from srm_open.propellant.burn_rate import build_burn_rate, register_burn_rate_law

    def flat_builder(model: BurnRateModel):
        return lambda p: model.a  # r independent of p

    register_burn_rate_law(BurnRateLaw.SAINT_ROBERT, flat_builder)
    try:
        model = BurnRateModel(law=BurnRateLaw.SAINT_ROBERT, a=0.005, n=0.5)
        assert build_burn_rate(model)(1e6) == pytest.approx(0.005)
    finally:  # restore the shipped implementation for other tests
        import importlib

        import srm_open.propellant.burn_rate as br

        importlib.reload(br)
