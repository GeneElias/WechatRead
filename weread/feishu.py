"""Feishu custom-bot notifications. Webhook URLs and QR tokens stay out of logs."""
import base64
import hashlib
import hmac
import os
import time
from urllib.parse import urlsplit

import httpx


class NotificationError(Exception):
    def __init__(self, message, code=None):
        super().__init__(message)
        self.code = code if isinstance(code, int) else None


def official_login_url(value):
    p = urlsplit(value)
    host = p.hostname or ""
    return (p.scheme == "https" and not p.username and not p.password and p.port in (None, 443)
            and (host == "weread.qq.com" or host == "weixin.qq.com" or host.endswith(".weixin.qq.com")
                 or host == "wechat.com" or host.endswith(".wechat.com")))


class Feishu:
    def __init__(self, webhook, signing_secret="", transport=None):
        p = urlsplit(webhook)
        if (p.scheme != "https" or p.hostname != "open.feishu.cn" or p.username or p.password
                or p.port not in (None, 443) or not p.path.startswith("/open-apis/bot/v2/hook/")
                or not p.path.removeprefix("/open-apis/bot/v2/hook/") or p.query or p.fragment):
            raise ValueError("飞书机器人地址无效")
        self.webhook, self.signing_secret, self.transport = webhook, signing_secret, transport

    @classmethod
    def from_environment(cls):
        url = os.environ.get("FEISHU_WEBHOOK_URL", "").strip()
        return cls(url, os.environ.get("FEISHU_SIGN_SECRET", "")) if url else None

    async def send(self, card):
        payload = {"msg_type": "interactive", "card": card}
        if self.signing_secret:
            timestamp = str(int(time.time()))
            key = f"{timestamp}\n{self.signing_secret}".encode()
            payload.update(timestamp=timestamp, sign=base64.b64encode(hmac.new(key, b"", hashlib.sha256).digest()).decode())
        try:
            async with httpx.AsyncClient(timeout=20, transport=self.transport, follow_redirects=False) as client:
                response = await client.post(self.webhook, json=payload)
                response.raise_for_status()
                body = response.json()
                if body.get("code", body.get("StatusCode")) != 0:
                    raise NotificationError("飞书拒绝了通知，请检查签名、关键词与机器人配置", body.get("code", body.get("StatusCode")))
        except NotificationError:
            raise
        except Exception:
            raise NotificationError("飞书通知请求失败，请检查网络和机器人配置") from None

    async def login(self, login_url, image_url=None):
        if not official_login_url(login_url) or (image_url and not official_login_url(image_url)):
            raise NotificationError("二维码不是受支持的官方登录地址")
        elements = [{"tag":"div", "text":{"tag":"plain_text", "content":"微信读书：需要你完成一次官方扫码或登录确认。链接短时有效，程序正在等待，完成后会自动继续。请勿转发。"}}]
        if image_url:
            elements.append({"tag":"action", "actions":[{"tag":"button", "text":{"tag":"plain_text","content":"打开官方二维码"}, "url":image_url, "type":"primary"}]})
        elements.append({"tag":"action", "actions":[{"tag":"button", "text":{"tag":"plain_text","content":"打开二维码中的官方登录链接"}, "url":login_url}]})
        elements.append({"tag":"div", "text":{"tag":"plain_text", "content":"若链接未完成确认，请在微信中打开。只有官方页面确认登录后，程序才会继续；未发布二维码到 GitHub 或第三方图床。"}})
        await self.send({"header":{"title":{"tag":"plain_text","content":"微信读书 · 扫码登录"},"template":"blue"}, "elements":elements})

    async def result(self, result):
        names = {"confirmed_by_page":"页面字段已确认", "pending_verification":"待确认", "not_configured":"未配置"}
        text = (f"执行：{result['status']}\n阅读：{names.get(result['reading'], '待确认')}\n"
                f"签到：{names.get(result['checkin'], '待确认')}\n本地运行：{result.get('elapsed_seconds', 0)} 秒\n"
                f"阶段：{result.get('phase', 'unknown')}\n说明：{result.get('message', '无')}\n"
                "程序完成不代表官方计时或签到达标，请以核验状态为准。")
        await self.send({"header":{"title":{"tag":"plain_text","content":"微信读书 · 执行结果"}}, "elements":[{"tag":"div","text":{"tag":"plain_text","content":text}}]})
