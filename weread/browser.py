import asyncio
import os
import shutil
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit, unquote
from urllib.parse import urljoin

from playwright.async_api import async_playwright

from .models import Settings, parse_duration
from .store import TZ


class LoginRequired(Exception):
    pass


class Browser:
    def __init__(self, data_dir: Path, headless=True):
        self.directory = data_dir / "browser"
        self.context = None
        self.page = None
        self.playwright = None
        self.lock = asyncio.Lock()
        self.headless = headless

    async def open(self):
        if self.context is None:
            self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            self.playwright = await async_playwright().start()
            try:
                proxy_url = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
                proxy = None
                if proxy_url:
                    parsed = urlsplit(proxy_url)
                    proxy = {"server": f"{parsed.scheme}://{parsed.hostname}:{parsed.port or 80}", "bypass": "127.0.0.1,localhost"}
                    if parsed.username:
                        proxy["username"] = unquote(parsed.username)
                        proxy["password"] = unquote(parsed.password or "")
                executable = os.environ.get("WEREAD_CHROMIUM") or shutil.which("chromium") or shutil.which("chromium-browser")
                self.context = await self.playwright.chromium.launch_persistent_context(
                    str(self.directory), headless=self.headless, viewport={"width": 1280, "height": 900},
                    locale="zh-CN", timezone_id="Asia/Shanghai", accept_downloads=False,
                    executable_path=executable, proxy=proxy,
                )
            except Exception:
                await self.playwright.stop()
                self.playwright = None
                raise
            self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()

    async def close(self):
        if self.context:
            await self.context.close()
        if self.playwright:
            await self.playwright.stop()
        self.context = self.page = self.playwright = None

    async def login(self):
        async with self.lock:
            await self.open()
            response = await self.page.goto("https://weread.qq.com/", wait_until="domcontentloaded")
            if response and response.status >= 400:
                raise RuntimeError("官方登录页面不可访问")
            login = self.page.get_by_text("登录", exact=True)
            pages_before = set(self.context.pages)
            for index in range(await login.count()):
                item = login.nth(index)
                if await item.is_visible():
                    await item.click()
                    break
            await self.page.wait_for_timeout(1500)
            opened = [page for page in self.context.pages if page not in pages_before and not page.is_closed()]
            if opened:
                self.page = opened[-1]
                await self.page.wait_for_load_state("domcontentloaded")

    async def saved_session(self):
        if not self.context:
            return False
        return any(c["name"] == "wr_skey" for c in await self.context.cookies("https://weread.qq.com/"))

    async def authenticated(self):
        if not await self.saved_session():
            return False
        try:
            response = await self.context.request.get("https://weread.qq.com/web/shelf/sync", timeout=15000)
            body = await response.json()
            return response.ok and isinstance(body, dict) and not body.get("errCode") and not body.get("errcode") and ("books" in body or "bookIds" in body)
        except Exception:
            return False

    async def discover_book(self):
        await self.page.goto("https://weread.qq.com/web/shelf", wait_until="domcontentloaded")
        links = self.page.locator('a[href*="/web/reader/"]')
        await links.first.wait_for(state="attached", timeout=15000)
        href = urljoin(self.page.url, await links.first.get_attribute("href"))
        return Settings(book_url=href).book_url

    async def screenshot(self):
        if self.lock.locked():
            raise RuntimeError("任务运行中，暂不可刷新登录截图")
        async with self.lock:
            if not self.page:
                raise RuntimeError("请先打开登录页面")
            return await self.page.screenshot(type="png")

    async def statistic(self, settings):
        if not settings.statistics_url:
            return None
        try:
            await self.page.goto(settings.statistics_url, wait_until="domcontentloaded")
            field = self.page.locator(settings.statistics_selector)
            await field.wait_for(state="visible", timeout=8000)
            if await field.count() != 1:
                return None
            return parse_duration(await field.inner_text(timeout=8000))
        except Exception:
            return None

    async def checkin(self, settings):
        if not settings.checkin_url:
            return "not_configured"
        await self.page.goto(settings.checkin_url, wait_until="domcontentloaded")
        success = self.page.get_by_text(settings.checkin_success, exact=True)
        if await success.count() == 1 and await success.is_visible():
            return "confirmed_by_page"
        button = self.page.get_by_text(settings.checkin_button, exact=True)
        try:
            await button.wait_for(state="visible", timeout=8000)
        except Exception:
            return "pending_verification"
        if await button.count() != 1:
            return "pending_verification"
        await button.click(timeout=8000)
        try:
            await success.wait_for(state="visible", timeout=10000)
            return "confirmed_by_page"
        except Exception:
            return "pending_verification"

    async def read(self, seconds):
        """Operate the official reader; elapsed time is never treated as credited time."""
        counts = {"sent": 0, "accepted": 0, "rejected": 0}

        def request(req):
            if req.url.split("?")[0] == "https://weread.qq.com/web/book/read":
                counts["sent"] += 1

        async def response(resp):
            if resp.url.split("?")[0] == "https://weread.qq.com/web/book/read":
                try:
                    body = await resp.json()
                    counts["accepted" if body.get("succ") == 1 else "rejected"] += 1
                except Exception:
                    counts["rejected"] += 1

        self.page.on("request", request)
        self.page.on("response", response)
        start = time.monotonic()
        try:
            while time.monotonic() - start < seconds:
                if not await self.saved_session():
                    raise LoginRequired("登录态缺失，请重新扫码")
                if counts["rejected"] >= 3:
                    raise RuntimeError("阅读请求多次被拒绝，请检查官方页面状态")
                await self.page.keyboard.press("PageDown")
                await self.page.wait_for_timeout(min(15000, max(1, (seconds - (time.monotonic() - start)) * 1000)))
        finally:
            self.page.remove_listener("request", request)
            self.page.remove_listener("response", response)
        return {"elapsed_seconds": round(time.monotonic() - start), "requests": counts}

    async def run(self, settings: Settings):
        async with self.lock:
            started_day = datetime.now(TZ).date()
            await self.open()
            if not await self.saved_session():
                raise LoginRequired("请先扫描官方二维码登录")
            target = settings.target_minutes * 60
            before = await self.statistic(settings)
            result = {"before_seconds": before, "after_seconds": before, "reading": "pending_verification", "checkin": "not_configured", "elapsed_seconds": 0}
            if settings.goal == "daily" and before is not None and before >= target:
                result["reading"] = "confirmed_by_page"
                result["message"] = "官方页面所选字段显示今日已达目标，跳过阅读"
            else:
                response = await self.page.goto(settings.book_url, wait_until="domcontentloaded")
                if response and response.status >= 400:
                    raise RuntimeError("阅读器页面不可访问")
                if "/web/reader/" not in self.page.url:
                    raise LoginRequired("未进入阅读器，请检查登录和书籍链接")
                needed = max(0, target - (before or 0)) if settings.goal == "daily" else target
                # Configurable margin, bounded by the total runtime budget.
                seconds = min(settings.max_minutes * 60, needed + 60)
                result.update(await self.read(seconds))
                after = None
                if settings.statistics_url:
                    for attempt in range(3):
                        after = await self.statistic(settings)
                        measured = after if settings.goal == "daily" else (after - before if after is not None and before is not None else None)
                        if measured is not None and measured >= target:
                            result["reading"] = "confirmed_by_page"
                            break
                        if attempt < 2:
                            await asyncio.sleep(5)
                result["after_seconds"] = after
                result["message"] = "已执行网页阅读；运行时间和请求计数不代表官方计入时长"
            if datetime.now(TZ).date() != started_day:
                result["reading"] = "pending_verification"
                result["message"] = "任务跨越自然日，统计不可直接比较，请核对官方记录"
            try:
                result["checkin"] = await self.checkin(settings)
            except Exception:
                result["checkin"] = "pending_verification"
            return result
