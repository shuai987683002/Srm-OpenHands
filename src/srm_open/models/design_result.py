"""Top-level design result container and provenance."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from .grain import GrainGeometry
from .nozzle import NozzleModel
from .propellant import PropellantModel
from .requirements import RequirementSet
from .results import (
    BurnbackResult,
    GrainBodyResult,
    IntegrityResult,
    InternalBallisticsResult,
    PerformanceResult,
)


class Provenance(BaseModel):
    """Everything needed to reproduce a result."""

    software_version: str = ""
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    model_choices: dict[str, str] = Field(default_factory=dict)
    random_seed: int | None = None
    dependency_versions: dict[str, str] = Field(default_factory=dict)
    data_sources: list[str] = Field(default_factory=list)
    input_hash: str = ""


class VerificationReport(BaseModel):
    """Checks performed on a design and their residuals."""

    checks: list[dict[str, Any]] = Field(default_factory=list)

    def add(self, name: str, passed: bool, value: Any = None, tol: Any = None, note: str = "") -> None:
        self.checks.append(
            {"name": name, "passed": bool(passed), "value": value, "tol": tol, "note": note}
        )

    @property
    def all_passed(self) -> bool:
        return all(c["passed"] for c in self.checks) if self.checks else True


class DesignResult(BaseModel):
    """The single object that flows to the report layer."""

    mode: str = Field(default="forward", description="forward | inverse")
    requirement: RequirementSet | None = None
    propellant: PropellantModel | None = None
    nozzle: NozzleModel | None = None
    grain: GrainGeometry | None = None
    burnback: BurnbackResult | None = None
    ballistics: InternalBallisticsResult | None = None
    performance: PerformanceResult | None = None
    grain_body: GrainBodyResult | None = None
    integrity: IntegrityResult | None = None
    verification: VerificationReport = Field(default_factory=VerificationReport)
    provenance: Provenance = Field(default_factory=Provenance)
