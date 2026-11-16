import json
import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from repotrail.team.database import Conversation, Course, Job, Membership, Message, Progress, User
from repotrail.team.security import create_user
from repotrail.team.server import create_team_app


class FakeModel:
    def __init__(self, settings):
        self.settings = settings

    def complete(self, messages, **kwargs):
        if "Summarize this learning conversation" in messages[0]["content"]:
            return {"content": "已讨论项目入口与模块职责，需要继续追踪核心流程。"}
        if kwargs.get("tools"):
            return {"tool_calls": [{"id": "search-1", "type": "function", "function": {
                "name": "search_project", "arguments": json.dumps({"query": "main entry", "scope": "code"})}}]}
        system = messages[0]["content"]
        context = json.loads(messages[-1]["content"])
        if "Design 4" in system:
            value = {"title": "项目入职培训", "chapters": [
                {"title": title, "objectives": ["解释" + title], "query": title + " main run"}
                for title in ["业务背景", "系统架构", "核心流程", "开发规范"]]}
        elif "source_ids" in system:
            sources = context["sources"]
            value = {"content": "本章节围绕所提供的源码与项目文档解释具体职责和执行流程。" * 20,
                     "source_ids": [sources[0]["id"]] if sources else []}
        else:
            value = {"entries": [{"entry": item["id"], "title": item["id"], "reason": "项目入口"}
                                  for item in context["symbols"][:5]]}
        return {"content": json.dumps(value, ensure_ascii=False)}

    def stream(self, messages):
        assert messages[1]["role"] == "user"
        yield "根据当前课程与源码，"
        yield "这里是项目的核心执行流程。"

    def metrics(self):
        return {"model": "test-model", "calls": 1, "prompt_tokens": 20, "completion_tokens": 10,
                "usage_complete": True, "estimated_cost": None, "currency": "CNY", "elapsed_seconds": 0.1}


@pytest.fixture(params=["sqlite", "postgres"] if os.environ.get("REPOTRAIL_TEST_DATABASE_URL") else ["sqlite"])
def team(tmp_path, monkeypatch, request):
    connection = None
    database_name = None
    monkeypatch.delenv("REPOTRAIL_DATABASE_URL", raising=False)
    if request.param == "postgres":
        import uuid
        import psycopg
        from psycopg import sql
        from sqlalchemy.engine import make_url
        url = make_url(os.environ["REPOTRAIL_TEST_DATABASE_URL"])
        connection = psycopg.connect(url.set(drivername="postgresql").render_as_string(hide_password=False), autocommit=True)
        database_name = "repotrail_test_" + uuid.uuid4().hex
        connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name)))
        monkeypatch.setenv("REPOTRAIL_DATABASE_URL", url.set(database=database_name).render_as_string(hide_password=False))
    monkeypatch.setenv("REPOTRAIL_EMBEDDINGS", "off")
    monkeypatch.setenv("REPOTRAIL_API_KEY", "test-only-placeholder")
    monkeypatch.setattr("repotrail.team.worker.ModelClient", FakeModel)
    data = tmp_path / "state"
    source = tmp_path / "source"
    source.mkdir()
    (source / "main.py").write_text("\n".join(f"def entry_{n}():\n    return {n}\n" for n in range(6)))
    (source / "README.md").write_text("# Example\n业务背景与开发规范 main run", encoding="utf-8")
    app = create_team_app(data, start_worker=False)
    database = app.state.runtime.database
    with database.session.begin() as session:
        admin = create_user(session, "admin", "Admin", "example-password-123", admin=True)
        member = create_user(session, "member", "Member", "example-password-123")
        outsider = create_user(session, "outsider", "Outsider", "example-password-123")
    clients = [TestClient(app) for _ in range(3)]
    for client, username in zip(clients, ["admin", "member", "outsider"]):
        response = client.post("/api/login", json={"username": username, "password": "example-password-123"})
        assert response.status_code == 200
        client.headers["X-CSRF-Token"] = response.json()["csrf"]
    project = clients[0].post("/api/projects", json={"name": "Example", "source": str(source)}).json()["id"]
    clients[0].post(f"/api/projects/{project}/members", json={"user_id": member.id})
    yield {"app": app, "database": database, "admin": clients[0], "member": clients[1],
           "outsider": clients[2], "project": project, "member_id": member.id, "data": data}
    for client in clients:
        client.close()
    app.state.worker.pool.shutdown(wait=True)
    database.engine.dispose()
    if connection:
        connection.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database_name)))
        connection.close()


def finish(team, response):
    assert response.status_code == 202, response.text
    key = response.json()["id"]
    worker = team["app"].state.worker
    job = worker.claim(chat=False) or worker.claim(chat=True)
    assert job.id == key
    worker.run_job(job)
    result = team["admin"].get("/api/jobs/" + key)
    if result.status_code == 404:
        result = team["member"].get("/api/jobs/" + key)
    assert result.json()["status"] == "complete", result.text
    return result.json()


def published_course(team):
    admin, project = team["admin"], team["project"]
    finish(team, admin.post(f"/api/projects/{project}/index"))
    result = finish(team, admin.post(f"/api/projects/{project}/courses"))
    key = result["result"]["course_id"]
    assert admin.post(f"/api/courses/{key}/publish").status_code == 200
    return key


def test_authentication_csrf_roles_and_login_rate_limit(team):
    anonymous = TestClient(team["app"])
    assert anonymous.get("/api/projects").status_code == 401
    assert team["member"].get("/api/provider").status_code == 403
    assert team["member"].post("/api/users", json={"username": "new", "password": "long-test-password"}).status_code == 403
    assert team["admin"].post("/api/projects", json={"name": "bad", "source": "/x"},
                               headers={"X-CSRF-Token": "wrong"}).status_code == 403
    for _ in range(5):
        assert anonymous.post("/api/login", json={"username": "missing", "password": "bad"}).status_code == 401
    assert anonymous.post("/api/login", json={"username": "missing", "password": "bad"}).status_code == 429


def test_project_isolation_and_revocation(team):
    project = team["project"]
    assert team["outsider"].get(f"/api/projects/{project}").status_code == 404
    assert team["outsider"].get(f"/api/projects/{project}/documents").status_code == 404
    assert team["member"].get(f"/api/projects/{project}").status_code == 200
    team["admin"].delete(f"/api/projects/{project}/members/{team['member_id']}")
    assert team["member"].get(f"/api/projects/{project}").status_code == 404


def test_index_survives_restart_and_entries_exceed_three(team):
    result = finish(team, team["admin"].post(f"/api/projects/{team['project']}/index"))
    snapshot = result["result"]["snapshot_id"]
    info = team["member"].get(f"/api/projects/{team['project']}").json()
    assert len(info["overview"]["flows"]) > 3
    fresh = TestClient(create_team_app(team["data"], start_worker=False))
    fresh.cookies.update(team["member"].cookies)
    assert fresh.get(f"/api/snapshots/{snapshot}/source?file=main.py").status_code == 200
    assert team["outsider"].get(f"/api/snapshots/{snapshot}/source?file=main.py").status_code == 404
    assert fresh.get(f"/api/snapshots/{snapshot}/source?file=../.env").status_code == 404
    finish(team, team["member"].post(f"/api/projects/{team['project']}/entries"))
    assert len(team["member"].get(f"/api/projects/{team['project']}").json()["overview"]["flows"]) == 5


def test_documents_courses_and_admin_acceptance(team):
    admin, member, project = team["admin"], team["member"], team["project"]
    assert member.post(f"/api/projects/{project}/documents", files={"file": ("notes.md", b"content")}).status_code == 403
    upload = admin.post(f"/api/projects/{project}/documents", files={"file": ("notes.md", "# Team\n业务背景与开发规范".encode())})
    document_id = finish(team, upload)["result"]["document_id"]
    course = published_course(team)
    body = member.get(f"/api/courses/{course}").json()
    assert len(body["chapters"]) == 4
    assert body["chapters"][0]["citations"]
    assert member.put(f"/api/courses/{course}/progress", json={"chapters": [0, 1]}).status_code == 200
    assert member.put(f"/api/courses/{course}/progress", json={"chapters": [99]}).status_code == 400
    assert member.post(f"/api/courses/{course}/summary", json={"summary": "业务、架构、流程与开发规范的学习总结。" * 5}).status_code == 200
    submission = admin.get(f"/api/courses/{course}/submissions").json()[0]
    assert admin.post(f"/api/submissions/{submission['id']}/review", json={"approved": True, "feedback": "已完成"}).status_code == 200
    assert member.get(f"/api/courses/{course}/progress").json()["status"] == "approved"
    assert admin.delete(f"/api/projects/{project}/documents/{document_id}").status_code == 200
    assert admin.get(f"/api/courses/{course}").json()["newer_materials"] is True


def test_draft_visibility_and_immutable_publication(team):
    admin, project = team["admin"], team["project"]
    finish(team, admin.post(f"/api/projects/{project}/index"))
    course = finish(team, admin.post(f"/api/projects/{project}/courses"))["result"]["course_id"]
    assert team["member"].get(f"/api/courses/{course}").status_code == 404
    body = admin.get(f"/api/courses/{course}").json()
    edit = {"title": body["title"], "chapters": [{**{key: chapter[key] for key in ["title", "objectives", "content"]},
             "source_ids": [ref["id"] for ref in chapter["citations"]]} for chapter in body["chapters"]]}
    assert admin.put(f"/api/courses/{course}", json=edit).status_code == 200
    admin.post(f"/api/courses/{course}/publish")
    assert admin.put(f"/api/courses/{course}", json=edit).status_code == 409


def test_private_chat_idempotency_sharing_and_recovery(team):
    course = published_course(team)
    member, admin, project = team["member"], team["admin"], team["project"]
    conversation = member.post(f"/api/projects/{project}/conversations", json={"course_id": course}).json()["id"]
    assert admin.get(f"/api/conversations/{conversation}").status_code == 404
    request = {"content": "main 的执行流程是什么？", "request_id": "request-12345"}
    first = member.post(f"/api/conversations/{conversation}/messages", json=request)
    second = member.post(f"/api/conversations/{conversation}/messages", json=request)
    assert first.json()["id"] == second.json()["id"]
    assert admin.get("/api/jobs/" + first.json()["id"]).status_code == 404
    finish(team, first)
    assert member.get("/api/jobs/" + first.json()["id"]).json()["result"]["searches"][0]["scope"] == "code"
    state = member.get(f"/api/conversations/{conversation}").json()
    assert len(state["messages"]) == 2
    assert state["active_job_id"] is None
    assert state["messages"][-1]["status"] == "complete"
    answer = state["messages"][-1]["id"]
    assert admin.get(f"/api/projects/{project}/shared").json() == []
    member.post(f"/api/messages/{answer}/share", json={"shared": True})
    assert admin.get(f"/api/projects/{project}/shared").json()[0]["question"] == request["content"]
    member.post(f"/api/messages/{answer}/share", json={"shared": False})
    assert admin.get(f"/api/projects/{project}/shared").json() == []
    member.put(f"/api/conversations/{conversation}/position", json={"chapter": 2})
    fresh = TestClient(create_team_app(team["data"], start_worker=False))
    fresh.cookies.update(member.cookies)
    restored = fresh.get(f"/api/conversations/{conversation}").json()
    assert restored["chapter"] == 2 and len(restored["messages"]) == 2


def test_queued_cancellation_and_expired_lease_recovery(team):
    response = team["admin"].post(f"/api/projects/{team['project']}/index")
    key = response.json()["id"]
    assert team["admin"].post(f"/api/jobs/{key}/cancel").status_code == 200
    assert team["admin"].get(f"/api/jobs/{key}").json()["status"] == "cancelled"
    retry = team["admin"].post(f"/api/jobs/{key}/retry")
    worker = team["app"].state.worker
    job = worker.claim()
    with team["database"].session.begin() as session:
        session.get(Job, key).lease_until = time.time() - 1
    worker.tick()
    # tick reclaims the expired job, and its handler finishes asynchronously.
    for _ in range(100):
        current = team["admin"].get(f"/api/jobs/{key}").json()
        if current["status"] == "complete":
            break
        time.sleep(0.02)
    assert current["status"] == "complete"


def test_credentials_and_validation_errors_are_redacted(team):
    secret = "test-only-private-token"
    response = team["admin"].post("/api/provider", json={"name": "Test", "base_url": "https://model.example/v1",
        "api_key": secret, "model": "test"})
    assert response.status_code == 200
    assert secret not in response.text
    from repotrail.team.database import Secret
    with team["database"].session() as session:
        assert all(secret not in row.ciphertext for row in session.scalars(select(Secret)))
    if team["database"].engine.dialect.name == "sqlite":
        assert secret.encode() not in (team["data"] / "team.db").read_bytes()
    bad = team["admin"].post("/api/provider", json={"base_url": "x", "api_key": secret, "model": [secret]})
    assert bad.status_code == 422 and secret not in bad.text


def test_rejects_embedded_git_credentials_before_storage(team):
    response = team["admin"].post("/api/projects", json={"name": "Bad",
        "source": "https://name:private-placeholder@git.example/team/repo.git"})
    assert response.status_code == 400
    assert "private-placeholder" not in response.text


def test_course_retry_resumes_completed_chapters(team, monkeypatch):
    admin, project = team["admin"], team["project"]
    finish(team, admin.post(f"/api/projects/{project}/index"))
    original = FakeModel.complete
    calls = {"chapters": 0, "outline": 0}
    def failing(self, messages, **kwargs):
        if "Design 4" in messages[0]["content"]:
            calls["outline"] += 1
        elif "source_ids" in messages[0]["content"]:
            calls["chapters"] += 1
            if calls["chapters"] == 2:
                raise ValueError("temporary provider failure")
        return original(self, messages, **kwargs)
    monkeypatch.setattr(FakeModel, "complete", failing)
    response = admin.post(f"/api/projects/{project}/courses")
    worker = team["app"].state.worker
    worker.run_job(worker.claim())
    failed = admin.get("/api/jobs/" + response.json()["id"]).json()
    assert failed["status"] == "failed"
    assert len(failed["result"]["checkpoint"]["chapters"]) == 1
    finish(team, admin.post(f"/api/jobs/{failed['id']}/retry"))
    assert calls == {"chapters": 5, "outline": 1}


def test_revoked_member_chat_is_cancelled_before_model_call(team):
    course = published_course(team)
    member, admin, project = team["member"], team["admin"], team["project"]
    conversation = member.post(f"/api/projects/{project}/conversations", json={"course_id": course}).json()["id"]
    response = member.post(f"/api/conversations/{conversation}/messages",
                           json={"content": "Explain main", "request_id": "revoke-request-1"})
    admin.delete(f"/api/projects/{project}/members/{team['member_id']}")
    worker = team["app"].state.worker
    worker.run_job(worker.claim(chat=True))
    with team["database"].session() as session:
        assert session.get(Job, response.json()["id"]).status == "cancelled"
        assert session.get(Conversation, conversation).active_job_id is None


def test_concurrent_questions_keep_one_active_turn(team):
    from concurrent.futures import ThreadPoolExecutor
    course = published_course(team)
    member, project = team["member"], team["project"]
    key = member.post(f"/api/projects/{project}/conversations", json={"course_id": course}).json()["id"]
    def ask(number):
        client = TestClient(team["app"])
        client.cookies.update(member.cookies)
        client.headers.update(member.headers)
        response = client.post(f"/api/conversations/{key}/messages",
            json={"content": "Explain main", "request_id": f"concurrent-request-{number}"})
        return response.status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        codes = list(pool.map(ask, range(2)))
    assert sorted(codes) == [202, 409]
    with team["database"].session() as session:
        assert len(session.scalars(select(Message).where(Message.conversation_id == key)).all()) == 2


def test_five_chat_tasks_and_one_background_task_can_run_together(team, monkeypatch):
    import threading
    from sqlalchemy import func
    course = published_course(team)
    member, project = team["member"], team["project"]
    gate = threading.Event()
    def stream(self, messages):
        gate.wait(5)
        yield "completed answer"
    monkeypatch.setattr(FakeModel, "stream", stream)
    extra_clients = []
    with team["database"].session.begin() as session:
        for number in range(17):
            user = create_user(session, f"scale-{number:02d}", "Scale member", "scale-password-123")
            session.add(Membership(project_id=project, user_id=user.id))
    for number in range(4):
        client = TestClient(team["app"])
        response = client.post("/api/login", json={"username": f"scale-{number:02d}", "password": "scale-password-123"})
        client.headers["X-CSRF-Token"] = response.json()["csrf"]
        extra_clients.append(client)
    for number, client in enumerate([member, *extra_clients]):
        key = client.post(f"/api/projects/{project}/conversations", json={"course_id": course}).json()["id"]
        client.post(f"/api/conversations/{key}/messages", json={"content": "Explain main", "request_id": f"load-question-{number}"})
    runtime = team["app"].state.runtime
    def background(job):
        gate.wait(5)
        return {"chunks": 0}
    monkeypatch.setattr(runtime, "handle_reembed", background)
    team["admin"].post(f"/api/projects/{project}/reembed")
    worker = team["app"].state.worker
    try:
        for _ in range(5):
            worker.tick()
        with team["database"].session() as session:
            assert session.scalar(select(func.count()).select_from(User)) == 20
            assert session.scalar(select(func.count()).select_from(Job).where(Job.status == "running", Job.kind == "chat")) == 5
            assert session.scalar(select(func.count()).select_from(Job).where(Job.status == "running", Job.kind != "chat")) == 1
    finally:
        gate.set()
        worker.pool.shutdown(wait=True)
        for client in extra_clients:
            client.close()


def test_restored_backup_preserves_learning_and_encrypted_provider(team, tmp_path):
    if team["database"].engine.dialect.name != "sqlite":
        pytest.skip("SQLite file backup is validated with the SQLite fixture")
    import shutil
    import sqlite3
    course = published_course(team)
    team["admin"].post("/api/provider", json={"name": "Test", "base_url": "https://provider.example/v1",
        "api_key": "test-only-encrypted-credential", "model": "test-model"})
    target = tmp_path / "restored"
    target.mkdir()
    with sqlite3.connect(team["data"] / "team.db") as source, sqlite3.connect(target / "team.db") as destination:
        source.backup(destination)
    for name in ["team.key", "provider.json"]:
        shutil.copy2(team["data"] / name, target / name)
    app = create_team_app(target, start_worker=False)
    client = TestClient(app)
    client.cookies.update(team["member"].cookies)
    assert client.get(f"/api/courses/{course}").status_code == 200
    assert app.state.runtime.provider().current().key == "test-only-encrypted-credential"


def test_large_and_chunked_requests_are_bounded(team):
    client = team["admin"]
    assert client.post("/api/login", content=b"x" * (13 * 1024 * 1024),
                       headers={"Content-Type": "application/json"}).status_code == 413
    def chunks():
        for _ in range(13):
            yield b"x" * (1024 * 1024)
    assert client.post("/api/login", content=chunks(), headers={"Content-Type": "application/json"}).status_code == 413


def test_committed_document_is_not_recreated_after_worker_interruption(team):
    from repotrail.team.database import Chunk, Project
    response = team["admin"].post(f"/api/projects/{team['project']}/documents",
        files={"file": ("guide.md", b"# Guide\nTeam onboarding")})
    worker = team["app"].state.worker
    job = worker.claim()
    team["app"].state.runtime.handle(job)
    with team["database"].session() as session:
        before = list(session.scalars(select(Chunk.id)))
        revision = session.get(Project, team["project"]).knowledge_revision
    worker.run_job(job)
    with team["database"].session() as session:
        assert list(session.scalars(select(Chunk.id))) == before
        assert session.get(Project, team["project"]).knowledge_revision == revision
        assert session.get(Job, response.json()["id"]).status == "complete"


def test_long_conversation_keeps_original_messages_and_summarizes_context(team):
    course = published_course(team)
    member, project = team["member"], team["project"]
    key = member.post(f"/api/projects/{project}/conversations", json={"course_id": course}).json()["id"]
    with team["database"].session.begin() as session:
        for ordinal in range(16):
            session.add(Message(conversation_id=key, request_id=f"history-{ordinal // 2}", ordinal=ordinal,
                role="user" if ordinal % 2 == 0 else "assistant", content="Explain project main"))
    finish(team, member.post(f"/api/conversations/{key}/messages",
        json={"content": "Continue explaining main", "request_id": "long-history-request"}))
    with team["database"].session() as session:
        conversation = session.get(Conversation, key)
        assert conversation.summary
        assert conversation.summary_until == 3
    assert len(member.get(f"/api/conversations/{key}").json()["messages"]) == 18


def test_team_provider_pricing_and_credentials_restore_in_cli(team):
    from repotrail.provider import ProviderStore
    result = team["admin"].post("/api/provider", json={"name": "Test", "base_url": "https://pricing.example/v1",
        "api_key": "test-only-pricing-credential", "model": "example", "input_price": 0.3, "output_price": 1.2, "currency": "USD"})
    assert result.status_code == 200
    assert result.json()["storage"] == "encrypted_database"
    restored = ProviderStore(team["data"]).current()
    assert restored.key == "test-only-pricing-credential"
    assert restored.input_price == 0.3 and restored.output_price == 1.2
    assert restored.currency == "USD"


def test_conversation_history_can_page_back_without_loss(team):
    course = published_course(team)
    member, project = team["member"], team["project"]
    key = member.post(f"/api/projects/{project}/conversations", json={"course_id": course}).json()["id"]
    with team["database"].session.begin() as session:
        for ordinal in range(110):
            session.add(Message(conversation_id=key, request_id=f"page-{ordinal // 2}", ordinal=ordinal,
                role="user" if ordinal % 2 == 0 else "assistant", content=str(ordinal)))
    latest = member.get(f"/api/conversations/{key}").json()
    assert latest["has_more"] is True
    assert [item["ordinal"] for item in latest["messages"]] == list(range(60, 110))
    older = member.get(f"/api/conversations/{key}?before=60").json()
    assert [item["ordinal"] for item in older["messages"]] == list(range(10, 60))
    first = member.get(f"/api/conversations/{key}?before=10").json()
    assert first["has_more"] is False
    assert [item["ordinal"] for item in first["messages"]] == list(range(10))


def test_vector_storage_and_semantic_search_round_trip(team):
    from repotrail.team.database import Chunk, Project
    from repotrail.team.knowledge import retrieve
    published_course(team)
    with team["database"].session.begin() as session:
        project = session.get(Project, team["project"])
        row = session.scalar(select(Chunk).where(Chunk.snapshot_id == project.snapshot_id))
        row.embedding, row.embedding_model = [1.0, 0.0], "test-vector-model"
        source_id, snapshot = row.id, project.snapshot_id
    class QueryEmbedding:
        def embed(self, texts, query=False):
            return "test-vector-model", [[1.0, 0.0]]
    found = retrieve(team["database"], QueryEmbedding(), team["project"], snapshot, [],
                     "unmatchedlexicalquery", scope="code")
    assert [source["id"] for source in found] == [source_id]


def test_team_credential_failure_preserves_existing_configuration(team, monkeypatch):
    def fail(*args):
        raise RuntimeError("database temporarily unavailable")
    monkeypatch.setattr(team["app"].state.runtime.vault, "set_password", fail)
    before = team["admin"].get("/api/provider").json()
    response = team["admin"].post("/api/provider", json={"name": "Test", "base_url": "https://new.example/v1",
        "api_key": "test-only-unsaved-credential", "model": "test"})
    assert response.status_code == 400
    assert "test-only-unsaved-credential" not in response.text
    assert team["admin"].get("/api/provider").json() == before
