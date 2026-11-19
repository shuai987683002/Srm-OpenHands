"""Optional REST API (plan §6: FastAPI backend).

The CLI and the Python API already cover scripted use; this layer exists so a
teaching front end or a lab dashboard can call the same engine over HTTP
without shelling out. It is a thin wrapper over :mod:`srm_open.api.facade` —
no numerics live here, so the engine stays importable without FastAPI.

FastAPI is an optional dependency::

    pip install -e ".[api]"        # or: pip install fastapi uvicorn

Run it::

    uvicorn srm_open.api.rest:app --reload        # http://127.0.0.1:8000/docs

Design requests return the full ``DesignResult`` as JSON. That object is large
(per-step time series), so ``?summary=true`` returns only the integrated
metrics — the usual thing a dashboard wants.
"""

from __future__ import annotations

from typing import Any

try:  # pragma: no cover - exercised only when the extra is installed
    from fastapi import FastAPI, HTTPException
    from pydantic import BaseModel, Field
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "the REST API needs the optional extra: pip install -e '.[api]'"
    ) from exc

from ..geometry.sketch import geometry_backend_available
from ..io.templates import TemplateLibrary
from .facade import design_forward, design_from_template, get_propellant

app = FastAPI(
    title="Srm-Open",
    version="0.1.0",
    description=(
        "Open-source solid rocket motor design (teaching / non-commercial R&D). "
        "Results are preliminary estimates, not engineering deliverables."
    ),
)


class DesignRequest(BaseModel):
    """Forward-design request: indicators in, grain + performance out."""

    thrust: float = Field(..., gt=0, description="target thrust [N]")
    burn_time: float = Field(..., gt=0, description="target burn time [s]")
    propellant: str = "ap_htpb_80"
    max_chamber_pressure: float | None = Field(default=None, gt=0, description="[Pa]")
    outer_diameter: float | None = Field(default=None, gt=0, description="[m]")
    length_ratio: float | None = Field(default=None, gt=0)
    thrust_profile: str | None = None
    family: str | None = Field(default=None, description="force a grain family")
    template: str | None = Field(
        default=None,
        description="evaluate this fixed template instead of synthesising a grain",
    )


def _summary(result) -> dict[str, Any]:
    p = result.performance
    n = result.nozzle
    return {
        "mode": result.mode,
        "input_hash": result.provenance.input_hash,
        "nozzle": {
            "throat_diameter": None if n is None else n.throat_diameter,
            "expansion_ratio": None if n is None else n.expansion_ratio,
            "exit_area": None if n is None else n.exit_area,
        },
        "performance": {
            "average_thrust": None if p is None else p.average_thrust,
            "peak_pressure": None if p is None else p.peak_pressure,
            "burn_time": None if p is None else p.burn_time,
            "total_impulse": None if p is None else p.total_impulse,
            "specific_impulse": None if p is None else p.specific_impulse,
            "propellant_mass": None if p is None else p.propellant_mass,
            "loading_fraction": None if p is None else p.loading_fraction,
            "sliver_fraction": None if p is None else p.sliver_fraction,
        },
        "verification": {
            "passed": sum(1 for c in result.verification.checks if c["passed"]),
            "total": len(result.verification.checks),
            "failed": [c["name"] for c in result.verification.checks if not c["passed"]],
        },
    }


@app.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "cad_export": geometry_backend_available()}


@app.get("/templates")
def templates() -> dict[str, Any]:
    return {"templates": TemplateLibrary.load_default().ids()}


@app.get("/propellants")
def propellants() -> dict[str, Any]:
    from ..propellant.database import PropellantDB

    db = PropellantDB.load_default()
    return {
        "propellants": [
            {"id": p.id, "name": p.name, "density": p.density, "source": p.source}
            for p in db.all()
        ]
    }


@app.post("/design")
def design(req: DesignRequest, summary: bool = False) -> dict[str, Any]:
    indicators = {
        "thrust": req.thrust,
        "burn_time": req.burn_time,
    }
    for key in ("max_chamber_pressure", "outer_diameter", "length_ratio", "thrust_profile"):
        value = getattr(req, key)
        if value is not None:
            indicators[key] = value

    try:
        if req.template:
            result = design_from_template(req.template, req.propellant, indicators)
        else:
            result = design_forward(indicators, req.propellant, family=req.family)
    except KeyError as exc:
        # unknown template / propellant id
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return _summary(result) if summary else result.model_dump(mode="json")


@app.get("/propellants/{propellant_id}/thermo")
def thermo(propellant_id: str, pressure: float = 7.0e6) -> dict[str, Any]:
    try:
        prop = get_propellant(propellant_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    from ..thermo.backend import evaluate

    res = evaluate(prop, pressure)
    return {
        "propellant": propellant_id,
        "pressure": pressure,
        "c_star": res.c_star,
        "gamma": res.gamma,
        "molecular_weight": res.molecular_weight,
        "chamber_temperature": res.chamber_temperature,
        "mode": res.mode,
    }
