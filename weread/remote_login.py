import asyncio
import base64
import binascii
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
    # Official login can use a popup, an iframe, or an unlabelled data-URI image.
    # Locator queries on the original main page do not reach those documents.
    for document in reversed(page.context.pages):
        if document.is_closed():
            continue
        for frame in document.frames:
            candidates = frame.locator('img[class*="qr" i], img[src*="qr" i], img[alt*="二维码"], img[src^="data:image/png"], canvas')
            for index in range(min(await candidates.count(), 20)):
                item = candidates.nth(index)
                if not await item.is_visible():
                    continue
                source = await item.get_attribute("src") or ""
                decoded = None
                if source.startswith("data:image/png;base64,") and len(source) < 5_000_000:
                    try:
                        # Read the original QR pixels; an iframe can clip an
                        # element screenshot even though the full image loaded.
                        decoded = decode_qr(base64.b64decode(source.split(",",1)[1], validate=True))
                    except (ValueError, binascii.Error, OSError):
                        pass
                if not decoded:
                    decoded = decode_qr(await item.screenshot())
                if decoded:
                    src = urljoin(frame.url, source)
                    return decoded, src if src != frame.url and official_login_url(src) else None
        decoded = decode_qr(await document.screenshot())
        if decoded:
            return decoded, None
    return None, None


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
            print("官方登录链接已发送至飞书，等待扫码确认", flush=True)
            last_url, sent = link, sent + 1
        if time.monotonic() - refreshed >= 90:
            await browser.login()
            refreshed = time.monotonic()
        await asyncio.sleep(poll)
    raise LoginRequired("扫码等待超时")
