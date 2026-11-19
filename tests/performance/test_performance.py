"""Performance budget checks (NFR-3).

The plan sets two budgets: a zero-dimensional internal-ballistics solve under
one second, and a level-set run under thirty seconds on a teaching-grade grid.
Those are the two numbers that decide whether a student can iterate a design in
a lecture slot, so they are asserted here rather than eyeballed.

Timings are wall clock on the machine running the test and are printed, so a
failure shows how far over budget the run went. The forward-design budget is
loose (several times the plan figure) to stay meaningful on slow CI runners
while still catching an algorithmic regression, which shows up as a change of
order rather than a few percent.

Run with::

    PYTHONPATH=src pytest -m performance -q -s
"""

import time

import pytest

from srm_open.api import design_from_template
from srm_open.api.facade import get_propellant
from srm_open.burnback.levelset import levelset_grain_burnback
from srm_open.io import TemplateLibrary
from srm_open.models.requirements import RequirementSet
from srm_open.orchestration.study import monte_carlo

pytestmark = pytest.mark.performance


def _timed(fn, *args, **kwargs):
    t0 = time.perf_counter()
    out = fn(*args, **kwargs)
    return out, time.perf_counter() - t0


def test_zd_ballistics_under_one_second():
    """A full forward design (thermo + burn-back + p_c(t) integration)."""
    _, dt = _timed(design_from_template, "tube_simple", "ap_htpb_80",
                   {"thrust": 4000, "burn_time": 3.0})
    print(f"\nNFR-3 forward design wall clock: {dt * 1000:.0f} ms (budget 1000 ms)")
    assert dt < 1.0


def test_levelset_under_thirty_seconds():
    grain = TemplateLibrary.load_default().geometry("star_6_point")
    _, dt = _timed(levelset_grain_burnback, grain, n_snapshots=20, nx=60, ny=60)
    print(f"NFR-3 level-set 60x60x20 wall clock: {dt:.2f} s (budget 30 s)")
    assert dt < 30.0


def test_monte_carlo_batch_stays_within_budget():
    """A 20-sample study must stay within 40x a single design.

    Guards against recomputing per-sample work that should be hoisted, which is
    invisible in a correctness test.
    """
    req = RequirementSet(name="perf", thrust=4000.0, burn_time=3.0)
    grain = TemplateLibrary.load_default().geometry("tube_simple")
    prop = get_propellant("ap_htpb_80")

    _, dt1 = _timed(design_from_template, "tube_simple", "ap_htpb_80",
                    {"thrust": 4000, "burn_time": 3.0})
    study, dt_n = _timed(monte_carlo, req, grain, prop,
                         {"thrust": {"sigma": 80.0, "target": "requirement"}},
                         n_samples=20, seed=7)
    print(f"NFR-3 batch: 1 design {dt1 * 1000:.0f} ms, 20 samples {dt_n:.2f} s")
    assert len(study.samples) == 20
    assert dt_n < 40 * dt1
