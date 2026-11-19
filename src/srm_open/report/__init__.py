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
    from matplotlib.lines import Line2D

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
    if result.provenance.dependency_versions:
        deps = ", ".join(f"{k} {v}" for k, v in result.provenance.dependency_versions.items())
        L.append(f"- dependency versions: {deps}")
    if result.provenance.input_hash:
        L.append(f"- input hash (sha256): {result.provenance.input_hash}")
    if result.provenance.random_seed is not None:
        L.append(f"- random seed: {result.provenance.random_seed}")
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
                 include_plots: bool = True,
                 include_pdf: bool = True) -> dict[str, Path]:
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    plots = plot_results(result, outdir) if include_plots else []
    md = render_markdown(result, plots)
    md_path = outdir / "report.md"
    md_path.write_text(md, encoding="utf-8")
    html_path = outdir / "report.html"
    html_path.write_text(_to_html(md, outdir), encoding="utf-8")
    out = {"markdown": md_path, "html": html_path}
    if include_pdf:
        pdf_path = write_pdf(md, outdir / "report.pdf", outdir)
        if pdf_path is not None:
            out["pdf"] = pdf_path
    for p in plots:
        out[Path(p).stem] = p
    return out


# ---------------------------------------------------------------------------
# PDF rendering
# ---------------------------------------------------------------------------
# The PDF is rendered from the same Markdown the other two formats use, so the
# three files can never disagree.  Matplotlib is the only dependency (it already
# draws the plots), which keeps `pip install` unchanged.
_PAGE = (8.27, 11.69)  # A4 portrait, inches
_MARGIN_X, _MARGIN_Y = 0.09, 0.07  # fractions of the page


class _PageWriter:
    """Lays text blocks and images onto A4 pages, breaking when full."""

    def __init__(self, pdf) -> None:
        self.pdf = pdf
        self.fig = None
        self.y = 0.0
        self.pages = 0

    def _new_page(self) -> None:
        if self.fig is not None:
            self.pdf.savefig(self.fig)
            plt.close(self.fig)
        self.fig = plt.figure(figsize=_PAGE)
        self.y = 1.0 - _MARGIN_Y
        self.pages += 1

    def ensure(self, height: float) -> None:
        if self.fig is None:
            self._new_page()
        if self.y - height < _MARGIN_Y:
            self._new_page()

    def text(self, s: str, size: float = 9.5, weight: str = "normal",
             x: float = _MARGIN_X, indent: float = 0.0,
             family: str = "sans-serif") -> None:
        if not s:
            self.y -= size / 72.0 / _PAGE[1] * 0.6
            return
        # wrap manually so the caller controls page breaks per line
        width_chars = max(int((1 - 2 * _MARGIN_X - indent) * 105), 20)
        for line in _wrap(s, width_chars):
            h = size / 72.0 / _PAGE[1] * 1.45
            self.ensure(h)
            self.fig.text(x + indent, self.y, line, size=size, weight=weight,
                          va="top", ha="left", family=family)
            self.y -= h

    def image(self, path: Path, caption: str = "") -> None:
        if not path.exists():
            return
        width = 1 - 2 * _MARGIN_X
        # reserve a fixed fraction of the page for the figure
        img_h = 0.30
        self.ensure(img_h + 0.03)
        ax = self.fig.add_axes([_MARGIN_X, self.y - img_h, width, img_h])
        ax.imshow(plt.imread(path))
        ax.axis("off")
        self.y -= img_h + 0.01
        if caption:
            self.text(caption, size=8, family="sans-serif")

    def table(self, rows: list[list[str]], size: float = 7.5) -> None:
        if not rows:
            return
        ncol = max(len(r) for r in rows)
        rows = [r + [""] * (ncol - len(r)) for r in rows]
        available = 1 - 2 * _MARGIN_X
        # Monospace glyphs advance ~0.6 em; shrink the font until the widest
        # cell of every column fits, then truncate what still overflows.
        widths = [max(len(r[c]) for r in rows) + 2 for c in range(ncol)]
        while size > 5.0 and sum(widths) * (0.6 * size / 72.0 / _PAGE[0]) > available:
            size -= 0.5
        char_w = 0.6 * size / 72.0 / _PAGE[0]
        budget = int(available / char_w)
        while sum(widths) > budget and max(widths) > 8:
            widths[widths.index(max(widths))] -= 1
        col_w = [w * char_w for w in widths]
        for i, row in enumerate(rows):
            h = size / 72.0 / _PAGE[1] * 1.7
            self.ensure(h)
            x = _MARGIN_X
            weight = "bold" if i == 0 else "normal"
            for c, cell in enumerate(row):
                room = widths[c] - 2
                cell = cell if len(cell) <= room else cell[: max(room - 2, 1)] + ".."
                self.fig.text(x + 0.004, self.y, cell, size=size, weight=weight,
                              va="top", ha="left", family="monospace")
                x += col_w[c]
            self.y -= h
            if i == 0:
                self.fig.add_artist(Line2D(
                    [_MARGIN_X, 1 - _MARGIN_X], [self.y, self.y],
                    transform=self.fig.transFigure, color="0.4", linewidth=0.6))
        self.y -= 0.004

    def close(self) -> int:
        if self.fig is not None:
            self.pdf.savefig(self.fig)
            plt.close(self.fig)
            self.fig = None
        self.pdf.close()
        return self.pages


def _strip_inline(text: str) -> str:
    """Drop Markdown inline markup so the PDF shows prose, not syntax."""
    return text.replace("`", "").replace("**", "").replace("__", "")


def _wrap(text: str, width: int) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        if not cur:
            cur = w
        elif len(cur) + 1 + len(w) <= width:
            cur += " " + w
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines or [""]


def _md_blocks(markdown_text: str, outdir: Path):
    """Split Markdown into ('h1'|'h2'|'p'|'bullet'|'table'|'image', payload)."""
    table: list[list[str]] = []
    for raw in markdown_text.splitlines():
        line = raw.rstrip()
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if all(set(c) <= set("-: ") and c for c in cells):
                continue
            table.append(cells)
            continue
        if table:
            yield "table", table
            table = []
        if not line:
            continue
        if line.startswith("### "):
            yield "h3", line[4:]
        elif line.startswith("## "):
            yield "h2", line[3:]
        elif line.startswith("# "):
            yield "h1", line[2:]
        elif line.startswith("!["):
            label = line[2: line.index("]")]
            target = line[line.index("(") + 1: line.rindex(")")]
            yield "image", (outdir / target, label)
        elif line.startswith("- "):
            yield "bullet", line[2:]
        elif line.startswith(">"):
            yield "quote", line.lstrip("> ").strip()
        else:
            yield "p", line
    if table:
        yield "table", table


def write_pdf(markdown_text: str, path: Path, outdir: Path) -> Path | None:
    """Render the Markdown report to a multi-page PDF. ``None`` without matplotlib."""
    if not _HAVE_MPL:  # pragma: no cover - matplotlib is an install extra
        return None
    from matplotlib.backends.backend_pdf import PdfPages

    writer = _PageWriter(PdfPages(path))
    try:
        writer._new_page()
        for kind, payload in _md_blocks(markdown_text, outdir):
            if kind == "h1":
                writer.text(payload, size=16, weight="bold")
                writer.text("", size=6)
            elif kind == "h2":
                writer.text("", size=4)
                writer.text(_strip_inline(payload), size=12, weight="bold")
            elif kind == "h3":
                writer.text(payload, size=10.5, weight="bold")
            elif kind == "bullet":
                writer.text("- " + _strip_inline(payload), size=9.5, indent=0.012)
            elif kind == "quote":
                writer.text(payload, size=8.5, indent=0.012, family="monospace")
            elif kind == "table":
                writer.table(payload)
            elif kind == "image":
                img, label = payload
                writer.image(img, label)
            else:
                writer.text(_strip_inline(payload), size=9.5)
        writer.close()
    except Exception:  # pragma: no cover - keep the other formats usable
        try:
            writer.pdf.close()
        except Exception:
            pass
        return None
    return path


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
