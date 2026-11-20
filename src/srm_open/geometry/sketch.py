"""2-D sketch generation and 3-D solid construction.

* :func:`segment_outline` returns the ordered port outline points (the "2-D
  sketch") for a segment, suitable for display or for editing in the UI.
* :func:`grain_to_step` / :func:`grain_to_stl` build a 3-D solid.  When CadQuery
  (OpenCASCADE) is available the export is a true B-rep STEP; otherwise a
  triangulated mesh is produced from the shapely polygons (pure-Python fallback,
  always available offline).
"""

from __future__ import annotations

from pathlib import Path

from ..models.grain import GrainGeometry, GrainSegment
from .analysis import outer_circle, port_polygon


def segment_outline(segment: GrainSegment, n: int | None = None) -> list[tuple[float, float]]:
    """Ordered port outline points for the sketch.

    A multi-perforated port has several disjoint boundaries; they are returned
    end to end in one list, which is what a sketch renderer that draws polylines
    wants.  Use :func:`segment_outlines` when the components must stay separate.
    """
    return [pt for ring in segment_outlines(segment) for pt in ring]


def segment_outlines(segment: GrainSegment) -> list[list[tuple[float, float]]]:
    """One point ring per connected port component (outer circle for a solid)."""
    port = port_polygon(segment)
    if port.is_empty:
        return [[(x, y) for x, y in outer_circle(segment).exterior.coords]]
    geoms = port.geoms if port.geom_type == "MultiPolygon" else [port]
    return [[(float(x), float(y)) for x, y in g.exterior.coords] for g in geoms]


def sketch_payload(grain: GrainGeometry) -> dict:
    """A serialisable 2-D sketch description (list of segment outlines)."""
    segs = []
    for i, seg in enumerate(grain.segments):
        outer = outer_circle(seg)
        segs.append(
            {
                "index": i,
                "family": seg.family.value,
                "length": seg.length,
                "burn_mode": seg.burn_mode.value,
                "outer": [(float(x), float(y)) for x, y in outer.exterior.coords],
                "port": segment_outline(seg),
                "port_rings": segment_outlines(seg),
                "params": seg.params,
            }
        )
    return {"segments": segs}


# ---------------------------------------------------------------------------
# 3-D construction
# ---------------------------------------------------------------------------
def _segment_solids(segment: GrainSegment):
    """Return (outer_polygon, port_polygon) for a segment."""
    return outer_circle(segment), port_polygon(segment)


def grain_to_step(grain: GrainGeometry, path: str | Path) -> Path:
    """Export the grain solid to STEP using CadQuery (OpenCASCADE)."""
    import cadquery as cq  # local import; optional dependency

    solids = []
    z = 0.0
    for seg in grain.segments:
        outer, port = _segment_solids(seg)
        outer_pts = [(float(x), float(y)) for x, y in outer.exterior.coords[:-1]]
        solid = cq.Workplane("XY").polyline(outer_pts).close().extrude(seg.length)
        if not port.is_empty:
            geoms = port.geoms if port.geom_type == "MultiPolygon" else [port]
            for g in geoms:
                pts = [(float(x), float(y)) for x, y in g.exterior.coords[:-1]]
                cutter = cq.Workplane("XY").polyline(pts).close().extrude(seg.length)
                solid = solid.cut(cutter)
        solid = solid.translate((0, 0, z))
        solids.append(solid.val())
        z += seg.length
    result = solids[0]
    for s in solids[1:]:
        result = result.fuse(s)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    cq.exporters.export(cq.Workplane("XY").newObject([result]), str(path))
    return path


def grain_to_stl(grain: GrainGeometry, path: str | Path, segments_per_circle: int = 96) -> Path:
    """Export a triangulated STL (pure-Python fallback; no CadQuery needed)."""
    triangles: list[tuple[tuple[float, float, float], ...]] = []
    z = 0.0
    for seg in grain.segments:
        outer, port = _segment_solids(seg)
        z0, z1 = z, z + seg.length
        # side walls (outer) and caps, port walls
        _extrude_polygon_into(outer, z0, z1, triangles, reverse=False)
        if not port.is_empty:
            geoms = port.geoms if port.geom_type == "MultiPolygon" else [port]
            for g in geoms:
                _extrude_polygon_into(g, z0, z1, triangles, reverse=True)
        z = z1
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="ascii") as fh:
        fh.write("solid grain\n")
        for tri in triangles:
            fh.write(" facet normal 0 0 0\n  outer loop\n")
            for x, y, zz in tri:
                fh.write(f"   vertex {x:.6e} {y:.6e} {zz:.6e}\n")
            fh.write("  endloop\n endfacet\n")
        fh.write("endsolid grain\n")
    return path


def grain_to_pointcloud(grain: GrainGeometry, path: str | Path,
                        n_axial: int = 20, n_radial: int = 64) -> Path:
    """Export the grain *propellant* volume as a plain ``x y z`` point cloud.

    Points are sampled on the outer wall, the port wall and both end faces so the
    cloud outlines the solid a designer can re-import or compare against a scan.
    Sampling is stratified (fixed axial/angular stations), not random, so the
    file is deterministic.  Output is a header-less ASCII ``x y z`` triple per
    line, readable by MeshLab / CloudCompare / numpy.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pts: list[tuple[float, float, float]] = []
    z = 0.0
    for seg in grain.segments:
        outer, port = _segment_solids(seg)
        z0, z1 = z, z + seg.length
        zs = [z0 + (z1 - z0) * k / max(n_axial - 1, 1) for k in range(n_axial)]
        polys = [outer]
        if not port.is_empty:
            polys.extend(port.geoms if port.geom_type == "MultiPolygon" else [port])
        for zz in zs:
            for poly in polys:
                coords = list(poly.exterior.coords)[:-1]
                if not coords:
                    continue
                step = max(len(coords) // n_radial, 1)
                for (x, y) in coords[::step]:
                    pts.append((x, y, zz))
        z = z1
    with open(path, "w", encoding="ascii") as fh:
        fh.write(f"# srm_open point cloud: {len(pts)} points (x y z, metres)\n")
        for x, y, zz in pts:
            fh.write(f"{x:.6e} {y:.6e} {zz:.6e}\n")
    return path


def _extrude_polygon_into(poly, z0: float, z1: float, out: list, reverse: bool) -> None:
    coords = list(poly.exterior.coords)[:-1]
    n = len(coords)
    # walls
    for i in range(n):
        x0, y0 = coords[i]
        x1, y1 = coords[(i + 1) % n]
        quad = [
            (x0, y0, z0), (x1, y1, z0), (x1, y1, z1), (x0, y0, z1),
        ]
        if reverse:
            out.append((quad[0], quad[2], quad[1]))
            out.append((quad[0], quad[3], quad[2]))
        else:
            out.append((quad[0], quad[1], quad[2]))
            out.append((quad[0], quad[2], quad[3]))
    # caps (fan triangulation from centroid)
    cx = sum(c[0] for c in coords) / n
    cy = sum(c[1] for c in coords) / n
    for i in range(n):
        x0, y0 = coords[i]
        x1, y1 = coords[(i + 1) % n]
        if reverse:
            out.append(((cx, cy, z0), (x1, y1, z0), (x0, y0, z0)))
            out.append(((cx, cy, z1), (x0, y0, z1), (x1, y1, z1)))
        else:
            out.append(((cx, cy, z0), (x0, y0, z0), (x1, y1, z0)))
            out.append(((cx, cy, z1), (x1, y1, z1), (x0, y0, z1)))


def geometry_backend_available() -> bool:
    try:
        import cadquery  # noqa: F401

        return True
    except Exception:
        return False
