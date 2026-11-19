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
