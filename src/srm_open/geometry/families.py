"""Grain cross-section family registry.

Each family maps a parameter dict + outer radius to a *port polygon* (the void
cross-section) as a :class:`shapely.geometry.Polygon`.  Everything downstream
(burn-back, area/perimeter, 2D sketch, 3D solid) is derived from this polygon,
so adding a family only means adding one function here.

Family parameter schemas are declared in :data:`FAMILY_PARAMS` so the UI/API can
render forms and validate inputs generically.
"""

from __future__ import annotations

import math
from collections.abc import Callable

from shapely.geometry import Point, Polygon
from shapely.ops import unary_union

from ..models.grain import GrainFamily

# ---------------------------------------------------------------------------
# parameter schemas: name -> (required, unit, description, default)
# ---------------------------------------------------------------------------
FAMILY_PARAMS: dict[GrainFamily, dict[str, tuple[bool, str, str, float | None]]] = {
    GrainFamily.CYLINDRICAL: {},
    GrainFamily.TUBE: {
        "inner_diameter": (True, "m", "central bore diameter", None),
    },
    GrainFamily.TUBE_INHIBITED: {
        "inner_diameter": (True, "m", "central bore diameter", None),
    },
    GrainFamily.STAR: {
        "n_points": (True, "-", "number of star points", None),
        "inner_diameter": (True, "m", "star valley (bore) diameter", None),
        "point_diameter": (True, "m", "diameter to star point tips", None),
        "point_half_angle": (False, "deg", "half angle of a star point", 25.0),
        "fillet": (False, "m", "tip/valley fillet radius", 0.002),
    },
    GrainFamily.WAGON_WHEEL: {
        "n_spokes": (True, "-", "number of spokes", None),
        "inner_diameter": (True, "m", "hub bore diameter", None),
        "spoke_width": (True, "m", "spoke width", None),
        "spoke_length": (True, "m", "spoke radial length", None),
    },
    GrainFamily.SLOTTED: {
        "inner_diameter": (True, "m", "central bore diameter", None),
        "n_slots": (True, "-", "number of slots", None),
        "slot_width": (True, "m", "slot width", None),
        "slot_depth": (True, "m", "slot radial depth beyond bore", None),
    },
    GrainFamily.FINOCYL: {
        "inner_diameter": (True, "m", "central bore diameter", None),
        "n_fins": (True, "-", "number of fins", None),
        "fin_width": (True, "m", "fin width", None),
        "fin_length": (True, "m", "fin radial length", None),
    },
    GrainFamily.SPHERICAL: {
        "cavity_diameter": (True, "m", "spherical cavity diameter", None),
    },
    GrainFamily.CONICAL: {
        "base_diameter": (True, "m", "cone base diameter", None),
        "apex_diameter": (False, "m", "cone apex diameter (0=sharp)", 0.0),
    },
    GrainFamily.MULTI_PERFORATED: {
        "n_perforations": (True, "-", "number of circular perforations (7 or 19)", None),
        "perf_diameter": (True, "m", "perforation diameter", None),
    },
    GrainFamily.CUSTOM_POLYGON: {},
}


def _circle(r: float) -> Polygon:
    return Point(0.0, 0.0).buffer(r, quad_segs=96)


def _circle_at(cx: float, cy: float, r: float) -> Polygon:
    return Point(cx, cy).buffer(r, quad_segs=48)


def _regular_polygon(radius: float, n: int, rotation: float = 0.0) -> Polygon:
    pts = [
        (radius * math.cos(rotation + 2 * math.pi * k / n),
         radius * math.sin(rotation + 2 * math.pi * k / n))
        for k in range(n)
    ]
    return Polygon(pts)


# ---------------------------------------------------------------------------
# family builders
# ---------------------------------------------------------------------------
def build_cylindrical(p: dict, r_outer: float) -> Polygon | None:
    """Solid grain: no port (end-burning). Returns None."""
    return None


def build_tube(p: dict, r_outer: float) -> Polygon:
    return _circle(p["inner_diameter"] / 2.0)


def build_tube_inhibited(p: dict, r_outer: float) -> Polygon:
    return _circle(p["inner_diameter"] / 2.0)


def build_star(p: dict, r_outer: float) -> Polygon:
    """Classic star port (alternating tip/valley polygon).

    Tips sit on the tip circle (radius ``r_tip``); valleys sit on the valley
    circle (radius ``r_valley``) at the angular midpoints between tips.  If the
    user gives ``point_half_angle`` (half the angle subtended at a tip) the
    valley angular half-width ``phi`` is solved from

        tan(alpha) = r_valley sin(phi) / (r_tip - r_valley cos(phi))

    and clamped to the feasible range ``[0, pi/n]`` (infeasible angles -- too
    sharp for the given radii -- are clamped and a note is implied by the
    geometry, never by crashing).  A ``fillet`` radius rounds tips and valleys.

    Degenerate inputs (tip <= valley, zero points) fall back to the plain bore.
    """
    n = int(round(p["n_points"]))
    r_valley = p["inner_diameter"] / 2.0
    r_tip = min(p["point_diameter"] / 2.0, r_outer)
    fillet = p.get("fillet", 0.0)

    bore = _circle(r_valley)
    if n <= 0 or r_tip <= r_valley:
        return bore

    phi = math.pi / n  # symmetric default
    if "point_half_angle" in p and p["point_half_angle"] is not None:
        alpha = math.radians(p["point_half_angle"])
        if 0.0 < alpha < math.pi / 2:
            s = (r_tip / r_valley) * math.sin(alpha)
            if s < 1.0:
                phi = math.asin(s) - alpha
            else:
                phi = math.pi / 2 - alpha  # clamped: sharpest feasible point
            phi = min(max(phi, 1e-4), math.pi / n)

    pts = []
    for k in range(n):
        tip_ang = 2 * math.pi * k / n
        pts.append((r_tip * math.cos(tip_ang), r_tip * math.sin(tip_ang)))
        val_ang = tip_ang + phi
        pts.append((r_valley * math.cos(val_ang), r_valley * math.sin(val_ang)))
    port = Polygon(pts).intersection(_circle(r_outer))
    if fillet and fillet > 0:
        port = port.buffer(fillet, join_style=1).buffer(-fillet, join_style=1)
    return _safe_polygon(port)


def _safe_polygon(poly):
    from shapely.validation import make_valid

    if poly is None or poly.is_empty:
        return Polygon()
    if not poly.is_valid:
        poly = make_valid(poly)
    if poly.geom_type == "MultiPolygon":
        poly = max(poly.geoms, key=lambda g: g.area)
    return poly


def build_wagon_wheel(p: dict, r_outer: float) -> Polygon:
    """Wagon-wheel port: hub bore + narrow radial channels + circular outer bulbs.

    Each "spoke" is a thin channel (35 % of ``spoke_width``) running from the hub
    to a circular bulb of diameter ``spoke_width`` centred at ``r_hub +
    spoke_length``.  The channels keep the port connected (a disconnected union
    would be truncated downstream), and the bulbs are what distinguish a wagon
    wheel from a slotted grain.
    """
    n = int(round(p["n_spokes"]))
    r_hub = p["inner_diameter"] / 2.0
    w = p["spoke_width"]
    r_bulb = w / 2.0
    pitch = min(r_hub + p["spoke_length"], max(r_hub, r_outer - r_bulb))
    w_ch = 0.35 * w
    parts: list[Polygon] = [_circle(r_hub)]
    for k in range(n):
        theta = 2 * math.pi * k / n
        ca, sa = math.cos(theta), math.sin(theta)
        channel = [(r_hub * 0.5, -w_ch / 2), (pitch, -w_ch / 2),
                   (pitch, w_ch / 2), (r_hub * 0.5, w_ch / 2)]
        parts.append(Polygon([(x * ca - y * sa, x * sa + y * ca) for x, y in channel]))
        parts.append(_circle_at(pitch * ca, pitch * sa, r_bulb))
    port = unary_union(parts).intersection(_circle(r_outer))
    return _safe_polygon(port)


def build_slotted(p: dict, r_outer: float) -> Polygon:
    """Slotted port: central bore plus thin, straight-sided radial slots.

    Slots have constant width and a square outer end -- the simple slotted
    (stress-relief) geometry.  Rounded tips belong to the wagon wheel and
    tapered sides to the finocyl, so the three families stay geometrically
    distinct rather than being renames of one another.
    """
    n = int(round(p["n_slots"]))
    r_bore = p["inner_diameter"] / 2.0
    w = p["slot_width"]
    r_end = min(r_bore + p["slot_depth"], max(r_bore, r_outer - w / 2.0))
    parts: list[Polygon] = [_circle(r_bore)]
    for k in range(n):
        theta = 2 * math.pi * k / n
        ca, sa = math.cos(theta), math.sin(theta)
        slot = [(r_bore * 0.5, -w / 2), (r_end, -w / 2),
                (r_end, w / 2), (r_bore * 0.5, w / 2)]
        parts.append(Polygon([(x * ca - y * sa, x * sa + y * ca) for x, y in slot]))
    port = unary_union(parts).intersection(_circle(r_outer))
    return _safe_polygon(port)


def build_finocyl(p: dict, r_outer: float) -> Polygon:
    """Finocyl port: central bore plus radial fins that taper toward the tip.

    Fins are wide at the bore (``fin_width``) and narrow to ``fin_tip_ratio`` of
    that width at ``r_bore + fin_length``, which is what distinguishes a finocyl
    from a slotted grain.  The ratio is a fixed modelling choice (0.4).
    """
    n = int(round(p["n_fins"]))
    r_bore = p["inner_diameter"] / 2.0
    w = p["fin_width"]
    r_tip = min(r_bore + p["fin_length"], r_outer)
    w_tip = 0.4 * w
    parts: list[Polygon] = [_circle(r_bore)]
    for k in range(n):
        theta = 2 * math.pi * k / n
        ca, sa = math.cos(theta), math.sin(theta)
        fin = [(r_bore * 0.5, -w / 2), (r_tip, -w_tip / 2),
               (r_tip, w_tip / 2), (r_bore * 0.5, w / 2)]
        rot = [(x * ca - y * sa, x * sa + y * ca) for x, y in fin]
        parts.append(Polygon(rot))
    port = unary_union(parts).intersection(_circle(r_outer))
    return _safe_polygon(port)


def build_spherical(p: dict, r_outer: float) -> Polygon:
    """Cross-section through the equator of a spherical cavity."""
    return _circle(min(p["cavity_diameter"] / 2.0, r_outer))


def build_conical(p: dict, r_outer: float) -> Polygon:
    """Cross-section uses the base diameter (largest)."""
    return _circle(min(p["base_diameter"] / 2.0, r_outer))


def build_multi_perforated(p: dict, r_outer: float) -> Polygon:
    n = int(round(p["n_perforations"]))
    rp = p["perf_diameter"] / 2.0
    parts: list[Polygon] = [Point(0.0, 0.0).buffer(rp, quad_segs=64)]
    # ring radius: keep perforations inside the grain
    if n <= 1:
        return unary_union(parts)
    if n == 7:
        ring = 2.0 * rp
        k_ring = 6
    elif n == 19:
        ring = 2.0 * rp
        k_ring = 6
    else:
        k_ring = n - 1
        ring = 2.0 * rp
    for k in range(k_ring):
        theta = 2 * math.pi * k / k_ring
        parts.append(Point(ring * math.cos(theta), ring * math.sin(theta)).buffer(rp, quad_segs=64))
    if n == 19:
        ring2 = 4.0 * rp
        for k in range(12):
            theta = 2 * math.pi * k / 12 + math.pi / 12
            parts.append(Point(ring2 * math.cos(theta), ring2 * math.sin(theta)).buffer(rp, quad_segs=64))
    return unary_union(parts).intersection(_circle(r_outer))


def build_custom_polygon(p: dict, r_outer: float, polygon: list[tuple[float, float]] | None = None) -> Polygon:
    if not polygon:
        return _circle(r_outer * 0.5)
    poly = Polygon(polygon)
    if not poly.is_valid:
        poly = poly.buffer(0)
    return poly.intersection(_circle(r_outer))


_BUILDERS: dict[GrainFamily, Callable] = {
    GrainFamily.CYLINDRICAL: build_cylindrical,
    GrainFamily.TUBE: build_tube,
    GrainFamily.TUBE_INHIBITED: build_tube_inhibited,
    GrainFamily.STAR: build_star,
    GrainFamily.WAGON_WHEEL: build_wagon_wheel,
    GrainFamily.SLOTTED: build_slotted,
    GrainFamily.FINOCYL: build_finocyl,
    GrainFamily.SPHERICAL: build_spherical,
    GrainFamily.CONICAL: build_conical,
    GrainFamily.MULTI_PERFORATED: build_multi_perforated,
    GrainFamily.CUSTOM_POLYGON: build_custom_polygon,
}


def register_family(family: GrainFamily, builder: Callable, schema: dict | None = None) -> None:
    """Extension point: add a custom grain family."""
    _BUILDERS[family] = builder
    if schema is not None:
        FAMILY_PARAMS[family] = schema


def build_port(family: GrainFamily, params: dict, r_outer: float,
               polygon: list[tuple[float, float]] | None = None) -> Polygon | None:
    """Build the port polygon for a family (None for solid grains)."""
    if family not in _BUILDERS:
        raise KeyError(f"unknown grain family: {family}")
    if family == GrainFamily.CUSTOM_POLYGON:
        return build_custom_polygon(params, r_outer, polygon)
    return _BUILDERS[family](params, r_outer)
