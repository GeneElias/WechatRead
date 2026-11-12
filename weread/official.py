"""Official statistics contract: Tencent/WeChatReading skills/readdata.md.

Only the current natural week's totalReadTime (seconds) is accepted. Credentials
and raw gateway responses must never enter reports, exceptions or logs.
"""
from datetime import datetime, timedelta
import os

import httpx

from .store import TZ

GATEWAY = 'https://i.weread.qq.com/api/agent/gateway'
SKILL_VERSION = '1.0.5'


class StatisticsUnavailable(Exception):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


def week_start(current):
    local = current.astimezone(TZ)
    return int((local - timedelta(days=local.weekday())).replace(hour=0, minute=0, second=0, microsecond=0).timestamp())


def weekly_seconds(body, current):
    if not isinstance(body, dict):
        raise StatisticsUnavailable('invalid_response')
    if 'upgrade_info' in body:
        raise StatisticsUnavailable('api_upgrade_required')
    if any(body.get(field) not in (None, 0) for field in ('errcode', 'errCode')):
        raise StatisticsUnavailable('api_rejected')
    # Reject absent, stale, daily/monthly, string, bool and negative values.
    base, total = body.get('baseTime'), body.get('totalReadTime')
    if type(base) is not int or base != week_start(current):
        raise StatisticsUnavailable('week_not_verified')
    if type(total) is not int or not 0 <= total <= 7 * 86400:
        raise StatisticsUnavailable('duration_not_verified')
    return total


class OfficialStatistics:
    def __init__(self, browser, transport=None):
        self.browser = browser
        self.transport = transport

    async def existing_key(self):
        key = os.environ.get('WEREAD_API_KEY', '').strip()
        if key:
            return key
        # Read an EXISTING key only. Never generate/reset a credential. Endpoint
        # and only_show flag are from the official weread-skills page's JS.
        try:
            response = await self.browser.context.request.get(
                'https://weread.qq.com/api/skills/apikeyGet?only_show=1', timeout=15000)
            if response.ok:
                body = await response.json()
                key = body.get('apikey') if isinstance(body, dict) else None
                if isinstance(key, str) and key.startswith('wrk-'):
                    return key
        except Exception:
            pass
        raise StatisticsUnavailable('api_key_unavailable')

    async def weekly(self, current=None):
        current = current or datetime.now(TZ)
        key = await self.existing_key()
        try:
            async with httpx.AsyncClient(timeout=20, transport=self.transport, follow_redirects=False) as client:
                response = await client.post(GATEWAY, headers={'Authorization': 'Bearer ' + key},
                    json={'api_name': '/readdata/detail', 'mode': 'weekly', 'baseTime': 0, 'skill_version': SKILL_VERSION})
                response.raise_for_status()
                return weekly_seconds(response.json(), current)
        except StatisticsUnavailable:
            raise
        except Exception:
            raise StatisticsUnavailable('statistics_request_failed') from None
