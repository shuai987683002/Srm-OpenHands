"""Burn-back engine: geometric (vector) and level-set (grid) channels."""

from .engine import burn_area_at, burnback_geometric, compute_burnback, crosscheck
from .geometric import BurnbackCurve
from .levelset import levelset_burnback, levelset_grain_burnback

__all__ = [
    "burn_area_at", "burnback_geometric", "compute_burnback", "crosscheck",
    "BurnbackCurve", "levelset_burnback", "levelset_grain_burnback",
]
