import asyncio
import base64
import hashlib
import hmac
import io
import json

import httpx
import pytest
from PIL import Image
import zxingcpp

from weread.browser import Browser, LoginRequired
from weread.feishu import Feishu, NotificationError, official_login_url
from weread.remote_login import decode_qr, ensure_login

WEBHOOK = "https://open.feishu.cn/open-apis/bot/v2/hook/fixture-only-secret"
LINK = "https://open.weixin.qq.com/connect/confirm?uuid=fixture-private-qr"


def qr_png(text):
    barcode = zxingcpp.create_barcode(text, zxingcpp.BarcodeFormat.QRCode)
    image = Image.fromarray(zxingcpp.write_barcode_to_image(barcode, scale=5))
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def test_real_qr_decode_and_url_validation():
    assert decode_qr(qr_png(LINK)) == LINK
    assert decode_qr(qr_png("https://attacker.example/login")) is None
    assert not official_login_url("https://open.weixin.qq.com.attacker.example/")
    assert not official_login_url("https://user@open.weixin.qq.com/")


def test_bot_signature_and_errors():
    async def scenario():
        received = []
        def handle(request):
            body = json.loads(request.content)
            received.append(body)
            expected = base64.b64encode(hmac.new((body['timestamp']+'\nsigning-key').encode(), b'', hashlib.sha256).digest()).decode()
            assert body['sign'] == expected
            return httpx.Response(200, json={"code":0})
        bot = Feishu(WEBHOOK, "signing-key", httpx.MockTransport(handle))
        await bot.login(LINK, "https://open.weixin.qq.com/connect/qrcode/fixture")
        assert received[0]["card"]["elements"][1]["actions"][0]["text"]["content"] == "打开官方二维码"
        bot.transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"code":19021,"msg":WEBHOOK}))
        with pytest.raises(NotificationError) as error:
            await bot.login(LINK)
        assert "fixture-only-secret" not in str(error.value)
        with pytest.raises(NotificationError):
            await bot.login("https://attacker.example/")
    asyncio.run(scenario())


def test_browser_qr_notification_and_automatic_resume(tmp_path):
    async def scenario():
        browser = Browser(tmp_path)
        await browser.open()
        image = base64.b64encode(qr_png(LINK)).decode()
        received = []
        async def route(request):
            if "/web/shelf/sync" in request.request.url:
                await request.fulfill(json={"books":[]})
            elif "/web/shelf" in request.request.url:
                await request.fulfill(content_type="text/html", body='<a href="/web/reader/test">book</a>')
            else:
                await request.fulfill(content_type="text/html", body=f'<img class="qr" src="data:image/png;base64,{image}">')
        await browser.context.route("https://weread.qq.com/**", route)
        # context.request does not use page route interception; replace just
        # the authentication probe while exercising the actual page/QR flow.
        async def authenticated():
            return await browser.saved_session()
        browser.authenticated = authenticated
        async def handle(request):
            body = json.loads(request.content)
            received.append(body)
            await browser.context.add_cookies([{"name":"wr_skey","value":"fixture","domain":"weread.qq.com","path":"/","secure":True}])
            return httpx.Response(200,json={"code":0})
        bot = Feishu(WEBHOOK, transport=httpx.MockTransport(handle))
        try:
            await ensure_login(browser, bot, timeout=10, poll=.01)
            assert await browser.saved_session()
            assert len(received) == 1
            assert LINK in json.dumps(received)
            assert await browser.discover_book() == "https://weread.qq.com/web/reader/test"
            # A valid session resumes without a second QR notification.
            await ensure_login(browser, bot, timeout=1, poll=.01)
            assert len(received) == 1
        finally:
            await browser.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("layout", ["iframe", "popup", "inline", "hydrated"])
def test_login_qr_in_nested_and_unlabelled_documents(tmp_path, layout):
    from weread.remote_login import qr_link

    async def scenario():
        browser = Browser(tmp_path)
        await browser.open()
        image = base64.b64encode(qr_png(LINK)).decode()
        qr = f'<img src="data:image/png;base64,{image}">'
        async def route(request):
            if request.request.url.endswith("/qr"):
                body = qr
            elif layout == "popup":
                body = '<button onclick="window.open(\'/qr\',\'_blank\')">登录</button>'
            elif layout == "iframe":
                body = '<button onclick="this.insertAdjacentHTML(\'afterend\',\'<iframe src=/qr></iframe>\')">登录</button>'
            elif layout == "hydrated":
                markup = '<button onclick="this.nextElementSibling.hidden=false">登录</button><div hidden>'+qr+'</div>'
                body = '<script>setTimeout(()=>document.body.innerHTML='+json.dumps(markup)+',300)</script>'
            else:
                body = '<span style="display:none">登录</span><button onclick="this.nextElementSibling.hidden=false">登录</button><div hidden>'+qr+'</div>'
            await request.fulfill(content_type="text/html; charset=utf-8",body=body)
        await browser.context.route("https://weread.qq.com/**", route)
        try:
            await browser.login()
            link, _ = await qr_link(browser.page)
            assert link == LINK
            if layout == "popup":
                assert browser.page == browser.context.pages[-1]
                assert len(browser.context.pages) == 2
        finally:
            await browser.close()
    asyncio.run(scenario())
