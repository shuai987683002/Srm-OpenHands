"""2-D grain cross-section analysis and burn-surface geometry.

The unit of computation is one :class:`~srm_open.models.grain.GrainSegment`.
For a segment we keep:

* ``port``  -- the void (port) polygon,
* ``outer`` -- the grain outer circle.

Burning rules per :class:`~srm_open.models.grain.BurnMode`:

* ``INTERNAL``: only the port boundary burns; the outer surface is inhibited.
* ``EXTERNAL``: only the outer surface burns; the port is inhibited.
* ``BOTH``    : port boundary and outer surface both burn.
* ``END``     : only the axial end face burns (cigarette burning).

Burn distance ``e`` advances each burning surface along its outward normal; for
the port this is a Minkowski dilation (``polygon.buffer(e)``), for the outer
surface a radius reduction.

Degenerate / boundary cases handled explicitly:

* solid grain (no port)               -> internal burning area is zero
* port already touching the outer wall -> web thickness zero, burn ends
* self-intersecting custom polygon     -> repaired with ``buffer(0)``
* multi-component port (multi-perf)    -> total perimeter summed
* fillet collapse at large ``e``       -> handled by shapely buffer
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from shapely.geometry import Point, Polygon
from shapely.validation import make_valid

from ..models.grain import BurnMode, GrainSegment
from .families import build_port


@dataclass
class SegmentAnalysis:
    port_area: float
    port_perimeter: float
    outer_perimeter: float
    port_volume: float
    propellant_area: float
    propellant_volume: float
    web_thickness: float
    outer_radius: float
    notes: list[str]


def _safe(poly: Polygon) -> Polygon:
    if poly is None or poly.is_empty:
        return Polygon()
    if not poly.is_valid:
        poly = make_valid(poly)
    if poly.geom_type == "MultiPolygon":
        poly = max(poly.geoms, key=lambda g: g.area)
    return poly


def outer_circle(segment: GrainSegment) -> Polygon:
    return Point(0.0, 0.0).buffer(segment.outer_diameter / 2.0, quad_segs=128)


def port_polygon(segment: GrainSegment) -> Polygon:
    r_out = segment.outer_diameter / 2.0
    poly = build_port(segment.family, segment.params, r_out, segment.polygon)
    if poly is None:
        return Polygon()
    return _safe(poly)


def _burning_port(segment: GrainSegment, e: float) -> Polygon:
    """Port void after burn distance ``e`` (internal burning)."""
    port = port_polygon(segment)
    if port.is_empty:
        return Polygon()
    grown = port.buffer(e, join_style=1, quad_segs=64)
    return _safe(grown.intersection(outer_circle(segment)))


def _outer_radius_at(segment: GrainSegment, e: float) -> float:
    return max(segment.outer_diameter / 2.0 - e, 0.0)


def burning_perimeter(segment: GrainSegment, e: float) -> float:
    """Total burning perimeter per unit axial length at burn distance ``e``."""
    mode = segment.burn_mode
    per = 0.0
    if mode in (BurnMode.INTERNAL, BurnMode.BOTH):
        per += _burning_port(segment, e).length
    if mode in (BurnMode.EXTERNAL, BurnMode.BOTH):
        r = _outer_radius_at(segment, e)
        per += 2.0 * math.pi * r
    return per


def burning_area(segment: GrainSegment, e: float) -> float:
    """Burning surface area of the segment [m2] at burn distance ``e``."""
    if segment.burn_mode == BurnMode.END:
        # cigarette burning: the end face is consumed axially, its area stays
        # constant (the outer radius does not shrink)
        r_o = segment.outer_diameter / 2.0
        port = port_polygon(segment)
        inner = math.sqrt(port.area / math.pi) if port.area > 0 else 0.0
        return math.pi * max(r_o**2 - inner**2, 0.0)
    return burning_perimeter(segment, e) * segment.length


def web_thickness(segment: GrainSegment) -> float:
    """Maximum burn distance before the grain is consumed."""
    r_out = segment.outer_diameter / 2.0
    port = port_polygon(segment)
    if port.is_empty:
        # solid grain: only end/external burning can consume it
        if segment.burn_mode == BurnMode.EXTERNAL:
            return r_out
        return segment.length if segment.burn_mode == BurnMode.END else 0.0
    # internal: distance from port boundary to the outer wall
    # approximate: nearest approach of port to outer circle

    # radial gap = min over port points of (r_out - r_point)
    coords = list(port.exterior.coords)
    min_gap = min(r_out - math.hypot(x, y) for x, y in coords) if coords else r_out
    if segment.burn_mode == BurnMode.BOTH:
        # both surfaces burn towards each other
        return min_gap / 2.0
    return max(min_gap, 0.0)


def analyze_segment(segment: GrainSegment) -> SegmentAnalysis:
    notes: list[str] = []
    r_out = segment.outer_diameter / 2.0
    outer = outer_circle(segment)
    port = port_polygon(segment)
    if port.is_empty:
        notes.append("solid grain (no port)")
    if not port.is_empty and not port.is_valid:
        notes.append("port polygon was invalid and repaired")
    port_area = port.area
    port_per = port.length if not port.is_empty else 0.0
    prop_area = max(outer.area - port_area, 0.0)
    web = web_thickness(segment)
    if web <= 0:
        notes.append("zero web thickness (port touches outer wall)")
    return SegmentAnalysis(
        port_area=port_area,
        port_perimeter=port_per,
        outer_perimeter=outer.length,
        port_volume=port_area * segment.length,
        propellant_area=prop_area,
        propellant_volume=prop_area * segment.length,
        web_thickness=web,
        outer_radius=r_out,
        notes=notes,
    )


def initial_burning_area(segment: GrainSegment) -> float:
    return burning_area(segment, 0.0)
