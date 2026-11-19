"""Example 08: inverse path A, identifiability, sweep and Monte-Carlo.

Runs the whole study from synthetic-but-reproducible inputs so every number in
the output can be regenerated: no stored results are read.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from srm_open.geometry.analysis import port_polygon
from srm_open.inverse.design import (
    default_design_space,
    fit_outline,
    sensitivity_matrix,
)
from srm_open.io.templates import TemplateLibrary
from srm_open.models.grain import GrainFamily
from srm_open.models.requirements import RequirementSet
from srm_open.orchestration.study import monte_carlo, sweep
from srm_open.propellant.database import PropellantDB


def path_a() -> dict:
    """Recover slotted-grain parameters from a noisy 2D port outline."""
    truth = {"inner_diameter": 0.05, "n_slots": 6, "slot_width": 0.012, "slot_depth": 0.04}
    space = default_design_space(GrainFamily.SLOTTED, 0.16, (0.6, 0.6))
    poly = port_polygon(space.to_geometry(space.to_vector(truth, 0.6)).segments[0])
    pts = np.asarray(poly.exterior.coords)
    pts = pts + np.random.default_rng(11).normal(0.0, 1e-4, pts.shape)  # 0.1 mm noise
    res = fit_outline(list(map(tuple, pts)), GrainFamily.SLOTTED, 0.16, 0.6,
                      method="nelder_mead", max_iter=400)
    return {
        "truth": truth,
        "fit": res["params"],
        "objective": res["objective"],
        "relative_errors": res["relative_errors"],
    }


def conditioning() -> dict:
    """Condition number and collinearity for a star grain."""
    space = default_design_space(GrainFamily.STAR, 0.16, (0.5, 1.0))
    x = space.to_vector({"inner_diameter": 0.05, "point_diameter": 0.13,
                         "point_half_angle": 25, "n_points": 6}, 0.8)
    info = sensitivity_matrix(space, x)
    return {
        "condition_number": info["condition_number"],
        "conditioning": info["conditioning"],
        "frozen": info["frozen"],
        "inactive": info["inactive"],
        "verdicts": {p["name"]: p["verdict"] for p in info["per_parameter"]},
    }


def main() -> None:
    grain = TemplateLibrary.load_default().get("tube_simple")
    prop = PropellantDB.load_default().get("ap_htpb_80")
    req = RequirementSet(name="study", thrust=6000.0, burn_time=4.0,
                         max_chamber_pressure=9e6)

    report = {
        "inverse_path_a": path_a(),
        "identifiability": conditioning(),
        "sweep": sweep(req, grain, prop, "max_chamber_pressure",
                       [6e6, 7e6, 8e6]).summary(),
        "monte_carlo": monte_carlo(
            req, grain, prop,
            {"density": {"sigma": 15.0}, "burn_rate.a": {"sigma": 2e-7}},
            n_samples=40, seed=12345).summary(),
    }
    out = Path(__file__).with_name("study_result.json")
    out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(json.dumps(report, indent=2, default=str))
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
