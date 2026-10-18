import asyncio
from datetime import datetime

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from weread.app import Controller, create_app
from weread.browser import LoginRequired
from weread.models import Settings, parse_duration
from weread.store import Store, TZ


class FakeBrowser:
    def __init__(self):
        self.lock = asyncio.Lock()

    async def saved_session(self):
        return False

    async def close(self):
        pass

    async def run(self, settings):
        return {"reading": "pending_verification", "checkin": "not_configured", "elapsed_seconds": 300}


def test_access_settings_and_pending_outcome(tmp_path):
    app = create_app(tmp_path, FakeBrowser())
    with TestClient(app) as client:
        assert client.get("/").status_code == 200
        assert client.get("/api/settings").status_code == 401
        assert client.post("/api/auth", json={"token": "wrong"}).status_code == 401
        assert client.post("/api/auth", json={"token": tmp_path.joinpath("admin-token").read_text()}).status_code == 204
        config = Settings(book_url="https://weread.qq.com/web/reader/test").model_dump()
        assert client.put("/api/settings", json=config).status_code == 200
        assert client.put("/api/settings", json=config, headers={"Origin": "https://attacker.example"}).status_code == 403
        bad = config | {"book_url": "http://127.0.0.1/private"}
        assert client.put("/api/settings", json=bad).status_code == 422
        assert client.post("/api/jobs").status_code == 200
        # Let the worker complete on the server event loop.
        client.portal.call(lambda: app.state.controller.task)
        job = client.get("/api/jobs").json()[0]
        assert job["status"] == "pending_verification"
        assert job["result"]["elapsed_seconds"] == 300
        assert job["result"]["checkin"] == "not_configured"
        assert client.post("/api/logout").status_code == 204
        assert client.get("/api/jobs").status_code == 401
    assert Store(tmp_path).settings().book_url == config["book_url"]


def test_scheduler_once_per_day_and_recovery(tmp_path):
    async def scenario():
        store = Store(tmp_path)
        store.save_settings(Settings(book_url="https://weread.qq.com/web/reader/test", scheduled=True))
        controller = Controller(store, FakeBrowser())
        assert controller.tick(datetime(2026, 10, 18, 8, 59, tzinfo=TZ)) is None
        assert controller.tick(datetime(2026, 10, 18, 9, 0, tzinfo=TZ))
        with pytest.raises(HTTPException):
            controller.launch()
        await controller.task
        assert controller.tick(datetime(2026, 10, 18, 12, 0, tzinfo=TZ)) is None
        assert controller.tick(datetime(2026, 10, 19, 9, 30, tzinfo=TZ))
        await controller.task
        assert len(store.history()) == 2
        store.create_job("interrupted", "manual", None, store.settings())
        store.recover()
        assert store.history()[0]["status"] == "interrupted"
    asyncio.run(scenario())


def test_cancellation_and_login_required(tmp_path):
    class Slow(FakeBrowser):
        async def run(self, settings):
            await asyncio.sleep(600)

    async def scenario():
        store = Store(tmp_path)
        store.save_settings(Settings(book_url="https://weread.qq.com/web/reader/test"))
        controller = Controller(store, Slow())
        controller.launch()
        # Cancelling before the worker starts must not leave a queued job behind.
        await controller.cancel()
        assert store.history()[0]["status"] == "interrupted"

        async def login_error(settings):
            raise LoginRequired()
        controller.browser.run = login_error
        controller.launch()
        await controller.task
        assert store.history()[0]["status"] == "needs_login"
    asyncio.run(scenario())


@pytest.mark.parametrize("text,expected", [("5分钟30秒",330),("1小时2分钟",3720),("05:00",300),("01:02:03",3723),("300",None),("今日5分钟 总计6小时",None),("1:99",None),("",None)])
def test_duration(text, expected):
    assert parse_duration(text) == expected


@pytest.mark.parametrize("changes", [{"max_minutes":4},{"daily_time":"25:00"},{"scheduled":True},{"statistics_url":"https://weread.qq.com/"},{"checkin_url":"https://weread.qq.com/"},{"goal":"unknown"},{"book_url":"https://weread.qq.com.evil.test/"}])
def test_configuration_invariants(changes):
    with pytest.raises(ValueError):
        Settings(**changes)
