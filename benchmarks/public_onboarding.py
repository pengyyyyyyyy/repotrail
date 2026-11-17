"""Run the pinned public onboarding corpus with prepare, index, or retrieval."""
from __future__ import annotations

import argparse
import getpass
import json
import platform
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
from sqlalchemy import func, select

from repotrail.export import write_json
from repotrail.index import build_index
from repotrail.team.database import Chunk, Database, Project, Snapshot
from repotrail.team.knowledge import active_documents, retrieve
from repotrail.team.worker import Runtime
from .team import KeywordOnly


def prepare(repositories, checkouts):
    for repository in repositories:
        root = (checkouts / repository["name"]).resolve()
        if not root.exists():
            subprocess.run(["git", "clone", "--no-checkout", repository["url"], str(root)],
                           check=True, timeout=180)
            subprocess.run(["git", "-C", str(root), "checkout", "--detach", repository["revision"]],
                           check=True, timeout=30)
        index = build_index(root)
        if index.revision != repository["revision"] or index.dirty:
            raise ValueError(f"Checkout must be clean and pinned: {repository['name']}")
        if index.warnings:
            raise ValueError(f"Source analysis warnings: {repository['name']}: {index.warnings}")
        for question in repository["questions"]:
            source = (root / question["file"]).read_text("utf-8")
            if question["fragment"] not in source:
                raise ValueError(f"Reference fragment missing: {question['id']}")
            if question.get("symbol") and question["symbol"] not in index.symbols:
                raise ValueError(f"Reference symbol missing: {question['id']}")
        print(json.dumps({"repository": repository["name"], "revision": index.revision,
                          "files": len(index.files), "symbols": len(index.symbols),
                          "references_verified": len(repository["questions"])}, ensure_ascii=False))


def index_projects(repositories, args):
    password = args.password_file.read_text("utf-8").rstrip("\r\n") if args.password_file else getpass.getpass("Password: ")
    with httpx.Client(base_url=args.base_url.rstrip("/"), timeout=30) as client:
        response = client.post("/api/login", json={"username": args.username, "password": password})
        response.raise_for_status()
        client.headers["X-CSRF-Token"] = response.json()["csrf"]
        response = client.get("/api/projects")
        response.raise_for_status()
        projects = response.json()
        for repository in repositories:
            existing = next((p for p in projects if p["name"] == repository["project_name"]), None)
            if existing:
                key = existing["id"]
                if existing["snapshot_id"]:
                    response = client.get(f"/api/projects/{key}")
                    response.raise_for_status()
                    overview = response.json().get("overview") or {}
                    if overview.get("revision") == repository["revision"]:
                        continue
                response = client.get(f"/api/projects/{key}")
                response.raise_for_status()
                if response.json()["source"] != str((args.checkouts / repository["name"]).resolve()):
                    raise ValueError(f"Existing project uses a different source: {repository['name']}")
            else:
                response = client.post("/api/projects", json={"name": repository["project_name"],
                    "source": str((args.checkouts / repository["name"]).resolve()),
                    "description": f"公开入职培训评测 · {repository['url']} · {repository['license']}"})
                response.raise_for_status()
                key = response.json()["id"]
            response = client.post(f"/api/projects/{key}/index", json={})
            response.raise_for_status()
            job_id = response.json()["id"]
            started = time.monotonic()
            previous = None
            while time.monotonic() - started < 900:
                response = client.get(f"/api/jobs/{job_id}")
                response.raise_for_status()
                job = response.json()
                if job["stage"] != previous:
                    print(repository["name"], job["stage"], flush=True)
                    previous = job["stage"]
                if job["status"] == "complete":
                    break
                if job["status"] in {"failed", "cancelled"}:
                    raise ValueError(job["error"] or job["status"])
                time.sleep(1)
            else:
                raise TimeoutError(f"Indexing still running: {repository['name']}")


def retrieval(repositories, args):
    database = Database(args.data_dir)
    runtime = Runtime(database, args.data_dir)
    if runtime.embeddings.settings()["mode"] == "off":
        raise ValueError("Configure an embedding model before comparing hybrid retrieval")
    results, corpus = [], []
    for repository in repositories:
        with database.session() as session:
            project = session.scalar(select(Project).where(Project.name == repository["project_name"]))
            if not project or not project.snapshot_id:
                raise ValueError(f"Index this project first: {repository['name']}")
            snapshot = session.get(Snapshot, project.snapshot_id)
            if snapshot.payload["revision"] != repository["revision"]:
                raise ValueError(f"Indexed revision differs: {repository['name']}")
            documents = active_documents(session, project.id)
            chunks = session.scalar(select(func.count()).select_from(Chunk).where(Chunk.project_id == project.id))
            corpus.append({"repository": repository["name"], "url": repository["url"],
                           "revision": repository["revision"], "license": repository["license"],
                           "files": len(snapshot.payload["files"]), "symbols": len(snapshot.payload["symbols"]),
                           "documents": len(documents), "chunks": chunks})
        for mode, embeddings in [("keyword", KeywordOnly()), ("hybrid", runtime.embeddings)]:
            for case in repository["questions"]:
                started = time.perf_counter()
                found = retrieve(database, embeddings, project.id, project.snapshot_id, documents,
                                 case["question"], limit=5, scope=case["scope"])
                rank = next((rank for rank, source in enumerate(found, 1)
                             if source["location"]["file"] == case["file"]), None)
                symbol_hit = any(source["location"].get("symbol_id") == case["symbol"]
                                 for source in found) if "symbol" in case else None
                results.append({"repository": repository["name"], "id": case["id"], "mode": mode,
                    "question": case["question"], "scope": case["scope"], "expected_file": case["file"],
                    "file_rank": rank, "symbol_hit_at_5": symbol_hit,
                    "seconds": round(time.perf_counter() - started, 4),
                    "sources": [source["location"] for source in found]})
    summary = {}
    for mode in ("keyword", "hybrid"):
        values = [row for row in results if row["mode"] == mode]
        symbols = [row for row in values if row["symbol_hit_at_5"] is not None]
        summary[mode] = {"file_hits_at_5": sum(row["file_rank"] is not None for row in values),
                         "questions": len(values), "symbol_hits_at_5": sum(row["symbol_hit_at_5"] for row in symbols),
                         "symbol_questions": len(symbols),
                         "mrr_at_5": round(sum(1 / row["file_rank"] for row in values if row["file_rank"]) / len(values), 4)}
    return {"date_utc": datetime.now(timezone.utc).isoformat(), "python": platform.python_version(),
            "platform": platform.system(), "embedding": runtime.embeddings.settings(), "corpus": corpus,
            "metric": "file Hit@5 and file MRR@5; exact symbol Hit@5 for code questions",
            "reference_review": "source-inspected; independent human review pending",
            "summary": summary, "results": results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["prepare", "index", "retrieval"])
    parser.add_argument("--checkouts", type=Path, default=Path("benchmarks/checkouts"))
    parser.add_argument("--data-dir", type=Path, default=Path(".repotrail"))
    parser.add_argument("--base-url", default="http://127.0.0.1:8766")
    parser.add_argument("--username", default="admin")
    parser.add_argument("--password-file", type=Path)
    parser.add_argument("--output", type=Path, default=Path("benchmarks/results/public-onboarding.json"))
    args = parser.parse_args()
    repositories = json.loads(Path(__file__).with_name("public-onboarding.json").read_text("utf-8"))["repositories"]
    if args.command in {"prepare", "index"}:
        prepare(repositories, args.checkouts)
    if args.command == "index":
        index_projects(repositories, args)
    if args.command == "retrieval":
        report = retrieval(repositories, args)
        write_json(args.output, report)
        print(json.dumps(report["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
