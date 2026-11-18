"""Physical constants and unit helpers.

Only *definitional* constants live here (standard gravity, universal gas
constant, reference pressure).  No empirical/engineering coefficients.
"""

from __future__ import annotations

G0 = 9.80665  # standard gravity [m/s2]
R_UNIVERSAL = 8.314462618  # universal gas constant [J/mol/K]
P_ATM = 101325.0  # standard atmosphere [Pa]
T_REF = 293.15  # 20 degC reference temperature [K]

__all__ = ["G0", "R_UNIVERSAL", "P_ATM", "T_REF"]
