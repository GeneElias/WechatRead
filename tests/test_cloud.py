import asyncio
import json

import pytest
from cryptography.fernet import Fernet, InvalidToken

from weread.browser import Browser, LoginRequired
from weread.cloud import decrypt_state, encrypt_state, load_settings, load_state, restore, run


def test_authenticated_encryption_and_fallback(tmp_path, monkeypatch):
    key = Fernet.generate_key().decode()
    state = {"cookies": [], "origins": []}
    encrypted = encrypt_state(state, key)
    assert decrypt_state(encrypted, key) == state
    with pytest.raises(InvalidToken):
        decrypt_state(encrypted, Fernet.generate_key())
    monkeypatch.setenv("WEREAD_INITIAL_STATE", encrypted)
    cache = tmp_path / "session.enc"
    assert load_state(cache, key) == state
    cache.write_text("corrupt-cache")
    assert load_state(cache, key) == state
    cache.write_text(encrypt_state({"cookies": [{"name":"updated"}]}, key))
    assert load_state(cache, key)["cookies"][0]["name"] == "updated"


def test_settings_and_missing_secrets(tmp_path, monkeypatch):
    config = tmp_path / "config.json"
    config.write_text('{}')
    monkeypatch.setenv("WEREAD_BOOK_URL", "https://weread.qq.com/web/reader/test")
    assert load_settings(config).target_minutes == 5
    monkeypatch.delenv("WEREAD_SESSION_KEY", raising=False)
    assert asyncio.run(run(config, tmp_path / "data", tmp_path / "reports")) == 1
    result = json.loads((tmp_path / "reports/result.json").read_text())
    assert result["status"] == "failed"
    assert result["reading"] == "pending_verification"


def test_cloud_browser_restore_and_results(tmp_path, monkeypatch):
    key = Fernet.generate_key().decode()
    state = {"cookies":[{"name":"wr_skey","value":"fixture-secret","domain":"weread.qq.com","path":"/","secure":True}], "origins":[{"origin":"https://weread.qq.com", "localStorage":[{"name":"fixture","value":"restored"}]}]}
    monkeypatch.setenv("WEREAD_SESSION_KEY", key)
    monkeypatch.setenv("WEREAD_INITIAL_STATE", encrypt_state(state, key))
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"book_url":"https://weread.qq.com/web/reader/test"}))

    class FixtureBrowser(Browser):
        async def run(self, settings):
            async def route(request):
                await request.fulfill(content_type="text/html", body="<p>fixture</p>")
            await self.context.route("https://weread.qq.com/**", route)
            await self.page.goto(settings.book_url)
            assert await self.page.evaluate("localStorage.getItem('fixture')") == "restored"
            await self.page.evaluate("localStorage.setItem('fixture','renewed')")
            await self.page.reload()
            assert await self.page.evaluate("localStorage.getItem('fixture')") == "renewed"
            assert await self.saved_session()
            return {"reading":"pending_verification","checkin":"not_configured","elapsed_seconds":300}

    assert asyncio.run(run(config, tmp_path / "data", tmp_path / "reports", FixtureBrowser)) == 0
    cached = decrypt_state((tmp_path / "data/session.enc").read_text(), key)
    assert cached["origins"][0]["localStorage"][0]["value"] == "renewed"
    for file in (tmp_path / "reports").iterdir():
        assert "fixture-secret" not in file.read_text()
        assert key not in file.read_text()
    result = json.loads((tmp_path / "reports/result.json").read_text())
    assert result["status"] == "executed"
    assert result["reading"] == "pending_verification"

    class Expired(FixtureBrowser):
        async def run(self, settings):
            raise LoginRequired()
    assert asyncio.run(run(config, tmp_path / "data", tmp_path / "reports", Expired)) == 1
    assert json.loads((tmp_path / "reports/result.json").read_text())["status"] == "needs_login"
