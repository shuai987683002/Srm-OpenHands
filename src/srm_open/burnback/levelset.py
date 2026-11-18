"""Grid-based burn-back (channel B), independent from the vector method.

This channel discretises the cross-section on a grid and advances the burning
front with an eikonal / level-set formulation.  It is deliberately a *different
algorithm* from the shapely Minkowski-dilation channel so that agreement between
the two is meaningful cross-validation.

Method
------
Let ``phi`` be the geodesic distance (in metres) from the initial port surface,
computed *inside the propellant only* so the front naturally stops at the
inhibited outer wall.  The set of points consumed after burning ``e`` metres is
``phi <= e``; the burning surface is the ``phi = e`` contour and the burning
area at that instant is (contour length) x (segment length).

The geodesic distance is obtained by a fast-marching / Dijkstra solve on the
grid graph (8-connected, axis steps ``dx``, diagonal steps ``dx*sqrt(2)``), which
is exactly the first-arrival time of the normal-advancing front.

Boundary / degeneracy handling
------------------------------
* inhibited outer wall -> mask the grid to the propellant region, so fronts stop;
* port touching the wall   -> zero web;
* multiple port components -> all fronts advance together (multi-source);
* solid grain              -> treated as end burning (area from the end faces).
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass

import numpy as np

from ..geometry.analysis import port_polygon
from ..models.grain import BurnMode, GrainSegment


@dataclass
class LevelSetResult:
    burn_distance: np.ndarray
    burn_area: np.ndarray
    nx: int
    ny: int
    notes: list[str]


def _rasterize(poly, xx, yy):
    from shapely.geometry import Point
    from shapely.prepared import prep

    if poly is None or poly.is_empty:
        return np.zeros(xx.shape, dtype=bool)
    prepared = prep(poly)
    flat = np.array([prepared.contains(Point(x, y)) for x, y in zip(xx.ravel(), yy.ravel())])
    return flat.reshape(xx.shape)


def _geodesic_distance(prop_mask: np.ndarray, sources: np.ndarray, dx: float) -> np.ndarray:
    """First-arrival (geodesic) distance from ``sources`` through ``prop_mask``."""
    ny, nx = prop_mask.shape
    INF = np.inf
    dist = np.full((ny, nx), INF)
    pq: list[tuple[float, int, int]] = []
    ys, xs = np.where(sources & prop_mask)
    for y, x in zip(ys, xs):
        dist[y, x] = 0.0
        heapq.heappush(pq, (0.0, int(y), int(x)))
    diag = dx * np.sqrt(2.0)
    neigh = [(-1, 0, dx), (1, 0, dx), (0, -1, dx), (0, 1, dx),
             (-1, -1, diag), (-1, 1, diag), (1, -1, diag), (1, 1, diag)]
    while pq:
        d, y, x = heapq.heappop(pq)
        if d > dist[y, x]:
            continue
        for dy, dxp, step in neigh:
            yy, xxi = y + dy, x + dxp
            if 0 <= yy < ny and 0 <= xxi < nx and prop_mask[yy, xxi]:
                nd = d + step
                if nd < dist[yy, xxi]:
                    dist[yy, xxi] = nd
                    heapq.heappush(pq, (nd, yy, xxi))
    return dist


def _contour_area(dist: np.ndarray, level: float, dx: float, length: float) -> float:
    """Burning area = length of the ``dist = level`` contour times axial length.

    Marching squares (``skimage.measure.find_contours``) gives sub-cell accuracy;
    infinite distances (unreached cells) are replaced by a large finite value so
    they never generate spurious contours.
    """
    from skimage import measure

    field = np.where(np.isfinite(dist), dist, 1e6)
    if level <= 0:
        level = 1e-9
    total = 0.0
    for c in measure.find_contours(field, level):
        if len(c) < 2:
            continue
        d = np.diff(c, axis=0)
        total += float(np.sum(np.hypot(d[:, 0], d[:, 1]))) * dx
    return total * length


def _combine_signed(prop_mask, port_mask, dx):
    """Signed geodesic field: +d inside propellant, -d inside the port."""
    from scipy.ndimage import binary_dilation

    port_adj_to_prop = binary_dilation(prop_mask, iterations=1) & port_mask
    prop_adj_to_port = binary_dilation(port_mask, iterations=1) & prop_mask

    d_prop = _geodesic_distance(prop_mask, prop_adj_to_port, dx)
    d_port = _geodesic_distance(port_mask, port_adj_to_prop, dx)
    field = np.full(prop_mask.shape, np.nan)
    field[prop_mask] = d_prop[prop_mask]
    field[port_mask] = -d_port[port_mask]
    return field


def levelset_burnback(segment: GrainSegment, e_max: float | None = None,
                      n_snapshots: int = 40, nx: int = 240, ny: int = 240) -> LevelSetResult:
    r_out = segment.outer_diameter / 2.0
    domain = r_out * 1.05
    xs = np.linspace(-domain, domain, nx)
    ys = np.linspace(-domain, domain, ny)
    xx, yy = np.meshgrid(xs, ys)
    dx = xs[1] - xs[0]

    inside_outer = xx**2 + yy**2 <= r_out**2
    port = port_polygon(segment)
    port_mask = _rasterize(port, xx, yy) & inside_outer
    notes: list[str] = []

    if segment.burn_mode == BurnMode.END or (port.is_empty and segment.burn_mode != BurnMode.EXTERNAL):
        r_o = r_out
        inner = np.sqrt(port.area / np.pi) if not port.is_empty else 0.0
        area0 = np.pi * max(r_o**2 - inner**2, 0.0)
        e_grid = np.linspace(0.0, segment.length, n_snapshots)
        area = np.full(n_snapshots, area0)
        return LevelSetResult(e_grid, area, nx, ny, notes or ["end burning"])

    prop_mask = inside_outer & ~port_mask
    from scipy.ndimage import binary_dilation

    if segment.burn_mode == BurnMode.EXTERNAL:
        # void = outside the grain (front moves inward); port is inhibited
        sources = prop_mask & ~_erode(prop_mask)
        d_prop = _geodesic_distance(prop_mask, sources, dx)
        field = np.where(prop_mask, d_prop, -1e3)
        web = float(np.max(d_prop[prop_mask])) if prop_mask.any() else 0.0
    else:
        # internal burning: void = port (negative); outside the grain is inhibited
        port_shell = binary_dilation(port_mask, iterations=1) & prop_mask
        d_prop = _geodesic_distance(prop_mask, port_shell, dx)
        port_adj = binary_dilation(prop_mask, iterations=1) & port_mask
        d_port = _geodesic_distance(port_mask, port_adj, dx) if port_mask.any() else np.zeros_like(d_prop)
        # propellant: +distance; port interior: -distance; outside the grain: +large
        # (a large positive value prevents a spurious front at the inhibited wall)
        field = np.where(prop_mask, d_prop, np.where(port_mask, -d_port, 1e3))
        web = float(np.max(d_prop[prop_mask])) if prop_mask.any() else 0.0

    if web <= 0 or not np.isfinite(web):
        notes.append("zero web thickness")
        e_grid = np.linspace(0.0, r_out, n_snapshots)
        return LevelSetResult(e_grid, np.zeros(n_snapshots), nx, ny, notes)

    if e_max is None:
        e_max = web
    e_grid = np.linspace(0.0, e_max, n_snapshots)
    area = np.array([_contour_area(field, float(e), dx, segment.length) for e in e_grid])
    return LevelSetResult(e_grid, area, nx, ny, notes)


def _shell(mask: np.ndarray) -> np.ndarray:
    """Boundary shell of a mask (outer edge cells)."""
    from scipy.ndimage import binary_dilation

    return binary_dilation(mask, iterations=1) & ~mask


def _erode(mask: np.ndarray) -> np.ndarray:
    from scipy.ndimage import binary_erosion

    return binary_erosion(mask)


def _geodesic_from_outer(prop_mask: np.ndarray, dx: float) -> np.ndarray:
    """Geodesic distance from the outer boundary of the propellant region."""
    from scipy.ndimage import binary_dilation

    outer_shell = binary_dilation(prop_mask, iterations=2) & ~prop_mask
    sources = binary_dilation(outer_shell, iterations=1) & prop_mask
    return _geodesic_distance(prop_mask, sources, dx)


def levelset_grain_burnback(grain, e_max=None, n_snapshots=40, nx=240, ny=240):
    """Sum level-set A_b(e) over segments on a common burn-distance grid."""
    results = [levelset_burnback(s, e_max=None, n_snapshots=n_snapshots, nx=nx, ny=ny)
               for s in grain.segments]
    webs = [float(r.burn_distance[-1]) for r in results]
    web_max = max(webs) if webs else 0.0
    e_grid = np.linspace(0.0, e_max if e_max else web_max, n_snapshots)
    area = np.zeros_like(e_grid)
    for r in results:
        area += np.interp(e_grid, r.burn_distance, r.burn_area, left=r.burn_area[0], right=0.0)
    notes = [n for r in results for n in r.notes]
    return LevelSetResult(e_grid, area, nx, ny, notes)
