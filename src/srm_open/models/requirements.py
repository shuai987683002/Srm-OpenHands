"""Requirement set: the initial top-level design targets.

Only the user's stated targets live here.  Derived / redundant quantities are
computed by :mod:`srm_open.orchestration.requirements` which also flags
inconsistencies -- we never silently invent a target.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class RequirementSet(BaseModel):
    """Top-level design requirements (all SI unless noted).

    Any subset may be provided; :func:`consistency_check` reports which
    redundant relations hold and which are over/under-determined.
    """

    name: str = Field(default="unnamed", description="case name")

    # --- ballistic targets -------------------------------------------------
    thrust: float | None = Field(default=None, gt=0, description="vacuum/sea-level thrust F [N]")
    burn_time: float | None = Field(default=None, gt=0, description="burn time t_b [s]")
    propellant_mass: float | None = Field(default=None, gt=0, description="propellant mass m_p [kg]")
    total_impulse: float | None = Field(default=None, gt=0, description="total impulse I_t [N.s]")
    specific_impulse: float | None = Field(default=None, gt=0, description="specific impulse I_sp [s]")
    average_thrust: float | None = Field(default=None, gt=0, description="average thrust [N]")

    # --- geometry envelope -------------------------------------------------
    outer_diameter: float | None = Field(default=None, gt=0, description="motor OD D [m]")
    length_ratio: float | None = Field(default=None, gt=0, description="L/D ratio [-]")
    chamber_length: float | None = Field(default=None, gt=0, description="grain length L [m]")

    # --- chamber / nozzle constraints -------------------------------------
    max_chamber_pressure: float | None = Field(default=None, gt=0, description="p_c max [Pa]")
    throat_diameter: float | None = Field(default=None, gt=0, description="d_t [m]")
    expansion_ratio: float | None = Field(default=None, gt=1, description="eps = A_e/A_t [-]")
    ambient_pressure: float = Field(default=101325.0, ge=0, description="p_a [Pa]")

    # --- thrust shape preference ------------------------------------------
    thrust_profile: Literal["neutral", "progressive", "regressive"] | None = Field(
        default=None, description="thrust shape preference")

    # --- integrity constraints --------------------------------------------
    min_safety_factor: float | None = Field(default=None, gt=1, description="required SF [-]")
    min_load_fraction: float | None = Field(default=None, gt=0, le=1, description="min volumetric loading [-]")
    max_sliver_fraction: float | None = Field(default=None, ge=0, le=1, description="max sliver fraction [-]")

    @model_validator(mode="after")
    def _at_least_one_ballistic(self) -> RequirementSet:
        if all(
            v is None
            for v in (
                self.thrust,
                self.total_impulse,
                self.propellant_mass,
                self.average_thrust,
            )
        ):
            raise ValueError(
                "RequirementSet needs at least one of thrust / total_impulse / "
                "propellant_mass / average_thrust"
            )
        return self
