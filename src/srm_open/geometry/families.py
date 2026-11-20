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

from shapely.geometry import MultiPolygon, Point, Polygon
from shapely.ops import unary_union
from shapely.validation import make_valid

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
        "web_fraction": (False, "-", "minimum web between holes as a fraction of "
                                     "the hole diameter (default 0.15)", 0.15),
    },
    GrainFamily.WEDGE: {
        "n_wedges": (True, "-", "number of wedge (sector) cavities", None),
        "inner_diameter": (True, "m", "hub bore diameter the wedges open from", None),
        "tip_diameter": (True, "m", "diameter of the outer arc of each wedge", None),
        "wedge_angle": (True, "deg", "full included angle of one wedge", 30.0),
    },
    GrainFamily.AIRFOIL: {
        "n_blades": (True, "-", "number of airfoil blades", None),
        "inner_diameter": (True, "m", "hub bore diameter the blades open from", None),
        "blade_chord": (True, "m", "radial chord of one blade", None),
        "thickness_ratio": (False, "-", "max thickness / chord (NACA t/c)", 0.12),
    },
    GrainFamily.EMBEDDED_WIRE: {
        "inner_diameter": (True, "m", "central bore diameter", None),
        "n_wires": (True, "-", "number of radial metal wires", None),
        "wire_diameter": (True, "m", "wire diameter", None),
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


def max_star_half_angle(n: int, r_tip: float, r_valley: float) -> float:
    """Largest ``point_half_angle`` [deg] that still changes the star outline.

    The valley half-width is ``phi = asin((r_tip/r_valley) sin a) - a`` and it is
    clamped at the symmetric value ``pi/n``.  Once the requested angle pushes
    ``phi`` past that limit every larger angle produces the same symmetric star,
    so the parameter becomes unidentifiable.  For the usual 3:1 tip-to-valley
    ratio this ceiling is only ~10-13 deg, well below the 25-30 deg values that
    look natural, which is why the templates use low angles.
    """
    if n <= 0 or r_valley <= 0.0 or r_tip <= r_valley:
        return 0.0

    def phi_of(a_deg: float) -> float:
        a = math.radians(a_deg)
        s = (r_tip / r_valley) * math.sin(a)
        return math.asin(s) - a if s < 1.0 else math.pi / 2.0 - a

    limit = math.pi / n
    hi = math.degrees(math.asin(min(r_valley / r_tip, 1.0)))
    if phi_of(hi * (1.0 - 1e-9)) <= limit:
        return hi * (1.0 - 1e-9)
    lo = 0.0
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if phi_of(mid) > limit:
            hi = mid
        else:
            lo = mid
    return lo


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
    return repair_polygon(port)


def repair_polygon(poly):
    """Repair a port outline, keeping every component.

    A port may legitimately be *disconnected* -- seven perforations are seven
    separate voids, not one.  Collapsing a MultiPolygon to its largest part
    silently drops most of the perforations and halves the burning area, so the
    components are kept and only invalid geometry is repaired.

    This is the single implementation; ``geometry.analysis`` re-exports it
    because the burn-back path needs exactly the same semantics.
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
    # The bulbs must stop short of the outer surface: clamping them to
    # ``r_outer - r_bulb`` puts the port exactly on the case wall, which leaves a
    # zero web and a degenerate burn-back.  Keep a tenth of the radius as web.
    r_limit = r_outer - r_bulb - 0.1 * r_outer
    pitch = min(r_hub + p["spoke_length"], max(r_hub, r_limit))
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
    return repair_polygon(port)


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
    return repair_polygon(port)


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
    return repair_polygon(port)


def build_spherical(p: dict, r_outer: float) -> Polygon:
    """Cross-section through the equator of a spherical cavity."""
    return _circle(min(p["cavity_diameter"] / 2.0, r_outer))


def build_conical(p: dict, r_outer: float) -> Polygon:
    """Cross-section uses the base diameter (largest)."""
    return _circle(min(p["base_diameter"] / 2.0, r_outer))


def build_multi_perforated(p: dict, r_outer: float) -> Polygon:
    """Centre hole plus rings of equal holes, separated by a real web.

    The holes must not touch: a ring radius of exactly ``2*rp`` puts the centre
    hole tangent to all six ring holes and each ring hole tangent to its
    neighbours. The web is then zero, the port merges into one blob within the
    first millimetre of burn, and the burning perimeter *drops* instead of
    growing. ``web_fraction`` sets the minimum web as a fraction of the hole
    diameter (default 0.15), so the ring pitch is ``2*rp*(1+web_fraction)``.
    """
    n = int(round(p["n_perforations"]))
    rp = p["perf_diameter"] / 2.0
    web = max(float(p.get("web_fraction", 0.15)), 0.0)
    pitch = 2.0 * rp * (1.0 + web)
    parts: list[Polygon] = [Point(0.0, 0.0).buffer(rp, quad_segs=64)]
    if n <= 1:
        return unary_union(parts).intersection(_circle(r_outer))
    if n == 19:
        rings = ((6, pitch), (12, 2.0 * pitch))
    else:
        rings = ((n - 1, pitch),)
    for k_ring, ring in rings:
        for k in range(k_ring):
            theta = 2 * math.pi * k / k_ring
            parts.append(Point(ring * math.cos(theta),
                               ring * math.sin(theta)).buffer(rp, quad_segs=64))
    return unary_union(parts).intersection(_circle(r_outer))


def build_wedge(p: dict, r_outer: float) -> Polygon:
    """Wedge (sector) port: N constant-angle cavities with radial straight flanks.

    Each cavity is a sector of full included angle ``wedge_angle`` running from
    the hub circle to an outer *arc* at ``tip_diameter``.  Constant angular width
    keeps the flanks radial, which is what separates a wedge from a star (sharp
    tips), a slotted grain (parallel-sided slots) and a finocyl (tapering fins
    with flat ends).  The outer end is an arc rather than a flat tip, so the
    cavity stays rounded where it approaches the case wall.
    """
    n = int(round(p["n_wedges"]))
    r_hub = p["inner_diameter"] / 2.0
    r_tip = min(p["tip_diameter"] / 2.0, r_outer)
    half = math.radians(p["wedge_angle"]) / 2.0

    parts: list[Polygon] = [_circle(r_hub)]
    if n <= 0 or r_tip <= r_hub or half <= 0.0:
        return repair_polygon(_circle(r_hub))
    # keep neighbouring wedges from overlapping: the full angle cannot exceed the
    # pitch, or the cavities merge into an annulus
    half = min(half, 0.5 * math.pi / n)

    n_arc = max(int(12 * half / math.pi * 2), 6)
    for k in range(n):
        theta = 2 * math.pi * k / n
        pts: list[tuple[float, float]] = []
        for j in range(n_arc + 1):  # hub arc, increasing angle
            a = theta - half + 2.0 * half * j / n_arc
            pts.append((r_hub * math.cos(a), r_hub * math.sin(a)))
        for j in range(n_arc + 1):  # outer arc, decreasing angle
            a = theta + half - 2.0 * half * j / n_arc
            pts.append((r_tip * math.cos(a), r_tip * math.sin(a)))
        parts.append(Polygon(pts))

    port = unary_union(parts).intersection(_circle(r_outer))
    return repair_polygon(port)


def _naca_half_thickness(x: float, thickness_ratio: float) -> float:
    """NACA 4-digit symmetric half-thickness (closed trailing edge)."""
    return 5.0 * thickness_ratio * (
        0.2969 * math.sqrt(max(x, 0.0))
        - 0.1260 * x
        - 0.3516 * x**2
        + 0.2843 * x**3
        - 0.1036 * x**4
    )


def build_airfoil(p: dict, r_outer: float) -> Polygon:
    """Airfoil (blade) port: N symmetric NACA 4-digit profiles on a hub bore.

    Each blade lies along a radius: chord along the radial direction, thickness
    in the tangential direction, maximum thickness ``thickness_ratio * chord``.
    The smooth thickness distribution avoids the sharp concave corners of a star
    or slot, which is the reason airfoil ports are chosen where stress
    concentration in the grain or case bond matters.
    """
    n = int(round(p["n_blades"]))
    r_hub = p["inner_diameter"] / 2.0
    chord = p["blade_chord"]
    ratio = p["thickness_ratio"]
    r_tip = min(r_hub + chord, r_outer)
    chord = max(r_tip - r_hub, 0.0)

    parts: list[Polygon] = [_circle(r_hub)]
    if n <= 0 or chord <= 0.0 or ratio <= 0.0:
        return repair_polygon(_circle(r_hub))

    # The blade root is buried inside the hub bore.  A root that merely touched
    # the hub circle would meet it at a single point, and the union of the parts
    # would then be a *disconnected* MultiPolygon -- which the safety net reduces
    # to one component, silently dropping every blade but the first.
    r_root = max(r_hub - 0.25 * chord, 0.0)
    span = r_tip - r_root
    n_pts = 40
    xs = [0.5 * (1.0 - math.cos(math.pi * i / n_pts)) for i in range(n_pts + 1)]
    for k in range(n):
        theta = 2 * math.pi * k / n
        ca, sa = math.cos(theta), math.sin(theta)
        upper: list[tuple[float, float]] = []
        lower: list[tuple[float, float]] = []
        for x in xs:
            r = r_root + x * span
            y = _naca_half_thickness(x, ratio) * span
            upper.append((r, y))
            lower.append((r, -y))
        # close the trailing edge so the polygon has no zero-width seam
        outline = upper + lower[::-1][1:]
        # local (radial, tangential) -> global
        rot = [(rr * ca - tt * sa, rr * sa + tt * ca) for rr, tt in outline]
        parts.append(Polygon(rot))

    port = unary_union(parts).intersection(_circle(r_outer))
    return repair_polygon(port)


def build_embedded_wire(p: dict, r_outer: float) -> Polygon:
    """Port for a wire-reinforced grain: the bore only.

    The radial metal wires are *not* voids -- they stay in place while the
    propellant recedes, so they are not part of the port polygon.  Their
    contribution to the burning area is added analytically in
    :func:`srm_open.geometry.analysis.burning_area` (a protruding wire exposes
    its cylindrical side, ``2*pi*r_w*e`` per wire, which is what makes the
    thrust progressive).
    """
    d = p.get("inner_diameter", 0.0)
    return _circle(d / 2.0) if d > 0.0 else Polygon()


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
    GrainFamily.WEDGE: build_wedge,
    GrainFamily.AIRFOIL: build_airfoil,
    GrainFamily.EMBEDDED_WIRE: build_embedded_wire,
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
