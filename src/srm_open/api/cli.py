"""Command-line interface: ``srm <command>``.

Commands
--------
  srm design      --thrust F --burn-time t [--family F] [--no-refine]  (indicators -> grain)
  srm forward     --template T --propellant P --thrust F --burn-time t [--report DIR]
  srm inverse     --template T | --family F --outer-diameter D
                  --propellant P --thrust F --burn-time t [--report DIR]
  srm thermo      --propellant P [--pressure p]
  srm templates / srm propellants
  srm reverse     --project FILE            (reverse-engineer an existing grain)
  srm fit-outline --points FILE --family F  (path A: outline -> parameters)
  srm sensitivity --family F --outer-diameter D  (identifiability / conditioning)
  srm sweep       --parameter P --values a,b,c    (parametric sweep)
  srm monte-carlo --vary PATH:SIGMA [--vary ...]  (uncertainty propagation)
  srm export-cad  --template T --format stl|step|pointcloud --out FILE
  srm serve       [--host H] [--port P]   (local web UI, needs the 'api' extra)
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import tempfile
from pathlib import Path

from ..geometry.sketch import (
    geometry_backend_available,
    grain_to_pointcloud,
    grain_to_step,
    grain_to_stl,
)
from ..inverse.design import (
    analyze_existing_grain,
    default_design_space,
    fit_outline,
    identifiability,
    inverse_design,
)
from ..io.project import load_project, save_project, save_result_json
from ..io.templates import TemplateLibrary
from ..models.design_result import DesignResult
from ..models.grain import BurnMode, GrainFamily
from ..models.requirements import RequirementSet
from ..orchestration.forward import forward_design
from ..orchestration.study import monte_carlo, sweep
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
        formats = ", ".join(sorted(k for k in paths if k in ("markdown", "html", "pdf")))
        print("report            : %s (%s)" % (paths["markdown"], formats))
    if args.json:
        save_result_json(result, args.json)
        print("json              : %s" % args.json)
    return 0


def cmd_forward(args) -> int:
    grain = TemplateLibrary.load_default().geometry(args.template)
    prop = get_propellant(args.propellant)
    result = forward_design(_req_from_args(args), grain, prop)
    return _emit(result, args)


def cmd_design(args) -> int:
    """Requirement-driven forward design: indicators -> synthesised grain."""
    from .facade import design_forward as design_from_indicators

    req = _req_from_args(args)
    if args.thrust_profile:
        req.thrust_profile = args.thrust_profile
    if args.length_ratio:
        req.length_ratio = args.length_ratio
    result = design_from_indicators(req, args.propellant, family=args.family,
                                    refine=not args.no_refine)
    return _emit(result, args)


def cmd_inverse(args) -> int:
    """Recover grain parameters from ballistic requirements.

    A template is one starting point, but it is not required: ``--family`` with
    ``--outer-diameter`` (and optionally ``--length``) is enough to build the
    design space, which is what an engineer normally has before a grain exists.
    """
    if args.template:
        seg = TemplateLibrary.load_default().geometry(args.template).segments[0]
        family, outer_diameter = seg.family, seg.outer_diameter
        length_hint = seg.length
    else:
        if not args.family or args.outer_diameter is None:
            print("srm inverse: give --template, or --family together with "
                  "--outer-diameter", file=sys.stderr)
            return 2
        family = GrainFamily(args.family)
        outer_diameter = args.outer_diameter
        length_hint = args.length or outer_diameter * 3.0

    prop = get_propellant(args.propellant)
    space = default_design_space(family, outer_diameter,
                                 (0.1, max(0.3, length_hint * 1.5)),
                                 burn_mode=BurnMode(args.burn_mode))
    result = inverse_design(_req_from_args(args), prop, space,
                            method=args.optimizer, max_iter=args.max_iter)
    print("recovered params  : %s" % result.grain.segments[0].params)
    return _emit(result, args)


def _port_is_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
        except OSError:
            return False
        return True


def cmd_serve(args) -> int:
    try:
        import uvicorn  # noqa: F401
    except ImportError:
        if getattr(sys, "frozen", False):
            print("this build was packaged without the web UI; use the CLI "
                  "subcommands (srm --help)", file=sys.stderr)
            _pause_if_interactive()
            return 2
        print("the web UI needs the optional extra: pip install -e '.[api]'",
              file=sys.stderr)
        return 2

    from ..api.rest import frontend_available

    if not frontend_available():
        print("front end assets not found in this build", file=sys.stderr)
        _pause_if_interactive()
        return 2

    # Double-clicking the exe lands here with the default port, and a second
    # instance (or anything else on 8000) would otherwise die with an opaque
    # "address already in use" traceback. Walk forward instead.
    port = args.port
    if not _port_is_free(args.host, port):
        for candidate in range(port + 1, port + 21):
            if _port_is_free(args.host, candidate):
                print(f"port {port} is busy, using {candidate}", file=sys.stderr)
                port = candidate
                break
        else:
            print(f"ports {port}-{port + 20} are all busy; pass --port", file=sys.stderr)
            return 2

    url = f"http://{args.host}:{port}/ui/"
    print(f"Srm-Open web UI on {url}  (Ctrl-C to stop)")
    sys.stdout.flush()

    if args.open_browser:
        # Only after the server is up, or the browser shows a connection error.
        import threading
        import time
        import webbrowser

        def _open() -> None:
            time.sleep(1.5)
            try:
                webbrowser.open(url)
            except Exception:
                pass  # a headless box has no browser; the URL is already printed

        threading.Thread(target=_open, daemon=True).start()

    uvicorn.run("srm_open.api.rest:app", host=args.host, port=port,
                log_level=args.log_level)
    return 0


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
    elif args.format == "pointcloud":
        p = grain_to_pointcloud(grain, out)
    else:
        print("unknown format %s" % args.format, file=sys.stderr)
        return 2
    print("wrote %s" % p)
    return 0


def _emit_json(payload: dict, json_path: str | None) -> None:
    text = json.dumps(payload, indent=2, default=str)
    if json_path:
        Path(json_path).parent.mkdir(parents=True, exist_ok=True)
        Path(json_path).write_text(text, encoding="utf-8")
        print("wrote %s" % json_path)
    else:
        print(text)


def cmd_sensitivity(args) -> int:
    family = GrainFamily(args.family)
    od = args.outer_diameter
    # a fixed --length pins the length parameter; otherwise scan a plausible
    # L/D window so the length itself is assessed for identifiability too
    lr = (args.length, args.length) if args.length is not None else (2.0 * od, 8.0 * od)
    length = args.length if args.length is not None else 4.0 * od
    space = default_design_space(family, od, lr)
    if args.params:
        params = json.loads(args.params)
    else:
        params = {p.name: 0.5 * (p.low + p.high) for p in space.parameters}
    x = space.to_vector(params, length)
    info = identifiability(space, x)
    info.pop("matrix", None)
    info.pop("e_grid", None)
    _emit_json(info, args.json)
    return 0


def _study_common(args):
    grain = TemplateLibrary.load_default().geometry(args.template)
    prop = PropellantDB.load_default().get(args.propellant)
    return grain, prop, _req_from_args(args)


def cmd_sweep(args) -> int:
    grain, prop, req = _study_common(args)
    values = [float(v) for v in args.values.split(",") if v.strip()]
    study = sweep(req, grain, prop, args.parameter, values, target=args.target)
    _emit_json(study.to_dict(), args.json)
    return 0


def cmd_monte_carlo(args) -> int:
    grain, prop, req = _study_common(args)
    variations: dict[str, dict] = {}
    for spec in args.vary:
        parts = spec.split(":")
        if len(parts) < 2:
            print("--vary expects PATH:SIGMA[:TARGET]", file=sys.stderr)
            return 2
        entry: dict = {"sigma": float(parts[1])}
        if len(parts) > 2:
            entry["target"] = parts[2]
        variations[parts[0]] = entry
    if not variations:
        print("give at least one --vary PATH:SIGMA", file=sys.stderr)
        return 2
    study = monte_carlo(req, grain, prop, variations,
                        n_samples=args.samples, seed=args.seed)
    _emit_json(study.to_dict(), args.json)
    return 0


def cmd_fit_outline(args) -> int:
    raw = Path(args.points).read_text(encoding="utf-8")
    pts: list[tuple[float, float]] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.replace(",", " ").split()
        if len(parts) < 2:
            continue
        pts.append((float(parts[0]), float(parts[1])))
    if len(pts) < 3:
        print("need >= 3 outline points", file=sys.stderr)
        return 2
    res = fit_outline(pts, GrainFamily(args.family), args.outer_diameter, args.length,
                      method=args.optimizer, max_iter=args.max_iter)
    res.pop("geometry", None)
    _emit_json(res, args.json)
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

    def add_common(sp, template_required=True):
        sp.add_argument("--name", default="cli")
        sp.add_argument("--template", required=template_required)
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

    sp = sub.add_parser("design", help="requirement-driven design: indicators -> synthesised grain")
    sp.add_argument("--name", default="cli")
    sp.add_argument("--propellant", default="ap_htpb_80")
    sp.add_argument("--family", default=None,
                    help="grain family (tube, star, wagon_wheel, ...); auto-selected if omitted")
    sp.add_argument("--thrust", type=float, default=None)
    sp.add_argument("--burn-time", type=float, default=None)
    sp.add_argument("--total-impulse", type=float, default=None)
    sp.add_argument("--isp", type=float, default=None)
    sp.add_argument("--outer-diameter", type=float, default=None)
    sp.add_argument("--length-ratio", type=float, default=None)
    sp.add_argument("--thrust-profile", default=None,
                    choices=["neutral", "progressive", "regressive"])
    sp.add_argument("--max-pressure", type=float, default=None)
    sp.add_argument("--expansion-ratio", type=float, default=None)
    sp.add_argument("--throat-diameter", type=float, default=None)
    sp.add_argument("--ambient-pressure", type=float, default=101325.0)
    sp.add_argument("--no-refine", action="store_true",
                    help="skip the M9 local refinement (faster, less exact)")
    sp.add_argument("--report", default=None)
    sp.add_argument("--json", default=None)
    sp.set_defaults(func=cmd_design)

    sp = sub.add_parser("inverse", help="inverse design from requirements")
    # ``--template`` only seeds the design space; a family and an envelope are
    # enough, so it is not mandatory here.
    add_common(sp, template_required=False)
    sp.add_argument("--family", default=None,
                    help="grain family; used when --template is omitted")
    sp.add_argument("--length", type=float, default=None,
                    help="envelope length hint in m (default 3x outer diameter)")
    sp.add_argument("--burn-mode", default="internal",
                    choices=[m.value for m in BurnMode])
    sp.add_argument("--optimizer", default="nelder_mead")
    sp.add_argument("--max-iter", type=int, default=120)
    sp.set_defaults(func=cmd_inverse)

    sp = sub.add_parser("serve", help="start the local web UI (needs the 'api' extra)")
    sp.add_argument("--host", default="127.0.0.1")
    sp.add_argument("--port", type=int, default=8000)
    sp.add_argument("--log-level", default="info",
                    choices=["critical", "error", "warning", "info", "debug"])
    sp.add_argument("--open-browser", action=argparse.BooleanOptionalAction,
                    default=False,
                    help="open the UI in the default browser once it is listening")
    sp.set_defaults(func=cmd_serve)

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
    sp.add_argument("--format", default="stl", choices=["stl", "step", "pointcloud"])
    sp.add_argument("--out", required=True)
    sp.set_defaults(func=cmd_export_cad)

    sp = sub.add_parser("sensitivity",
                        help="inverse identifiability: condition number, VIF, frozen parameters")
    sp.add_argument("--family", required=True)
    sp.add_argument("--outer-diameter", type=float, required=True)
    sp.add_argument("--length", type=float, default=None, help="segment length [m]")
    sp.add_argument("--params", default=None,
                    help="JSON dict of family parameters; mid-range if omitted")
    sp.add_argument("--json", default=None)
    sp.set_defaults(func=cmd_sensitivity)

    sp = sub.add_parser("sweep", help="parametric sweep of one input across values")
    add_common(sp)
    sp.add_argument("--parameter", required=True,
                    help="RequirementSet field or propellant path (e.g. burn_rate.a)")
    sp.add_argument("--values", required=True,
                    help="comma-separated values")
    sp.add_argument("--target", default="requirement",
                    choices=["requirement", "propellant"])
    sp.set_defaults(func=cmd_sweep)

    sp = sub.add_parser("monte-carlo", help="Monte-Carlo uncertainty propagation")
    add_common(sp)
    sp.add_argument("--vary", action="append", default=[],
                    metavar="PATH:SIGMA[:TARGET]",
                    help="input path, standard deviation, optional target "
                         "(requirement|propellant); repeatable")
    sp.add_argument("--samples", type=int, default=100)
    sp.add_argument("--seed", type=int, default=12345)
    sp.set_defaults(func=cmd_monte_carlo)

    sp = sub.add_parser("fit-outline",
                        help="reverse path A: fit family parameters to a 2D port outline")
    sp.add_argument("--points", required=True,
                    help="CSV/whitespace file of x,y outline points [m]")
    sp.add_argument("--family", required=True)
    sp.add_argument("--outer-diameter", type=float, required=True)
    sp.add_argument("--length", type=float, required=True, help="segment length [m]")
    sp.add_argument("--optimizer", default="nelder_mead")
    sp.add_argument("--max-iter", type=int, default=400)
    sp.add_argument("--json", default=None)
    sp.set_defaults(func=cmd_fit_outline)

    sp = sub.add_parser("save-project", help="write a project file")
    add_common(sp)
    sp.add_argument("--out", required=True)
    sp.set_defaults(func=cmd_save_project)

    return p


def _launch_default() -> int:
    """What a double-clicked exe should do: start the web UI and open a browser.

    With no arguments the CLI used to exit 2 with "the following arguments are
    required: command", which from Explorer looks like the window flashing and
    closing. A desktop user has no console to read that in.
    """
    return cmd_serve(argparse.Namespace(host="127.0.0.1", port=8000,
                                        log_level="warning", open_browser=True))


def _pause_if_interactive() -> None:
    """Hold a double-clicked window open so its message can be read."""
    if not getattr(sys, "frozen", False):
        return
    try:
        if sys.stdin is None or not sys.stdin.isatty():
            return
        input("\n按回车键退出 ...")
    except (EOFError, KeyboardInterrupt):
        pass


def _crash_log_path() -> Path:
    """Prefer the exe's folder, but fall back when it is read-only.

    An exe under Program Files cannot write next to itself, and silently losing
    the log defeats the purpose of writing one.
    """
    candidates = []
    try:
        candidates.append(Path(sys.executable).parent / "srm-error.log")
    except Exception:  # pragma: no cover - exotic embedders
        pass
    candidates.append(Path.cwd() / "srm-error.log")
    local = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_STATE_HOME")
    if local:
        candidates.append(Path(local) / "srm-open" / "srm-error.log")
    candidates.append(Path(tempfile.gettempdir()) / "srm-error.log")

    for path in candidates:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8"):
                return path
        except OSError:
            continue
    return candidates[-1]


def _report_crash(exc: BaseException) -> None:
    """Keep a double-clicked window readable instead of vanishing.

    Explorer closes the console the instant the process exits, so an unhandled
    traceback is invisible.
    """
    import traceback

    traceback.print_exception(type(exc), exc, exc.__traceback__)
    log = _crash_log_path()
    try:
        with open(log, "a", encoding="utf-8") as fh:
            fh.write("".join(traceback.format_exception(
                type(exc), exc, exc.__traceback__)))
        print(f"\n详细错误已写入 {log}", file=sys.stderr)
    except OSError:
        pass
    _pause_if_interactive()


def main(argv: list[str] | None = None) -> int:
    # Checked before parsing: argparse would print "the following arguments are
    # required: command" to stderr first, and a desktop user who double-clicked
    # the exe would see that error flash before the browser opened.
    if not (sys.argv[1:] if argv is None else argv):
        return _launch_default()

    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # noqa: BLE001 - last resort for a frozen exe
        if getattr(sys, "frozen", False):
            _report_crash(exc)
            return 1
        raise


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
