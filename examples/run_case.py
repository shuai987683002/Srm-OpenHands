"""Run an example design case end to end.

Usage::

    PYTHONPATH=src python examples/run_case.py examples/projects/<case>

A case directory holds one of two project files:

* ``project.yaml`` -- a *fixed-geometry* case (requirement + grain +
  propellant).  The stored grain is evaluated as-is, which shows the
  verification checks reporting the margin of a given design.
* ``synthesis.yaml`` -- a *requirement-only* case.  A grain is synthesised from
  the indicators (forward design, FR-2) and then evaluated, which closes the
  loop from top-level targets to performance.

Either way the pipeline is re-run from the stored inputs, so the result is
reproducible from the inputs alone.  Reports, plots and a result JSON are
written into the case directory.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

from srm_open.api.facade import design_forward
from srm_open.io.project import load_project, save_result_json
from srm_open.models.requirements import RequirementSet
from srm_open.orchestration.forward import forward_design
from srm_open.report import write_report


def _run(case_dir: Path):
    project = case_dir / "project.yaml"
    synth = case_dir / "synthesis.yaml"
    if synth.exists():
        spec = yaml.safe_load(synth.read_text()) or {}
        req = RequirementSet(**spec["requirement"])
        result = design_forward(req, spec.get("propellant", "ap_htpb_80"),
                                family=spec.get("family"))
        return result, "synthesis"
    if project.exists():
        data = load_project(project)
        result = forward_design(data["requirement"], data["grain"], data["propellant"],
                                nozzle=data.get("nozzle"))
        return result, "fixed-geometry"
    raise FileNotFoundError(f"no project.yaml or synthesis.yaml in {case_dir}")


def run_case(case_dir: str | Path) -> int:
    case_dir = Path(case_dir)
    try:
        result, kind = _run(case_dir)
    except FileNotFoundError as exc:
        print(exc, file=sys.stderr)
        return 2
    paths = write_report(result, case_dir / "output")
    save_result_json(result, case_dir / "output" / "result.json")

    p = result.performance
    print(f"case              : {case_dir.name}  ({kind})")
    print(f"template family   : {result.grain.segments[0].family.value}")
    print(f"propellant        : {result.propellant.id}")
    print(f"throat diameter   : {result.nozzle.throat_diameter:.5f} m")
    print(f"peak pressure     : {result.ballistics.peak_pressure / 1e6:.4f} MPa")
    print(f"average thrust    : {p.average_thrust:.1f} N")
    print(f"burn time         : {p.burn_time:.4f} s")
    print(f"total impulse     : {p.total_impulse:.1f} N.s")
    print(f"specific impulse  : {p.specific_impulse:.1f} s")
    print(f"propellant mass   : {p.propellant_mass:.3f} kg")
    passed = sum(c["passed"] for c in result.verification.checks)
    print(f"verification      : {passed}/{len(result.verification.checks)} checks passed")
    print(f"report            : {paths['markdown']}")
    if "pdf" in paths:
        print(f"report (pdf)      : {paths['pdf']}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(run_case(sys.argv[1]))
