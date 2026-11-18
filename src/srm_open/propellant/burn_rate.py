"""Burning-rate laws.

Each law is a small callable object with a consistent signature so the rest of
the code never branches on the law type.  New laws can be registered at
runtime with :func:`register_burn_rate_law`.
"""

from __future__ import annotations

import bisect
import math
from collections.abc import Callable
from typing import Protocol

from ..models.propellant import BurnRateLaw, BurnRateModel


class BurnRateLawProtocol(Protocol):
    def __call__(self, pressure: float, temperature: float = 293.15) -> float: ...


def saint_robert(model: BurnRateModel) -> BurnRateLawProtocol:
    a, n = model.a, model.n

    def r(p: float, T: float = 293.15) -> float:
        return a * p**n

    return r


def saint_robert_temp(model: BurnRateModel) -> BurnRateLawProtocol:
    a, n, sigma, Tref = model.a, model.n, model.sigma_p, model.reference_temperature

    def r(p: float, T: float = 293.15) -> float:
        return a * p**n * math.exp(sigma * (T - Tref))

    return r


def constant(model: BurnRateModel) -> BurnRateLawProtocol:
    a = model.a

    def r(p: float, T: float = 293.15) -> float:
        return a

    return r


def table(model: BurnRateModel) -> BurnRateLawProtocol:
    p_tab = list(model.pressure_table or [])
    r_tab = list(model.rate_table or [])

    def r(p: float, T: float = 293.15) -> float:
        if p <= p_tab[0]:
            return r_tab[0]
        if p >= p_tab[-1]:
            return r_tab[-1]
        i = bisect.bisect_left(p_tab, p)
        p0, p1 = p_tab[i - 1], p_tab[i]
        r0, r1 = r_tab[i - 1], r_tab[i]
        frac = (p - p0) / (p1 - p0)
        return r0 + frac * (r1 - r0)

    return r


_REGISTRY: dict[BurnRateLaw, Callable[[BurnRateModel], BurnRateLawProtocol]] = {
    BurnRateLaw.SAINT_ROBERT: saint_robert,
    BurnRateLaw.SAINT_ROBERT_TEMP: saint_robert_temp,
    BurnRateLaw.CONSTANT: constant,
    BurnRateLaw.TABLE: table,
}


def register_burn_rate_law(law: BurnRateLaw, builder) -> None:
    """Extension point: add a custom burning-rate law."""
    _REGISTRY[law] = builder


def build_burn_rate(model: BurnRateModel) -> BurnRateLawProtocol:
    if model.law not in _REGISTRY:
        raise KeyError(f"unknown burn-rate law: {model.law}")
    return _REGISTRY[model.law](model)
