from pathlib import Path
import time

import pytest
from fastapi.testclient import TestClient

from repotrail.server import create_app


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path)) as test_client:
        yield test_client


def test_demo_source_and_exports(client):
    response = client.get("/api/demo")
    assert response.status_code == 200
    data = response.json()
    assert data["trail"]["mode"] == "offline_demo"
    key = data["repository"]["repository_id"]
    assert client.get(f"/api/source/{key}", params={"file": "cli.py"}).status_code == 200
    assert client.get(f"/api/source/{key}", params={"file": "../.env"}).status_code == 404
    for format in ["markdown", "codetour", "json"]:
        exported = client.get(f"/api/export/{data['job_id']}", params={"format": format})
        assert exported.status_code == 200
        assert "attachment" in exported.headers["content-disposition"]
    assert client.get(f"/api/export/{data['job_id']}?format=exe").status_code == 400


def test_demo_generation_runs_through_normal_job_entry(client):
    data = client.get("/api/demo").json()
    response = client.post("/api/generate", json={"repository_id": data["repository"]["repository_id"],
                                                "flow_id": data["repository"]["flows"][0]["id"]})
    assert response.status_code == 202
    for _ in range(50):
        result = client.get("/api/jobs/" + response.json()["job_id"]).json()
        if result["status"] == "complete":
            break
        time.sleep(0.01)
    assert result["status"] == "complete"
    assert result["result"]["metrics"]["calls"] == 0


def test_analyze_without_model_and_reject_missing_key(client, monkeypatch):
    monkeypatch.delenv("REPOTRAIL_API_KEY", raising=False)
    root = Path(__file__).parents[1] / "repotrail" / "sample"
    response = client.post("/api/analyze", json={"source": str(root)})
    assert response.status_code == 200
    data = response.json()
    result = client.post("/api/generate", json={"repository_id": data["repository_id"],
                                            "flow_id": data["flows"][0]["id"]})
    assert result.status_code == 400
    assert "REPOTRAIL_API_KEY" in result.json()["detail"]


def test_bad_origin_and_host_are_rejected(client):
    response = client.post("/api/analyze", json={"source": "anything"}, headers={"origin": "https://other.example"})
    assert response.status_code == 403
    assert client.get("/api/health", headers={"host": "other.example"}).status_code == 400


def test_unknown_snapshots_and_invalid_inputs(client):
    assert client.get("/api/jobs/missing").status_code == 404
    assert client.post("/api/analyze", json={"source": "https://example.com/repo"}).status_code == 400
    assert client.post("/api/analyze", json={"source": ""}).status_code == 422
    assert client.post("/api/generate", json={"repository_id": "missing", "flow_id": "x"}).status_code == 404


def test_index_and_static_assets_are_served(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "RepoTrail" in response.text
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/style.css").status_code == 200

