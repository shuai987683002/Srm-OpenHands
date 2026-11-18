"""Report generation: Markdown / HTML summary plus PNG plots.

The report is a faithful rendering of the :class:`DesignResult`; every number in
it comes from the result object, and every assumption the engine made is listed
in the "Assumptions and provenance" section.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from ..models.design_result import DesignResult

try:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    _HAVE_MPL = True
except Exception:  # pragma: no cover
    _HAVE_MPL = False


def _fmt(v, unit: str = "", digits: int = 4) -> str:
    if v is None:
        return "n/a"
    if isinstance(v, (int, float)):
        return f"{v:.{digits}g} {unit}".strip()
    return str(v)


def plot_results(result: DesignResult, outdir: Path) -> list[Path]:
    """Write PNG plots and return their paths."""
    if not _HAVE_MPL:
        return []
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []

    if result.ballistics and result.ballistics.time:
        fig, ax = plt.subplots(figsize=(7, 4))
        ax.plot(result.ballistics.time, np.array(result.ballistics.chamber_pressure) / 1e6)
        ax.set_xlabel("time [s]")
        ax.set_ylabel("chamber pressure [MPa]")
        ax.set_title("Chamber pressure history")
        ax.grid(True, alpha=0.3)
        p = outdir / "pressure_history.png"
        fig.tight_layout(); fig.savefig(p, dpi=130); plt.close(fig)
        paths.append(p)

    if result.performance and result.performance.time:
        fig, ax = plt.subplots(figsize=(7, 4))
        ax.plot(result.performance.time, result.performance.thrust)
        ax.set_xlabel("time [s]")
        ax.set_ylabel("thrust [N]")
        ax.set_title("Thrust history")
        ax.grid(True, alpha=0.3)
        p = outdir / "thrust_history.png"
        fig.tight_layout(); fig.savefig(p, dpi=130); plt.close(fig)
        paths.append(p)

    if result.burnback and result.burnback.burn_distance:
        fig, ax = plt.subplots(figsize=(7, 4))
        ax.plot(result.burnback.burn_distance, result.burnback.burn_area)
        ax.set_xlabel("burn distance e [m]")
        ax.set_ylabel("burning area A_b [m2]")
        ax.set_title("Burning area vs web consumed")
        ax.grid(True, alpha=0.3)
        p = outdir / "burn_area.png"
        fig.tight_layout(); fig.savefig(p, dpi=130); plt.close(fig)
        paths.append(p)

    if result.integrity and result.integrity.radius_curve:
        fig, ax = plt.subplots(figsize=(7, 4))
        ax.plot(result.integrity.radius_curve, np.array(result.integrity.stress_curve) / 1e6)
        ax.set_xlabel("radius [m]")
        ax.set_ylabel("von Mises stress [MPa]")
        ax.set_title("Grain stress distribution")
        ax.grid(True, alpha=0.3)
        p = outdir / "stress.png"
        fig.tight_layout(); fig.savefig(p, dpi=130); plt.close(fig)
        paths.append(p)

    return paths


def render_markdown(result: DesignResult, plots: list[Path] | None = None) -> str:
    L: list[str] = []
    L.append(f"# Srm-Open design report ({result.mode} design)\n")
    req = result.requirement
    if req is not None:
        L.append("## 1. Requirements\n")
        L.append("| quantity | value |")
        L.append("|---|---|")
        for k, v in req.model_dump().items():
            if v is not None:
                L.append(f"| {k} | {v} |")
        L.append("")

    if result.propellant is not None:
        p = result.propellant
        L.append("## 2. Propellant\n")
        L.append(f"- id: `{p.id}` ({p.name})")
        L.append(f"- density: {_fmt(p.density, 'kg/m3')}")
        L.append(f"- burn rate: {p.burn_rate.law.value}, a={_fmt(p.burn_rate.a)}, n={_fmt(p.burn_rate.n)}")
        L.append(f"- thermo mode: {p.thermo.mode}")
        L.append(f"- source: {p.source}")
        L.append("")

    if result.nozzle is not None:
        n = result.nozzle
        L.append("## 3. Nozzle\n")
        L.append(f"- throat diameter: {_fmt(n.throat_diameter, 'm')}")
        L.append(f"- expansion ratio: {_fmt(n.expansion_ratio)}")
        L.append(f"- source: {n.source}")
        L.append("")

    if result.grain is not None:
        L.append("## 4. Grain geometry\n")
        L.append("| seg | family | length [m] | OD [m] | burn mode | params |")
        L.append("|---|---|---|---|---|---|")
        for i, s in enumerate(result.grain.segments):
            L.append(f"| {i} | {s.family.value} | {s.length} | {s.outer_diameter} | "
                     f"{s.burn_mode.value} | {s.params} |")
        L.append("")

    if result.grain_body is not None:
        gb = result.grain_body
        L.append("## 5. Cylindrical grain body\n")
        L.append(f"- initial burning area: {_fmt(gb.burn_area_initial, 'm2')}")
        L.append(f"- web thickness: {_fmt(gb.web_thickness, 'm')}")
        L.append(f"- propellant volume: {_fmt(gb.propellant_volume, 'm3')}")
        L.append(f"- loading fraction: {_fmt(gb.loading_fraction)}")
        L.append(f"- sliver mass: {_fmt(gb.sliver_mass, 'kg')} "
                 f"({_fmt(gb.sliver_fraction)})")
        L.append(f"- L/D: {_fmt(gb.length_to_diameter)}")
        L.append("")

    if result.ballistics is not None:
        b = result.ballistics
        L.append("## 6. Internal ballistics\n")
        L.append(f"- model: {b.model}")
        L.append(f"- peak pressure: {_fmt(b.peak_pressure/1e6, 'MPa')}")
        L.append(f"- time at peak: {_fmt(b.time_at_peak, 's')}")
        L.append(f"- converged: {b.converged}")
        L.append("")

    if result.performance is not None:
        pf = result.performance
        L.append("## 7. Performance\n")
        L.append(f"- total impulse: {_fmt(pf.total_impulse, 'N.s')}")
        L.append(f"- specific impulse: {_fmt(pf.specific_impulse, 's')}")
        L.append(f"- average thrust: {_fmt(pf.average_thrust, 'N')}")
        L.append(f"- burn time: {_fmt(pf.burn_time, 's')}")
        L.append(f"- propellant mass: {_fmt(pf.propellant_mass, 'kg')}")
        L.append("")

    if result.integrity is not None:
        it = result.integrity
        L.append("## 8. Structural integrity\n")
        L.append(f"- method: {it.method}")
        L.append(f"- max von Mises: {_fmt(it.max_von_mises/1e6, 'MPa')}")
        L.append(f"- safety factor: {_fmt(it.safety_factor)}")
        L.append(f"- location: {it.location}")
        L.append("")

    if result.verification.checks:
        L.append("## 9. Verification checks\n")
        L.append("| check | passed | value | target |")
        L.append("|---|---|---|---|")
        for c in result.verification.checks:
            v = c.get("value")
            v = f"{v:.5g}" if isinstance(v, (int, float)) else str(v)
            t = c.get("tol")
            t = f"{t:.5g}" if isinstance(t, (int, float)) else str(t)
            L.append(f"| {c['name']} | {'PASS' if c['passed'] else 'FAIL'} | {v} | {t} |")
        L.append("")

    if plots:
        L.append("## 10. Plots\n")
        for p in plots:
            L.append(f"![{Path(p).stem}]({Path(p).name})")
        L.append("")

    L.append("## 11. Assumptions and provenance\n")
    L.append(f"- software version: {result.provenance.software_version}")
    L.append(f"- model choices: {result.provenance.model_choices}")
    L.append(f"- data sources: {result.provenance.data_sources}")
    if result.grain_body and result.grain_body.assumptions:
        for a in result.grain_body.assumptions:
            L.append(f"- {a}")
    if result.integrity and result.integrity.assumptions:
        for a in result.integrity.assumptions:
            L.append(f"- {a}")
    L.append("")
    L.append("> This report was produced by open-source Srm-Open. Results are "
             "engineering estimates and are not a substitute for validated design "
             "authority review.")
    return "\n".join(L)


def write_report(result: DesignResult, outdir: str | Path,
                 include_plots: bool = True) -> dict[str, Path]:
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    plots = plot_results(result, outdir) if include_plots else []
    md = render_markdown(result, plots)
    md_path = outdir / "report.md"
    md_path.write_text(md, encoding="utf-8")
    html_path = outdir / "report.html"
    html_path.write_text(_to_html(md, outdir), encoding="utf-8")
    out = {"markdown": md_path, "html": html_path}
    for p in plots:
        out[Path(p).stem] = p
    return out


def _to_html(markdown_text: str, outdir: Path) -> str:
    try:
        import markdown as md  # optional

        body = md.markdown(markdown_text, extensions=["tables"])
    except Exception:
        body = "<pre>" + markdown_text.replace("<", "&lt;") + "</pre>"
    return ("<!doctype html><html><head><meta charset='utf-8'>"
            "<title>Srm-Open report</title>"
            "<style>body{font-family:sans-serif;max-width:900px;margin:2rem auto;}"
            "table{border-collapse:collapse}td,th{border:1px solid #ccc;padding:4px 8px}"
            "img{max-width:100%}</style></head><body>" + body + "</body></html>")
