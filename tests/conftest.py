"""Shared pytest fixtures."""

import pytest

from srm_open.api.facade import get_propellant, make_grain


@pytest.fixture
def ap_htpb():
    return get_propellant("ap_htpb_80")


@pytest.fixture
def tube_grain():
    return make_grain("tube", 0.5, 0.16, {"inner_diameter": 0.06})


@pytest.fixture
def star_grain():
    return make_grain("star", 0.5, 0.16,
                      {"n_points": 6, "inner_diameter": 0.04,
                       "point_diameter": 0.12, "point_half_angle": 30})
