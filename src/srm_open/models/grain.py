"""Parametric grain geometry models.

A grain is a stack of one or more *segments* (multi-segment grains are common:
a booster grain with different port geometry fore and aft).  Each segment is a
family plus a family-specific parameter vector ``params``.

The set of valid parameters per family is declared by the geometry registry
(:mod:`srm_open.geometry.families`), which keeps this module free of geometry
code and makes families pluggable.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field, model_validator


class GrainFamily(str, Enum):
    """All supported grain cross-section families."""

    CYLINDRICAL = "cylindrical"  # solid cylinder, end-burning only
    TUBE = "tube"  # simple central cylindrical bore
    TUBE_INHIBITED = "tube_inhibited"  # bore with inhibited ends (internal burning)
    STAR = "star"  # N-point star port
    WAGON_WHEEL = "wagon_wheel"  # wagon-wheel / wheel port
    SLOTTED = "slotted"  # longitudinal slots (finocyl-like)
    FINOCYL = "finocyl"  # fins + central cylinder
    SPHERICAL = "spherical"  # spherical cavity
    CONICAL = "conical"  # conical cavity
    MULTI_PERFORATED = "multi_perforated"  # 7-perf / 19-perf classic
    CUSTOM_POLYGON = "custom_polygon"  # user supplied polygon port (sketch)


class BurnMode(str, Enum):
    """Which surfaces of a segment burn."""

    INTERNAL = "internal"  # bore burns, outer surface inhibited
    EXTERNAL = "external"  # outer surface burns, bore inhibited
    BOTH = "both"  # inner and outer both burn (uninhibited tube)
    END = "end"  # end face burns (cigarette burning)


class GrainSegment(BaseModel):
    """One axial segment of the grain."""

    family: GrainFamily
    length: float = Field(..., gt=0, description="segment length [m]")
    outer_diameter: float = Field(..., gt=0, description="segment OD [m]")
    burn_mode: BurnMode = BurnMode.INTERNAL
    # family-specific parameters; validated against the family registry
    params: dict[str, float] = Field(default_factory=dict)
    # for CUSTOM_POLYGON: closed port outline as (x, y) points [m]
    polygon: list[tuple[float, float]] | None = None
    label: str = ""

    @model_validator(mode="after")
    def _check(self) -> GrainSegment:
        if self.family == GrainFamily.CUSTOM_POLYGON and not self.polygon:
            raise ValueError("CUSTOM_POLYGON requires a closed polygon outline")
        return self


class GrainGeometry(BaseModel):
    """Full grain: ordered segments from fore to aft."""

    segments: list[GrainSegment]
    inhibit_ends: bool = Field(
        default=True, description="True: both axial ends inhibited (internal burning)"
    )
    source: str = Field(default="user")

    @model_validator(mode="after")
    def _non_empty(self) -> GrainGeometry:
        if not self.segments:
            raise ValueError("GrainGeometry requires at least one segment")
        return self

    # -- convenience derived quantities (geometry only, no physics) ---------
    @property
    def total_length(self) -> float:
        return sum(s.length for s in self.segments)

    @property
    def max_outer_diameter(self) -> float:
        return max(s.outer_diameter for s in self.segments)
