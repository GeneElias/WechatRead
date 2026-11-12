"""Read-only capability discovery. Never export DOM, URLs, cookies or API keys."""
import asyncio
import json
import os
from pathlib import Path

from .browser import Browser
from .cloud import load_state, restore, session_key, private_write, encrypt_state
from .feishu import Feishu
from .remote_login import ensure_login


async def probe(data_dir=Path('.data/cloud'), reports=Path('reports')):
    result = {'authenticated': False, 'pages': {}, 'api_key_available': False}
    browser = Browser(data_dir)
    try:
        await restore(browser, load_state(data_dir / 'session.enc', session_key()))
        await browser.page.goto('https://weread.qq.com/web/shelf', wait_until='load')
        result['cookie_present'] = await browser.saved_session()
        if os.environ.get('WEREAD_REMOTE_LOGIN') == 'true':
            await ensure_login(browser, Feishu.from_environment())
        result['authenticated'] = await browser.authenticated()
        if result['authenticated']:
            for name, url in [('home', 'https://weread.qq.com/'), ('shelf', 'https://weread.qq.com/web/shelf')]:
                await browser.page.goto(url, wait_until='load')
                await browser.page.wait_for_timeout(1500)
                labels = {}
                for label in ('我的阅读', '阅读奖励', '本周阅读', '阅读时长', '本周', '领取'):
                    items = browser.page.get_by_text(label, exact=True).filter(visible=True)
                    labels[label] = await items.count()
                result['pages'][name] = labels
            response = await browser.context.request.get('https://weread.qq.com/api/skills/apikeyGet?only_show=1')
            result['key_lookup_status'] = response.status
            if response.ok:
                body = await response.json()
                result['key_lookup_fields'] = sorted(body) if isinstance(body, dict) else []
                key = body.get('apikey') if isinstance(body, dict) else None
                result['api_key_available'] = isinstance(key, str) and key.startswith('wrk-')
                if result['api_key_available']:
                    response = await browser.context.request.post('https://i.weread.qq.com/api/agent/gateway', headers={'Authorization': 'Bearer ' + key}, data={'api_name': '/readdata/detail', 'mode': 'weekly', 'baseTime': 0, 'skill_version': '1.0.5'})
                    result['statistics_status'] = response.status
                    body = await response.json()
                    result['statistics_fields'] = sorted(body) if isinstance(body, dict) else []
                    for field in ('baseTime', 'totalReadTime', 'errcode'):
                        if type(body.get(field)) is int:
                            result[field] = body[field]
    except Exception as error:
        result['error_type'] = type(error).__name__
    finally:
        if result['authenticated']:
            private_write(data_dir / 'session.enc', encrypt_state(await browser.context.storage_state(), session_key()))
        await browser.close()
    private_write(reports / 'capabilities.json', json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['authenticated'] else 1


if __name__ == '__main__':
    raise SystemExit(asyncio.run(probe()))
