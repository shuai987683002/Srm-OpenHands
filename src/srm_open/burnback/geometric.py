"""Geometric burn-back (channel A).

Burning surfaces advance along their outward normal as the web is consumed.
For the *port* this is exactly a Minkowski dilation, which shapely performs with
``Polygon.buffer(e)``; for the *outer* surface it is a radius reduction.  This
channel is fast and exact for polygonal ports and is the production path.

Outputs the burning area as a function of the burn distance ``e`` (a pure
geometry quantity).  Mapping ``e`` to time needs the burning rate, which the
internal-ballistics solver supplies -- see :mod:`srm_open.burnback.engine`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..geometry.analysis import burning_area as _seg_area
from ..geometry.analysis import web_thickness as _seg_web
from ..models.grain import GrainGeometry, GrainSegment


@dataclass
class BurnbackCurve:
    burn_distance: np.ndarray  # e [m]
    burn_area: np.ndarray  # A_b(e) [m2]
    propellant_volume: float
    port_volume: float
    web_thickness: float
    segment_web: list[float] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _segment_area_at(segment: GrainSegment, e: float, web: float,
                     quad_segs: int = 32) -> float:
    """Burning area of one segment at burn distance ``e``.

    A segment is fully consumed once ``e`` reaches its web thickness; beyond
    that it contributes no burning surface.
    """
    if web <= 0.0:
        return 0.0
    if e >= web:
        return 0.0
    return _seg_area(segment, e, quad_segs=quad_segs)


def burnback_geometric(grain: GrainGeometry, n_steps: int = 200,
                       quad_segs: int = 32) -> BurnbackCurve:
    """Compute A_b(e) for the whole grain (sum over segments).

    ``quad_segs`` controls the polygon resolution of the Minkowski dilation; the
    optimiser inner loop lowers it to trade a little accuracy for speed.
    """
    segs = grain.segments
    webs = [_seg_web(s) for s in segs]
    web_max = max(webs) if webs else 0.0
    notes: list[str] = []
    if web_max <= 0:
        notes.append("grain has zero web thickness; check geometry")

    e_grid = np.linspace(0.0, web_max, n_steps) if web_max > 0 else np.array([0.0])
    area = np.zeros_like(e_grid)
    for i, e in enumerate(e_grid):
        total = 0.0
        for seg, w in zip(segs, webs):
            total += _segment_area_at(seg, float(e), w, quad_segs=quad_segs)
        area[i] = total

    prop_vol = 0.0
    port_vol = 0.0
    from ..geometry.analysis import analyze_segment

    for seg in segs:
        a = analyze_segment(seg)
        prop_vol += a.propellant_volume
        port_vol += a.port_volume

    return BurnbackCurve(
        burn_distance=e_grid,
        burn_area=area,
        propellant_volume=prop_vol,
        port_volume=port_vol,
        web_thickness=web_max,
        segment_web=webs,
        notes=notes,
    )


def burn_area_at(grain: GrainGeometry, e: float) -> float:
    """Burning area at a single burn distance (used by the ballistics integrator)."""
    total = 0.0
    for seg in grain.segments:
        total += _segment_area_at(seg, e, _seg_web(seg))
    return total
