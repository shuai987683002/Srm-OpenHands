"""Batch studies: parametric sweep and Monte-Carlo uncertainty (FR-12).

Both functions run the *same* forward pipeline as a single design, so a study
result is always consistent with the one-shot numbers.  Randomness is drawn
from a seeded generator and the seed is recorded, satisfying the
reproducibility requirement (NFR-2): the same input, version and seed produce
the same study.

Scope note
----------
Inputs are perturbed as **independent** quantities (no correlation matrix yet),
and the sampled distributions are the ones a designer can justify from a
datasheet: normal for measurements, uniform for bounded manufacturing
tolerances.  A correlated sampler is a straightforward extension of
:func:`_draw`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from ..models.grain import GrainGeometry
from ..models.propellant import PropellantModel
from ..models.requirements import RequirementSet
from .forward import forward_design


@dataclass
class StudySample:
    """One evaluated point of a study."""

    label: str
    inputs: dict[str, float]
    outputs: dict[str, float]
    passed: bool = True
    error: str = ""


@dataclass
class StudyResult:
    """Aggregated outcome of a sweep or Monte-Carlo run."""

    kind: str
    samples: list[StudySample] = field(default_factory=list)
    seed: int | None = None
    failed: int = 0
    notes: str = ""

    def metric(self, name: str) -> list[float]:
        return [s.outputs[name] for s in self.samples if name in s.outputs]

    def summary(self) -> dict[str, dict[str, float]]:
        """Mean / std / min / max / p05 / p95 per output metric."""
        out: dict[str, dict[str, float]] = {}
        names = {k for s in self.samples for k in s.outputs}
        for name in sorted(names):
            vals = np.asarray(self.metric(name), dtype=float)
            if vals.size == 0:
                continue
            out[name] = {
                "mean": float(np.mean(vals)),
                "std": float(np.std(vals, ddof=1)) if vals.size > 1 else 0.0,
                "min": float(np.min(vals)),
                "max": float(np.max(vals)),
                "p05": float(np.percentile(vals, 5)),
                "p95": float(np.percentile(vals, 95)),
                "n": float(vals.size),
            }
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "seed": self.seed,
            "failed": self.failed,
            "notes": self.notes,
            "summary": self.summary(),
            "samples": [
                {"label": s.label, "inputs": s.inputs, "outputs": s.outputs,
                 "passed": s.passed, "error": s.error}
                for s in self.samples
            ],
        }


_METRICS = {
    "average_thrust": lambda r: r.performance.average_thrust,
    "burn_time": lambda r: r.performance.burn_time,
    "total_impulse": lambda r: r.performance.total_impulse,
    "specific_impulse": lambda r: r.performance.specific_impulse,
    "peak_pressure": lambda r: r.performance.peak_pressure,
    "peak_thrust": lambda r: r.performance.peak_thrust,
    "propellant_mass": lambda r: r.performance.propellant_mass,
    "loading_fraction": lambda r: r.performance.loading_fraction,
    "sliver_fraction": lambda r: r.performance.sliver_fraction,
}


def _evaluate(grain: GrainGeometry, req: RequirementSet, propellant: PropellantModel,
              label: str, inputs: dict[str, float]) -> StudySample:
    try:
        res = forward_design(req, grain, propellant)
    except Exception as exc:  # a bad sample must not abort the whole study
        return StudySample(label=label, inputs=inputs, outputs={}, passed=False,
                           error=f"{type(exc).__name__}: {exc}")
    outputs: dict[str, float] = {}
    if res.performance is not None:
        for name, getter in _METRICS.items():
            value = getter(res)
            if value is not None and math.isfinite(value):
                outputs[name] = float(value)
    return StudySample(label=label, inputs=inputs, outputs=outputs,
                       passed=res.verification.all_passed)


def _with_overrides(req: RequirementSet, overrides: dict[str, float]) -> RequirementSet:
    return req.model_copy(update=overrides)


def _perturb_propellant(prop: PropellantModel, path: str, value: float) -> PropellantModel:
    """Return a copy of ``prop`` with one (possibly dotted) field replaced."""
    head, _, tail = path.partition(".")
    if not tail:
        return prop.model_copy(update={head: value})
    child = getattr(prop, head, None)
    if child is None or not hasattr(child, "model_copy"):
        raise ValueError(f"propellant field {path!r} is not settable")
    return prop.model_copy(update={head: child.model_copy(update={tail: value})})


def _resolve_nominal(req: RequirementSet, propellant: PropellantModel,
                     path: str, target: str) -> tuple[bool, float | None]:
    """Return ``(resolvable, nominal)``.

    A requirement field that exists but is unset (``None``) is still resolvable:
    a sweep may be *introducing* a constraint, so the absence of a nominal value
    is not an error.  A path that does not exist at all is.
    """
    if target == "requirement":
        if path not in RequirementSet.model_fields:
            return False, None
        value = getattr(req, path, None)
    else:
        value = propellant
        for part in path.split("."):
            if not hasattr(value, part):
                return False, None
            value = getattr(value, part)
            if value is None:
                return False, None
    return True, None if value is None else float(value)


def sweep(req: RequirementSet, grain: GrainGeometry, propellant: PropellantModel,
          parameter: str, values: list[float], *,
          target: str = "requirement") -> StudyResult:
    """Evaluate one requirement or propellant input across a list of values.

    ``target`` selects what ``parameter`` refers to:

    * ``"requirement"`` -- a :class:`RequirementSet` field (``max_chamber_pressure``,
      ``expansion_ratio``, ...);
    * ``"propellant"`` -- a ``PropellantModel`` scalar such as ``density`` or
      ``burn_rate.a`` (dotted path allowed).

    The grain geometry is held fixed, so the sweep answers "how does this design
    respond to a change in its environment / inputs", not "re-optimise".
    """
    if not values:
        raise ValueError("sweep needs at least one value")
    if target not in ("requirement", "propellant"):
        raise ValueError(f"unknown sweep target {target!r}")
    ok, _ = _resolve_nominal(req, propellant, parameter, target)
    if not ok:
        raise ValueError(f"unknown {target} field {parameter!r}")

    study = StudyResult(kind=f"sweep:{target}:{parameter}")
    for value in values:
        if target == "requirement":
            req_i, prop_i = _with_overrides(req, {parameter: float(value)}), propellant
        else:
            req_i, prop_i = req, _perturb_propellant(propellant, parameter, float(value))
        study.samples.append(
            _evaluate(grain, req_i, prop_i, f"{parameter}={value:g}", {parameter: float(value)}))
    study.failed = sum(1 for s in study.samples if not s.outputs)
    return study


@dataclass
class _InputSpec:
    """A sampled input: where it lives and how it varies."""

    path: str
    nominal: float
    sigma: float = 0.0
    low: float | None = None
    high: float | None = None
    distribution: str = "normal"  # normal | uniform
    target: str = "propellant"  # propellant | requirement


def _draw(rng: np.random.Generator, spec: _InputSpec) -> float:
    if spec.distribution == "uniform":
        lo = spec.low if spec.low is not None else spec.nominal - spec.sigma
        hi = spec.high if spec.high is not None else spec.nominal + spec.sigma
        return float(rng.uniform(lo, hi))
    if spec.sigma <= 0:
        return spec.nominal
    return float(rng.normal(spec.nominal, spec.sigma))


def monte_carlo(req: RequirementSet, grain: GrainGeometry, propellant: PropellantModel,
                variations: dict[str, dict[str, float]], *,
                n_samples: int = 200, seed: int = 12345) -> StudyResult:
    """Propagate input uncertainty through the forward pipeline.

    ``variations`` maps a settable path to its spread, e.g.::

        {"density": {"sigma": 5.0},                        # propellant (kg/m3)
         "burn_rate.a": {"sigma": 1e-6},                   # dotted path
         "max_chamber_pressure": {"low": 8e6, "high": 1e7, # requirement, uniform
                                  "distribution": "uniform", "target": "requirement"}}

    A ``normal`` spread uses ``sigma``; a ``uniform`` spread uses ``low``/``high``
    (or ``nominal ± sigma``).  The seed is recorded in the result.
    """
    if n_samples < 1:
        raise ValueError("n_samples must be >= 1")

    specs: list[_InputSpec] = []
    for path, cfg in variations.items():
        target = str(cfg.get("target", "propellant"))
        ok, nominal = _resolve_nominal(req, propellant, path, target)
        if not ok or nominal is None:
            raise ValueError(f"cannot resolve nominal value for {path!r}")
        specs.append(_InputSpec(
            path=path, nominal=nominal, sigma=float(cfg.get("sigma", 0.0)),
            low=cfg.get("low"), high=cfg.get("high"),
            distribution=str(cfg.get("distribution", "normal")), target=target))

    rng = np.random.default_rng(seed)
    study = StudyResult(kind="monte_carlo", seed=seed,
                        notes=f"{n_samples} samples, {len(specs)} varied inputs")
    for k in range(n_samples):
        req_i, prop_i = req, propellant
        inputs: dict[str, float] = {}
        for spec in specs:
            value = _draw(rng, spec)
            inputs[spec.path] = value
            if spec.target == "requirement":
                req_i = _with_overrides(req_i, {spec.path: value})
            else:
                prop_i = _perturb_propellant(prop_i, spec.path, value)
        study.samples.append(_evaluate(grain, req_i, prop_i, f"sample-{k}", inputs))
    study.failed = sum(1 for s in study.samples if not s.outputs)
    return study
