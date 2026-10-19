"""Read-only hosted verification of cached login and prior execution evidence."""
import argparse
import asyncio
import json
import re
from pathlib import Path

from .browser import Browser
from .cloud import decrypt_state, private_write, restore, session_key
from .feishu import Feishu, NotificationError
from .models import parse_duration


async def verify(data_dir, reports, previous, browser_factory=Browser):
    result = {"cache_found": False, "authenticated": False, "official_today_seconds": None}
    browser = None
    try:
        prior_files = list(previous.rglob("result.json"))
        if len(prior_files) == 1:
            prior = json.loads(prior_files[0].read_text())
            counts = prior.get("requests", {})
            result["prior_elapsed_seconds"] = prior.get("elapsed_seconds")
            result["prior_requests"] = {key: counts.get(key) for key in ("sent", "accepted", "rejected")}
        cache = data_dir / "session.enc"
        result["cache_found"] = cache.exists()
        if not result["cache_found"]:
            raise ValueError("Cache missing")
        state = decrypt_state(cache.read_text(), session_key())
        browser = browser_factory(data_dir)
        await restore(browser, state)
        result["authenticated"] = bool(await browser.authenticated())
        if result["authenticated"]:
            await browser.page.goto("https://weread.qq.com/web/shelf", wait_until="load")
            text = await browser.page.locator("body").inner_text(timeout=10000)
            # Accept only explicitly labelled daily values, never weekly or
            # lifetime totals. Page text itself is not logged or uploaded.
            matches = re.findall(r"(?:^|\n)(?:今日阅读|今日已读|今天阅读)[：:\s]+([\d:时小时分钟秒\s]+)", text)
            times = [parse_duration(value.strip()) for value in matches]
            if len(times) == 1:
                result["official_today_seconds"] = times[0]
    except Exception as error:
        result["error_type"] = type(error).__name__
    finally:
        if browser:
            await browser.close()
    private_write(reports / "session-verification.json", json.dumps(result,ensure_ascii=False,indent=2))
    print("::notice title=会话与阅读证据复核::"+json.dumps(result,ensure_ascii=False),flush=True)
    bot = Feishu.from_environment()
    if bot:
        counts = result.get("prior_requests", {})
        text = (f"新环境恢复登录态：{'成功' if result['authenticated'] else '未通过'}\n"
                f"上次本地执行：{result.get('prior_elapsed_seconds', '未知')} 秒\n"
                f"阅读请求：发送 {counts.get('sent', '未知')} / 接受 {counts.get('accepted', '未知')} / 拒绝 {counts.get('rejected', '未知')}\n"
                f"官方今日时长：{result['official_today_seconds'] if result['official_today_seconds'] is not None else '页面未提供可确认字段'}\n"
                "本次只读复核，没有重复执行阅读。请求被接受不等于同等时长已计入，签到仍单独核验。")
        try:
            await bot.send({"header":{"title":{"tag":"plain_text","content":"微信读书 · 会话与结果复核"}},
                            "elements":[{"tag":"div","text":{"tag":"plain_text","content":text}}]})
        except NotificationError:
            return 1
    return 0 if result["authenticated"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--previous-reports",type=Path,required=True)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(verify(Path(".data/cloud"),Path("reports"),args.previous_reports)))
