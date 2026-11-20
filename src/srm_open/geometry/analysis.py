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
from functools import lru_cache

import numpy as np
from shapely.geometry import MultiPolygon, Point, Polygon
from shapely.validation import make_valid

from ..models.grain import BurnMode, GrainFamily, GrainSegment
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


def _safe(poly):
    """Repair a port geometry without discarding any of its components.

    A port may legitimately be *disconnected* -- seven perforations are seven
    separate voids, not one.  Reducing a MultiPolygon to its largest part (the
    old behaviour) silently dropped six of the seven holes and halved the
    burning area, so the components are kept and only invalid geometry is
    repaired.
    """
    if poly is None or poly.is_empty:
        return Polygon()
    if not poly.is_valid:
        poly = make_valid(poly)
    if poly.geom_type == "GeometryCollection":
        polys = [g for g in poly.geoms if g.geom_type == "Polygon" and not g.is_empty]
        if not polys:
            return Polygon()
        poly = polys[0] if len(polys) == 1 else MultiPolygon(polys)
    return poly


def _parts(geom) -> list[Polygon]:
    """The polygon components of a port geometry (always at least one)."""
    if geom is None or geom.is_empty:
        return []
    if geom.geom_type == "MultiPolygon":
        return [g for g in geom.geoms if not g.is_empty]
    return [geom]


def _boundary_coords(geom) -> np.ndarray:
    """All exterior boundary coordinates of a (possibly multi-part) port."""
    chunks = [np.asarray(g.exterior.coords, dtype=float) for g in _parts(geom)]
    return np.vstack(chunks) if chunks else np.empty((0, 2))


def outer_circle(segment: GrainSegment) -> Polygon:
    return _outer_circle_cached(segment.outer_diameter)


@lru_cache(maxsize=512)
def _outer_circle_cached(diameter: float) -> Polygon:
    return Point(0.0, 0.0).buffer(diameter / 2.0, quad_segs=128)


# Port polygons are rebuilt on every burn-step evaluation; the outline depends
# only on the family, the outer radius and the parameters, so memoise on that
# signature.  A plain dict is used rather than ``lru_cache`` because
# ``GrainSegment`` is a pydantic model and therefore unhashable.
_PORT_CACHE: dict[tuple, Polygon] = {}
_PORT_CACHE_LIMIT = 512


def _segment_key(segment: GrainSegment) -> tuple:
    return (segment.family.value, segment.outer_diameter,
            tuple(sorted(segment.params.items())),
            tuple(segment.polygon) if segment.polygon else None)


def port_polygon(segment: GrainSegment) -> Polygon:
    key = _segment_key(segment)
    cached = _PORT_CACHE.get(key)
    if cached is not None:
        return cached
    r_out = segment.outer_diameter / 2.0
    poly = build_port(segment.family, segment.params, r_out, segment.polygon)
    poly = Polygon() if poly is None else _safe(poly)
    if len(_PORT_CACHE) >= _PORT_CACHE_LIMIT:
        _PORT_CACHE.clear()
    _PORT_CACHE[key] = poly
    return poly


def _burning_port(segment: GrainSegment, e: float, quad_segs: int = 64) -> Polygon:
    """Port void after burn distance ``e`` (internal burning)."""
    port = port_polygon(segment)
    if port.is_empty:
        return Polygon()
    grown = port.buffer(e, join_style=1, quad_segs=quad_segs)
    # While the grown port is still inside the bore envelope the clipping is a
    # no-op, and the intersection is by far the most expensive step here, so test
    # containment first.  The test is on the offset ring, whose radius is the
    # largest distance from the axis.
    if e > 0.0:
        coords = _boundary_coords(grown)
        r_out = segment.outer_diameter / 2.0
        if coords.size and float(np.max(np.hypot(coords[:, 0], coords[:, 1]))) <= r_out:
            return _safe(grown)
    return _safe(grown.intersection(outer_circle(segment)))


def _outer_radius_at(segment: GrainSegment, e: float) -> float:
    return max(segment.outer_diameter / 2.0 - e, 0.0)


def burning_perimeter(segment: GrainSegment, e: float,
                      quad_segs: int = 64) -> float:
    """Total burning perimeter per unit axial length at burn distance ``e``."""
    mode = segment.burn_mode
    per = 0.0
    if mode in (BurnMode.INTERNAL, BurnMode.BOTH):
        per += _burning_port(segment, e, quad_segs=quad_segs).length
    if mode in (BurnMode.EXTERNAL, BurnMode.BOTH):
        r = _outer_radius_at(segment, e)
        per += 2.0 * math.pi * r
    return per


def _wire_area(segment: GrainSegment, e: float, web: float) -> float:
    """Extra burning area from embedded radial wires [m2].

    The wires do not burn: they stay put while the propellant recedes, so each
    wire exposes its cylindrical side over the regressed length.  For ``n``
    wires of radius ``r_w`` that is ``n * 2*pi*r_w*e`` -- a term that *grows*
    with burn distance, which is exactly why wire-reinforced grains are used to
    make a charge progressive.  The wires are assumed to span the full web, so
    the exposed length saturates at ``web``.
    """
    n = int(round(segment.params.get("n_wires", 0.0)))
    r_w = segment.params.get("wire_diameter", 0.0) / 2.0
    if n <= 0 or r_w <= 0.0:
        return 0.0
    return n * 2.0 * math.pi * r_w * min(max(e, 0.0), web)


def burning_area(segment: GrainSegment, e: float,
                 quad_segs: int = 64) -> float:
    """Burning surface area of the segment [m2] at burn distance ``e``."""
    if segment.burn_mode == BurnMode.END:
        # cigarette burning: the end face is consumed axially, its area stays
        # constant (the outer radius does not shrink)
        r_o = segment.outer_diameter / 2.0
        port = port_polygon(segment)
        inner = math.sqrt(port.area / math.pi) if port.area > 0 else 0.0
        return math.pi * max(r_o**2 - inner**2, 0.0)
    area = burning_perimeter(segment, e, quad_segs=quad_segs) * segment.length
    if segment.family == GrainFamily.EMBEDDED_WIRE:
        area += _wire_area(segment, e, web_thickness(segment))
    return area


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
    coords = _boundary_coords(port)
    min_gap = (min(r_out - math.hypot(x, y) for x, y in coords)
               if len(coords) else r_out)
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
    if segment.family == GrainFamily.EMBEDDED_WIRE:
        # the wires displace propellant: subtract their cross-section, otherwise
        # the propellant mass (hence total impulse) is overestimated
        n = int(round(segment.params.get("n_wires", 0.0)))
        r_w = segment.params.get("wire_diameter", 0.0) / 2.0
        wire_area = n * math.pi * r_w**2 if n > 0 and r_w > 0 else 0.0
        if wire_area > 0:
            fraction = wire_area / outer.area
            notes.append(
                f"embedded wires: {n} x {2 * r_w * 1e3:.2f} mm, "
                f"{fraction:.2%} of the cross-section is metal")
        prop_area = max(prop_area - wire_area, 0.0)
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
