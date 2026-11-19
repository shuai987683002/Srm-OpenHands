"""Static 3-D grain previews (plan §M5: visualisation).

Deliberately matplotlib-based rather than PyVista: the purpose of this view is a
*teaching* picture — "this is the port, this is the web, this is how far it has
burned" — and that does not justify a VTK dependency in a package whose core is
meant to install offline. For real geometry work use the STEP/STL export in
:mod:`srm_open.geometry.sketch`.

Two figures are produced:

* ``plot_grain_3d`` — the port extruded through the segments, with the outer
  surface drawn translucent so the bore is visible.
* ``plot_burnback_3d`` — initial port and the port after a web depth is burned,
  so the regression direction is legible.

Matplotlib is an optional extra (``.[viz]``).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from ..models.grain import BurnMode, GrainGeometry, GrainSegment
from ..models.results import BurnbackResult


def _require_matplotlib():
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from mpl_toolkits.mplot3d.art3d import Poly3DCollection
    except ImportError as exc:  # pragma: no cover
        raise ImportError("3-D previews need matplotlib: pip install -e '.[viz]'") from exc
    return plt, Poly3DCollection


def _port_ring(segment: GrainSegment) -> np.ndarray:
    """Port boundary as an (m, 2) array; the outer circle for a solid grain."""
    from ..geometry.analysis import outer_circle, port_polygon

    poly = port_polygon(segment)
    if poly.is_empty:
        poly = outer_circle(segment)  # end burner: the visible surface is the OD
    return np.asarray(poly.exterior.coords, dtype=float)


def _side_faces(ring: np.ndarray, z0: float, z1: float) -> list[list[tuple[float, float, float]]]:
    faces = []
    for i in range(len(ring) - 1):
        x0, y0 = ring[i]
        x1, y1 = ring[i + 1]
        faces.append([(x0, y0, z0), (x1, y1, z0), (x1, y1, z1), (x0, y0, z1)])
    return faces


def plot_grain_3d(grain: GrainGeometry, path: str | Path) -> Path:
    """Port and outer surface extruded through every segment."""
    plt, Poly3DCollection = _require_matplotlib()
    from ..geometry.analysis import outer_circle

    fig = plt.figure(figsize=(7, 6))
    ax = fig.add_subplot(111, projection="3d")

    z = 0.0
    for seg in grain.segments:
        z1 = z + seg.length
        outer = np.asarray(outer_circle(seg).exterior.coords, dtype=float)
        ax.add_collection3d(
            Poly3DCollection(
                _side_faces(outer, z, z1),
                facecolor="#c9d6e4", edgecolor="#7d8fa3", linewidths=0.2, alpha=0.30,
            )
        )
        ax.add_collection3d(
            Poly3DCollection(
                _side_faces(_port_ring(seg), z, z1),
                facecolor="#f2c6a0", edgecolor="#b4743f", linewidths=0.3, alpha=0.60,
            )
        )
        z = z1

    d = max(seg.outer_diameter for seg in grain.segments)
    ax.set_xlim(-d, d)
    ax.set_ylim(-d, d)
    ax.set_zlim(0, z)
    ax.set_box_aspect((1, 1, z / d if d else 1))
    ax.set_xlabel("x [m]")
    ax.set_ylabel("y [m]")
    ax.set_zlabel("axial [m]")
    ax.set_title(f"Grain — {len(grain.segments)} segment(s), L={z:.3f} m")
    fig.tight_layout()
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=130)
    plt.close(fig)
    return out


def plot_burnback_3d(grain: GrainGeometry, burnback: BurnbackResult | None,
                     path: str | Path) -> Path:
    """Initial port vs. a regressed port (schematic, first segment only)."""
    plt, Poly3DCollection = _require_matplotlib()

    fig = plt.figure(figsize=(9, 5))
    ax = fig.add_subplot(111, projection="3d")

    seg = grain.segments[0]
    z0, z1 = 0.0, seg.length
    initial = _port_ring(seg)

    web = 0.0
    if burnback is not None:
        # total web thickness is the physically meaningful regression depth here
        web = float(burnback.web_thickness or 0.0)

    # Offset outward by the burned web. This is a schematic of regression
    # direction, not a solved level-set contour — the exact shape is in the 2-D
    # burnback plot, which is the authoritative one.
    center = initial.mean(axis=0)
    offsets = initial - center
    norms = np.linalg.norm(offsets, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    regressed = center + offsets * (1.0 + web / norms) if web > 0 else initial

    for ring, color, alpha, label in (
        (initial, "#3b6ea5", 0.20, "initial port"),
        (regressed, "#c0392b", 0.50, f"after {web * 1e3:.1f} mm web"),
    ):
        ax.add_collection3d(
            Poly3DCollection(
                _side_faces(ring, z0, z1),
                facecolor=color, edgecolor=color, linewidths=0.4, alpha=alpha,
            )
        )
        ax.plot(ring[:, 0], ring[:, 1], np.full(len(ring), z0), color=color, label=label, lw=1.2)

    d = seg.outer_diameter
    ax.set_xlim(-d, d)
    ax.set_ylim(-d, d)
    ax.set_zlim(0, z1)
    ax.set_xlabel("x [m]")
    ax.set_ylabel("y [m]")
    ax.set_zlabel("axial [m]")
    ax.set_title("Burnback: port regression (schematic)")
    ax.legend(loc="upper right", fontsize=8)
    fig.tight_layout()
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=130)
    plt.close(fig)
    return out


def burn_modes_summary(grain: GrainGeometry) -> list[str]:
    """One line per segment describing which faces burn (teaching aid)."""
    labels = {
        BurnMode.INTERNAL: "internal bore only (outer surface inhibited)",
        BurnMode.EXTERNAL: "outer surface only (bore inhibited)",
        BurnMode.BOTH: "bore + outer surface (uninhibited tube)",
        BurnMode.END: "end face only (cigarette burning)",
    }
    return [
        f"segment {i}: {seg.family.value}, {labels.get(seg.burn_mode, seg.burn_mode.value)}"
        for i, seg in enumerate(grain.segments)
    ]


__all__ = ["plot_grain_3d", "plot_burnback_3d", "burn_modes_summary"]
