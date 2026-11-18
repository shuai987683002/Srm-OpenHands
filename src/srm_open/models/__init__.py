"""Core data models for Srm-Open.

All models are pydantic v2 ``BaseModel`` subclasses so that inputs are
validated, serialisable and round-trippable (project files, API payloads,
report provenance).

Design rule: **no physical constants or empirical coefficients are baked in**.
Every number that a user must supply is a required field or an explicitly
documented optional field with a *neutral* default (0 / None), never a guessed
engineering value.
"""

from .design_result import DesignResult, Provenance, VerificationReport
from .grain import GrainFamily, GrainGeometry, GrainSegment
from .nozzle import NozzleModel
from .propellant import BurnRateLaw, PropellantModel, ThermalChem
from .requirements import RequirementSet
from .results import (
    BurnbackResult,
    GrainBodyResult,
    IntegrityResult,
    InternalBallisticsResult,
    PerformanceResult,
)

__all__ = [
    "RequirementSet",
    "BurnRateLaw",
    "PropellantModel",
    "ThermalChem",
    "NozzleModel",
    "GrainGeometry",
    "GrainSegment",
    "GrainFamily",
    "BurnbackResult",
    "InternalBallisticsResult",
    "PerformanceResult",
    "GrainBodyResult",
    "IntegrityResult",
    "DesignResult",
    "Provenance",
    "VerificationReport",
]
