"""Discover official reward entry; fail closed until claim semantics are verified."""
from urllib.parse import urlsplit


async def check_rewards(browser):
    result = {'status': 'entry_unavailable', 'claimed': 0,
              'reason': '当前官方网页版未核实到“我的阅读 → 阅读奖励”入口，未执行领取'}
    try:
        async with browser.lock:
            await browser.page.goto('https://weread.qq.com/web/shelf', wait_until='load')
            for label in ('我的阅读', '阅读奖励'):
                control = browser.page.get_by_text(label, exact=True).filter(visible=True)
                try:
                    await control.first.wait_for(state='visible', timeout=5000)
                except Exception:
                    return result
                if await control.count() != 1:
                    return result
                await control.click(timeout=8000)
                await browser.page.wait_for_load_state('load', timeout=15000)
                if urlsplit(browser.page.url).hostname != 'weread.qq.com':
                    return result
            result.update(status='claim_rules_unverified',
                          reason='已找到阅读奖励入口，但尚未核实免费、达标、无需确认及到账标志；未点击领取')
    except Exception:
        result.update(status='unavailable', reason='官方奖励入口检查失败，未执行领取')
    return result
