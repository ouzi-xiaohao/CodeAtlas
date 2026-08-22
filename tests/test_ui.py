from fastapi.testclient import TestClient

from app.main import app


def test_frontend_and_assets_are_served():
    with TestClient(app) as client:
        page = client.get("/")
        script = client.get("/static/app.js")
        styles = client.get("/static/styles.css")
        health = client.get("/api/v1/health")

    assert page.status_code == 200
    assert "研发知识助手" in page.text
    assert script.status_code == 200
    assert styles.status_code == 200
    assert health.status_code == 200
