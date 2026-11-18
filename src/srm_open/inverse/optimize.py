"""Optimizer wrappers used by inverse design.

A single :func:`minimize` entry point with a few interchangeable algorithms so
the choice is configuration, not code.  ``scipy`` is used when available;
a deterministic Nelder-Mead fallback is included so the package has no hard
dependency for basic use.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np


@dataclass
class OptimizeResult:
    x: np.ndarray
    fun: float
    success: bool
    nit: int
    message: str = ""
    history: list[float] = field(default_factory=list)


def minimize(fun: Callable[[np.ndarray], float], x0: np.ndarray,
             bounds: list[tuple[float, float]] | None = None,
             method: str = "cma", max_iter: int = 400, seed: int = 12345,
             callback: Callable[[np.ndarray], None] | None = None) -> OptimizeResult:
    x0 = np.asarray(x0, dtype=float)
    history: list[float] = []

    def wrapped(x):
        val = float(fun(np.asarray(x, dtype=float)))
        history.append(val)
        if callback is not None:
            callback(np.asarray(x, dtype=float))
        return val

    lo = np.array([b[0] for b in bounds]) if bounds else None
    hi = np.array([b[1] for b in bounds]) if bounds else None

    method = (method or "cma").lower()
    try:
        from scipy.optimize import differential_evolution
        from scipy.optimize import minimize as scipy_minimize

        if method in ("de", "cma", "differential_evolution"):
            res = differential_evolution(
                wrapped, list(zip(lo, hi)) if bounds else None,
                maxiter=max_iter, seed=seed, tol=1e-8, polish=True,
                strategy="best1bin", mutation=(0.3, 1.0), recombination=0.7,
            )
            return OptimizeResult(np.asarray(res.x), float(res.fun), bool(res.success),
                                  int(res.nit), str(res.message), history)
        if method in ("nelder_mead", "nm", "neldermead"):
            res = scipy_minimize(wrapped, x0, method="Nelder-Mead",
                                 bounds=bounds, options={"maxiter": max_iter, "xatol": 1e-9,
                                                        "fatol": 1e-12})
            return OptimizeResult(np.asarray(res.x), float(res.fun), bool(res.success),
                                  int(res.nit), str(res.message), history)
        if method in ("lbfgsb", "lbfgs"):
            res = scipy_minimize(wrapped, x0, method="L-BFGS-B", bounds=bounds,
                                 options={"maxiter": max_iter})
            return OptimizeResult(np.asarray(res.x), float(res.fun), bool(res.success),
                                  int(res.nit), str(res.message), history)
    except Exception:  # pragma: no cover - fall back to pure python
        pass

    return _nelder_mead_python(wrapped, x0, lo, hi, max_iter, history)


def _nelder_mead_python(fun, x0, lo, hi, max_iter, history) -> OptimizeResult:
    n = len(x0)
    step = np.where(x0 != 0, 0.05 * np.abs(x0), 1e-3)

    def clip(x):
        if lo is None:
            return x
        return np.minimum(np.maximum(x, lo), hi)

    simplex = [x0]
    for i in range(n):
        p = x0.copy()
        p[i] += step[i]
        simplex.append(clip(p))
    simplex = [np.asarray(s) for s in simplex]
    fvals = [fun(s) for s in simplex]
    for _ in range(max_iter):
        order = np.argsort(fvals)
        simplex = [simplex[i] for i in order]
        fvals = [fvals[i] for i in order]
        if np.max(np.abs(np.array(fvals[1:]) - fvals[0])) < 1e-12:
            break
        centroid = np.mean(simplex[:-1], axis=0)
        xr = clip(centroid + (centroid - simplex[-1]))
        fr = fun(xr)
        if fr < fvals[0]:
            xe = clip(centroid + 2.0 * (xr - centroid))
            fe = fun(xe)
            simplex[-1], fvals[-1] = (xe, fe) if fe < fr else (xr, fr)
        elif fr < fvals[-2]:
            simplex[-1], fvals[-1] = xr, fr
        else:
            xc = clip(centroid + 0.5 * (simplex[-1] - centroid))
            fc = fun(xc)
            if fc < fvals[-1]:
                simplex[-1], fvals[-1] = xc, fc
            else:
                for i in range(1, n + 1):
                    simplex[i] = clip(simplex[0] + 0.5 * (simplex[i] - simplex[0]))
                    fvals[i] = fun(simplex[i])
    best = int(np.argmin(fvals))
    return OptimizeResult(simplex[best], float(fvals[best]), True, max_iter,
                          "pure-python Nelder-Mead", history)
