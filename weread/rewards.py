"""Discover official reward entry; fail closed until claim semantics are verified."""
from urllib.parse import urlsplit


async def check_rewards(browser):
    result = {'status': 'entry_unavailable', 'claimed': 0,
              'reason': '当前官方网页版未核实到“我的阅读 → 阅读奖励”入口，未执行领取'}
    try:
        async with browser.lock:
            for url in ('https://weread.qq.com/web/shelf', 'https://weread.qq.com/'):
                await browser.page.goto(url, wait_until='load')
                # These account-menu classes were observed on official live pages.
                avatar = browser.page.locator('.navBar_avatar, .wr_index_page_top_section_header_action_avatar_wrapper').filter(visible=True)
                if await avatar.count() == 1:
                    await avatar.hover()
                control = browser.page.get_by_text('我的阅读', exact=True).filter(visible=True)
                try:
                    await control.first.wait_for(state='visible', timeout=5000)
                except Exception:
                    continue
                if await control.count() != 1:
                    continue
                await control.click(timeout=8000)
                await browser.page.wait_for_load_state('load', timeout=15000)
                if urlsplit(browser.page.url).hostname != 'weread.qq.com':
                    return result
                reward = browser.page.get_by_text('阅读奖励', exact=True).filter(visible=True)
                try:
                    await reward.first.wait_for(state='visible', timeout=5000)
                except Exception:
                    continue
                if await reward.count() != 1:
                    continue
                await reward.click(timeout=8000)
                await browser.page.wait_for_load_state('load', timeout=15000)
                if urlsplit(browser.page.url).hostname != 'weread.qq.com':
                    return result
                result.update(status='claim_rules_unverified',
                              reason='已找到阅读奖励入口，但尚未核实免费、达标、无需确认及到账标志；未点击领取')
                return result
    except Exception:
        result.update(status='unavailable', reason='官方奖励入口检查失败，未执行领取')
    return result
