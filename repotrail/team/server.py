from __future__ import annotations

import asyncio
import hashlib
import json
import os
import secrets
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from starlette.middleware.trustedhost import TrustedHostMiddleware

from ..export import codetour, markdown
from ..llm import fetch_models, test_connection
from ..server import ProviderRequest, STATIC
from .database import Conversation, Course, Database, Document, Job, LoginAttempt, LoginSession
from .database import Membership, Message, Progress, Project, Secret, Snapshot, Submission, Usage, User, identifier
from .knowledge import MAX_UPLOAD, active_documents, parse_document
from .security import DUMMY_HASH, PASSWORDS, RequestSizeLimit, create_user, digest, verify_password
from .worker import Runtime, Worker, course_chapters, restore_index


class LoginBody(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=256, repr=False)


class UserBody(BaseModel):
    username: str = Field(min_length=3, max_length=80)
    name: str = Field(default="", max_length=100)
    password: str = Field(min_length=12, max_length=256, repr=False)


class PasswordBody(BaseModel):
    current: str = Field(max_length=256, repr=False)
    password: str = Field(min_length=12, max_length=256, repr=False)


class ProjectBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    source: str = Field(min_length=1, max_length=1000)
    description: str = Field(default="", max_length=3000)
    token: str = Field(default="", max_length=8192, repr=False)


class MemberBody(BaseModel):
    user_id: str


class EntryBody(BaseModel):
    entry: str = Field(min_length=1, max_length=400)
    title: str = Field(default="阅读路线", max_length=160)


class ChapterEdit(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    objectives: list[str] = Field(min_length=1, max_length=6)
    content: str = Field(min_length=1, max_length=18000)
    source_ids: list[str] = Field(default_factory=list, max_length=20)


class CourseEdit(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    chapters: list[ChapterEdit] = Field(min_length=1, max_length=12)


class ConversationBody(BaseModel):
    course_id: str


class PositionBody(BaseModel):
    chapter: int = Field(ge=0, le=11)


class ChatBody(BaseModel):
    content: str = Field(min_length=1, max_length=6000)
    request_id: str = Field(min_length=8, max_length=64)


class ShareBody(BaseModel):
    shared: bool


class ProgressBody(BaseModel):
    chapters: list[int] = Field(max_length=12)
    chapter: int | None = Field(default=None, ge=0, le=11)


class SummaryBody(BaseModel):
    summary: str = Field(min_length=1, max_length=20000)


class ReviewBody(BaseModel):
    approved: bool
    feedback: str = Field(default="", max_length=10000)


class EmbeddingBody(BaseModel):
    mode: str
    model: str = Field(max_length=300)
    base_url: str = Field(default="", max_length=1000)
    api_key: str = Field(default="", max_length=8192, repr=False)


def validate_source(source):
    from urllib.parse import urlsplit
    if source.startswith(("http://", "https://", "ssh://", "git@")):
        parts = urlsplit(source)
        if (parts.scheme != "https" or not parts.hostname or parts.username or parts.password
                or parts.query or parts.fragment or not parts.path.strip("/")
                or any(character.isspace() for character in source)):
            raise ValueError("请填写 HTTPS Git 地址，访问令牌请使用独立输入框")


def public_user(user):
    return {"id": user.id, "username": user.username, "name": user.name,
            "admin": user.admin, "active": user.active}


def public_job(job):
    return {"id": job.id, "kind": job.kind, "status": job.status, "stage": job.stage,
            "error": job.error, "result": job.result, "created": job.created,
            "attempts": job.attempts, "project_id": job.project_id,
            "snapshot_id": job.payload.get("snapshot_id"),
            "conversation_id": job.payload.get("conversation_id"),
            "message_id": job.payload.get("assistant_message_id")}


def public_message(message):
    return {"id": message.id, "role": message.role, "content": message.content,
            "status": message.status, "citations": message.citations,
            "shared": message.shared, "created": message.created, "ordinal": message.ordinal}


def create_team_app(directory: Path | None = None, database_url=None, start_worker=True):
    directory = directory or Path(os.environ.get("REPOTRAIL_DATA_DIR", Path.cwd() / ".repotrail"))
    database = Database(directory, database_url)
    database.migrate()
    runtime = Runtime(database, directory)
    worker = Worker(runtime)

    @asynccontextmanager
    async def lifespan(app):
        thread = None
        if start_worker:
            thread = threading.Thread(target=worker.run, daemon=True, name="team-worker")
            thread.start()
        yield
        worker.stop()
        if thread:
            await asyncio.to_thread(thread.join, 10)

    app = FastAPI(title="RepoTrail Team", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.state.runtime = runtime
    app.state.worker = worker
    hosts = os.environ.get("REPOTRAIL_ALLOWED_HOSTS", "localhost,127.0.0.1,testserver").split(",")
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=[host.strip() for host in hosts])
    app.add_middleware(RequestSizeLimit)

    @app.exception_handler(RequestValidationError)
    async def invalid_input(request, exc):
        return JSONResponse({"detail": "输入格式或长度无效，请检查表单"}, status_code=422)

    @app.exception_handler(ValueError)
    async def invalid_value(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.exception_handler(IntegrityError)
    async def conflicting_update(request, exc):
        return JSONResponse({"detail": "记录已存在或已被更新，请刷新后重试"}, status_code=409)

    @app.middleware("http")
    async def headers(request, call_next):
        origin = request.headers.get("origin")
        expected = os.environ.get("REPOTRAIL_PUBLIC_URL", str(request.base_url)).rstrip("/")
        if request.method not in {"GET", "HEAD", "OPTIONS"} and origin and origin != expected:
            return JSONResponse({"detail": "请求来源与服务地址不匹配"}, status_code=403)
        response = await call_next(request)
        response.headers.update({"X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer",
            "Cache-Control": "no-store", "Content-Security-Policy": "default-src 'self'; script-src 'self'; "
            "style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"})
        return response

    def identity(request: Request):
        token = request.cookies.get("repotrail_session", "")
        with database.session() as session:
            login = session.get(LoginSession, digest(token)) if token else None
            account = session.get(User, login.user_id) if login and login.expires > time.time() else None
            if not account or not account.active:
                raise HTTPException(401, "请登录")
            if request.method not in {"GET", "HEAD", "OPTIONS"}:
                csrf = request.headers.get("X-CSRF-Token", "")
                if not csrf.isascii() or not secrets.compare_digest(csrf, login.csrf):
                    raise HTTPException(403, "登录状态已更新，请刷新后重试")
            request.state.csrf = login.csrf
            return account

    def administrator(account=Depends(identity)):
        if not account.admin:
            raise HTTPException(403, "此操作需要管理员权限")
        return account

    def project_access(session, project_id, account):
        project = session.get(Project, project_id)
        if not project or (not account.admin and not session.get(Membership, (project_id, account.id))):
            raise HTTPException(404, "项目不存在或访问权限已变更")
        return project

    def course_access(session, course_id, account, draft=False):
        course = session.get(Course, course_id)
        if not course:
            raise HTTPException(404, "课程不存在")
        project_access(session, course.project_id, account)
        if course.status != "published" and not (draft and account.admin):
            raise HTTPException(404, "课程尚未发布")
        return course

    def conversation_access(session, key, account):
        conversation = session.get(Conversation, key)
        if not conversation or conversation.user_id != account.id:
            raise HTTPException(404, "会话不存在")
        project_access(session, conversation.project_id, account)
        return conversation

    def job_access(session, key, account):
        job = session.get(Job, key)
        if not job:
            raise HTTPException(404, "任务不存在")
        project_access(session, job.project_id, account)
        if job.user_id != account.id and (job.kind == "chat" or not account.admin):
            raise HTTPException(404, "任务不存在")
        return job

    def enqueue(session, project_id, account, kind, payload=None, key=None):
        job = Job(id=key or identifier(), project_id=project_id, user_id=account.id,
                  kind=kind, payload=payload or {})
        session.add(job)
        session.flush()
        return public_job(job)

    @app.get("/api/health")
    def health():
        with database.session() as session:
            session.execute(select(1))
        return {"status": "ok", "mode": "team"}

    @app.post("/api/login")
    def login(body: LoginBody, request: Request):
        username = body.username.strip().lower()
        attempt_key = digest("username:" + username)
        ip_key = digest("ip:" + (request.client.host if request.client else "unknown"))
        now = time.time()
        with database.session.begin() as session:
            for key, limit in ((attempt_key, 5), (ip_key, 30)):
                attempt = session.get(LoginAttempt, key)
                if attempt and now - attempt.since < 900 and attempt.failures >= limit:
                    raise HTTPException(429, "登录尝试过于频繁，请稍后重试")
            account = session.scalar(select(User).where(User.username == username))
            valid = verify_password(account.password_hash if account else DUMMY_HASH, body.password)
            if not account or not account.active or not valid:
                from sqlalchemy.dialects.postgresql import insert as pg_insert
                from sqlalchemy.dialects.sqlite import insert as sqlite_insert
                from sqlalchemy import case
                insert = pg_insert if database.engine.dialect.name == "postgresql" else sqlite_insert
                for key in (attempt_key, ip_key):
                    statement = insert(LoginAttempt).values(key=key, failures=1, since=now)
                    expired = LoginAttempt.since < now - 900
                    session.execute(statement.on_conflict_do_update(index_elements=[LoginAttempt.key],
                        set_={"failures": case((expired, 1), else_=LoginAttempt.failures + 1),
                              "since": case((expired, now), else_=LoginAttempt.since)}))
                response = JSONResponse({"detail": "用户名或密码错误"}, status_code=401)
            else:
                if PASSWORDS.check_needs_rehash(account.password_hash):
                    account.password_hash = PASSWORDS.hash(body.password)
                session.execute(delete(LoginAttempt).where(LoginAttempt.key == attempt_key))
                session.execute(delete(LoginSession).where(LoginSession.expires < now))
                token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
                session.add(LoginSession(token_hash=digest(token), user_id=account.id,
                                         csrf=csrf, expires=now + 12 * 3600))
                response = JSONResponse({"user": public_user(account), "csrf": csrf})
                secure = os.environ.get("REPOTRAIL_COOKIE_SECURE", "0") == "1" or request.url.scheme == "https"
                response.set_cookie("repotrail_session", token, httponly=True, secure=secure,
                                    samesite="strict", max_age=12 * 3600)
        return response

    @app.get("/api/me")
    def me(request: Request, account=Depends(identity)):
        return {"user": public_user(account), "csrf": request.state.csrf}

    @app.post("/api/logout")
    def logout(request: Request, account=Depends(identity)):
        with database.session.begin() as session:
            session.execute(delete(LoginSession).where(LoginSession.token_hash == digest(
                request.cookies.get("repotrail_session", ""))))
        response = JSONResponse({"ok": True})
        response.delete_cookie("repotrail_session")
        return response

    @app.post("/api/password")
    def password(body: PasswordBody, account=Depends(identity)):
        if not verify_password(account.password_hash, body.current):
            raise HTTPException(400, "当前密码错误")
        with database.session.begin() as session:
            session.get(User, account.id).password_hash = PASSWORDS.hash(body.password)
            session.execute(delete(LoginSession).where(LoginSession.user_id == account.id))
        return {"ok": True}

    @app.get("/api/users")
    def users(account=Depends(administrator)):
        with database.session() as session:
            return [public_user(row) for row in session.scalars(select(User).order_by(User.username))]

    @app.post("/api/users", status_code=201)
    def add_user(body: UserBody, account=Depends(administrator)):
        with database.session.begin() as session:
            return public_user(create_user(session, body.username, body.name, body.password))

    @app.post("/api/users/{key}/disable")
    def disable_user(key: str, account=Depends(administrator)):
        with database.session.begin() as session:
            row = session.get(User, key)
            if not row:
                raise HTTPException(404, "成员不存在")
            if row.admin:
                raise HTTPException(400, "请通过管理员命令管理管理员账号")
            row.active = False
            session.execute(delete(LoginSession).where(LoginSession.user_id == key))
        return {"ok": True}

    @app.get("/api/provider")
    def provider(account=Depends(administrator)):
        return runtime.provider().public()

    @app.post("/api/provider")
    def save_provider(body: ProviderRequest, account=Depends(administrator)):
        with runtime.config_lock:
            pricing = body.model_dump(include={"input_price", "output_price", "currency"}, exclude_unset=True)
            return runtime.provider().configure(body.name, body.base_url, body.api_key, body.model, pricing)

    @app.post("/api/provider/models")
    def provider_models(body: ProviderRequest, account=Depends(administrator)):
        settings = runtime.provider().resolve(body.base_url, body.api_key, body.model, require_model=False)
        return {"models": fetch_models(settings)}

    @app.post("/api/provider/test")
    def provider_test(body: ProviderRequest, account=Depends(administrator)):
        return test_connection(runtime.provider().resolve(body.base_url, body.api_key, body.model))

    @app.get("/api/embeddings")
    def embeddings(account=Depends(administrator)):
        return runtime.embeddings.public()

    @app.post("/api/embeddings")
    def configure_embeddings(body: EmbeddingBody, account=Depends(administrator)):
        with runtime.config_lock:
            return runtime.embeddings.configure(body.mode, body.model, body.base_url, body.api_key)

    @app.get("/api/projects")
    def projects(account=Depends(identity)):
        with database.session() as session:
            query = select(Project).order_by(Project.created.desc())
            if not account.admin:
                query = query.join(Membership).where(Membership.user_id == account.id)
            return [{"id": row.id, "name": row.name, "description": row.description,
                     "snapshot_id": row.snapshot_id, "published_course_id": row.published_course_id}
                    for row in session.scalars(query)]

    @app.post("/api/projects", status_code=201)
    def add_project(body: ProjectBody, account=Depends(administrator)):
        if not body.name.strip() or not body.source.strip():
            raise ValueError("请填写项目名称和源码位置")
        validate_source(body.source)
        key = identifier()
        with database.session.begin() as session:
            row = Project(id=key, name=body.name.strip(), source=body.source.strip(), description=body.description)
            session.add(row)
            session.flush()
            session.add(Membership(project_id=key, user_id=account.id))
            if body.token:
                session.add(Secret(id="git:" + key,
                                   ciphertext=runtime.vault.cipher.encrypt(body.token.encode()).decode()))
        return {"id": key}

    @app.put("/api/projects/{key}")
    def edit_project(key: str, body: ProjectBody, account=Depends(administrator)):
        if not body.name.strip() or not body.source.strip():
            raise ValueError("请填写项目名称和源码位置")
        validate_source(body.source)
        with database.session.begin() as session:
            row = project_access(session, key, account)
            if body.source.strip() != row.source and not body.token:
                secret = session.get(Secret, "git:" + key)
                if secret:
                    session.delete(secret)
            row.name, row.description, row.source = body.name.strip(), body.description, body.source.strip()
            if body.token:
                session.merge(Secret(id="git:" + key,
                                     ciphertext=runtime.vault.cipher.encrypt(body.token.encode()).decode()))
        return {"ok": True}

    @app.get("/api/projects/{key}")
    def project(key: str, account=Depends(identity)):
        with database.session() as session:
            row = project_access(session, key, account)
            snapshot = session.get(Snapshot, row.snapshot_id) if row.snapshot_id else None
            overview = restore_index(snapshot.payload).overview() if snapshot else None
            if overview:
                overview["flows"] = snapshot.entries
            return {"id": row.id, "name": row.name, "description": row.description,
                    "source": row.source if account.admin else "", "snapshot_id": row.snapshot_id,
                    "published_course_id": row.published_course_id, "knowledge_revision": row.knowledge_revision,
                    "overview": overview}

    @app.get("/api/projects/{key}/members")
    def members(key: str, account=Depends(administrator)):
        with database.session() as session:
            project_access(session, key, account)
            return [public_user(row) for row in session.scalars(select(User).join(Membership)
                    .where(Membership.project_id == key))]

    @app.post("/api/projects/{key}/members")
    def add_member(key: str, body: MemberBody, account=Depends(administrator)):
        with database.session.begin() as session:
            project_access(session, key, account)
            row = session.get(User, body.user_id)
            if not row or not row.active:
                raise HTTPException(404, "成员不存在或已停用")
            session.merge(Membership(project_id=key, user_id=body.user_id))
        return {"ok": True}

    @app.delete("/api/projects/{key}/members/{user_id}")
    def remove_member(key: str, user_id: str, account=Depends(administrator)):
        with database.session.begin() as session:
            project_access(session, key, account)
            session.execute(delete(Membership).where(Membership.project_id == key, Membership.user_id == user_id))
        return {"ok": True}

    @app.post("/api/projects/{key}/index", status_code=202)
    def index_project(key: str, account=Depends(administrator)):
        with database.session.begin() as session:
            project_access(session, key, account)
            if session.scalar(select(Job.id).where(Job.project_id == key, Job.kind == "index",
                                                  Job.status.in_(["queued", "running"]))):
                raise HTTPException(409, "该项目已有索引任务")
            return enqueue(session, key, account, "index")

    @app.post("/api/projects/{key}/entries", status_code=202)
    def recommend_entries(key: str, account=Depends(identity)):
        with database.session.begin() as session:
            row = project_access(session, key, account)
            if not row.snapshot_id:
                raise HTTPException(409, "请先建立源码索引")
            return enqueue(session, key, account, "entries", {"snapshot_id": row.snapshot_id})

    @app.post("/api/projects/{key}/trails", status_code=202)
    def trail(key: str, body: EntryBody, account=Depends(identity)):
        with database.session.begin() as session:
            row = project_access(session, key, account)
            if not row.snapshot_id or body.entry not in session.get(Snapshot, row.snapshot_id).payload["symbols"]:
                raise HTTPException(400, "请选择当前源码快照中的符号")
            return enqueue(session, key, account, "trail", {"snapshot_id": row.snapshot_id, **body.model_dump()})

    @app.get("/api/projects/{key}/symbols")
    def symbols(key: str, q: str = "", account=Depends(identity)):
        with database.session() as session:
            row = project_access(session, key, account)
            if not row.snapshot_id:
                return []
            values = session.get(Snapshot, row.snapshot_id).payload["symbols"].values()
            return [value for value in values if q.lower() in value["id"].lower()][:100]

    @app.get("/api/projects/{key}/documents")
    def documents(key: str, account=Depends(identity)):
        with database.session() as session:
            project_access(session, key, account)
            return [{"id": row.id, "name": row.name, "digest": row.digest, "origin": row.origin,
                     "created": row.created} for row in session.scalars(select(Document).where(
                         Document.project_id == key, Document.active.is_(True)).order_by(Document.name))]

    @app.post("/api/projects/{key}/documents", status_code=202)
    async def upload_document(key: str, file: UploadFile = File(), account=Depends(administrator)):
        with database.session() as session:
            project_access(session, key, account)
        data = await file.read(MAX_UPLOAD + 1)
        name = (file.filename or "document.md").replace("\\", "/").rsplit("/", 1)[-1][:240]
        body = await asyncio.to_thread(parse_document, name, data)
        with database.session.begin() as session:
            document = Document(project_id=key, name=name, body=body, digest=hashlib.sha256(data).hexdigest())
            session.add(document)
            session.flush()
            return enqueue(session, key, account, "document", {"document_id": document.id})

    @app.delete("/api/projects/{key}/documents/{document_id}")
    def remove_document(key: str, document_id: str, account=Depends(administrator)):
        with database.session.begin() as session:
            project = project_access(session, key, account)
            row = session.get(Document, document_id)
            if not row or row.project_id != key:
                raise HTTPException(404, "文档不存在")
            session.delete(row)
            project.knowledge_revision += 1
        return {"ok": True}

    @app.get("/api/chunks/{key}")
    def source(key: str, account=Depends(identity)):
        from .database import Chunk
        with database.session() as session:
            chunk = session.get(Chunk, key)
            if not chunk:
                raise HTTPException(404, "引用资料已删除")
            project_access(session, chunk.project_id, account)
            return {"id": chunk.id, "title": chunk.title, "body": chunk.body, "location": chunk.location}

    @app.get("/api/documents/{key}")
    def document(key: str, account=Depends(identity)):
        with database.session() as session:
            row = session.get(Document, key)
            if not row:
                raise HTTPException(404, "文档不存在")
            project_access(session, row.project_id, account)
            return {"id": row.id, "name": row.name, "body": row.body, "digest": row.digest}

    @app.post("/api/projects/{key}/reembed", status_code=202)
    def reembed(key: str, account=Depends(administrator)):
        with database.session.begin() as session:
            project_access(session, key, account)
            return enqueue(session, key, account, "reembed")

    @app.get("/api/snapshots/{key}/source")
    def source_file(key: str, file: str, account=Depends(identity)):
        with database.session() as session:
            snapshot = session.get(Snapshot, key)
            if not snapshot:
                raise HTTPException(404, "源码快照不存在")
            project_access(session, snapshot.project_id, account)
            content = snapshot.payload["files"].get(file)
            if content is None:
                raise HTTPException(404, "文件不存在于当前源码快照")
            return {"file": file, "source": content}

    @app.get("/api/projects/{key}/courses")
    def courses(key: str, account=Depends(identity)):
        with database.session() as session:
            project_access(session, key, account)
            query = select(Course).where(Course.project_id == key).order_by(Course.created.desc())
            if not account.admin:
                query = query.where(Course.status == "published")
            return [{"id": row.id, "title": row.title, "status": row.status, "created": row.created,
                     "snapshot_id": row.snapshot_id, "chapter_count": len(row.chapters)}
                    for row in session.scalars(query)]

    @app.post("/api/projects/{key}/courses", status_code=202)
    def generate_course(key: str, account=Depends(administrator)):
        with database.session.begin() as session:
            row = project_access(session, key, account)
            if not row.snapshot_id:
                raise HTTPException(409, "请先建立源码索引")
            return enqueue(session, key, account, "course", {"snapshot_id": row.snapshot_id,
                           "document_ids": active_documents(session, key), "knowledge_revision": row.knowledge_revision})

    @app.get("/api/courses/{key}")
    def course(key: str, account=Depends(identity)):
        with database.session() as session:
            row = course_access(session, key, account, draft=True)
            project = session.get(Project, row.project_id)
            return {"id": row.id, "project_id": row.project_id, "title": row.title, "status": row.status,
                    "chapters": course_chapters(row.chapters), "snapshot_id": row.snapshot_id, "created": row.created,
                    "newer_materials": project.knowledge_revision != row.knowledge_revision}

    @app.put("/api/courses/{key}")
    def edit_course(key: str, body: CourseEdit, account=Depends(administrator)):
        with database.session.begin() as session:
            row = course_access(session, key, account, draft=True)
            if row.status != "draft":
                raise HTTPException(409, "已发布课程保留原版本，请创建新版草稿")
            from .database import Chunk
            chapters = []
            for chapter in body.chapters:
                refs = []
                for source_id in dict.fromkeys(chapter.source_ids):
                    source = session.get(Chunk, source_id)
                    if (not source or source.project_id != row.project_id or not (
                            source.snapshot_id == row.snapshot_id or source.document_id in row.document_ids)):
                        raise HTTPException(400, "引用需要来自该课程绑定的资料版本")
                    refs.append({"id": source.id, "title": source.title, "location": source.location,
                                 "document_id": source.document_id, "snapshot_id": source.snapshot_id})
                chapters.append({"title": chapter.title, "objectives": chapter.objectives,
                                 "content": chapter.content, "citations": refs})
            row.title, row.chapters = body.title, chapters
        return {"ok": True}

    @app.post("/api/courses/{key}/publish")
    def publish_course(key: str, account=Depends(administrator)):
        with database.session.begin() as session:
            row = course_access(session, key, account, draft=True)
            if not row.chapters:
                raise HTTPException(409, "请先生成课程章节")
            row.status, row.published = "published", time.time()
            session.get(Project, row.project_id).published_course_id = row.id
        return {"ok": True}

    @app.get("/api/projects/{key}/conversations")
    def conversations(key: str, account=Depends(identity)):
        with database.session() as session:
            project_access(session, key, account)
            return [{"id": row.id, "title": row.title, "course_id": row.course_id, "chapter": row.chapter,
                     "active_job_id": row.active_job_id, "created": row.created}
                    for row in session.scalars(select(Conversation).where(Conversation.project_id == key,
                        Conversation.user_id == account.id).order_by(Conversation.created.desc()))]

    @app.post("/api/projects/{key}/conversations", status_code=201)
    def add_conversation(key: str, body: ConversationBody, account=Depends(identity)):
        with database.session.begin() as session:
            project_access(session, key, account)
            course = course_access(session, body.course_id, account)
            if course.project_id != key:
                raise HTTPException(400, "课程所属项目不匹配")
            row = Conversation(project_id=key, user_id=account.id, course_id=course.id)
            session.add(row)
            session.flush()
            return {"id": row.id}

    @app.get("/api/conversations/{key}")
    def conversation(key: str, before: int | None = None, account=Depends(identity)):
        with database.session() as session:
            row = conversation_access(session, key, account)
            statement = select(Message).where(Message.conversation_id == key)
            if before is not None:
                statement = statement.where(Message.ordinal < before)
            values = session.scalars(statement.order_by(Message.ordinal.desc()).limit(51)).all()
            has_more = len(values) > 50
            messages = list(reversed(values[:50]))
            jobs = session.scalars(select(Job).where(Job.project_id == row.project_id,
                Job.user_id == account.id, Job.kind == "chat",
                Job.payload["assistant_message_id"].as_string().in_([message.id for message in messages])))
            job_map = {job.payload.get("assistant_message_id"): job.id for job in jobs
                       if job.payload.get("conversation_id") == key}
            return {"id": row.id, "project_id": row.project_id, "course_id": row.course_id,
                    "chapter": row.chapter, "active_job_id": row.active_job_id, "has_more": has_more,
                    "messages": [{**public_message(message), "job_id": job_map.get(message.id)} for message in messages]}

    @app.put("/api/conversations/{key}/position")
    def position(key: str, body: PositionBody, account=Depends(identity)):
        with database.session.begin() as session:
            row = conversation_access(session, key, account)
            course = session.get(Course, row.course_id)
            if body.chapter >= len(course.chapters):
                raise HTTPException(400, "章节不存在")
            row.chapter = body.chapter
        return {"ok": True}

    @app.delete("/api/conversations/{key}")
    def remove_conversation(key: str, account=Depends(identity)):
        with database.session.begin() as session:
            row = conversation_access(session, key, account)
            if row.active_job_id:
                raise HTTPException(409, "请先停止当前回答")
            deleted = session.execute(delete(Conversation).where(Conversation.id == key,
                                                                 Conversation.active_job_id.is_(None)))
            if not deleted.rowcount:
                raise HTTPException(409, "会话已有回答任务，请先停止当前回答")
        return {"ok": True}

    @app.post("/api/conversations/{key}/messages", status_code=202)
    def ask(key: str, body: ChatBody, account=Depends(identity)):
        if not body.content.strip():
            raise ValueError("请输入问题")
        with database.session.begin() as session:
            row = conversation_access(session, key, account)
            session.refresh(row, with_for_update=True)
            existing = session.scalar(select(Message).where(Message.conversation_id == key,
                Message.request_id == body.request_id, Message.role == "user"))
            if existing:
                candidates = session.scalars(select(Job).where(Job.project_id == row.project_id,
                    Job.user_id == account.id, Job.kind == "chat"))
                job = next((item for item in candidates if item.payload.get("user_message_id") == existing.id), None)
                if not job:
                    raise HTTPException(409, "该问题已提交，请刷新会话")
                return public_job(job)
            if row.active_job_id:
                raise HTTPException(409, "请等待当前回答完成或先停止")
            job_id = identifier()
            claimed = session.execute(update(Conversation).where(Conversation.id == key,
                Conversation.active_job_id.is_(None)).values(active_job_id=job_id))
            if not claimed.rowcount:
                raise HTTPException(409, "当前会话已有回答任务，请刷新会话")
            if not session.scalar(select(Message.id).where(Message.conversation_id == key)):
                row.title = body.content.strip()[:80]
            last = session.scalar(select(func.max(Message.ordinal)).where(Message.conversation_id == key))
            ordinal = last + 1 if last is not None else 0
            question = Message(conversation_id=key, request_id=body.request_id,
                               role="user", ordinal=ordinal, content=body.content.strip())
            answer = Message(conversation_id=key, request_id=body.request_id, ordinal=ordinal + 1,
                             role="assistant", status="queued")
            session.add_all([question, answer])
            session.flush()
            job = enqueue(session, row.project_id, account, "chat", {
                "conversation_id": key, "chapter": row.chapter, "user_message_id": question.id,
                "assistant_message_id": answer.id}, key=job_id)
            return job

    @app.post("/api/messages/{key}/share")
    def share_message(key: str, body: ShareBody, account=Depends(identity)):
        with database.session.begin() as session:
            row = session.get(Message, key)
            if not row:
                raise HTTPException(404, "问答不存在")
            conversation_access(session, row.conversation_id, account)
            if row.role != "assistant" or row.status != "complete":
                raise HTTPException(409, "请选择已完成的回答")
            row.shared = body.shared
        return {"ok": True}

    @app.get("/api/projects/{key}/shared")
    def shared(key: str, account=Depends(identity)):
        with database.session() as session:
            project_access(session, key, account)
            rows = session.scalars(select(Message).join(Conversation).join(User, Conversation.user_id == User.id)
                .where(Conversation.project_id == key, Message.shared.is_(True), User.active.is_(True))
                .order_by(Message.created.desc())).all()
            result = []
            for row in rows:
                owner = session.get(Conversation, row.conversation_id).user_id
                if not session.get(Membership, (key, owner)) and not session.get(User, owner).admin:
                    continue
                question = session.scalar(select(Message.content).where(Message.conversation_id == row.conversation_id,
                    Message.request_id == row.request_id, Message.role == "user"))
                result.append({**public_message(row), "question": question})
            return result

    @app.get("/api/courses/{key}/progress")
    def progress(key: str, account=Depends(identity)):
        with database.session() as session:
            course_access(session, key, account)
            row = session.get(Progress, (account.id, key))
            return {"chapters": row.chapters, "summary": row.summary, "status": row.status,
                    "chapter": row.chapter, "feedback": row.feedback} if row else {
                        "chapters": [], "chapter": 0, "summary": "", "status": "learning", "feedback": ""}

    @app.put("/api/courses/{key}/progress")
    def save_progress(key: str, body: ProgressBody, account=Depends(identity)):
        with database.session.begin() as session:
            course = course_access(session, key, account)
            if any(number < 0 or number >= len(course.chapters) for number in body.chapters):
                raise HTTPException(400, "阅读进度中包含不存在的章节")
            if body.chapter is not None and body.chapter >= len(course.chapters):
                raise HTTPException(400, "章节不存在")
            row = session.get(Progress, (account.id, key))
            if not row:
                row = Progress(user_id=account.id, course_id=key)
                session.add(row)
            row.chapters, row.updated = sorted(set(body.chapters)), time.time()
            if body.chapter is not None:
                row.chapter = body.chapter
        return {"ok": True}

    @app.post("/api/courses/{key}/summary")
    def submit_summary(key: str, body: SummaryBody, account=Depends(identity)):
        if not body.summary.strip():
            raise ValueError("请填写学习总结")
        with database.session.begin() as session:
            course_access(session, key, account)
            row = session.get(Progress, (account.id, key))
            if not row:
                row = Progress(user_id=account.id, course_id=key)
                session.add(row)
            row.summary, row.status, row.updated = body.summary, "submitted", time.time()
            row.feedback, row.reviewed_by = "", None
            session.add(Submission(user_id=account.id, course_id=key, summary=body.summary))
        return {"ok": True}

    @app.get("/api/courses/{key}/submissions")
    def submissions(key: str, account=Depends(administrator)):
        with database.session() as session:
            course_access(session, key, account)
            return [{"id": row.id, "user": public_user(session.get(User, row.user_id)),
                     "summary": row.summary, "status": row.status, "feedback": row.feedback,
                     "created": row.created} for row in session.scalars(select(Submission).where(
                         Submission.course_id == key).order_by(Submission.created.desc()))]

    @app.get("/api/projects/{key}/learning")
    def learning(key: str, account=Depends(administrator)):
        with database.session() as session:
            project_access(session, key, account)
            rows = session.execute(select(Progress, Course, User).join(Course, Progress.course_id == Course.id)
                .join(User, Progress.user_id == User.id).where(Course.project_id == key)).all()
            return [{"user": public_user(user), "course_id": course.id, "course_title": course.title,
                     "read_chapters": len(progress.chapters), "total_chapters": len(course.chapters),
                     "status": progress.status} for progress, course, user in rows]

    @app.post("/api/submissions/{key}/review")
    def review(key: str, body: ReviewBody, account=Depends(administrator)):
        with database.session.begin() as session:
            row = session.get(Submission, key)
            if not row:
                raise HTTPException(404, "学习总结不存在")
            course_access(session, row.course_id, account)
            if row.status != "submitted":
                raise HTTPException(409, "该总结已审核")
            if not body.approved and not body.feedback.strip():
                raise ValueError("请填写需要补充的学习内容")
            row.status = "approved" if body.approved else "changes_requested"
            row.feedback, row.reviewer_id, row.reviewed = body.feedback, account.id, time.time()
            latest = session.scalar(select(Submission.id).where(Submission.course_id == row.course_id,
                Submission.user_id == row.user_id).order_by(Submission.created.desc()).limit(1))
            if latest == row.id:
                state = session.get(Progress, (row.user_id, row.course_id))
                state.status, state.feedback, state.reviewed_by = row.status, body.feedback, account.id
                state.updated = time.time()
        return {"ok": True}

    @app.get("/api/projects/{key}/jobs")
    def jobs(key: str, account=Depends(identity)):
        with database.session() as session:
            project_access(session, key, account)
            return [public_job(row) for row in session.scalars(select(Job).where(
                Job.project_id == key, Job.user_id == account.id).order_by(Job.created.desc()).limit(40))]

    @app.get("/api/jobs/{key}")
    def job(key: str, account=Depends(identity)):
        with database.session() as session:
            return public_job(job_access(session, key, account))

    @app.get("/api/jobs/{key}/events")
    async def events(key: str, request: Request, account=Depends(identity)):
        with database.session() as session:
            job_access(session, key, account)

        async def stream():
            previous = ""
            while True:
                if await request.is_disconnected():
                    return
                # Recheck login and project access while a stream is open.
                try:
                    current_account = await asyncio.to_thread(identity, request)
                    with database.session() as session:
                        row = job_access(session, key, current_account)
                        payload = public_job(row)
                        if row.kind == "chat":
                            message = session.get(Message, row.payload["assistant_message_id"])
                            payload["message"] = public_message(message) if message else None
                except HTTPException:
                    yield 'event: error\ndata: {"detail":"访问权限或登录状态已更新"}\n\n'
                    return
                value = json.dumps(payload, ensure_ascii=False)
                if value != previous:
                    yield "data: " + value + "\n\n"
                    previous = value
                else:
                    yield ": keepalive\n\n"
                if row.status in {"complete", "failed", "cancelled"}:
                    return
                await asyncio.sleep(0.5)
        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"X-Accel-Buffering": "no"})

    @app.post("/api/jobs/{key}/cancel")
    def cancel_job(key: str, account=Depends(identity)):
        with database.session.begin() as session:
            row = job_access(session, key, account)
            if row.status in {"queued", "running"}:
                row.cancelled = True
                if row.status == "queued":
                    row.status, row.stage = "cancelled", "已停止"
                    if row.kind == "chat":
                        session.get(Conversation, row.payload["conversation_id"]).active_job_id = None
                        session.get(Message, row.payload["assistant_message_id"]).status = "cancelled"
        return {"ok": True}

    @app.post("/api/jobs/{key}/retry", status_code=202)
    def retry_job(key: str, account=Depends(identity)):
        with database.session.begin() as session:
            row = job_access(session, key, account)
            session.refresh(row, with_for_update=True)
            if row.status not in {"failed", "cancelled"}:
                raise HTTPException(409, "该任务正在处理或已完成")
            if row.kind == "chat":
                conversation = conversation_access(session, row.payload["conversation_id"], account)
                if conversation.active_job_id:
                    raise HTTPException(409, "请先完成当前回答")
                conversation.active_job_id = row.id
                message = session.get(Message, row.payload["assistant_message_id"])
                message.status, message.content, message.citations = "queued", "", []
            row.status, row.error, row.stage = "queued", "", "等待重试"
            row.cancelled, row.attempts, row.lease_until = False, 0, 0
            return public_job(row)

    @app.get("/api/jobs/{key}/export")
    def export(key: str, format: str = "markdown", account=Depends(identity)):
        with database.session() as session:
            row = job_access(session, key, account)
            if row.kind != "trail" or row.status != "complete":
                raise HTTPException(409, "请选择已生成的阅读路线")
            if format == "markdown":
                return Response(markdown(row.result), media_type="text/markdown", headers={
                    "Content-Disposition": 'attachment; filename="trail.md"'})
            if format in {"json", "codetour"}:
                return JSONResponse(codetour(row.result) if format == "codetour" else row.result,
                                    headers={"Content-Disposition": 'attachment; filename="trail.json"'})
            raise HTTPException(400, "请选择 markdown、json 或 codetour")

    @app.get("/api/usage")
    def usage(account=Depends(administrator)):
        with database.session() as session:
            return [{"id": row.id, "project_id": row.project_id, "user_id": row.user_id,
                     "job_id": row.job_id, "metrics": row.metrics, "created": row.created}
                    for row in session.scalars(select(Usage).order_by(Usage.created.desc()).limit(2000))]

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/")
    def home():
        return FileResponse(STATIC / "team.html")

    return app
