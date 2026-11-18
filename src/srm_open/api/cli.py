"""Command-line interface: ``srm <command>``.

Commands
--------
  srm forward     --template T --propellant P --thrust F --burn-time t [--report DIR]
  srm inverse     --template T --propellant P --thrust F --burn-time t [--report DIR]
  srm thermo      --propellant P [--pressure p]
  srm templates / srm propellants
  srm reverse     --project FILE            (reverse-engineer an existing grain)
  srm export-cad  --template T --format stl --out FILE
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..geometry.sketch import geometry_backend_available, grain_to_step, grain_to_stl
from ..inverse.design import analyze_existing_grain, default_design_space, inverse_design
from ..io.project import load_project, save_project, save_result_json
from ..io.templates import TemplateLibrary
from ..models.design_result import DesignResult
from ..models.requirements import RequirementSet
from ..orchestration.forward import forward_design
from ..propellant.database import PropellantDB
from ..report import write_report
from .facade import evaluate_propellant_thermo, get_propellant


def _req_from_args(a) -> RequirementSet:
    return RequirementSet(
        name=a.name, thrust=a.thrust, burn_time=a.burn_time,
        total_impulse=a.total_impulse, specific_impulse=a.isp,
        outer_diameter=a.outer_diameter, max_chamber_pressure=a.max_pressure,
        expansion_ratio=a.expansion_ratio, throat_diameter=a.throat_diameter,
        ambient_pressure=a.ambient_pressure,
    )


def _emit(result: DesignResult, args) -> int:
    perf = result.performance
    print("== Srm-Open %s design ==" % result.mode)
    if result.nozzle:
        print("throat diameter   : %.5f m" % result.nozzle.throat_diameter)
    if result.ballistics:
        print("peak pressure     : %.4f MPa" % (result.ballistics.peak_pressure / 1e6))
    if perf:
        print("total impulse     : %.1f N.s" % perf.total_impulse)
        print("specific impulse  : %.1f s" % perf.specific_impulse)
        print("average thrust    : %.1f N" % perf.average_thrust)
        print("burn time         : %.4f s" % perf.burn_time)
        print("propellant mass   : %.3f kg" % perf.propellant_mass)
    if result.integrity:
        print("max von Mises     : %.3f MPa (SF=%.2f)" %
              (result.integrity.max_von_mises / 1e6, result.integrity.safety_factor))
    failed = [c["name"] for c in result.verification.checks if not c["passed"]]
    print("verification      : %d/%d passed%s" %
          (len(result.verification.checks) - len(failed), len(result.verification.checks),
           ("  FAILED: " + ", ".join(failed)) if failed else ""))
    if args.report:
        paths = write_report(result, args.report)
        print("report            : %s" % paths["markdown"])
    if args.json:
        save_result_json(result, args.json)
        print("json              : %s" % args.json)
    return 0


def cmd_forward(args) -> int:
    grain = TemplateLibrary.load_default().geometry(args.template)
    prop = get_propellant(args.propellant)
    result = forward_design(_req_from_args(args), grain, prop)
    return _emit(result, args)


def cmd_inverse(args) -> int:
    grain_t = TemplateLibrary.load_default().geometry(args.template)
    family = grain_t.segments[0].family
    prop = get_propellant(args.propellant)
    space = default_design_space(family, grain_t.segments[0].outer_diameter,
                                 (0.1, max(0.3, grain_t.segments[0].length * 2)))
    result = inverse_design(_req_from_args(args), prop, space,
                            method=args.optimizer, max_iter=args.max_iter)
    print("recovered params  : %s" % result.grain.segments[0].params)
    return _emit(result, args)


def cmd_thermo(args) -> int:
    info = evaluate_propellant_thermo(args.propellant, args.pressure)
    print(json.dumps(info, indent=2, default=str))
    return 0


def cmd_templates(args) -> int:
    for k, v in TemplateLibrary.load_default().names().items():
        print(f"  {k:<24} {v}")
    return 0


def cmd_propellants(args) -> int:
    for k, v in PropellantDB.load_default().names().items():
        print(f"  {k:<24} {v}")
    return 0


def cmd_reverse(args) -> int:
    proj = load_project(args.project)
    info = analyze_existing_grain(proj["grain"], proj["propellant"].density)
    info.pop("burn_area_curve", None)
    info.pop("burn_distance_curve", None)
    print(json.dumps(info, indent=2, default=str))
    return 0


def cmd_export_cad(args) -> int:
    grain = TemplateLibrary.load_default().geometry(args.template)
    out = Path(args.out)
    if args.format == "stl":
        p = grain_to_stl(grain, out)
    elif args.format == "step":
        if not geometry_backend_available():
            print("STEP export needs cadquery (pip install cadquery)", file=sys.stderr)
            return 2
        p = grain_to_step(grain, out)
    else:
        print("unknown format %s" % args.format, file=sys.stderr)
        return 2
    print("wrote %s" % p)
    return 0


def cmd_save_project(args) -> int:
    grain = TemplateLibrary.load_default().geometry(args.template)
    prop = get_propellant(args.propellant)
    save_project(args.out, _req_from_args(args), grain, prop)
    print("wrote %s" % args.out)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="srm", description="Open-source solid rocket motor design")
    sub = p.add_subparsers(dest="command", required=True)

    def add_common(sp):
        sp.add_argument("--name", default="cli")
        sp.add_argument("--template", required=True)
        sp.add_argument("--propellant", default="ap_htpb_80")
        sp.add_argument("--thrust", type=float, default=None)
        sp.add_argument("--burn-time", type=float, default=None)
        sp.add_argument("--total-impulse", type=float, default=None)
        sp.add_argument("--isp", type=float, default=None)
        sp.add_argument("--outer-diameter", type=float, default=None)
        sp.add_argument("--max-pressure", type=float, default=None)
        sp.add_argument("--expansion-ratio", type=float, default=None)
        sp.add_argument("--throat-diameter", type=float, default=None)
        sp.add_argument("--ambient-pressure", type=float, default=101325.0)
        sp.add_argument("--report", default=None)
        sp.add_argument("--json", default=None)

    sp = sub.add_parser("forward", help="forward design from requirements")
    add_common(sp); sp.set_defaults(func=cmd_forward)

    sp = sub.add_parser("inverse", help="inverse design from requirements")
    add_common(sp)
    sp.add_argument("--optimizer", default="nelder_mead")
    sp.add_argument("--max-iter", type=int, default=120)
    sp.set_defaults(func=cmd_inverse)

    sp = sub.add_parser("thermo", help="evaluate propellant thermochemistry")
    sp.add_argument("--propellant", default="ap_htpb_80")
    sp.add_argument("--pressure", type=float, default=7e6)
    sp.set_defaults(func=cmd_thermo)

    sp = sub.add_parser("templates", help="list grain templates")
    sp.set_defaults(func=cmd_templates)

    sp = sub.add_parser("propellants", help="list propellants")
    sp.set_defaults(func=cmd_propellants)

    sp = sub.add_parser("reverse", help="reverse-engineer an existing grain project")
    sp.add_argument("--project", required=True)
    sp.set_defaults(func=cmd_reverse)

    sp = sub.add_parser("export-cad", help="export grain geometry")
    sp.add_argument("--template", required=True)
    sp.add_argument("--format", default="stl", choices=["stl", "step"])
    sp.add_argument("--out", required=True)
    sp.set_defaults(func=cmd_export_cad)

    sp = sub.add_parser("save-project", help="write a project file")
    add_common(sp)
    sp.add_argument("--out", required=True)
    sp.set_defaults(func=cmd_save_project)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
