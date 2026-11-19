"""Integration tests: end-to-end forward / inverse design, IO, report."""

import math

import pytest

from srm_open.api import design_forward, design_from_template, list_templates
from srm_open.api.facade import get_propellant
from srm_open.burnback.geometric import burnback_geometric
from srm_open.inverse import (
    analyze_existing_grain,
    default_design_space,
    fit_burn_area,
    inverse_burn_rate,
    inverse_design,
)
from srm_open.io import TemplateLibrary, load_project, save_project
from srm_open.models.grain import GrainFamily
from srm_open.models.requirements import RequirementSet
from srm_open.report import write_report


def test_forward_from_template_runs_end_to_end():
    r = design_from_template("tube_simple", "ap_htpb_80",
                             {"thrust": 4000, "burn_time": 3.0,
                              "max_chamber_pressure": 9e6})
    assert r.performance is not None
    assert r.performance.total_impulse > 0
    assert r.ballistics.peak_pressure <= 9e6 * 1.001
    assert r.nozzle.throat_diameter > 0
    assert r.grain_body.propellant_volume > 0


def test_forward_design_synthesises_grain():
    r = design_forward({"thrust": 8000, "burn_time": 4.0, "max_chamber_pressure": 9e6,
                        "outer_diameter": 0.16, "length_ratio": 5,
                        "thrust_profile": "neutral"}, "ap_htpb_80")
    assert r.grain is not None
    assert r.grain.segments[0].family == GrainFamily.STAR
    # M9 refinement (plan 5.1 step 7) should land thrust and burn time on target
    assert abs(r.performance.average_thrust / 8000 - 1) < 0.02
    assert abs(r.performance.burn_time / 4.0 - 1) < 0.02


def test_forward_design_refinement_improves_target_match():
    req = {"thrust": 8000, "burn_time": 4.0, "max_chamber_pressure": 9e6,
           "outer_diameter": 0.16, "length_ratio": 5, "thrust_profile": "neutral"}
    coarse = design_forward(req, "ap_htpb_80", refine=False)
    refined = design_forward(req, "ap_htpb_80", refine=True)

    def err(r):
        return (abs(r.performance.average_thrust / 8000 - 1)
                + abs(r.performance.burn_time / 4.0 - 1))

    assert err(refined) < err(coarse)


def test_cli_design_command_parses():
    from srm_open.api.cli import build_parser

    args = build_parser().parse_args([
        "design", "--thrust", "8000", "--burn-time", "4",
        "--max-pressure", "9e6", "--outer-diameter", "0.16",
        "--length-ratio", "5", "--thrust-profile", "neutral", "--no-refine"])
    assert args.command == "design"
    assert args.func.__name__ == "cmd_design"
    assert args.thrust == 8000 and args.burn_time == 4.0
    assert args.length_ratio == 5.0 and args.no_refine is True


@pytest.mark.parametrize("tid", list_templates())
def test_all_templates_run_forward(tid):
    r = design_from_template(tid, "ap_htpb_80",
                             {"thrust": 5000, "burn_time": 3.0})
    assert r.performance is not None
    assert r.performance.total_impulse > 0


def test_reverse_engineering_recovers_tube_dimensions(tube_grain):
    info = analyze_existing_grain(tube_grain, propellant_density=1770.0)
    assert info["web_thickness"] == pytest.approx(0.05, rel=1e-6)
    assert info["propellant_mass"] == pytest.approx(
        math.pi / 4 * (0.16 ** 2 - 0.06 ** 2) * 0.5 * 1770.0, rel=5e-3)


def test_fit_burn_area_recovers_tube_geometry(tube_grain):
    curve = burnback_geometric(tube_grain, n_steps=40)
    space = default_design_space(GrainFamily.TUBE, 0.16, (0.2, 0.8))
    fit = fit_burn_area(space, list(curve.burn_distance), list(curve.burn_area),
                        method="nelder_mead", max_iter=200)
    assert fit["rms_relative_error"] < 0.02
    assert fit["params"]["inner_diameter"] == pytest.approx(0.06, abs=2e-3)
    assert fit["params"]["length"] == pytest.approx(0.5, abs=2e-3)


def test_inverse_burn_rate_recovers_coefficients():
    ps = [3e6, 5e6, 7e6, 9e6, 11e6]
    rs = [5e-5 * p ** 0.35 for p in ps]
    br = inverse_burn_rate(ps, rs)
    assert br["a"] == pytest.approx(5e-5, rel=1e-6)
    assert br["n"] == pytest.approx(0.35, rel=1e-6)


def test_inverse_design_closes_the_loop():
    prop = get_propellant("ap_htpb_80")
    req = RequirementSet(name="inv", thrust=6000.0, burn_time=4.0,
                         max_chamber_pressure=9e6, outer_diameter=0.16)
    space = default_design_space(GrainFamily.STAR, 0.16, (0.2, 0.8))
    r = inverse_design(req, prop, space, method="nelder_mead", max_iter=80)
    assert r.performance is not None
    assert abs(r.performance.average_thrust / 6000 - 1) < 0.05
    assert abs(r.performance.burn_time / 4.0 - 1) < 0.05


def test_project_round_trip(tmp_path):
    grain = TemplateLibrary.load_default().geometry("star_6_point")
    prop = get_propellant("ap_htpb_80")
    req = RequirementSet(name="rt", thrust=5000.0, burn_time=3.0)
    path = save_project(tmp_path / "p.yaml", req, grain, prop)
    loaded = load_project(path)
    assert loaded["grain"].segments[0].family == GrainFamily.STAR
    assert loaded["propellant"].id == "ap_htpb_80"
    assert loaded["requirement"].thrust == 5000.0


def test_report_and_plots_written(tmp_path):
    r = design_from_template("star_6_point", "ap_htpb_80",
                             {"thrust": 5000, "burn_time": 3.0})
    paths = write_report(r, tmp_path / "out")
    assert paths["markdown"].exists()
    assert "Srm-Open design report" in paths["markdown"].read_text()
    assert paths["html"].exists()
