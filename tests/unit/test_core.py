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
