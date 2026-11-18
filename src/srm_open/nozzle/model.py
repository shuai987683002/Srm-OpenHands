"""Nozzle flow model (1-D isentropic ideal gas).

Governing relations (public, standard compressible-flow theory)::

    Gamma(g)  = sqrt(g) * (2/(g+1))^((g+1)/(2(g-1)))
    mdot      = Gamma * A_t * p_c / sqrt(R T_c / M)      (choked)
    C_F       = Gamma*sqrt(2g/(g-1)*(1-(p_e/p_c)^((g-1)/g))) + (p_e-p_a)/p_c*eps
    p_e/p_c   = (1 + (g-1)/2 * Me^2)^(-g/(g-1)),  eps = A_e/A_t
"""

from __future__ import annotations

import math

from ..constants import R_UNIVERSAL
from ..models.nozzle import NozzleModel


def gamma_term(gamma: float) -> float:
    return math.sqrt(gamma) * (2.0 / (gamma + 1.0)) ** ((gamma + 1.0) / (2.0 * (gamma - 1.0)))


def exit_mach(expansion_ratio: float, gamma: float) -> float:
    """Supersonic exit Mach number for a given area ratio (Newton iteration)."""
    if expansion_ratio <= 1.0:
        return 1.0
    g = gamma

    def area_ratio(M: float) -> float:
        return (1.0 / M) * ((2.0 / (g + 1.0)) * (1.0 + (g - 1.0) / 2.0 * M * M)) ** (
            (g + 1.0) / (2.0 * (g - 1.0))
        )

    lo, hi = 1.0001, 50.0
    for _ in range(100):
        mid = 0.5 * (lo + hi)
        if area_ratio(mid) < expansion_ratio:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def pressure_ratio_from_mach(M: float, gamma: float) -> float:
    return (1.0 + (gamma - 1.0) / 2.0 * M * M) ** (-gamma / (gamma - 1.0))


class Nozzle:
    """Stateless-ish nozzle calculator built from a :class:`NozzleModel`."""

    def __init__(self, model: NozzleModel) -> None:
        self.model = model

    def mass_flow(self, p_chamber: float, gamma: float, temperature: float,
                  molecular_weight: float, time: float = 0.0) -> float:
        """Choked mass flow [kg/s] (ideal, times c* efficiency)."""
        a_t = self.model.throat_area_at(time)
        c_star = math.sqrt(R_UNIVERSAL * temperature / molecular_weight) / gamma_term(gamma)
        return self.model.c_star_efficiency * p_chamber * a_t / c_star

    def thrust_coefficient(self, p_chamber: float, gamma: float,
                           ambient_pressure: float | None = None) -> float:
        """Vacuum/ambient thrust coefficient [-], including divergence loss."""
        p_a = self.model.ambient_pressure if ambient_pressure is None else ambient_pressure
        eps = self.model.expansion_ratio
        Me = exit_mach(eps, gamma)
        pe_over_pc = pressure_ratio_from_mach(Me, gamma)
        g = gamma
        momentum = gamma_term(g) * math.sqrt(
            2.0 * g / (g - 1.0) * (1.0 - pe_over_pc ** ((g - 1.0) / g))
        )
        pressure = (pe_over_pc - p_a / p_chamber) * eps
        divergence = 0.5 * (1.0 + math.cos(self.model.divergence_half_angle))
        cf_ideal = momentum + pressure
        return cf_ideal * divergence * self.model.cf_efficiency

    def exit_pressure(self, p_chamber: float, gamma: float) -> float:
        Me = exit_mach(self.model.expansion_ratio, gamma)
        return p_chamber * pressure_ratio_from_mach(Me, gamma)

    def thrust(self, p_chamber: float, gamma: float, temperature: float,
               molecular_weight: float, ambient_pressure: float | None = None,
               time: float = 0.0) -> float:
        cf = self.thrust_coefficient(p_chamber, gamma, ambient_pressure)
        return cf * self.model.throat_area_at(time) * p_chamber
