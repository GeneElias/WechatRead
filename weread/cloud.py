"""One-shot GitHub Actions runner. Never writes plaintext account state to disk."""
import argparse
import asyncio
import base64
import hashlib
import json
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from .browser import Browser, LoginRequired
from .models import Settings
from .store import now
from .feishu import Feishu, NotificationError
from .remote_login import ensure_login


def private_write(path: Path, data: str):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(data)
    temporary.replace(path)


def encrypt_state(state, key):
    return Fernet(key).encrypt(json.dumps(state).encode()).decode()


def decrypt_state(token, key):
    state = json.loads(Fernet(key).decrypt(token.encode()))
    if not isinstance(state, dict) or not isinstance(state.get("cookies"), list) or not isinstance(state.get("origins", []), list):
        raise ValueError("Invalid session structure")
    return state


async def restore(browser, state):
    await browser.open()
    await browser.context.add_cookies(state["cookies"])
    # Install storage before the first page script runs. State stays in memory;
    # Chromium's temporary profile is removed with the GitHub runner.
    await browser.context.add_init_script("""(() => {
      const state = %s;
      for (const entry of state.origins || []) {
        if (entry.origin === location.origin) {
          for (const item of entry.localStorage || []) {
            if (localStorage.getItem(item.name) === null) localStorage.setItem(item.name,item.value);
          }
        }
      }
    })();""" % json.dumps(state).replace("<", "\\u003c"))


def load_settings(path: Path, allow_discovery=False):
    data = json.loads(path.read_text())
    book = os.environ.get("WEREAD_BOOK_URL", "").strip()
    if book:
        data["book_url"] = book
    settings = Settings.model_validate(data)
    if (not settings.book_url and not allow_discovery) or (settings.book_url and "/web/reader/" not in settings.book_url):
        raise ValueError("请设置仓库变量 WEREAD_BOOK_URL 为官方阅读器链接")
    return settings


def session_key():
    explicit = os.environ.get("WEREAD_SESSION_KEY", "").strip()
    if explicit:
        return explicit
    webhook = os.environ.get("FEISHU_WEBHOOK_URL", "").strip()
    # The webhook contains a high-entropy bearer token. Domain separation keeps
    # the derived encryption key distinct from the webhook credential itself.
    return base64.urlsafe_b64encode(hashlib.sha256(b"weread/session/v2\0" + webhook.encode()).digest()).decode() if webhook else ""


def load_state(cache: Path, key):
    if cache.exists():
        try:
            return decrypt_state(cache.read_text(), key)
        except (InvalidToken, ValueError, TypeError):
            print("缓存登录态无法解密，尝试初始登录态。")
    initial = os.environ.get("WEREAD_INITIAL_STATE", "").strip()
    if not initial:
        raise ValueError("缺少 WEREAD_INITIAL_STATE，请在本机登录后配置 GitHub Secrets")
    return decrypt_state(initial, key)


async def run(settings_path: Path, data_dir: Path, report_dir: Path, browser_factory=Browser):
    result = {"started": now(), "status": "failed", "phase": "configuration", "reading": "pending_verification", "checkin": "not_configured"}
    browser = None
    notifier = None
    remote = os.environ.get("WEREAD_REMOTE_LOGIN") == "true"
    key = session_key()
    cache = data_dir / "session.enc"
    exit_code = 1
    try:
        notifier = Feishu.from_environment()
        if not key:
            raise ValueError("缺少 GitHub Secret WEREAD_SESSION_KEY")
        Fernet(key)
        settings = load_settings(settings_path, allow_discovery=remote)
        try:
            state = load_state(cache, key)
        except (ValueError, InvalidToken):
            if not remote:
                raise
            state = {"cookies": [], "origins": []}
        browser = browser_factory(data_dir)
        result["phase"] = "browser_start"
        print("启动浏览器并恢复会话", flush=True)
        await restore(browser, state)
        if remote:
            result["phase"] = "remote_login"
            print("检查登录状态，必要时通过飞书请求扫码", flush=True)
            await ensure_login(browser, notifier)
            print("登录已验证", flush=True)
            if not settings.book_url:
                result["phase"] = "book_discovery"
                settings = settings.model_copy(update={"book_url": await browser.discover_book()})
        result["phase"] = "reading"
        print("开始阅读与官方结果核验", flush=True)
        result.update(await browser.run(settings))
        result["status"] = "executed"
        result["phase"] = "completed"
        exit_code = 0
    except LoginRequired:
        result.update(status="needs_login", message="请通过飞书中的官方链接扫码或完成登录确认；超时后重新触发任务")
    except NotificationError:
        result.update(status="notification_failed", message="飞书通知失败，请检查机器人签名、关键词和网络")
    except (ValueError, InvalidToken, TypeError):
        result["message"] = "配置或加密登录态无效；请检查书籍变量、配置文件及两项 Secrets，必要时重新导出"
    except Exception:
        result["message"] = "网页执行失败，请检查网络、登录状态、书籍权限及页面适配"
    finally:
        if browser:
            try:
                # Persist only a session that still has the account cookie.
                if await browser.saved_session():
                    state = await browser.context.storage_state()
                    private_write(cache, encrypt_state(state, key))
            except Exception:
                result["session_refresh"] = "failed"
                result["message"] = result.get("message", "") + "；本次登录态保存失败"
                exit_code = 1
            try:
                await browser.close()
            except Exception:
                pass
        result["finished"] = now()
        if notifier:
            try:
                await notifier.result(result)
                result["notification"] = "sent"
            except NotificationError:
                result["notification"] = "failed"
                exit_code = 1
        private_write(report_dir / "result.json", json.dumps(result, ensure_ascii=False, indent=2))
        # Public diagnostics contain statuses only; no URLs, QR payloads,
        # credential values, book identifiers or account information.
        diagnostic = {name: result.get(name) for name in ("status", "phase", "reading", "checkin", "notification", "elapsed_seconds")}
        print("::notice title=微信读书执行状态::" + json.dumps(diagnostic, ensure_ascii=False), flush=True)
        labels = {"confirmed_by_page":"页面字段已确认", "pending_verification":"待确认", "not_configured":"未配置"}
        summary = "\n".join([
            "## 微信读书每日任务", "",
            f"- 执行状态：{result['status']}",
            f"- 执行阶段：{result['phase']}",
            f"- 说明：{result.get('message', '无')}",
            f"- 结果通知：{result.get('notification', 'not_configured')}",
            f"- 阅读核验：{labels.get(result['reading'], result['reading'])}",
            f"- 签到核验：{labels.get(result['checkin'], result['checkin'])}",
            f"- 本地运行秒数：{result.get('elapsed_seconds', 0)}",
            "", "工作流执行成功仅表示程序完成；官方计时与签到请看上述核验结果。", "",
        ])
        private_write(report_dir / "summary.md", summary)
        if os.environ.get("GITHUB_STEP_SUMMARY"):
            with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
                f.write(summary)
        print(summary)
    return exit_code


async def export_session(data_dir: Path, login: bool):
    browser = Browser(data_dir, headless=not login)
    try:
        if login:
            await browser.login()
            await asyncio.to_thread(input, "请在打开的官方浏览器中扫码登录；看到个人书架后回到这里按回车：")
        else:
            await browser.open()
        if not await browser.saved_session():
            print("未发现登录态，请先运行 login，或用本地管理页面扫码登录后停止管理服务再运行 export。")
            return 1
        key_path = data_dir / "cloud" / "session-key"
        # Rotate on every export so restored caches from an old login cannot
        # override newly supplied initial state. Update BOTH GitHub Secrets.
        key = Fernet.generate_key().decode()
        encrypted = encrypt_state(await browser.context.storage_state(), key)
        if len(encrypted.encode()) > 47000:
            print("登录态超过 GitHub Secret 容量，请用独立干净的浏览器 profile 重新登录。")
            return 1
        private_write(key_path, key)
        private_write(data_dir / "cloud" / "initial-state", encrypted)
        print("已保存 .data/cloud/session-key 和 .data/cloud/initial-state（加密）。请用 gh secret set 从文件导入；不要发送文件内容到聊天。")
        return 0
    finally:
        await browser.close()


def main():
    parser = argparse.ArgumentParser(description="微信读书 GitHub Actions 单次执行工具")
    parser.add_argument("command", choices=["login", "export", "run"])
    parser.add_argument("--data-dir", type=Path, default=Path(".data"))
    parser.add_argument("--config", type=Path, default=Path("config/actions.json"))
    parser.add_argument("--reports", type=Path, default=Path("reports"))
    args = parser.parse_args()
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(Path(".data/browsers").resolve()))
    try:
        code = asyncio.run(run(args.config, args.data_dir / "cloud", args.reports)) if args.command == "run" else asyncio.run(export_session(args.data_dir, args.command == "login"))
    except Exception:
        print("登录或导出失败，请检查浏览器安装、官网连接及本地服务是否占用了同一个 profile。")
        code = 1
    raise SystemExit(code)


if __name__ == "__main__":
    main()
