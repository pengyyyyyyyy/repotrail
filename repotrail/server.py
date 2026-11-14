from __future__ import annotations

import secrets
import threading
from contextlib import asynccontextmanager
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .engine import deterministic_demo, generate
from .export import codetour, markdown
from .index import build_index
from .llm import ModelClient, fetch_models, test_connection
from .provider import ProviderStore
from .repository import acquire


STATIC = Path(__file__).parent / "static"
DEMO = Path(__file__).parent / "sample"


class AnalyzeRequest(BaseModel):
    source: str = Field(min_length=1, max_length=1000)


class GenerateRequest(BaseModel):
    repository_id: str
    flow_id: str


class ProviderRequest(BaseModel):
    name: str = Field(default="兼容 API", max_length=80)
    base_url: str = Field(min_length=1, max_length=1000)
    api_key: str = Field(default="", max_length=8192, repr=False)
    model: str = Field(default="", max_length=300)


def create_app(cache_dir: Path | None = None, provider_store: ProviderStore | None = None) -> FastAPI:
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="repotrail")

    @asynccontextmanager
    async def lifespan(app):
        yield
        pool.shutdown(wait=False, cancel_futures=True)

    app = FastAPI(title="RepoTrail", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "testserver"])
    cache_dir = cache_dir or Path.cwd() / ".repotrail"
    provider_store = provider_store or ProviderStore(cache_dir)
    repositories = OrderedDict()
    jobs = OrderedDict()
    lock = threading.Lock()
    slots = threading.BoundedSemaphore(2)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError):
        details = [{"loc": item["loc"], "msg": item["msg"], "type": item["type"]}
                   for item in error.errors()]
        return JSONResponse({"detail": details}, status_code=422)

    @app.middleware("http")
    async def local_requests(request: Request, call_next):
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            expected = f"{request.url.scheme}://{request.headers.get('host', '')}"
            if origin and origin != expected:
                return JSONResponse({"detail": "仅接受本地页面的同源请求"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'")
        response.headers["Cache-Control"] = "no-store"
        return response

    def remember(index, demo=False):
        key = secrets.token_urlsafe(16)
        with lock:
            repositories[key] = (index, demo)
            while len(repositories) > 6:
                repositories.popitem(last=False)
        return {"repository_id": key, "demo": demo, **index.overview()}

    @app.get("/api/health")
    def health():
        try:
            settings = provider_store.current()
            return {"status": "ok", "model_ready": bool(settings.key), "model": settings.model}
        except ValueError:
            return {"status": "ok", "model_ready": False, "model": "配置无效"}

    @app.get("/api/provider")
    def get_provider():
        try:
            return provider_store.public()
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    def resolve_provider(body: ProviderRequest, require_model=True):
        try:
            return provider_store.resolve(body.base_url, body.api_key, body.model, require_model)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    @app.post("/api/provider")
    def save_provider(body: ProviderRequest):
        try:
            return provider_store.configure(body.name, body.base_url, body.api_key, body.model)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    @app.post("/api/provider/models")
    def provider_models(body: ProviderRequest):
        settings = resolve_provider(body, require_model=False)
        try:
            return {"models": fetch_models(settings)}
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    @app.post("/api/provider/test")
    def check_provider(body: ProviderRequest):
        settings = resolve_provider(body)
        try:
            return test_connection(settings)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    @app.get("/api/demo")
    def demo():
        index = build_index(DEMO)
        info = remember(index, demo=True)
        trail = deterministic_demo(index, index.flows[0])
        key = secrets.token_urlsafe(16)
        with lock:
            jobs[key] = {"status": "complete", "result": trail}
            if len(jobs) > 24:
                removable = next((item for item, job in jobs.items()
                                  if job["status"] in {"complete", "failed"}), None)
                if removable:
                    del jobs[removable]
        return {"repository": info, "trail": trail, "job_id": key}

    @app.post("/api/analyze")
    def analyze(body: AnalyzeRequest):
        try:
            root = acquire(body.source, cache_dir)
            return remember(build_index(root))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        except OSError:
            raise HTTPException(400, "无法读取目录，请检查路径与访问权限") from None

    def run_job(key, index, flow, demo, settings):
        try:
            with lock:
                jobs[key]["status"] = "running"
            result = deterministic_demo(index, flow) if demo else generate(index, flow, ModelClient(settings))
            with lock:
                jobs[key].update(status="complete", result=result)
        except Exception as exc:
            # Model/provider errors are sanitized by the client. Never return raw exceptions.
            message = str(exc) if isinstance(exc, ValueError) else "生成失败，请在本地检查运行日志"
            with lock:
                jobs[key].update(status="failed", error=message)
        finally:
            slots.release()

    @app.post("/api/generate", status_code=202)
    def start_generation(body: GenerateRequest):
        with lock:
            stored = repositories.get(body.repository_id)
        if not stored:
            raise HTTPException(404, "仓库快照已失效，请重新打开仓库")
        index, demo = stored
        flow = next((item for item in index.flows if item.id == body.flow_id), None)
        if not flow:
            raise HTTPException(400, "请选择已识别的候选流程")
        settings = None
        if not demo:
            try:
                settings = provider_store.current()
                ModelClient(settings)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from None
        if not slots.acquire(blocking=False):
            raise HTTPException(429, "已有生成任务，请等待当前任务完成")
        key = secrets.token_urlsafe(16)
        with lock:
            jobs[key] = {"status": "queued"}
            if len(jobs) > 24:
                removable = next((item for item, job in jobs.items()
                                  if job["status"] in {"complete", "failed"}), None)
                if removable:
                    del jobs[removable]
        try:
            pool.submit(run_job, key, index, flow, demo, settings)
        except RuntimeError:
            slots.release()
            raise HTTPException(503, "服务正在关闭") from None
        return {"job_id": key}

    @app.get("/api/jobs/{key}")
    def get_job(key: str):
        with lock:
            job = jobs.get(key)
            if job is None:
                raise HTTPException(404, "生成任务不存在或已过期")
            return dict(job)

    @app.get("/api/source/{key}")
    def source(key: str, file: str):
        with lock:
            stored = repositories.get(key)
        if not stored:
            raise HTTPException(404, "仓库快照已失效")
        content = stored[0].files.get(file)
        if content is None:
            raise HTTPException(404, "文件不在当前源码快照中")
        return {"file": file, "source": content}

    @app.get("/api/export/{key}")
    def export_job(key: str, format: str = "markdown"):
        job = get_job(key)
        if job["status"] != "complete":
            raise HTTPException(409, "请等待导览生成完成")
        result = job["result"]
        if format == "markdown":
            return Response(markdown(result), media_type="text/markdown",
                            headers={"Content-Disposition": 'attachment; filename="trail.md"'})
        if format == "codetour":
            return JSONResponse(codetour(result), headers={
                "Content-Disposition": 'attachment; filename="reading.tour"'})
        if format == "json":
            return JSONResponse(result, headers={
                "Content-Disposition": 'attachment; filename="trail.json"'})
        raise HTTPException(400, "导出格式需要为 markdown、codetour 或 json")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/")
    def home():
        return FileResponse(STATIC / "index.html")

    return app

