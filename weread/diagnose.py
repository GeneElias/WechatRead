"""Bounded live probes. Outputs counts/statuses only, never QR or credentials."""
import asyncio
import json
import os
import re
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
            await browser.open()
            failures, script_errors = [], []
            def failed(request):
                failures.append({"host":urlsplit(request.url).hostname,"reason":request.failure})
            def page_error(error):
                text = re.sub(r"https?://\S+|[A-Za-z0-9_-]{24,}","[redacted]",str(error))
                script_errors.append(text[:160])
            browser.page.on("requestfailed",failed)
            browser.page.on("pageerror",page_error)
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
            result["png_images"] = await browser.page.locator('img[src^="data:image/png"]').count()
            result["pages"] = len(browser.context.pages)
            result["frame_hosts"] = [urlsplit(frame.url).hostname for page in browser.context.pages for frame in page.frames]
            result["frame_images"] = [await frame.locator("img").count() for page in browser.context.pages for frame in page.frames]
            hints = []
            for page in browser.context.pages:
                for frame in page.frames:
                    # This probe never restores an account. Retain only short
                    # visible login/error hints from the fresh public page.
                    text = await frame.locator("body").inner_text(timeout=5000)
                    text = re.sub(r"https?://\S+", "[url]", text)
                    text = re.sub(r"[A-Za-z0-9_-]{24,}|\d{7,}", "[redacted]", text)
                    hints.extend(re.findall(r"[^\n]{0,12}(?:登录|扫码|扫一扫|错误|失败|验证|频繁|限制|刷新|加载)[^\n]{0,32}", text))
            result["login_hints"] = hints[:12]
            result["failed_requests"] = failures[:12]
            result["script_errors"] = script_errors[:8]
            result["client_state"] = await browser.page.evaluate("""() => ({
                ready:document.readyState, nuxt:!!window.$nuxt,
                nuxtMounted:!!window.$nuxt?._isMounted,
                vueRoot:!!document.querySelector('#__nuxt')?.__vue__,
                scripts:document.scripts.length
            })""")
            result["script_hosts"] = await browser.page.locator("script[src]").evaluate_all("elements=>[...new Set(elements.map(e=>new URL(e.src).hostname))]")
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
        try:
            await browser.close()
        except Exception:
            result["browser_close_error"] = True
    payload = json.dumps(result,ensure_ascii=False,sort_keys=True)
    print("::notice title=飞书与官网登录诊断::"+payload,flush=True)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"],"a") as file:
            file.write("## 飞书与官网登录诊断\n\n```json\n"+json.dumps(result,ensure_ascii=False,indent=2)+"\n```\n")
    return 0 if result.get("feishu_accepted") and result.get("qr_decoded") else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(diagnose()))
