import asyncio
from datetime import datetime
import io
import json
import zipfile

import httpx
import pytest

from weread.status_report import BEIJING, GitHubStatus, make_card


def run(run_id, date="2026-10-23T01:17:00Z", status="completed", conclusion="success"):
    return {"id": run_id, "run_attempt": 2, "created_at": date, "status": status, "conclusion": conclusion}


def archive(result):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as output:
        output.writestr("result.json", json.dumps(result))
    return buffer.getvalue()


@pytest.mark.parametrize("latest_status,conclusion", [("completed", "skipped"), ("queued", None), ("in_progress", None)])
def test_reports_latest_state_without_calling_old_evidence_today(latest_status, conclusion):
    async def scenario():
        latest = run(20, status=latest_status, conclusion=conclusion)
        previous = run(10, "2026-10-19T01:49:00Z")
        result = {"status": "executed", "elapsed_seconds": 360, "reading": "pending_verification",
                  "checkin": "not_configured", "requests": {"sent": 12, "accepted": 12, "rejected": 0},
                  "message": "private-token", "book_url": "https://private.example/secret"}
        requests = []
        def handle(request):
            requests.append(request.url.path)
            path = request.url.path
            if path.endswith("/daily-read.yml"):
                return httpx.Response(200, json={"state": "active"})
            if path.endswith("/daily-read.yml/runs"):
                return httpx.Response(200, json={"workflow_runs": [latest]})
            if path.endswith("/feishu-login-test.yml/runs"):
                return httpx.Response(200, json={"workflow_runs": [previous]})
            if path.endswith("/runs/10/artifacts"):
                return httpx.Response(200, json={"artifacts": [{"id": 7, "name": "weread-result-10-1", "expired": False},
                                                              {"id": 8, "name": "weread-result-10-2", "expired": False}]})
            if path.endswith("/artifacts/8/zip"):
                return httpx.Response(200, content=archive(result))
            raise AssertionError(path)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            snapshot = await GitHubStatus(client, "GeneElias/WechatRead").collect()
        card = make_card("GeneElias/WechatRead", snapshot, False, datetime(2026, 10, 23, 18, tzinfo=BEIJING))
        text = json.dumps(card, ensure_ascii=False)
        assert "未启用" in text and "历史结果不代表今日已执行" in text
        assert "2026-10-19 09:49" in text and "接受 12" in text
        assert "待确认" in text and "未配置" in text
        assert "private" not in text
        assert "/actions/runs/20" in text
        assert not any("/web/" in path for path in requests)
    asyncio.run(scenario())


def test_empty_history_and_disabled_workflow():
    card = make_card("owner/repo", {"latest": None, "completed": None, "evidence": None,
                                   "evidence_error": False, "workflow_enabled": False}, True)
    text = json.dumps(card, ensure_ascii=False)
    assert "暂无记录" in text and "已停用" in text
    assert "本报告不执行阅读" in text


@pytest.mark.parametrize("mode", ["expired", "missing", "bad_zip", "too_large", "http_error"])
def test_unavailable_results_still_report_workflow_failure(mode):
    async def scenario():
        item = run(10, conclusion="failure")
        def handle(request):
            path = request.url.path
            if path.endswith("/daily-read.yml"):
                return httpx.Response(200, json={"state": "active"})
            if path.endswith("/runs"):
                return httpx.Response(200, json={"workflow_runs": [item] if "daily-read" in path else []})
            if path.endswith("/artifacts"):
                return httpx.Response(200, json={"artifacts": [] if mode == "missing" else [
                    {"id": 8, "name": "weread-result-10-2", "expired": mode == "expired"}]})
            if mode == "http_error":
                return httpx.Response(403)
            return httpx.Response(200, content=b"x" * (2_000_001 if mode == "too_large" else 20))
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            snapshot = await GitHubStatus(client, "owner/repo").collect()
        text = json.dumps(make_card("owner/repo", snapshot, True), ensure_ascii=False)
        assert "失败" in text and "详细结果" in text
        assert snapshot["evidence"] is None
    asyncio.run(scenario())


def test_artifact_redirect_does_not_forward_github_token():
    async def scenario():
        def handle(request):
            if request.url.host == "api.github.com":
                assert request.headers["Authorization"] == "Bearer fixture-token"
                if request.url.path.endswith("/artifacts"):
                    return httpx.Response(200, json={"artifacts": [{"id": 8, "name": "weread-result-10-2", "expired": False}]})
                return httpx.Response(302, headers={"Location": "https://artifact.example/signed-fixture"})
            assert "Authorization" not in request.headers
            return httpx.Response(200, content=archive({"status": "executed"}))
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle), follow_redirects=True,
                                     headers={"Authorization": "Bearer fixture-token"}) as client:
            assert await GitHubStatus(client, "owner/repo").result(run(10)) == {"status": "executed"}
    asyncio.run(scenario())


def test_github_outage_sends_failure_notice_without_leaking_exception(monkeypatch, capsys):
    from weread import status_report

    cards = []
    class Bot:
        async def send(self, card):
            cards.append(card)
    async def fail(self):
        raise RuntimeError("fixture-secret-signed-url")
    monkeypatch.setattr(status_report.Feishu, "from_environment", lambda: Bot())
    monkeypatch.setattr(GitHubStatus, "collect", fail)
    monkeypatch.setenv("GH_TOKEN", "fixture-token")
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    assert asyncio.run(status_report.main()) == 1
    text = json.dumps(cards, ensure_ascii=False) + capsys.readouterr().out
    assert "查询失败" in text
    assert "fixture" not in text
