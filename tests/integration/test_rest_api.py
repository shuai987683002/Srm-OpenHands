"""REST layer tests (optional ``api`` extra).

Skipped when FastAPI is absent so the core suite stays installable with the
light dependency set. These call the real engine through the HTTP layer; nothing
is mocked, so a broken facade shows up here too.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi", reason="REST API needs the optional 'api' extra")

from fastapi.testclient import TestClient  # noqa: E402

from srm_open.api.rest import app  # noqa: E402


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


def test_health_reports_cad_availability(client):
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert isinstance(body["cad_export"], bool)


def test_templates_and_propellants_are_listed(client):
    templates = client.get("/templates").json()["templates"]
    assert "tube_simple" in templates and len(templates) >= 10

    props = client.get("/propellants").json()["propellants"]
    ids = {p["id"] for p in props}
    assert "ap_htpb_80" in ids
    # every shipped entry must carry provenance, same rule as the database test
    assert all(p["source"] for p in props)


def test_thermo_endpoint_matches_the_library(client):
    from srm_open.api import get_propellant
    from srm_open.thermo.backend import evaluate

    body = client.get("/propellants/ap_htpb_80/thermo").json()
    ref = evaluate(get_propellant("ap_htpb_80"), 7.0e6)
    assert body["c_star"] == pytest.approx(ref.c_star, rel=1e-9)
    assert body["gamma"] == pytest.approx(ref.gamma, rel=1e-9)


def test_design_summary_hits_the_requested_indicators(client):
    resp = client.post(
        "/design?summary=true",
        json={
            "thrust": 8000.0,
            "burn_time": 4.0,
            "max_chamber_pressure": 9e6,
            "outer_diameter": 0.16,
            "length_ratio": 5.0,
            "thrust_profile": "neutral",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    perf = body["performance"]
    assert perf["average_thrust"] == pytest.approx(8000.0, rel=0.05)
    assert perf["burn_time"] == pytest.approx(4.0, rel=0.15)
    assert body["verification"]["failed"] == []
    # the summary must carry the provenance hash so a result can be traced back
    assert len(body["input_hash"]) == 64


def test_full_result_is_serialisable_json(client):
    resp = client.post(
        "/design",
        json={"thrust": 3000.0, "burn_time": 2.0, "template": "tube_simple"},
    )
    assert resp.status_code == 200
    body = resp.json()
    # time series survive the round trip as lists of numbers
    assert len(body["ballistics"]["time"]) > 10
    assert len(body["ballistics"]["chamber_pressure"]) == len(body["ballistics"]["time"])
    assert body["grain"]["segments"][0]["family"] == "tube"


def test_unknown_template_and_bad_input_are_distinguished(client):
    missing = client.post("/design", json={"thrust": 100.0, "burn_time": 1.0, "template": "nope"})
    assert missing.status_code == 404

    invalid = client.post("/design", json={"thrust": -5.0, "burn_time": 1.0})
    assert invalid.status_code == 422


# ---------------------------------------------------------------------------
# M5 web surface: families, inverse design, sketch, STL, front-end assets
# ---------------------------------------------------------------------------


def test_families_expose_inverse_design_bounds(client):
    body = client.get("/families").json()
    by_name = {f["family"]: f for f in body["families"]}
    # every family in the enum is reported, including the one with no parameters
    assert "star" in by_name and "custom_polygon" in by_name
    assert by_name["custom_polygon"]["params"] == []
    assert by_name["custom_polygon"]["note"]

    star = {p["name"]: p for p in by_name["star"]["params"]}
    assert star["n_points"]["integer"] is True
    assert star["n_points"]["low"] < star["n_points"]["high"]
    assert by_name["star"]["burn_mode"] == "internal"


def test_inverse_design_recovers_the_requested_thrust_and_burn_time(client):
    resp = client.post(
        "/inverse?curves=true&sketch=true",
        json={
            "family": "star",
            "outer_diameter": 0.14,
            "thrust": 4000.0,
            "burn_time": 2.5,
            "max_iter": 40,
        },
    )
    assert resp.status_code == 200
    body = resp.json()

    recovered = body["recovered"]
    assert recovered["family"] == "star"
    assert recovered["outer_diameter"] == pytest.approx(0.14)
    assert set(recovered["params"]) >= {"inner_diameter", "point_diameter", "n_points"}

    # the closed loop must land on the targets it was given
    assert body["performance"]["average_thrust"] == pytest.approx(4000.0, rel=0.10)
    assert body["performance"]["burn_time"] == pytest.approx(2.5, rel=0.10)
    assert body["verification"]["failed"] == []

    # curves and the cross-section come back from the same run
    assert len(body["curves"]["time"]) == len(body["curves"]["thrust"])
    assert body["sketch"]["segments"][0]["family"] == "star"


def test_inverse_design_rejects_requests_it_cannot_solve(client):
    no_target = client.post("/inverse", json={"family": "star", "outer_diameter": 0.14})
    assert no_target.status_code == 422

    bad_family = client.post(
        "/inverse",
        json={"family": "nope", "outer_diameter": 0.14, "thrust": 1000.0},
    )
    assert bad_family.status_code == 404

    # a free-form port has no parametric space, so there is nothing to invert
    no_space = client.post(
        "/inverse",
        json={"family": "custom_polygon", "outer_diameter": 0.14, "thrust": 1000.0},
    )
    assert no_space.status_code == 422


def test_sketch_endpoint_returns_closed_outlines(client):
    body = client.post(
        "/sketch",
        json={
            "family": "tube",
            "length": 0.3,
            "outer_diameter": 0.14,
            "params": {"inner_diameter": 0.05},
        },
    ).json()
    seg = body["segments"][0]
    assert seg["family"] == "tube"
    # the outer ring must close back on its first point
    assert seg["outer"][0] == seg["outer"][-1]
    assert len(seg["port_rings"]) == 1
    assert seg["port_rings"][0][0] == seg["port_rings"][0][-1]


def test_sketch_rejects_an_unknown_family(client):
    resp = client.post(
        "/sketch",
        json={"family": "nope", "length": 0.3, "outer_diameter": 0.14},
    )
    assert resp.status_code == 422


def test_grain_stl_download_is_a_valid_ascii_solid(client):
    resp = client.post(
        "/grain/stl",
        json={
            "family": "finocyl",
            "length": 0.3,
            "outer_diameter": 0.14,
            "params": {"inner_diameter": 0.05, "fin_width": 0.02,
                       "fin_length": 0.04, "n_fins": 6},
        },
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("model/stl")
    assert "attachment" in resp.headers["content-disposition"]

    text = resp.content.decode("ascii")
    assert text.startswith("solid grain")
    assert text.rstrip().endswith("endsolid grain")
    assert text.count("facet normal") == text.count("endfacet")
    assert text.count("facet normal") > 0


def test_front_end_assets_are_served(client):
    """The M5 UI ships inside the package, so it must load from the same app."""
    index = client.get("/ui/")
    assert index.status_code == 200
    assert "Srm-Open" in index.text

    for asset in ("app.js", "style.css"):
        assert client.get(f"/ui/{asset}").status_code == 200

    # the root redirects into the UI so a bare `srm serve` is usable
    root = client.get("/", follow_redirects=False)
    assert root.status_code in (302, 307)
    assert root.headers["location"].endswith("/ui/")
