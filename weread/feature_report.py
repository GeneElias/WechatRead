"""Fixed, credential-free wording shared by Actions and Feishu reports."""
WEEKLY = {
    'not_saturday': '非北京时间周六，未执行补时',
    'observed': '只读取得官方本周统计，未执行补时',
    'confirmed_by_official': '官方本周累计已达 5 小时',
    'unavailable': '无法取得或核实官方本周统计，未确认达标',
    'budget_exhausted': '已达运行上限，官方累计仍不足 5 小时',
    'no_official_progress': '连续未见官方计时增长，已停止补时',
    'day_changed': '已跨北京时间自然日，停止补时，未确认当天达标',
}
REASONS = {
    'api_key_unavailable': '未配置官方 API Key；请在官方 Skills 页面获取并保存为 GitHub Secret WEREAD_API_KEY',
    'api_upgrade_required': '官方 API 要求升级，需更新适配',
    'week_not_verified': '官方返回的周起点未通过校验',
    'duration_not_verified': '官方累计秒数字段未通过校验',
    'statistics_regressed': '官方累计值回退，已停止补时',
    'api_rejected': '官方 API 拒绝请求，请检查密钥有效性',
    'statistics_request_failed': '官方统计请求失败',
    'reader_unavailable': '官方阅读器不可用',
    'invalid_response': '官方统计响应格式无法核实',
    'weekly_execution_failed': '补时执行失败，请检查会话、网络和页面状态',
}
REWARDS = {
    'entry_unavailable': '网页版未核实到“我的阅读 → 阅读奖励”入口，未领取',
    'claim_rules_unverified': '入口存在，但免费、达标、无需确认和到账标志未核实，未领取',
    'unavailable': '官方奖励入口检查失败，未领取',
}


def feature_lines(result):
    lines = []
    if result.get("mode") == "feature_inspection":
        lines.append("本次为只读能力核实，未执行阅读或领取，不代表今日已完成阅读")
    weekly = result.get('weekly')
    if isinstance(weekly, dict):
        lines.append('周六补时：' + WEEKLY.get(weekly.get('status'), '未确认'))
        for field, label in [('before_seconds', '补时前'), ('after_seconds', '最新')]:
            value = weekly.get(field)
            if type(value) is int and value >= 0:
                lines.append(f'官方本周累计（{label}）：{value // 3600}小时{value % 3600 // 60}分{value % 60}秒')
        if weekly.get('reason') in REASONS:
            lines.append('周统计说明：' + REASONS[weekly['reason']])
    rewards = result.get('rewards')
    if isinstance(rewards, dict):
        lines.append('阅读奖励：' + REWARDS.get(rewards.get('status'), '未确认'))
    return lines
