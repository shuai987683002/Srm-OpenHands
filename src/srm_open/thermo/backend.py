"""Thermochemistry facade with pluggable backends.

Backends
--------
* ``gibbs``     -- built-in Gibbs minimiser (:mod:`srm_open.thermo.gibbs`)
* ``rocketcea`` -- optional wrapper around the NASA CEA Fortran code
* ``cantera``   -- optional wrapper around Cantera
* ``direct``    -- user typed c*, gamma, T_c

``auto`` tries gibbs (always available) and prefers an external backend only
when explicitly requested.  Results always carry the ``mode`` used so they stay
traceable.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..constants import R_UNIVERSAL
from ..models.propellant import PropellantModel, ThermalChem
from .gibbs import GibbsEquilibrium
from .species import SpeciesDatabase

_DB: SpeciesDatabase | None = None


def default_db() -> SpeciesDatabase:
    global _DB
    if _DB is None:
        _DB = SpeciesDatabase.load_default()
    return _DB


@dataclass
class ThermoResult:
    c_star: float
    gamma: float
    molecular_weight: float
    chamber_temperature: float
    chamber_pressure: float
    mode: str
    converged: bool = True
    mole_fractions: dict[str, float] | None = None


def gamma_from_cp(cp_molar: float) -> float:
    cv = cp_molar - R_UNIVERSAL
    return cp_molar / cv if cv > 0 else 1.2


def c_star_from(gamma: float, chamber_temperature: float, molecular_weight: float,
                p_chamber: float, p_throat_ratio: float = 1.0) -> float:
    """Characteristic velocity from frozen/equilibrium gamma.

    ``c* = sqrt(R T_c / M) / Gamma`` with
    ``Gamma = sqrt(gamma) (2/(gamma+1))^((gamma+1)/(2(gamma-1)))``.
    """
    gamma_term = math.sqrt(gamma) * (2.0 / (gamma + 1.0)) ** ((gamma + 1.0) / (2.0 * (gamma - 1.0)))
    return math.sqrt(R_UNIVERSAL * chamber_temperature / molecular_weight) / gamma_term


def evaluate(propellant: PropellantModel, pressure: float, backend: str = "auto") -> ThermoResult:
    """Compute c*, gamma, M, T_c for a propellant at a chamber pressure."""
    backend = (backend or "auto").lower()

    if backend in ("auto", "direct") and propellant.thermo.mode == "direct":
        t = propellant.thermo
        if None in (t.c_star, t.gamma, t.chamber_temperature, t.molecular_weight):
            raise ValueError(
                "thermo mode 'direct' requires c_star, gamma, chamber_temperature and "
                "molecular_weight to be supplied"
            )
        return ThermoResult(t.c_star, t.gamma, t.molecular_weight, t.chamber_temperature,
                            pressure, "direct")

    if backend in ("auto", "gibbs"):
        if propellant.composition and propellant.heat_of_formation is not None:
            return _evaluate_gibbs(propellant, pressure)

    if backend == "rocketcea":
        return _evaluate_rocketcea(propellant, pressure)
    if backend == "cantera":
        return _evaluate_cantera(propellant, pressure)

    # last resort: a direct record already computed
    if propellant.thermo.c_star is not None:
        t = propellant.thermo
        return ThermoResult(t.c_star, t.gamma, t.molecular_weight, t.chamber_temperature,
                            pressure, "direct")
    raise ValueError(
        "cannot evaluate thermochemistry: provide composition+heat_of_formation "
        "(gibbs) or a direct thermo record"
    )


def _evaluate_gibbs(propellant: PropellantModel, pressure: float) -> ThermoResult:
    solver = GibbsEquilibrium(default_db())
    res = solver.solve_adiabatic(
        pressure=pressure,
        composition=propellant.composition,
        formation_enthalpy_per_kg=propellant.heat_of_formation or 0.0,
    )
    # c* for equilibrium flow uses the equilibrium gamma here; both frozen and
    # equilibrium c* are within a few percent for typical propellants.
    cs = c_star_from(res.gamma, res.temperature, res.molecular_weight, pressure)
    return ThermoResult(cs, res.gamma, res.molecular_weight, res.temperature, pressure,
                        "gibbs", res.converged, res.mole_fractions)


def _evaluate_rocketcea(propellant: PropellantModel, pressure: float) -> ThermoResult:  # pragma: no cover
    # rocketcea needs a weight-based formulation; map composition to weights.
    from .gibbs import ATOMIC_WEIGHTS

    # Full custom-formulation support requires a CEA input deck; documented in
    # docs/theory/thermo_backends.md.  The atomic weights below are the mass
    # basis a future deck generator would use.
    _weights = {el: ATOMIC_WEIGHTS[el] * n for el, n in propellant.composition.items()}
    raise NotImplementedError(
        "rocketcea backend needs a custom formulation deck; see "
        "docs/theory/thermo_backends.md. Use backend='gibbs' instead."
    )


def _evaluate_cantera(propellant: PropellantModel, pressure: float) -> ThermoResult:  # pragma: no cover
    raise NotImplementedError(
        "cantera backend requires a user-supplied mechanism file; see "
        "docs/theory/thermo_backends.md. Use backend='gibbs' instead."
    )


__all__ = ["ThermoResult", "evaluate", "default_db", "c_star_from", "gamma_from_cp",
           "SpeciesDatabase", "ThermalChem"]
