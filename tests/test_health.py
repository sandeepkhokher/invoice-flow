def test_health_reports_database_ok(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "database": "ok"}


def test_home_renders(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "invoice-flow" in resp.text
