"""Bounded live probes. Outputs counts/statuses only, never QR or credentials."""
import asyncio
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

from .browser import Browser
from .feishu import Feishu, NotificationError
from .remote_login import qr_link


async def diagnose(data_dir=Path(".data/diagnose")):
    result = {"webhook_present": bool(os.environ.get("FEISHU_WEBHOOK_URL")),
              "signing_configured": bool(os.environ.get("FEISHU_SIGN_SECRET"))}
    browser = Browser(data_dir)
    try:
        try:
            bot = Feishu.from_environment()
            if bot is None:
                raise ValueError("missing webhook")
            await bot.send({"header":{"title":{"tag":"plain_text","content":"微信读书 · 连接测试"}},
                            "elements":[{"tag":"div","text":{"tag":"plain_text","content":"微信读书：机器人投递测试。这条不是登录二维码，暂时不需要操作；程序正在检查官网登录页。"}}]})
            result["feishu_accepted"] = True
        except NotificationError as error:
            result.update(feishu_accepted=False, feishu_error_code=error.code)
        except ValueError:
            result.update(feishu_accepted=False, webhook_valid=False)
        try:
            await browser.login()
            result["website_opened"] = True
            result["page_host"] = urlsplit(browser.page.url).hostname
            for _ in range(5):
                link, image = await qr_link(browser.page)
                if link:
                    result.update(qr_decoded=True, qr_image_link=bool(image))
                    break
                await asyncio.sleep(2)
            result.setdefault("qr_decoded", False)
            result["images"] = await browser.page.locator("img").count()
            result["canvases"] = await browser.page.locator("canvas").count()
            for text, name in [("登录","login_labels"),("扫码","scan_labels"),("安全验证","challenge_labels")]:
                fields = browser.page.get_by_text(text, exact=True)
                result[name] = await fields.count()
                visible = 0
                for index in range(min(await fields.count(),20)):
                    visible += int(await fields.nth(index).is_visible())
                result[name+"_visible"] = visible
        except Exception as error:
            result.update(website_opened=False, browser_error_type=type(error).__name__)
    finally:
        await browser.close()
    payload = json.dumps(result,ensure_ascii=False,sort_keys=True)
    print("::notice title=飞书与官网登录诊断::"+payload,flush=True)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"],"a") as file:
            file.write("## 飞书与官网登录诊断\n\n```json\n"+json.dumps(result,ensure_ascii=False,indent=2)+"\n```\n")
    return 0 if result.get("feishu_accepted") and result.get("qr_decoded") else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(diagnose()))
