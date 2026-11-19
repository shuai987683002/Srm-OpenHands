"""Verification cases (marker: ``verification``).

Each test compares a computed quantity against an independent analytical,
literature or reference-code value, and prints the comparison so the run doubles
as the evidence behind ``docs/verification/verification_report.md``.

Run just these with::

    PYTHONPATH=src pytest -m verification -q -s
"""

import math

import pytest

from srm_open.burnback.engine import crosscheck
from srm_open.burnback.geometric import burnback_geometric
from srm_open.geometry.analysis import analyze_segment, web_thickness
from srm_open.integrity.fem import solve_axisymmetric
from srm_open.integrity.lame import ElasticMaterial, lame_stresses
from srm_open.models.grain import BurnMode, GrainFamily, GrainGeometry, GrainSegment
from srm_open.models.nozzle import NozzleModel
from srm_open.nozzle.model import exit_mach
from srm_open.thermo import GibbsEquilibrium, default_db
from srm_open.thermo.backend import c_star_from

pytestmark = pytest.mark.verification


def _g(seg):
    return GrainGeometry(segments=[seg])


# ---------------------------------------------------------------------------
# V1 thermochemistry vs NASA CEA / Cantera
# ---------------------------------------------------------------------------
def test_v1_thermo_h2_o2():
    g = GibbsEquilibrium(default_db())
    r = g.solve_adiabatic(101325.0, {"H": 2.0, "O": 1.0}, 0.0)
    ref = 3077.1  # Cantera gri30, HP equilibrium
    print(f"\n[V1] H2+0.5O2: computed T_ad={r.temperature:.1f} K, ref={ref} K "
          f"({100*(r.temperature-ref)/ref:+.2f}%)")
    assert r.temperature == pytest.approx(ref, rel=0.01)


def test_v1_thermo_ch4_o2():
    g = GibbsEquilibrium(default_db())
    mw = (12.011 + 4 * 1.008 + 4 * 15.999) / 1000
    r = g.solve_adiabatic(101325.0, {"C": 1.0, "H": 4.0, "O": 4.0}, -74870 / mw)
    ref = 3052.2
    print(f"[V1] CH4+2O2: computed T_ad={r.temperature:.1f} K, ref={ref} K "
          f"({100*(r.temperature-ref)/ref:+.2f}%)")
    assert r.temperature == pytest.approx(ref, rel=0.01)


def test_v1_thermo_co_o2():
    # 1 mol CO + 0.5 O2: equivalent element basis C1 O3 with CO formation enthalpy
    g = GibbsEquilibrium(default_db())
    mw = (28.010 + 0.5 * 31.998) / 1000  # 1 mol CO + 0.5 mol O2
    r = g.solve_adiabatic(101325.0, {"C": 1.0, "O": 3.0}, -110525 / mw)
    ref = 2762.3  # Cantera gri30, 1 mol CO + 0.5 mol O2, HP equilibrium
    print(f"[V1] CO+0.5O2: computed T_ad={r.temperature:.1f} K, ref={ref} K "
          f"({100*(r.temperature-ref)/ref:+.2f}%)")
    assert r.temperature == pytest.approx(ref, rel=0.02)


# ---------------------------------------------------------------------------
# V2 nozzle isentropic relations
# ---------------------------------------------------------------------------
def test_v2_nozzle_area_ratio_identity():
    gamma = 1.2
    me = exit_mach(12.0, gamma)
    ar = (1 / me) * ((2 / (gamma + 1) * (1 + (gamma - 1) / 2 * me ** 2))
                     ** ((gamma + 1) / (2 * (gamma - 1))))
    print(f"\n[V2] exit Mach={me:.4f}, area-ratio identity={ar:.4f} (target 12.0)")
    assert ar == pytest.approx(12.0, rel=1e-6)


def test_v2_c_star_identity():
    gamma, T, Mw = 1.2, 3000.0, 0.024
    from srm_open.constants import R_UNIVERSAL
    R = R_UNIVERSAL / Mw
    gam = math.sqrt(gamma) * (2 / (gamma + 1)) ** ((gamma + 1) / (2 * (gamma - 1)))
    print(f"\n[V2] c*={c_star_from(gamma, T, Mw, 7e6):.2f} m/s")
    assert c_star_from(gamma, T, Mw, 7e6) == pytest.approx(math.sqrt(R * T) / gam, rel=1e-9)


# ---------------------------------------------------------------------------
# V3 geometry vs closed form
# ---------------------------------------------------------------------------
def test_v3_tube_geometry_closed_form():
    seg = GrainSegment(family=GrainFamily.TUBE, length=0.5, outer_diameter=0.16,
                       params={"inner_diameter": 0.06}, burn_mode=BurnMode.INTERNAL)
    a = analyze_segment(seg)
    exact_area = math.pi / 4 * (0.06 ** 2)
    exact_ab = math.pi * 0.06 * 0.5
    ab0 = burnback_geometric(_g(seg), n_steps=5).burn_area[0]
    print(f"\n[V3] tube port area: {a.port_area:.6f} vs exact {exact_area:.6f} "
          f"({100*(a.port_area/exact_area-1):+.3f}%)")
    print(f"[V3] tube A_b0: {ab0:.6f} vs exact {exact_ab:.6f} "
          f"({100*(ab0/exact_ab-1):+.3f}%)")
    assert a.port_area == pytest.approx(exact_area, rel=3e-3)
    assert ab0 == pytest.approx(exact_ab, rel=3e-3)


def test_v3_web_thickness_closed_form():
    seg = GrainSegment(family=GrainFamily.TUBE, length=0.5, outer_diameter=0.16,
                       params={"inner_diameter": 0.06})
    exact = (0.16 - 0.06) / 2
    print(f"\n[V3] web thickness: {web_thickness(seg):.6f} vs exact {exact:.6f}")
    assert web_thickness(seg) == pytest.approx(exact, rel=1e-9)


# ---------------------------------------------------------------------------
# V4 burn-back: two independent channels agree
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("name,seg", [
    ("tube", GrainSegment(family=GrainFamily.TUBE, length=0.5, outer_diameter=0.16,
                          params={"inner_diameter": 0.06}, burn_mode=BurnMode.INTERNAL)),
    ("star", GrainSegment(family=GrainFamily.STAR, length=0.5, outer_diameter=0.16,
                          params={"n_points": 6, "inner_diameter": 0.04,
                                  "point_diameter": 0.12, "point_half_angle": 30},
                          burn_mode=BurnMode.INTERNAL)),
])
def test_v4_burnback_crosscheck(name, seg):
    res = crosscheck(_g(seg), n_steps=60)
    mx = res["max_relative_deviation"]
    mean = res["mean_relative_deviation"]
    print(f"\n[V4] {name}: geometric vs level-set A_b: max={mx:.4f} mean={mean:.4f}")
    assert mx < 0.20


# ---------------------------------------------------------------------------
# V5 integrity: FEM vs Lame
# ---------------------------------------------------------------------------
def test_v5_fem_vs_lame_hoop_stress():
    a, b, p = 0.03, 0.08, 5e6
    mat = ElasticMaterial(youngs_modulus=5e6, poisson_ratio=0.4995,
                          allowable_stress=3e6)
    fem = solve_axisymmetric(a, b, 0.05, mat, p, nr=240, nz=2)
    # Lamé thick-cylinder inner hoop stress: sigma_t = p (b^2+a^2)/(b^2-a^2)
    hoop_lame = p * (b ** 2 + a ** 2) / (b ** 2 - a ** 2)
    print(f"\n[V5] inner hoop stress: Lame={hoop_lame/1e6:.4f} MPa, "
          f"FEM={fem.max_hoop_stress/1e6:.4f} MPa, "
          f"({100*(fem.max_hoop_stress/hoop_lame-1):+.3f}%)")
    assert fem.max_hoop_stress == pytest.approx(hoop_lame, rel=0.02)


def test_v5_fem_no_volumetric_locking():
    # the B-bar formulation must give the same stress for any Poisson ratio,
    # i.e. no volumetric locking as nu -> 0.5 (propellant-like materials)
    a, b, p = 0.03, 0.08, 5e6
    vals = []
    for nu in (0.30, 0.45, 0.4995):
        r = solve_axisymmetric(a, b, 0.05, ElasticMaterial(5e6, nu, 3e6), p, nr=120, nz=2)
        vals.append(r.max_hoop_stress)
    spread = (max(vals) - min(vals)) / max(vals)
    print(f"\n[V5] hoop stress across nu=0.30..0.4995: {[f'{v/1e6:.4f}' for v in vals]} MPa, "
          f"spread={spread:.2e}")
    assert spread < 1e-3


def test_v5_lame_radial_boundary_conditions():
    a, b, p = 0.03, 0.08, 5e6
    r, sr, st = lame_stresses(a, b, p)
    print(f"\n[V5] sigma_r(a)={sr[0]:.1f} Pa (=-p_i), sigma_r(b)={sr[-1]:.3f} Pa (=0)")
    assert sr[0] == pytest.approx(-p, rel=1e-6)
    assert sr[-1] == pytest.approx(0.0, abs=1e-3)


# ---------------------------------------------------------------------------
# V6 internal ballistics: closed-form equilibrium pressure
# ---------------------------------------------------------------------------
def test_v6_equilibrium_pressure_closed_form():
    from srm_open.ballistics.solver import equilibrium_pressure
    rho, cstar, a, n, ab, at = 1770.0, 1550.0, 5e-5, 0.35, 0.1, 1e-3
    p = equilibrium_pressure(ab, rho, cstar, a, n, at)
    expected = (rho * cstar * a * ab / at) ** (1 / (1 - n))
    print(f"\n[V6] equilibrium p_c: {p/1e6:.4f} MPa vs closed form {expected/1e6:.4f} MPa")
    assert p == pytest.approx(expected, rel=1e-9)


def test_v6_burn_area_over_throat_and_mass_balance():
    from srm_open.api.facade import get_propellant
    from srm_open.ballistics.solver import BallisticsInputs, solve_internal_ballistics
    seg = GrainSegment(family=GrainFamily.TUBE, length=0.5, outer_diameter=0.16,
                       params={"inner_diameter": 0.06}, burn_mode=BurnMode.INTERNAL)
    prop = get_propellant("ap_htpb_80")
    inp = BallisticsInputs(grain=_g(seg), propellant=prop,
                           nozzle=NozzleModel(throat_diameter=0.037, expansion_ratio=8.0),
                           c_star=1550.0, gamma=1.19, chamber_temperature=3100.0,
                           molecular_weight=0.0245, free_volume0=0.001)
    b = solve_internal_ballistics(inp)
    devs = [abs(g - o) / o for g, o in zip(b.mass_generated, b.mass_discharged) if o > 0]
    print(f"\n[V6] mass-balance residual: max={max(devs):.2e}")
    assert max(devs) < 1e-6
