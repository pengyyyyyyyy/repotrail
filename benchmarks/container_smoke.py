"""Exercise an empty, disposable Compose workspace through its HTTP API."""
from __future__ import annotations

import os
from pathlib import Path
import secrets
import time

import httpx
from sqlalchemy import func, select

from repotrail.team.database import Database, User
from repotrail.team.security import create_user


def main():
    if os.environ.get("REPOTRAIL_SMOKE_TEST") != "1":
        raise RuntimeError("Run this fixture in an isolated Compose test deployment with REPOTRAIL_SMOKE_TEST=1")
    database = Database(Path(os.environ["REPOTRAIL_DATA_DIR"]))
    database.migrate()
    password = secrets.token_urlsafe(24)
    with database.session.begin() as session:
        if session.scalar(select(func.count()).select_from(User)):
            raise RuntimeError("The Compose smoke fixture requires an empty test database")
        create_user(session, "ci-admin", "CI fixture", password, admin=True)
    with httpx.Client(base_url="http://127.0.0.1:8765", timeout=20) as client:
        response = client.post("/api/login", json={"username": "ci-admin", "password": password})
        response.raise_for_status()
        client.headers["X-CSRF-Token"] = response.json()["csrf"]
        assert "RepoTrail" in client.get("/").text
        response = client.post("/api/projects", json={"name": "Container fixture", "source": "/app/repotrail/sample"})
        response.raise_for_status()
        project = response.json()["id"]
        jobs = [client.post(f"/api/projects/{project}/index", json={}),
                client.post(f"/api/projects/{project}/documents", files={
                    "file": ("fixture.md", b"# Team fixture\nReview code and run tests before release.")})]
        for response in jobs:
            response.raise_for_status()
            key = response.json()["id"]
            for _ in range(120):
                result = client.get(f"/api/jobs/{key}").json()
                if result["status"] == "complete":
                    break
                if result["status"] in {"failed", "cancelled"}:
                    raise RuntimeError(result["error"])
                time.sleep(0.5)
            else:
                raise RuntimeError("Standalone worker did not complete the fixture job")
        info = client.get(f"/api/projects/{project}").json()
        assert info["overview"]["symbol_count"] > 0
        source = client.get(f"/api/snapshots/{info['snapshot_id']}/source", params={"file": "cli.py"})
        source.raise_for_status()
        assert "def main" in source.json()["source"]
        assert any(item["name"] == "fixture.md" for item in client.get(f"/api/projects/{project}/documents").json())
    print("Compose smoke passed: authentication, PostgreSQL, source snapshot, document ingestion, standalone worker")


if __name__ == "__main__":
    main()
