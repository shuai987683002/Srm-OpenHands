"""Nozzle model.

The default is a 1-D isentropic (ideal-gas) model.  Losses are applied through
user-supplied efficiency factors so the model stays traceable.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class NozzleModel(BaseModel):
    """Convergent-divergent nozzle definition and loss model."""

    throat_diameter: float = Field(..., gt=0, description="d_t [m]")
    expansion_ratio: float = Field(default=8.0, gt=1.0, description="eps = A_e/A_t [-]")
    divergence_half_angle: float = Field(
        default=0.2618, gt=0, description="cone half angle [rad], default 15 deg"
    )
    c_star_efficiency: float = Field(default=0.95, gt=0, le=1.0)
    cf_efficiency: float = Field(default=0.98, gt=0, le=1.0)
    ambient_pressure: float = Field(default=101325.0, ge=0)
    # optional throat erosion: d_t(t) = d_t0 + erosion_rate * t
    throat_erosion_rate: float = Field(default=0.0, ge=0, description="[m/s]")
    source: str = Field(default="user")

    @property
    def throat_area(self) -> float:
        import math

        return math.pi * (self.throat_diameter / 2.0) ** 2

    @property
    def exit_area(self) -> float:
        return self.expansion_ratio * self.throat_area

    def throat_area_at(self, t: float) -> float:
        import math

        d = self.throat_diameter + self.throat_erosion_rate * t
        return math.pi * (d / 2.0) ** 2
