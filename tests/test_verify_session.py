import asyncio
import json

from weread.verify_session import verify


def test_missing_cache_is_reported_without_requesting_new_login(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("FEISHU_WEBHOOK_URL", raising=False)
    previous = tmp_path / "previous"
    previous.mkdir()
    (previous / "result.json").write_text(json.dumps({"elapsed_seconds":360,"requests":{"sent":6,"accepted":6,"rejected":0}}))
    assert asyncio.run(verify(tmp_path/"data",tmp_path/"reports",previous)) == 1
    result = json.loads((tmp_path/"reports/session-verification.json").read_text())
    assert result["authenticated"] is False
    assert result["cache_found"] is False
    assert result["prior_requests"]["accepted"] == 6
    assert result["official_today_seconds"] is None
