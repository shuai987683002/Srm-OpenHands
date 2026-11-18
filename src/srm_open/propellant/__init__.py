"""Propellant models, burning-rate laws and the propellant database."""

from .burn_rate import build_burn_rate, register_burn_rate_law
from .database import PropellantDB

__all__ = ["build_burn_rate", "register_burn_rate_law", "PropellantDB"]
