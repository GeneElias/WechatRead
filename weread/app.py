import asyncio
import contextlib
import os
import secrets
import shutil
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from .browser import Browser, LoginRequired
from .models import Settings
from .store import Store, TZ

ROOT = Path(__file__).resolve().parent.parent


class Controller:
    def __init__(self, store, browser):
        self.store, self.browser = store, browser
        self.task = None
        self.active_job = None

    def busy(self):
        return self.task is not None and not self.task.done()

    async def execute(self, job_id, settings):
        self.store.update(job_id, "running", {})
        try:
            result = await self.browser.run(settings)
            # Reading and check-in are independently reported in result.
            status = "verified" if result["reading"] == "confirmed_by_page" else "pending_verification"
            self.store.update(job_id, status, result)
        except asyncio.CancelledError:
            self.store.update(job_id, "interrupted", {"message": "任务已取消或服务关闭；请核对官方结果"})
            raise
        except LoginRequired:
            self.store.update(job_id, "needs_login", {"message": "登录态不可用或未进入阅读器，请扫码并检查书籍链接"})
        except Exception:
            # Exception text can contain cookies or response bodies; do not expose it.
            self.store.update(job_id, "failed", {"message": "网页任务失败，请检查网络、登录态、书籍权限和页面配置"})

    def launch(self, source="manual", date=None):
        if self.busy() or self.browser.lock.locked():
            raise HTTPException(409, "已有任务或登录操作正在进行")
        settings = self.store.settings()
        if not settings.book_url or "/web/reader/" not in settings.book_url:
            raise HTTPException(422, "请先保存微信读书网页版阅读器链接")
        job_id = uuid4().hex
        if not self.store.create_job(job_id, source, date, settings):
            return None
        self.task = asyncio.create_task(self.execute(job_id, settings))
        self.active_job = job_id
        return job_id

    def tick(self, current):
        settings = self.store.settings()
        day = current.date().isoformat()
        if settings.scheduled and current.strftime("%H:%M") >= settings.daily_time and not self.busy() and not self.browser.lock.locked():
            return self.launch("schedule", day)
        return None

    async def scheduler(self):
        while True:
            try:
                self.tick(datetime.now(TZ))
            except HTTPException:
                pass
            await asyncio.sleep(20)

    async def cancel(self):
        if self.busy():
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task
            self.store.update(self.active_job, "interrupted", {"message": "任务已取消或服务关闭；请核对官方结果"})


def create_app(data_dir=None, browser=None):
    directory = Path(data_dir or os.environ.get("WEREAD_DATA_DIR", ROOT / ".data"))
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(ROOT / ".data" / "browsers"))
    token_path = directory / "admin-token"
    if not token_path.exists():
        # Exclusive creation avoids replacing an existing token.
        with token_path.open("x") as f:
            token_path.chmod(0o600)
            f.write(secrets.token_urlsafe(32))
    admin_token = token_path.read_text().strip()
    store = Store(directory)
    controller = Controller(store, browser or Browser(directory))
    sessions = set()

    @asynccontextmanager
    async def lifespan(app):
        store.recover()
        scheduler = asyncio.create_task(controller.scheduler())
        yield
        scheduler.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await scheduler
        await controller.cancel()
        await controller.browser.close()

    app = FastAPI(title="微读 · 每日阅读助手", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.controller = controller
    app.state.store = store

    @app.middleware("http")
    async def protect(request: Request, call_next):
        if request.url.path.startswith("/api/") and request.url.path != "/api/auth":
            cookie = request.cookies.get("weread_session", "")
            if cookie not in sessions:
                return Response("需要管理员登录", status_code=401)
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin")
            if origin and origin != f"{request.url.scheme}://{request.headers.get('host', '')}":
                return Response("来源不匹配", status_code=403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        return response

    class Auth(BaseModel):
        token: str

    @app.post("/api/auth")
    async def auth(payload: Auth):
        if not secrets.compare_digest(payload.token, admin_token):
            await asyncio.sleep(0.3)
            raise HTTPException(401, "管理口令不正确")
        session = secrets.token_urlsafe(32)
        sessions.add(session)
        response = Response(status_code=204)
        response.set_cookie("weread_session", session, httponly=True, samesite="strict", secure=os.environ.get("WEREAD_SECURE_COOKIE") == "1")
        return response

    @app.post("/api/logout")
    async def logout(request: Request):
        sessions.discard(request.cookies.get("weread_session", ""))
        response = Response(status_code=204)
        response.delete_cookie("weread_session")
        return response

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/api/status")
    async def status():
        return {"running": controller.busy(), "browser_busy": controller.browser.lock.locked(), "session_saved": await controller.browser.saved_session(), "timezone": "Asia/Shanghai"}

    @app.get("/api/settings")
    async def get_settings():
        return store.settings()

    @app.put("/api/settings")
    async def put_settings(settings: Settings):
        store.save_settings(settings)
        return settings

    @app.get("/api/jobs")
    async def jobs():
        return store.history()

    @app.post("/api/jobs")
    async def launch():
        return {"id": controller.launch()}

    @app.post("/api/jobs/cancel")
    async def cancel():
        await controller.cancel()
        return {"status": "cancelled"}

    @app.post("/api/browser/login")
    async def login():
        if controller.busy() or controller.browser.lock.locked():
            raise HTTPException(409, "任务运行中，请先停止任务")
        try:
            await controller.browser.login()
        except Exception:
            raise HTTPException(502, "无法打开官网。请检查网络是否允许 weread.qq.com，以及 Chromium 是否已安装") from None
        return {"status": "opened"}

    @app.get("/api/browser/screenshot")
    async def screenshot():
        try:
            return Response(await controller.browser.screenshot(), media_type="image/png")
        except RuntimeError as error:
            raise HTTPException(409, str(error)) from None

    @app.post("/api/browser/disconnect")
    async def disconnect():
        if controller.busy() or controller.browser.lock.locked():
            raise HTTPException(409, "请先停止任务")
        async with controller.browser.lock:
            await controller.browser.close()
            shutil.rmtree(controller.browser.directory, ignore_errors=True)
        return {"status": "disconnected"}

    @app.get("/")
    async def index():
        return FileResponse(ROOT / "weread" / "static" / "index.html")

    @app.get("/assets/{name}")
    async def asset(name: str):
        if name not in ("app.js", "style.css"):
            raise HTTPException(404)
        return FileResponse(ROOT / "weread" / "static" / name)

    return app
