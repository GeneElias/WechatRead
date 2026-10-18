import asyncio
import io
import time
from urllib.parse import urljoin

from PIL import Image
import zxingcpp

from .browser import LoginRequired
from .feishu import official_login_url


def decode_qr(png):
    for barcode in zxingcpp.read_barcodes(Image.open(io.BytesIO(png))):
        if official_login_url(barcode.text):
            return barcode.text
    return None


async def qr_link(page):
    candidates = page.locator('img[class*="qr"], img[src*="qr"], img[alt*="二维码"], canvas')
    for index in range(min(await candidates.count(), 10)):
        item = candidates.nth(index)
        if not await item.is_visible():
            continue
        decoded = decode_qr(await item.screenshot())
        if decoded:
            src = urljoin(page.url, await item.get_attribute("src") or "")
            return decoded, src if src != page.url and official_login_url(src) else None
    decoded = decode_qr(await page.screenshot())
    return (decoded, None) if decoded else (None, None)


async def ensure_login(browser, notifier, timeout=600, poll=5):
    if await browser.authenticated():
        return
    if notifier is None:
        raise LoginRequired("远程登录需要先配置飞书机器人")
    await browser.login()
    deadline = time.monotonic() + timeout
    last_url, refreshed, sent = None, time.monotonic(), 0
    while time.monotonic() < deadline:
        if await browser.authenticated():
            return
        link, image = await qr_link(browser.page)
        if link and link != last_url and sent < 6:
            await notifier.login(link, image)
            last_url, sent = link, sent + 1
        if time.monotonic() - refreshed >= 90:
            await browser.login()
            refreshed = time.monotonic()
        await asyncio.sleep(poll)
    raise LoginRequired("扫码等待超时")
