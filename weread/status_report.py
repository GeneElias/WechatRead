"""Report Actions status to the existing Feishu webhook without account access."""
import asyncio
from datetime import datetime, timedelta, timezone
import io
import json
import os
import re
import zipfile

import httpx

from .feishu import Feishu


BEIJING = timezone(timedelta(hours=8))
LABELS = {
    "success": "程序执行成功", "failure": "失败", "cancelled": "已取消",
    "skipped": "已跳过", "timed_out": "超时", "in_progress": "运行中",
    "queued": "排队中", "waiting": "等待中", "requested": "已请求",
    "pending": "等待中", "action_required": "需要处理", "neutral": "未产生结论",
    "confirmed_by_page": "页面字段已确认", "pending_verification": "待确认",
    "not_configured": "未配置", "executed": "已执行", "failed": "失败",
    "needs_login": "需要重新扫码", "notification_failed": "通知失败",
}


def label(value):
    return LABELS.get(value, "未知")


def timestamp(value):
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(BEIJING).strftime("%Y-%m-%d %H:%M")
    except (ValueError, AttributeError):
        return "未知"


def number(value):
    return value if type(value) is int and value >= 0 else "未知"


class GitHubStatus:
    def __init__(self, client, repository):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
            raise ValueError("Invalid repository")
        self.client = client
        self.base = f"https://api.github.com/repos/{repository}"

    async def get(self, path):
        response = await self.client.get(self.base + path)
        response.raise_for_status()
        return response.json()

    async def result(self, run):
        artifacts = await self.get(f"/actions/runs/{int(run['id'])}/artifacts?per_page=100")
        expected = f"weread-result-{run['id']}-{run.get('run_attempt', 1)}"
        matches = [item for item in artifacts["artifacts"] if item["name"] == expected and not item["expired"]]
        if len(matches) != 1:
            return None
        async with self.client.stream("GET", self.base + f"/actions/artifacts/{int(matches[0]['id'])}/zip") as response:
            response.raise_for_status()
            data = bytearray()
            async for chunk in response.aiter_bytes():
                data.extend(chunk)
                if len(data) > 2_000_000:
                    raise ValueError("Result archive too large")
        # Never extract the archive or publish arbitrary report text/URLs.
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            files = [info for info in archive.infolist() if info.filename == "result.json"]
            if len(files) != 1 or files[0].file_size > 256_000:
                return None
            result = json.loads(archive.read(files[0]))
            if not isinstance(result, dict):
                raise ValueError("Invalid result")
            return result

    async def collect(self):
        runs = []
        workflow = await self.get("/actions/workflows/daily-read.yml")
        for name in ("daily-read.yml", "feishu-login-test.yml"):
            page = await self.get(f"/actions/workflows/{name}/runs?branch=main&per_page=30")
            runs.extend(page["workflow_runs"])
        runs.sort(key=lambda run: (run["created_at"], run["id"]), reverse=True)
        latest = runs[0] if runs else None
        completed = next((run for run in runs if run["status"] == "completed" and run["conclusion"] != "skipped"), None)
        evidence, evidence_error = None, False
        if completed:
            try:
                evidence = await self.result(completed)
            except (httpx.HTTPError, ValueError, KeyError, zipfile.BadZipFile):
                evidence_error = True
        return {"latest": latest, "completed": completed, "evidence": evidence,
                "evidence_error": evidence_error, "workflow_enabled": workflow["state"] == "active"}


def make_card(repository, snapshot, enabled, current=None):
    current = current or datetime.now(BEIJING)
    latest, completed = snapshot["latest"], snapshot["completed"]
    lines = [f"报告时间：{current.astimezone(BEIJING):%Y-%m-%d %H:%M}（北京时间）",
             f"每日阅读开关：{'已启用' if enabled else '未启用'}",
             f"GitHub 阅读工作流：{'已启用' if snapshot['workflow_enabled'] else '已停用'}",
             "计划阅读时间：每天 09:17（GitHub 调度可能延迟）"]
    if latest:
        lines.append(f"最近触发：{timestamp(latest['created_at'])} / {label(latest.get('conclusion') or latest['status'])}")
    else:
        lines.append("最近触发：暂无记录")
    today = current.astimezone(BEIJING).date()
    if not completed or datetime.fromisoformat(completed["created_at"].replace("Z", "+00:00")).astimezone(BEIJING).date() != today:
        lines.append("今日结果：尚无已结束且未跳过的任务；下列历史结果不代表今日已执行。")
    if completed:
        lines.append(f"最近结束的未跳过任务：{timestamp(completed['created_at'])} / {label(completed['conclusion'])}")
    result = snapshot["evidence"]
    if result:
        counts = result.get("requests")
        counts = counts if isinstance(counts, dict) else {}
        lines.extend([f"该次执行：{label(result.get('status'))}，本地运行 {number(result.get('elapsed_seconds'))} 秒",
                      f"该次阅读请求：发送 {number(counts.get('sent'))} / 接受 {number(counts.get('accepted'))} / 拒绝 {number(counts.get('rejected'))}",
                      f"该次阅读核验：{label(result.get('reading'))}",
                      f"该次签到核验：{label(result.get('checkin'))}"])
    elif completed:
        lines.append("该次详细结果：读取失败" if snapshot["evidence_error"] else "该次详细结果：无可用附件（可能未生成或已过期）")
    lines.append("程序成功或请求被接受，不代表官方阅读时长及签到达标。本报告不执行阅读，也不验证当前登录是否有效。")
    base = f"https://github.com/{repository}/actions"
    actions = [{"tag": "button", "text": {"tag": "plain_text", "content": "查看阅读任务"}, "url": base + "/workflows/daily-read.yml"},
               {"tag": "button", "text": {"tag": "plain_text", "content": "手动查询 / 更新报告"}, "url": base + "/workflows/status-report.yml"}]
    if latest:
        actions.append({"tag": "button", "text": {"tag": "plain_text", "content": "最近一次运行"}, "url": base + f"/runs/{int(latest['id'])}"})
    return {"header": {"title": {"tag": "plain_text", "content": "微信读书 · 项目运行日报"},
                       "template": "blue" if enabled and snapshot["workflow_enabled"] else "orange"},
            "elements": [{"tag": "div", "text": {"tag": "plain_text", "content": "\n".join(lines)}},
                         {"tag": "action", "actions": actions}]}


async def main():
    try:
        bot = Feishu.from_environment()
        if not bot:
            raise ValueError("Missing webhook")
        token = os.environ["GH_TOKEN"]
        repository = os.environ["GITHUB_REPOSITORY"]
        async with httpx.AsyncClient(headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
                                     timeout=30, follow_redirects=True) as client:
            try:
                snapshot = await GitHubStatus(client, repository).collect()
            except Exception:
                await bot.send({"header": {"title": {"tag": "plain_text", "content": "微信读书 · 状态查询失败"}, "template": "red"},
                                "elements": [{"tag": "div", "text": {"tag": "plain_text", "content":
                                    "本次无法取得 GitHub 运行状态，请检查 Actions 读取权限和网络。不能据此判断今日已执行；本次没有触发阅读。"}}]})
                print("GitHub 状态查询失败，已向飞书发送故障通知。")
                return 1
        card = make_card(repository, snapshot, os.environ.get("WEREAD_ENABLED") == "true")
        await bot.send(card)
        print("飞书项目运行报告已发送。")
        return 0
    except Exception:
        # Exceptions may contain signed artifact URLs, tokens or webhook URLs.
        print("状态报告失败：请检查 GitHub Actions 读取权限、飞书配置和网络；本次未确认发送成功。")
        return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
