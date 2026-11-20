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

import tempfile
from pathlib import Path
from typing import Any

try:  # pragma: no cover - exercised only when the extra is installed
    from fastapi import FastAPI, HTTPException, Response
    from fastapi.responses import RedirectResponse
    from fastapi.staticfiles import StaticFiles
    from pydantic import BaseModel, Field
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "the REST API needs the optional extra: pip install -e '.[api]'"
    ) from exc

from ..geometry.sketch import geometry_backend_available, grain_to_stl, sketch_payload
from ..io.templates import TemplateLibrary
from ..models.grain import BurnMode, GrainFamily
from ..models.requirements import RequirementSet
from .facade import design_forward, design_from_template, get_propellant, make_grain

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


class InverseRequest(BaseModel):
    """Inverse-design request: ballistic targets in, grain parameters out."""

    family: str = Field(..., description="grain family to recover")
    outer_diameter: float = Field(..., gt=0, description="motor OD envelope [m]")
    thrust: float | None = Field(default=None, gt=0, description="[N]")
    burn_time: float | None = Field(default=None, gt=0, description="[s]")
    total_impulse: float | None = Field(default=None, gt=0, description="[N.s]")
    specific_impulse: float | None = Field(default=None, gt=0, description="[s]")
    max_chamber_pressure: float | None = Field(default=None, gt=0, description="[Pa]")
    propellant: str = "ap_htpb_80"
    length: float | None = Field(default=None, gt=0, description="envelope length hint [m]")
    burn_mode: str = "internal"
    method: str = Field(default="nelder_mead", description="nelder_mead | cma")
    max_iter: int = Field(default=120, ge=1, le=2000)


class GrainSpec(BaseModel):
    """A fully specified grain, for sketch / STL rendering."""

    family: str
    length: float = Field(..., gt=0, description="[m]")
    outer_diameter: float = Field(..., gt=0, description="[m]")
    params: dict[str, float] = Field(default_factory=dict)
    burn_mode: str = "internal"


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


def _run_design(req: DesignRequest):
    """Run the forward design for a request, mapping domain errors to HTTP."""
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
            return design_from_template(req.template, req.propellant, indicators)
        return design_forward(indicators, req.propellant, family=req.family)
    except KeyError as exc:
        # unknown template / propellant id
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/design")
def design(req: DesignRequest, summary: bool = False,
           curves: bool = False, sketch: bool = False) -> dict[str, Any]:
    """Forward design. ``summary`` / ``curves`` / ``sketch`` add derived views
    to the same run, so a dashboard never pays for the design twice."""
    result = _run_design(req)
    if not (summary or curves or sketch):
        return result.model_dump(mode="json")
    body = _summary(result)
    if curves:
        body["curves"] = _curves(result)
    if sketch and result.grain is not None:
        body["sketch"] = sketch_payload(result.grain)
    return body


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


def _curves(result) -> dict[str, list[float]]:
    """Time histories a dashboard plots, downsampled to keep the payload small."""
    out: dict[str, list[float]] = {}
    perf = result.performance
    if perf is not None and perf.time:
        step = max(1, len(perf.time) // 200)
        out["time"] = perf.time[::step]
        out["thrust"] = perf.thrust[::step]
        if result.ballistics is not None:
            out["chamber_pressure"] = result.ballistics.chamber_pressure[::step]
    if result.burnback is not None and result.burnback.burn_area:
        bb = result.burnback
        step = max(1, len(bb.burn_area) // 200)
        out["burn_distance"] = bb.burn_distance[::step]
        out["burn_area"] = bb.burn_area[::step]
    return out


@app.get("/families")
def families() -> dict[str, Any]:
    """Families plus the parameter bounds their inverse design space exposes."""
    from ..inverse.design import default_design_space

    out = []
    for family in GrainFamily:
        try:
            space = default_design_space(family, 0.15, (0.1, 0.6))
        except ValueError as exc:
            out.append({"family": family.value, "params": [], "note": str(exc)})
            continue
        out.append({
            "family": family.value,
            "burn_mode": space.burn_mode.value,
            "params": [
                {"name": p.name, "low": p.low, "high": p.high,
                 "scale": p.scale, "integer": p.integer}
                for p in space.parameters
            ],
        })
    return {"families": out}


@app.post("/inverse")
def inverse(req: InverseRequest, curves: bool = False,
            sketch: bool = False) -> dict[str, Any]:
    """Recover grain parameters and the throat from ballistic targets."""
    from ..inverse.design import default_design_space, inverse_design

    if not any((req.thrust, req.burn_time, req.total_impulse, req.specific_impulse)):
        raise HTTPException(
            status_code=422,
            detail="give at least one of thrust / burn_time / total_impulse / specific_impulse",
        )
    try:
        family = GrainFamily(req.family)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    try:
        prop = get_propellant(req.propellant)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    length_hint = req.length or req.outer_diameter * 3.0
    try:
        space = default_design_space(
            family, req.outer_diameter, (0.1, max(0.3, length_hint * 1.5)),
            burn_mode=BurnMode(req.burn_mode),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    requirement = RequirementSet(
        name="rest-inverse", thrust=req.thrust, burn_time=req.burn_time,
        total_impulse=req.total_impulse, specific_impulse=req.specific_impulse,
        outer_diameter=req.outer_diameter,
        max_chamber_pressure=req.max_chamber_pressure,
    )
    try:
        result = inverse_design(requirement, prop, space,
                                method=req.method, max_iter=req.max_iter)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    summary = _summary(result)
    seg = result.grain.segments[0]
    summary["recovered"] = {
        "family": seg.family.value,
        "length": seg.length,
        "outer_diameter": seg.outer_diameter,
        "burn_mode": seg.burn_mode.value,
        "params": seg.params,
    }
    if curves:
        summary["curves"] = _curves(result)
    if sketch:
        summary["sketch"] = sketch_payload(result.grain)
    return summary


@app.post("/sketch")
def sketch(spec: GrainSpec) -> dict[str, Any]:
    """2-D port outline(s) for a fully specified grain, for SVG rendering."""
    try:
        grain = make_grain(spec.family, spec.length, spec.outer_diameter,
                           spec.params, spec.burn_mode)
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return sketch_payload(grain)


@app.post("/grain/stl")
def grain_stl(spec: GrainSpec) -> Response:
    """Triangulated grain STL (ASCII) for download / 3-D preview."""
    try:
        grain = make_grain(spec.family, spec.length, spec.outer_diameter,
                           spec.params, spec.burn_mode)
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "grain.stl"
        try:
            grain_to_stl(grain, path)
        except Exception as exc:  # degenerate geometry (no positive area)
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        payload = path.read_bytes()
    return Response(
        content=payload, media_type="model/stl",
        headers={"Content-Disposition": 'attachment; filename="grain.stl"'},
    )


# ---------------------------------------------------------------------------
# static front end (plan §M5)
# ---------------------------------------------------------------------------
_FRONTEND = Path(__file__).resolve().parent.parent / "web"


def frontend_available() -> bool:
    return (_FRONTEND / "index.html").is_file()


if frontend_available():  # pragma: no branch - presence is fixed at import time
    @app.get("/", include_in_schema=False)
    def index() -> RedirectResponse:
        return RedirectResponse(url="/ui/")

    app.mount("/ui", StaticFiles(directory=str(_FRONTEND), html=True), name="ui")
