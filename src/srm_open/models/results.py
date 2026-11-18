"""Result models: burnback, internal ballistics, performance, integrity."""

from __future__ import annotations

from pydantic import BaseModel, Field


class BurnbackResult(BaseModel):
    """Burning-surface evolution result."""

    method: str = Field(default="analytic", description="analytic | levelset")
    burn_distance: list[float] = Field(default_factory=list, description="web consumed e [m]")
    time: list[float] = Field(default_factory=list, description="time [s]")
    burn_area: list[float] = Field(default_factory=list, description="A_b [m2]")
    burn_area_over_throat: list[float] = Field(default_factory=list, description="J = A_b/A_t [-]")
    web_thickness: float = Field(default=0.0, ge=0, description="total web e_max [m]")
    sliver_burn_distance: float | None = Field(default=None, description="e at sliver [m]")
    propellant_volume: float = Field(default=0.0, ge=0, description="initial propellant volume [m3]")
    port_volume: float = Field(default=0.0, ge=0, description="initial port/free volume [m3]")
    notes: str = ""


class InternalBallisticsResult(BaseModel):
    """Chamber pressure / mass-flow history."""

    model: str = Field(default="equilibrium", description="equilibrium | transient")
    time: list[float] = Field(default_factory=list)
    chamber_pressure: list[float] = Field(default_factory=list, description="p_c [Pa]")
    mass_generated: list[float] = Field(default_factory=list, description="mdot_gen [kg/s]")
    mass_discharged: list[float] = Field(default_factory=list, description="mdot_out [kg/s]")
    burn_area: list[float] = Field(default_factory=list, description="A_b [m2]")
    kn: list[float] = Field(default_factory=list, description="A_b/A_t [-]")
    peak_pressure: float = 0.0
    time_at_peak: float = 0.0
    free_volume_initial: float = 0.0
    converged: bool = True
    notes: str = ""


class PerformanceResult(BaseModel):
    """Integrated performance metrics."""

    time: list[float] = Field(default_factory=list)
    thrust: list[float] = Field(default_factory=list, description="F(t) [N]")
    thrust_coefficient: list[float] = Field(default_factory=list, description="C_F(t) [-]")
    total_impulse: float = 0.0
    specific_impulse: float = 0.0
    average_thrust: float = 0.0
    burn_time: float = 0.0
    propellant_mass: float = 0.0
    loading_fraction: float = 0.0
    sliver_fraction: float = 0.0
    mass_ratio: float = 0.0
    peak_thrust: float = 0.0
    peak_pressure: float = 0.0
    notes: str = ""


class GrainBodyResult(BaseModel):
    """Cylindrical grain main-body calculation (M8)."""

    burn_area_initial: float = 0.0
    burn_area_final: float = 0.0
    web_thickness: float = 0.0
    propellant_volume: float = 0.0
    chamber_volume: float = 0.0
    loading_fraction: float = 0.0
    sliver_mass: float = 0.0
    sliver_fraction: float = 0.0
    length_to_diameter: float = 0.0
    burn_area_curve: list[float] = Field(default_factory=list)
    burn_distance_curve: list[float] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)


class IntegrityResult(BaseModel):
    """Grain structural integrity (engineering check)."""

    method: str = Field(default="lame_thin_wall")
    max_stress: float = 0.0
    max_strain: float = 0.0
    max_von_mises: float = 0.0
    safety_factor: float = 0.0
    allowable_stress: float = 0.0
    location: str = ""
    stress_curve: list[float] = Field(default_factory=list)
    radius_curve: list[float] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    reference_comparison: dict | None = None
