"""Health endpoints and the headers every response carries."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import create_app

client = TestClient(create_app())


def test_healthz_is_ok_without_any_dependency() -> None:
    # No database is running in the unit suite. That is the point: /healthz must
    # not touch one, or a DB blip makes the load balancer kill healthy tasks.
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["service"] == "backend"


def test_every_response_carries_a_request_id() -> None:
    assert client.get("/healthz").headers["x-request-id"]


def test_an_upstream_request_id_is_honoured() -> None:
    # The load balancer and the sibling services propagate this; traces must
    # join up across service boundaries.
    response = client.get("/healthz", headers={"x-request-id": "abc-123"})
    assert response.headers["x-request-id"] == "abc-123"


def test_security_headers_are_present() -> None:
    headers = client.get("/healthz").headers
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in headers["content-security-policy"]


def test_readyz_reports_auth_as_disabled_rather_than_broken() -> None:
    """Auth being off must not read as a fault, and auth being *down* must not
    make this service unready — tokens are verified offline against cached keys,
    so an auth outage degrades new logins, not this service."""
    body = client.get("/readyz").json()
    assert body["checks"]["auth"] == "disabled"


def test_readyz_reports_the_configured_backends() -> None:
    checks = client.get("/readyz").json()["checks"]
    assert checks["storage"] == "local"
    assert checks["queue"] == "postgres"
