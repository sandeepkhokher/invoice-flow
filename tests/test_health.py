from fastapi.testclient import TestClient

HEALTHY = {"status": "ok", "database": "ok", "worker": "running"}


def test_health_reports_database_ok(app):
    # `with` runs the app's lifespan, which starts the in-process worker (S7, NFR-5).
    with TestClient(app) as client:
        resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == HEALTHY


def test_home_renders(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "invoice-flow" in resp.text
