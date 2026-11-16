"""Reproducible retrieval comparison and authenticated five-way live chat measurement."""
from __future__ import annotations

import argparse
import getpass
import json
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
from sqlalchemy import select

from repotrail.team.database import Database, Project
from repotrail.team.knowledge import active_documents, retrieve
from repotrail.team.worker import Runtime


class KeywordOnly:
    def embed(self, texts, query=False):
        return "", []


def retrieval(args):
    database = Database(args.data_dir)
    database.migrate()
    runtime = Runtime(database, args.data_dir)
    with database.session() as session:
        project = session.get(Project, args.project) if args.project else session.scalar(select(Project).order_by(Project.created))
        if not project or not project.snapshot_id:
            raise ValueError("请先建立示例项目源码索引")
        documents = active_documents(session, project.id)
    cases = json.loads((Path(__file__).parent / "onboarding-cases.json").read_text("utf-8"))
    results = []
    for mode, embeddings in [("keyword", KeywordOnly()), ("hybrid", runtime.embeddings)]:
        for case in cases:
            started = time.perf_counter()
            found = retrieve(database, embeddings, project.id, project.snapshot_id, documents,
                             case["question"], limit=5, scope=case["scope"])
            results.append({"mode": mode, "case": case["id"], "question": case["question"],
                "hit_at_5": any(source["location"]["file"].endswith(case["file_suffix"]) for source in found),
                "seconds": round(time.perf_counter() - started, 3),
                "sources": [{"id": source["id"], "location": source["location"]} for source in found]})
    return {"kind": "retrieval", "snapshot_id": project.snapshot_id,
            "embedding": runtime.embeddings.settings(), "cases": results,
            "summary": {mode: {"hits": sum(result["hit_at_5"] for result in results if result["mode"] == mode),
                                "total": len(cases)} for mode in ["keyword", "hybrid"]}}


def load(args):
    password = args.password_file.read_text("utf-8").rstrip("\r\n") if args.password_file else getpass.getpass("Password: ")
    with httpx.Client(base_url=args.base_url.rstrip("/"), timeout=30) as client:
        response = client.post("/api/login", json={"username": args.username, "password": password})
        response.raise_for_status()
        client.headers["X-CSRF-Token"] = response.json()["csrf"]
        project = args.project or client.get("/api/projects").json()[0]["id"]
        courses = client.get(f"/api/projects/{project}/courses").json()
        course = args.course or next(item["id"] for item in courses if item["status"] == "published")
        background = None
        background_started = time.perf_counter()
        if args.background:
            response = client.post(f"/api/projects/{project}/reembed", json={})
            response.raise_for_status()
            background = response.json()

        def measure(number):
            started = time.perf_counter()
            conversation = client.post(f"/api/projects/{project}/conversations", json={"course_id": course})
            conversation.raise_for_status()
            key = conversation.json()["id"]
            result = {"number": number, "conversation_id": key, "status": "failed"}
            try:
                question = client.post(f"/api/conversations/{key}/messages", json={
                    "content": "请用三点概括团队开发规范，并引用对应资料。", "request_id": f"benchmark-{time.time_ns()}-{number}"})
                question.raise_for_status()
                job = question.json()["id"]
                result["job_id"] = job
                while time.perf_counter() - started < 300:
                    response = client.get(f"/api/jobs/{job}")
                    response.raise_for_status()
                    current = response.json()
                    if current["status"] in {"complete", "failed", "cancelled"}:
                        result.update(status=current["status"], error=current["error"], searches=current["result"].get("searches", []))
                        break
                    time.sleep(0.25)
                state = client.get(f"/api/conversations/{key}").json()
                answer = state["messages"][-1]
                result.update(answer=answer["content"], citations=answer["citations"], seconds=round(time.perf_counter() - started, 3))
            except (httpx.HTTPError, KeyError, IndexError):
                result["error"] = "HTTP request or response failed"
            finally:
                if not args.keep and result.get("status") in {"complete", "failed", "cancelled"}:
                    client.delete(f"/api/conversations/{key}")
            return result

        with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
            results = list(pool.map(measure, range(args.concurrency)))
        if background:
            while time.perf_counter() - background_started < 300:
                background = client.get(f"/api/jobs/{background['id']}").json()
                if background["status"] in {"complete", "failed", "cancelled"}:
                    break
                time.sleep(0.5)
            background = {"id": background["id"], "status": background["status"],
                          "seconds": round(time.perf_counter() - background_started, 3)}
        response = client.get("/api/usage")
        records = response.json() if response.status_code == 200 else []
        ids = {item.get("job_id") for item in results}
        usage = [item["metrics"] for item in records if item["job_id"] in ids]
        times = sorted(item["seconds"] for item in results if "seconds" in item)
        return {"kind": "live_chat", "concurrency": args.concurrency, "course_id": course, "results": results,
                "background": background,
                "summary": {"complete": sum(item["status"] == "complete" for item in results), "total": len(results),
                    "median_seconds": statistics.median(times) if times else None,
                    "maximum_seconds": max(times) if times else None,
                    "usage_available": response.status_code == 200,
                    "prompt_tokens": sum(item["prompt_tokens"] for item in usage) if response.status_code == 200 else None,
                    "completion_tokens": sum(item["completion_tokens"] for item in usage) if response.status_code == 200 else None,
                    "estimated_costs": [{"amount": item["estimated_cost"], "currency": item["currency"]}
                                        for item in usage if item["estimated_cost"] is not None]}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["retrieval", "load"])
    parser.add_argument("--data-dir", type=Path, default=Path(".repotrail"))
    parser.add_argument("--project")
    parser.add_argument("--course")
    parser.add_argument("--base-url", default="http://127.0.0.1:8766")
    parser.add_argument("--username", default="admin")
    parser.add_argument("--password-file", type=Path)
    parser.add_argument("--concurrency", type=int, choices=range(1, 6), default=5)
    parser.add_argument("--keep", action="store_true", help="Keep benchmark conversations")
    parser.add_argument("--background", action="store_true", help="Rebuild project vectors concurrently")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = retrieval(args) if args.command == "retrieval" else load(args)
    output = args.output or Path("benchmarks/results") / ("team-" + args.command + ".json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
    print(json.dumps({"report": str(output), "summary": report["summary"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
