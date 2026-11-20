"""Integration tests: end-to-end forward / inverse design, IO, report."""

import math

import pytest

from srm_open.api import design_forward, design_from_template, list_templates
from srm_open.api.facade import get_propellant, make_grain
from srm_open.burnback.geometric import burnback_geometric
from srm_open.geometry.analysis import analyze_segment, burning_area, port_polygon
from srm_open.inverse import (
    analyze_existing_grain,
    default_design_space,
    fit_burn_area,
    inverse_burn_rate,
    inverse_design,
)
from srm_open.io import TemplateLibrary, load_project, save_project
from srm_open.models.grain import GrainFamily, GrainSegment
from srm_open.models.requirements import RequirementSet
from srm_open.orchestration.forward import forward_design
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


def test_report_pdf_is_a_valid_pdf(tmp_path):
    r = design_from_template("star_6_point", "ap_htpb_80",
                             {"thrust": 5000, "burn_time": 3.0})
    paths = write_report(r, tmp_path / "out")
    pdf = paths.get("pdf")
    if pdf is None:  # matplotlib is an optional extra
        pytest.skip("matplotlib not installed")
    data = pdf.read_bytes()
    assert data.startswith(b"%PDF-")
    assert data.rstrip().endswith(b"%%EOF")
    # the report body must actually be in there, not just an empty shell
    assert len(data) > 20_000


def test_report_pdf_matches_markdown_content(tmp_path):
    r = design_from_template("tube_simple", "ap_htpb_80",
                             {"thrust": 4000, "burn_time": 3.0})
    paths = write_report(r, tmp_path / "out")
    if "pdf" not in paths:
        pytest.skip("matplotlib not installed")
    from srm_open.report import _md_blocks

    md = paths["markdown"].read_text()
    headings = [p for k, p in _md_blocks(md, tmp_path / "out") if k == "h2"]
    assert len(headings) >= 8
    assert any("Internal ballistics" in h for h in headings)


def test_report_pdf_can_be_disabled(tmp_path):
    r = design_from_template("tube_simple", "ap_htpb_80",
                             {"thrust": 4000, "burn_time": 3.0})
    paths = write_report(r, tmp_path / "out", include_pdf=False)
    assert "pdf" not in paths
    assert not (tmp_path / "out" / "report.pdf").exists()


def test_provenance_records_dependencies_and_input_hash():
    r = design_from_template("tube_simple", "ap_htpb_80",
                             {"thrust": 4000, "burn_time": 3.0})
    prov = r.provenance
    assert prov.software_version
    assert prov.dependency_versions.get("numpy") not in (None, "absent")
    assert len(prov.input_hash) == 64

    same = design_from_template("tube_simple", "ap_htpb_80",
                                {"thrust": 4000, "burn_time": 3.0})
    assert same.provenance.input_hash == prov.input_hash

    other = design_from_template("tube_simple", "ap_htpb_80",
                                 {"thrust": 4500, "burn_time": 3.0})
    assert other.provenance.input_hash != prov.input_hash


def test_report_contains_reproducibility_block(tmp_path):
    r = design_from_template("tube_simple", "ap_htpb_80",
                             {"thrust": 4000, "burn_time": 3.0})
    text = write_report(r, tmp_path / "out", include_pdf=False)["markdown"].read_text()
    assert "dependency versions:" in text
    assert "input hash (sha256):" in text


def test_cli_forward_writes_report_and_json(tmp_path):
    """Regression: a duplicate ``_emit`` definition shadowed the design printer,
    so ``srm forward --report`` crashed with a TypeError on the Namespace."""
    from srm_open.api.cli import main
    rep = tmp_path / "rep"
    js = tmp_path / "r.json"
    assert main(["forward", "--template", "star_6_point",
                 "--propellant", "ap_htpb_80", "--thrust", "8000",
                 "--burn-time", "4", "--report", str(rep),
                 "--json", str(js)]) == 0
    assert (rep / "report.md").exists()
    assert js.exists()


def test_cli_inverse_works_without_a_template():
    """Regression: ``--template`` used to be mandatory for ``srm inverse``, so a
    designer who only knew the envelope had to pick an unrelated template."""
    from srm_open.api.cli import main
    assert main(["inverse", "--family", "star", "--outer-diameter", "0.15",
                 "--thrust", "5000", "--burn-time", "3", "--max-iter", "25"]) == 0


def test_cli_inverse_rejects_missing_envelope():
    """Without a template the family *and* the outer diameter are both needed."""
    from srm_open.api.cli import main
    assert main(["inverse", "--family", "star", "--thrust", "5000",
                 "--burn-time", "3"]) == 2


# --------------------------------------------------------------------------
# new CLI commands: sensitivity / sweep / monte-carlo / fit-outline / pointcloud
# --------------------------------------------------------------------------
def test_cli_sensitivity_command(tmp_path):
    import json

    from srm_open.api.cli import main
    out = tmp_path / "sens.json"
    assert main(["sensitivity", "--family", "tube", "--outer-diameter", "0.16",
                 "--json", str(out)]) == 0
    data = json.loads(out.read_text())
    assert data["condition_number"] is not None
    assert "inner_diameter" in data["identifiable"]


def test_cli_sweep_command(tmp_path):
    import json

    from srm_open.api.cli import main
    out = tmp_path / "sweep.json"
    assert main(["sweep", "--template", "tube_simple", "--thrust", "6000",
                 "--burn-time", "4", "--parameter", "max_chamber_pressure",
                 "--values", "6e6,8e6", "--json", str(out)]) == 0
    data = json.loads(out.read_text())
    assert len(data["samples"]) == 2
    assert data["summary"]["average_thrust"]["n"] == 2


def test_cli_monte_carlo_command(tmp_path):
    import json

    from srm_open.api.cli import main
    out = tmp_path / "mc.json"
    assert main(["monte-carlo", "--template", "tube_simple", "--thrust", "6000",
                 "--burn-time", "4", "--vary", "density:10", "--samples", "5",
                 "--seed", "1", "--json", str(out)]) == 0
    data = json.loads(out.read_text())
    assert data["seed"] == 1
    assert data["summary"]["total_impulse"]["n"] == 5


def test_cli_fit_outline_roundtrip(tmp_path):
    import json

    from srm_open.api.cli import main
    from srm_open.geometry.analysis import port_polygon
    sp = default_design_space(GrainFamily.TUBE, 0.16, (0.5, 0.5))
    poly = port_polygon(sp.to_geometry(sp.to_vector({"inner_diameter": 0.06}, 0.5)).segments[0])
    pts = tmp_path / "outline.csv"
    pts.write_text("\n".join(f"{x},{y}" for x, y in poly.exterior.coords))
    out = tmp_path / "fit.json"
    assert main(["fit-outline", "--points", str(pts), "--family", "tube",
                 "--outer-diameter", "0.16", "--length", "0.5",
                 "--json", str(out)]) == 0
    data = json.loads(out.read_text())
    assert data["success"] is True
    assert data["params"]["inner_diameter"] == pytest.approx(0.06, rel=2e-2)


def test_stl_export_is_watertight_ascii(tmp_path):
    from srm_open.geometry.sketch import grain_to_stl

    grain = TemplateLibrary.load_default().geometry("star_6_point")
    out = grain_to_stl(grain, tmp_path / "g.stl", segments_per_circle=64)
    text = out.read_text()
    assert text.startswith("solid")
    assert text.count("facet normal") > 500
    assert text.count("facet normal") == text.count("endfacet")


def test_step_export_writes_iso10303(tmp_path):
    from srm_open.geometry.sketch import grain_to_step

    pytest.importorskip("cadquery")
    grain = TemplateLibrary.load_default().geometry("tube_simple")
    out = grain_to_step(grain, tmp_path / "g.step")
    text = out.read_text()
    assert text.startswith("ISO-10303-21;")
    assert "MANIFOLD_SOLID_BREP" in text


def test_cli_pointcloud_export(tmp_path):
    from srm_open.api.cli import main
    out = tmp_path / "grain.xyz"
    assert main(["export-cad", "--template", "star_6_point",
                 "--format", "pointcloud", "--out", str(out)]) == 0
    lines = [ln for ln in out.read_text().splitlines() if not ln.startswith("#")]
    assert len(lines) > 100
    x, y, z = (float(v) for v in lines[0].split())
    assert math.hypot(x, y) <= 0.081


# --------------------------------------------------------------------------
# forward-design robustness: the failure modes that the refinement hid
# --------------------------------------------------------------------------
def test_forward_design_hits_targets_for_every_internal_burning_family():
    """Every family except the end burner must land on 5000 N / 3 s.

    The refinement is the only part of the chain that closes on thrust, so a
    family that silently fails to refine (a dead search direction, a parameter
    that does not move) shows up here as a large thrust error rather than as an
    exception.
    """
    families = [
        GrainFamily.TUBE, GrainFamily.TUBE_INHIBITED, GrainFamily.STAR,
        GrainFamily.WAGON_WHEEL, GrainFamily.SLOTTED, GrainFamily.FINOCYL,
        GrainFamily.SPHERICAL, GrainFamily.CONICAL, GrainFamily.MULTI_PERFORATED,
        GrainFamily.WEDGE, GrainFamily.AIRFOIL, GrainFamily.EMBEDDED_WIRE,
    ]
    for family in families:
        r = design_forward({"thrust": 5000, "burn_time": 3.0,
                            "outer_diameter": 0.16}, "ap_htpb_80", family=family)
        assert abs(r.performance.average_thrust / 5000 - 1) < 0.02, family
        assert abs(r.performance.burn_time / 3.0 - 1) < 0.02, family


def test_refinement_closes_on_the_full_model_not_the_screening_one():
    """The delivered result is recomputed at full resolution.

    The refinement screens with ``fast=True`` (16-segment polygons, 48 burn-back
    steps) and used to stop there.  On a multi-perforated grain the coarse sweep
    straddles the burn-area step where the holes merge, so the screening model
    read 5000 N / 3.00 s while the full model read 4897 N / 3.043 s: a 2 % thrust
    miss on a result the checks had already passed.  The polish on the full model
    has to keep every family inside 0.5 % on both indicators.
    """
    for family in (GrainFamily.MULTI_PERFORATED, GrainFamily.TUBE,
                   GrainFamily.WEDGE, GrainFamily.AIRFOIL):
        r = design_forward({"thrust": 5000, "burn_time": 3.0,
                            "outer_diameter": 0.16}, "ap_htpb_80", family=family)
        assert abs(r.performance.average_thrust / 5000 - 1) < 0.005, family
        assert abs(r.performance.burn_time / 3.0 - 1) < 0.005, family


def test_forward_design_accepts_an_explicit_fast_flag():
    """Passing ``fast`` must not collide with the internal screening call.

    ``design_forward`` runs its re-synthesis loop on the cheap model, so it
    passed ``fast=True`` positionally *and* forwarded ``**kwargs``.  A caller
    that also set ``fast`` then hit "got multiple values for keyword argument
    'fast'" instead of getting a design.
    """
    for flag in (True, False):
        r = design_forward({"thrust": 5000, "burn_time": 3.0,
                            "outer_diameter": 0.16}, family="tube", fast=flag)
        assert r.performance is not None and r.performance.average_thrust > 0


def test_inverse_design_selects_candidates_on_the_full_model():
    """The inverse loop must not judge polished candidates with the coarse model.

    The forward polish closes on the full model, but the inverse loop used to
    compare candidates with ``fast=True``.  A multi-perforated design that the
    full model read as 5000 N / 2.99 s therefore scored *worse* than an
    unpolished one on the coarse model and was thrown away, so the reported
    result stayed 2.06 % low even though the correct point had been found.
    """
    from srm_open.inverse.design import default_design_space, inverse_design

    req = RequirementSet(name="inv", thrust=5000.0, burn_time=3.0,
                         outer_diameter=0.16)
    prop = get_propellant("ap_htpb_80")
    space = default_design_space(GrainFamily.MULTI_PERFORATED, 0.16, (0.2, 0.9))
    r = inverse_design(req, prop, space, max_iter=8, polish_max_iter=40)
    assert abs(r.performance.average_thrust / 5000.0 - 1) < 0.005
    assert abs(r.performance.burn_time / 3.0 - 1) < 0.005
    conv = [c for c in r.verification.checks if c["name"] == "inverse_convergence"]
    assert conv and conv[0]["passed"]


def test_multi_perforation_keeps_every_hole():
    """A 7-perf port is seven disjoint voids.

    Reducing the MultiPolygon to its largest component (the old repair path)
    kept two of the seven holes and halved the burning area, which showed up as
    a wrong thrust level rather than as an error.
    """
    grain = make_grain("multi_perforated", 0.5, 0.18,
                       {"n_perforations": 7, "perf_diameter": 0.01})
    port = port_polygon(grain.segments[0])
    area = math.pi * 0.005 ** 2
    assert port.area == pytest.approx(7 * area, rel=0.02)
    assert analyze_segment(grain.segments[0]).web_thickness > 0.0


def test_embedded_wire_burning_area_grows_with_the_web():
    """The wires are inert and stay put, so each one exposes its side as the
    propellant recedes: A_b must increase, and by n*2*pi*r_w*e."""
    grain = make_grain("embedded_wire", 0.5, 0.18,
                       {"inner_diameter": 0.06, "n_wires": 8, "wire_diameter": 0.001})
    seg = grain.segments[0]
    web = analyze_segment(seg).web_thickness
    a0 = burning_area(seg, 0.0)
    a1 = burning_area(seg, 0.5 * web)
    wire_term = 8 * 2.0 * math.pi * 0.0005 * (0.5 * web)
    # the bore growth also adds area, so the wire term is a *lower* bound on the
    # increment and the exact difference must exceed it
    assert a1 - a0 > wire_term
    # and with the wires removed the increment must be smaller by exactly that
    bare = GrainSegment(family=GrainFamily.TUBE, length=seg.length,
                        outer_diameter=seg.outer_diameter,
                        params={"inner_diameter": 0.06})
    assert (a1 - a0) - (burning_area(bare, 0.5 * web) - burning_area(bare, 0.0)) == \
        pytest.approx(wire_term, rel=1e-6)


def test_wagon_wheel_leaves_web_between_bulbs_and_case():
    """The bulbs used to be clamped onto the case wall, giving zero web: the
    port filled the cross-section and the burn-back collapsed."""
    grain = make_grain("wagon_wheel", 0.4, 0.18,
                       {"n_spokes": 6, "inner_diameter": 0.05,
                        "spoke_width": 0.02, "spoke_length": 0.05})
    port = port_polygon(grain.segments[0])
    r_max = max(math.hypot(x, y) for x, y in port.exterior.coords)
    web = 0.18 / 2 - r_max
    assert web > 0.005, f"web {web:.4f} m is too thin to burn back"


def test_forward_design_rejects_a_port_that_fills_the_case():
    """A zero burning surface must fail with a readable message instead of
    producing a zero throat diameter and a pydantic error far from the cause."""
    grain = make_grain("tube", 0.4, 0.16, {"inner_diameter": 0.16})
    req = RequirementSet(name="degenerate", thrust=5000.0, burn_time=3.0,
                         outer_diameter=0.16)
    with pytest.raises(ValueError, match="no burning surface"):
        forward_design(req, grain, get_propellant("ap_htpb_80"))


def test_inverse_design_converges_from_a_documented_space():
    """The inverse loop must reach the targets, not merely run: the residual is
    reported as a verification check and is what a user reads."""
    prop = get_propellant("ap_htpb_80")
    req = RequirementSet(name="inv", thrust=5000.0, burn_time=3.0,
                         outer_diameter=0.16)
    space = default_design_space(GrainFamily.TUBE, 0.16, (0.1, 1.2))
    r = inverse_design(req, prop, space)
    conv = next(c for c in r.verification.checks if c["name"] == "inverse_convergence")
    assert conv["passed"], conv
    assert abs(r.performance.average_thrust / 5000 - 1) < 0.02
