# 微读 · 每日阅读助手

单账号微信读书网页版自动化 MVP：官方扫码登录、会话保留、每日定时阅读、手动运行与停止、SQLite 历史记录、可配置的官方页面字段核验和签到适配。

**推荐部署：GitHub Actions。** 每天北京时间 09:17 运行，也可手动触发；本地管理页面是可选调试工具，Actions 运行不需要管理口令。

**当前没有真实账号的计时或签到成功验证。** 默认只执行网页阅读，结果标记为“待确认”；运行五分钟、请求被接受和官方计入五分钟是不同结果。签到入口因活动而异，须先核实活动支持网页版，才能填写适配配置。

## GitHub Actions 部署

### 1. 准备本机登录（只在首次或失效时需要）

在你自己的、有桌面浏览器的电脑上检出本仓库，安装 Python 3.12+，执行：

```bash
python -m venv .venv
# Windows 将后续 .venv/bin/python 替换成 .venv\Scripts\python.exe
.venv/bin/python -m pip install -r requirements.lock.txt
.venv/bin/python -m playwright install chromium
.venv/bin/python -m weread.cloud login
```

在打开的官方浏览器窗口中扫码，确认进入个人书架后返回终端按回车。程序会在 `.data/cloud/` 中生成 `session-key` 和 `initial-state`；两者共同用于恢复账号，均须保密，不能提交 Git 或粘贴到聊天。若此前已在本地管理页面登录，先停止管理服务，再用 `python -m weread.cloud export` 导出即可。

### 2. 将两项 Secrets 和书籍变量设置到仓库

使用你电脑上已登录 GitHub 的 `gh` CLI，直接从文件导入，避免将内容打印到终端：

```bash
gh secret set WEREAD_SESSION_KEY -R GeneElias/WechatRead < .data/cloud/session-key
gh secret set WEREAD_INITIAL_STATE -R GeneElias/WechatRead < .data/cloud/initial-state
gh variable set WEREAD_BOOK_URL -R GeneElias/WechatRead --body 'https://weread.qq.com/web/reader/你的书籍链接'
```

也可在 GitHub 仓库 Settings → Secrets and variables → Actions 中设置同名 Secrets 和 Variables。书籍必须是该账号可以阅读的官方阅读器链接。`WEREAD_SESSION_KEY` 用于认证加密，`WEREAD_INITIAL_STATE` 是已加密的初始登录态，不是明文 Cookie。

### 3. 先手动运行，再打开每日调度

在仓库 Actions 中选择“每日微信读书”→ Run workflow，使用 `main` 分支。查看运行摘要与 `weread-result-…` 附件里的结果，并在官方客户端核对。运行后会加密保存刷新后的登录态，下次优先从 Actions 缓存恢复；缓存失效时回退到初始 Secret。登录过期时需重新扫码并重新导入**两项 Secrets**；每次导出都会更换加密密钥，使旧缓存失效。缓存可能被平台淘汰，不能承诺永不掉线。

确认配置和账号行为正确后，再启用定时：

```bash
gh variable set WEREAD_ENABLED -R GeneElias/WechatRead --body true
```

未设置 `WEREAD_ENABLED=true` 时，定时任务跳过，手动触发仍可用。默认每天北京时间 **09:17（UTC 01:17）**，修改 `.github/workflows/daily-read.yml` 的 Cron 可调整。`config/actions.json` 设置目标时长、统计字段和签到适配；仓库变量 `WEREAD_BOOK_URL` 覆盖文件中的书籍链接。GitHub 工作流不会使用本地管理页面保存的计划，也不会托管常驻网页，GitHub Pages 无法运行本程序。

**结果解释：** Actions 绿色只表示程序完成执行。是否计时、是否签到，应分别看摘要的“阅读核验”和“签到核验”；没有统计适配时是“待确认”，不是官方达标。工作流执行失败则显示红色。认证、配置或浏览器失败不会被当作成功。当前没有默认猜测的签到入口。

GitHub 调度可能延迟，不提供严格准点或补跑保证；定时工作流仅在默认分支生效，公开仓库长期无活动时还可能被平台停用。同一仓库的账号任务串行运行，不开启 PR 触发的账号任务。结果附件保留 14 天，不含浏览器 profile、Cookie 或加密密钥。建议使用私人仓库管理个人账号任务；公开仓库中不要提交包含个人信息的配置或运行结果。

手动运行与每日运行均为独立任务，多次手动触发可能重复阅读。仅 GitHub Actions 的并发控制负责串行化，不提供跨工作流、跨仓库的账号锁。若需停用，设置 `WEREAD_ENABLED=false` 或在 Actions 中禁用工作流。

## 本地启动

需要 Python 3.12+。使用现有 checkout；不需要建立 Git worktree。

```bash
cd /workspace/WechatRead
python -m venv .venv
.venv/bin/pip install -r requirements.lock.txt
# 优先使用已安装的 chromium；没有时安装 Playwright 配套版本：
PLAYWRIGHT_BROWSERS_PATH="$PWD/.data/browsers" .venv/bin/python -m playwright install chromium
.venv/bin/python -m weread
```

也可执行 `bash scripts/setup.sh` 安装锁定依赖并测试，然后执行 `bash scripts/start.sh` 启动。`requirements.txt` 和 `requirements-dev.txt` 用于维护直接依赖，`requirements.lock.txt` 固定本次验证的完整依赖版本。

若系统已有 `chromium`，可跳过浏览器下载。也可用 `WEREAD_CHROMIUM` 指定兼容的可执行文件。Linux 机器须安装 Chromium 所需系统库；Playwright 提供 `playwright install-deps chromium`，需系统安装权限。

默认仅监听本机 8000 端口。首次启动在 `.data/admin-token` 生成管理口令，在本机读取该文件并输入管理页面；不要把它提交到仓库或发送到聊天。远程访问使用受保护的端口转发，或有 HTTPS 的反向代理；HTTPS 部署设置 `WEREAD_SECURE_COOKIE=1`。不建议直接暴露管理端口。单实例、单 Worker 运行，不要加多个 Uvicorn Worker。

可配置环境变量：`WEREAD_DATA_DIR`、`WEREAD_HOST`、`WEREAD_PORT`、`WEREAD_CHROMIUM`、`PLAYWRIGHT_BROWSERS_PATH`、`WEREAD_SECURE_COOKIE`。浏览器使用环境中已有的 HTTPS 代理（若有），保留 TLS 校验。

## 使用步骤

1. 进入管理页面，输入本地管理口令。
2. 点击“打开登录页”，扫描官方页面截图中的二维码，在微信完成确认；刷新截图检查状态。验证码和额外验证需要本人处理。当前不提供远程浏览器任意点击操作。
3. 在官方网页版打开一本有权限阅读的书，把 `/web/reader/` 链接填入计划并保存。
4. 手动运行一次，在官方客户端核对当日统计和目标活动结果。
5. 核验成功后开启每日定时。默认北京时间 09:00、当日目标 5 分钟、运行上限 8 分钟；任务加入最多一分钟余量。没有配置统计读取时，不会宣称官方达标。

任务每天运行依赖本服务和机器持续在线。当天错过时间时，服务恢复后补执行一次；不补跑以前日期。每个自然日最多创建一条定时任务，失败后须人工处理并手动重试；手动任务可用于重试。活动“超过五分钟”的具体门槛须以当前规则为准。

## 官方结果配置

高级配置的“统计页面”和“CSS 选择器”必须一起填写。选择器须唯一匹配**当日阅读时长数值**，支持 `5分钟30秒`、`1小时2分钟`、`05:30`、`01:02:03`。无法确定字段含义时留空。每日累计模式检查末值是否达到目标；新增模式比较任务前后差值。期间不要用其他设备阅读，否则不能将差值归因于本任务。跨日时一律标记待确认。

签到适配需填写官方活动 URL、唯一的按钮完整文字和唯一的成功状态完整文字。系统在官方页面点击按钮后观察成功文字；这只证明配置的页面标志出现，不证明奖励到账。不要将“领取”“购买”或付费活动报名配置成签到按钮。若该活动没有网页版入口，保持未配置。

阅读采用正常的 PageDown 操作，不复刻私有签名、不自行上报阅读时长。页面适配、书籍末尾和付费章节可能导致阅读停滞；本地计时并不证明内容推进，需结合官方结果判断。统计同步最多查询三次，仍未知时保留待确认。任务中断或服务重启不自动宣称成功。

## 测试

```bash
.venv/bin/python -m pytest -q
```

覆盖鉴权、配置校验、任务结果、调度去重、取消、重启恢复，以及真实 Chromium 对拦截测试页面的翻页、统计读取和签到状态变化。浏览器测试使用虚构会话和本地路由，不登录或修改真实账号。真实网站的可用性、最新活动规则和账号计时仍需单独验收。

## 数据与运行边界

`.data/` 包含浏览器会话、管理口令和数据库，已被 Git 忽略。请保护整个目录。浏览器状态可能包含登录凭证；不上传公共存储。退出管理页不退出微信读书，清除本地登录态会移除浏览器 profile；撤销远程设备请在官方客户端操作。

当前版本没有通知渠道、自动提取官方统计选择器或长期运行保证。健康检查 `/health` 只表示管理服务可用，不表示官网可访问。当前 Codex 环境对官网的访问尚未放通，不能作为已经验证的每日托管服务。
