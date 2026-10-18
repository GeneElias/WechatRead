import asyncio

from weread.browser import Browser
from weread.models import Settings


def test_real_chromium_page_flow(tmp_path):
    """A real browser against intercepted fixtures, never a real account."""
    async def scenario():
        browser = Browser(tmp_path)
        await browser.open()
        await browser.context.add_cookies([{"name":"wr_skey","value":"fixture-only","domain":"weread.qq.com","path":"/","secure":True}])
        field = {"value":"2分钟"}

        async def route(request):
            path = request.request.url
            if "/statistics" in path:
                body = '<span id="today">'+field["value"]+'</span>'
            elif "/activity" in path:
                body = '<button onclick="this.textContent=\'已打卡\'">打卡</button>'
            elif "/web/book/read" in path:
                await request.fulfill(json={"succ":1})
                return
            else:
                body = '<p>阅读器测试页</p><script>document.addEventListener("keydown",()=>fetch("/web/book/read",{method:"POST"}));</script>'
            await request.fulfill(content_type="text/html; charset=utf-8", body=body)

        await browser.context.route("https://weread.qq.com/**", route)
        settings = Settings(book_url="https://weread.qq.com/web/reader/test", statistics_url="https://weread.qq.com/statistics", statistics_selector="#today", checkin_url="https://weread.qq.com/activity", checkin_button="打卡", checkin_success="已打卡")
        try:
            assert await browser.statistic(settings) == 120
            field["value"] = "无法解析的官方字段"
            assert await browser.statistic(settings) is None
            await browser.page.goto(settings.book_url)
            result = await browser.read(.3)
            assert result["requests"]["sent"] >= 1
            assert result["requests"]["accepted"] >= 1
            assert "reading" not in result  # Requests are diagnostic, not credited time.
            assert await browser.checkin(settings) == "confirmed_by_page"
            # A daily target already met skips reading, but check-in is still separate.
            field["value"] = "6分钟"
            result = await browser.run(settings)
            assert result["reading"] == "confirmed_by_page"
            assert result["elapsed_seconds"] == 0
            assert result["checkin"] == "confirmed_by_page"
            # Run the full orchestration with controlled page statistics; the
            # timed interaction above was already exercised by the real browser.
            async def simulated_read(seconds):
                assert seconds <= settings.max_minutes * 60
                field["value"] = "未知"
                return {"elapsed_seconds": 360}
            browser.read = simulated_read
            no_statistics = Settings(book_url=settings.book_url)
            result = await browser.run(no_statistics)
            assert result["reading"] == "pending_verification"
            assert result["checkin"] == "not_configured"
            assert result["after_seconds"] is None

            async def credited_read(seconds):
                field["value"] = "8分钟"
                return {"elapsed_seconds": 360}
            browser.read = credited_read
            field["value"] = "2分钟"
            additional = settings.model_copy(update={"goal":"additional"})
            result = await browser.run(additional)
            assert result["reading"] == "confirmed_by_page"
            assert result["after_seconds"] - result["before_seconds"] == 360
        finally:
            await browser.close()
    asyncio.run(scenario())
