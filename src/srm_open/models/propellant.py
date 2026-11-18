"""Propellant and thermal-chemistry models.

Coefficients (``a``, ``n``, ``c_star``, ``gamma`` ...) are *user supplied*.
The built-in database ships only values that are published in the open
literature for generic propellant classes; see ``data/propellants``.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field, model_validator


class BurnRateLaw(str, Enum):
    """Supported burning-rate correlations."""

    SAINT_ROBERT = "saint_robert"  # r = a * p**n
    SAINT_ROBERT_TEMP = "saint_robert_temp"  # r = a * p**n * exp(sigma_p*(T-T_ref))
    CONSTANT = "constant"  # r = a
    TABLE = "table"  # piecewise-linear r(p) from table


class BurnRateModel(BaseModel):
    """A single burning-rate law with its (user supplied) coefficients."""

    law: BurnRateLaw = BurnRateLaw.SAINT_ROBERT
    a: float = Field(..., gt=0, description="coefficient [m/s/Pa^n] or [m/s]")
    n: float = Field(default=0.0, ge=0.0, le=1.0, description="pressure exponent [-]")
    sigma_p: float = Field(default=0.0, description="temperature sensitivity [1/K]")
    reference_temperature: float = Field(default=293.15, gt=0, description="T_ref [K]")
    # for TABLE law: parallel lists pressure [Pa] / rate [m/s]
    pressure_table: list[float] | None = None
    rate_table: list[float] | None = None
    source: str = Field(default="user", description="data provenance note")

    @model_validator(mode="after")
    def _check_table(self) -> BurnRateModel:
        if self.law == BurnRateLaw.TABLE:
            if not self.pressure_table or not self.rate_table:
                raise ValueError("TABLE burn-rate law requires pressure_table and rate_table")
            if len(self.pressure_table) != len(self.rate_table):
                raise ValueError("pressure_table and rate_table must have equal length")
        return self


class ThermalChem(BaseModel):
    """Thermochemistry needed by the internal-ballistics model.

    ``mode`` selects how the values were obtained so results stay traceable:

    * ``direct``      -- user typed c*, gamma, T_c directly
    * ``gibbs``       -- computed by the built-in Gibbs minimiser
    * ``rocketcea``   -- computed by the RocketCEA backend
    * ``cantera``     -- computed by the Cantera backend
    """

    mode: str = Field(default="direct")
    c_star: float | None = Field(default=None, gt=0, description="characteristic velocity c* [m/s]")
    gamma: float | None = Field(default=None, gt=1.0, description="ratio of specific heats [-]")
    molecular_weight: float | None = Field(default=None, gt=0, description="exhaust M [kg/mol]")
    chamber_temperature: float | None = Field(default=None, gt=0, description="T_c [K]")
    chamber_pressure: float | None = Field(default=None, gt=0, description="p_c used for evaluation [Pa]")
    source: str = Field(default="user")


class PropellantModel(BaseModel):
    """Complete propellant definition."""

    id: str
    name: str = ""
    density: float = Field(..., gt=0, description="rho_p [kg/m3]")
    burn_rate: BurnRateModel
    thermo: ThermalChem = Field(default_factory=ThermalChem)

    # elemental composition (mole fractions) for the built-in Gibbs solver.
    # Left empty when the user supplies thermo directly.
    composition: dict[str, float] = Field(default_factory=dict)
    # e.g. {"C": 0.0, "H": 0.0, "O": 0.0, "N": 0.0, "Cl": 0.0, "Al": 0.0}
    heat_of_formation: float | None = Field(
        default=None, description="propellant H_f [J/kg] (for Gibbs)"
    )
    class_name: str = Field(default="", description="e.g. AP/HTPB, double-base, AP/Al/HTPB")
    source: str = Field(default="user", description="data provenance note")
    notes: str = ""
