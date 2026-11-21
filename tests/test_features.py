import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest

from weread.models import Settings
from weread.official import OfficialStatistics, StatisticsUnavailable, weekly_seconds, week_start
from weread.weekly import catch_up, TARGET, BUDGET
from weread.feature_report import feature_lines

SATURDAY = datetime(2026, 11, 14, 1, 17, tzinfo=timezone.utc)  # Beijing 09:17


def test_official_week_contract_and_units():
    base = week_start(SATURDAY)
    assert datetime.fromtimestamp(base, timezone.utc).isoformat() == '2026-11-08T16:00:00+00:00'
    assert weekly_seconds({'baseTime': base, 'totalReadTime': 18000}, SATURDAY) == 18000
    for body in [None, {}, {'baseTime': base-604800, 'totalReadTime': 18000},
                 {'baseTime': base, 'totalReadTime': True}, {'baseTime': base, 'totalReadTime': '18000'},
                 {'baseTime': base, 'totalReadTime': -1}, {'baseTime': base, 'totalReadTime': 604801},
                 {'baseTime': base, 'totalReadTime': 18000, 'errcode': -1}]:
        with pytest.raises(StatisticsUnavailable):
            weekly_seconds(body, SATURDAY)


def test_upgrade_notice_does_not_invalidate_verified_business_data():
    body = {'baseTime': week_start(SATURDAY), 'totalReadTime': 18000,
            'upgrade_info': {'message': 'private-upgrade-fixture'}}
    assert weekly_seconds(body, SATURDAY) == 18000
    for invalid in ({'errcode': -1}, {'baseTime': 0}, {'totalReadTime': '18000'}):
        with pytest.raises(StatisticsUnavailable):
            weekly_seconds(body | invalid, SATURDAY)


def test_gateway_auth_parameters_and_error_redaction(monkeypatch):
    secret = 'wrk-private-fixture'
    monkeypatch.setenv('WEREAD_API_KEY', secret)
    async def scenario():
        async def handle(request):
            import json
            assert str(request.url) == 'https://i.weread.qq.com/api/agent/gateway'
            assert request.headers['Authorization'] == 'Bearer ' + secret
            payload = json.loads(request.content)
            assert payload == {'api_name': '/readdata/detail', 'mode': 'weekly', 'baseTime': 0, 'skill_version': '1.0.6'}
            return httpx.Response(200, json={'baseTime': week_start(SATURDAY), 'totalReadTime': 123,
                                           'upgrade_info': {'message': secret}})
        statistics = OfficialStatistics(None, transport=httpx.MockTransport(handle))
        assert await statistics.weekly(SATURDAY) == 123
        async def fail(request):
            return httpx.Response(403, text=secret)
        statistics.transport = httpx.MockTransport(fail)
        with pytest.raises(StatisticsUnavailable) as error:
            await statistics.weekly(SATURDAY)
        assert secret not in str(error.value)
    asyncio.run(scenario())


def test_only_reads_existing_key_never_generates(monkeypatch):
    monkeypatch.delenv('WEREAD_API_KEY', raising=False)
    class Request:
        async def get(self, url, **kwargs):
            assert url == 'https://weread.qq.com/api/skills/apikeyGet?only_show=1'
            class Response:
                ok = True
                async def json(self):
                    return {'isEmpty': True}
            return Response()
    async def scenario():
        browser = SimpleNamespace(context=SimpleNamespace(request=Request()))
        with pytest.raises(StatisticsUnavailable, match='api_key_unavailable'):
            await OfficialStatistics(browser).existing_key()
    asyncio.run(scenario())


class Stats:
    def __init__(self, values):
        self.values = iter(values)
        self.last = None
    async def weekly(self):
        value = next(self.values, self.last)
        if isinstance(value, Exception):
            raise value
        self.last = value
        return value


async def weekly_scenario(values, start=SATURDAY, rollover=False, slow=False, sunday_date=None):
    state = {'time': 0, 'date': start, 'reads': []}
    class Page:
        url = 'https://weread.qq.com/web/reader/test'
        async def goto(self, url, **kwargs):
            assert url == self.url
            return SimpleNamespace(status=200)
    class Browser:
        lock = asyncio.Lock()
        page = Page()
        async def read(self, seconds):
            state['reads'].append(seconds)
            state['time'] += BUDGET if slow else seconds
            if rollover:
                state['date'] += timedelta(days=1)
            return {'elapsed_seconds': seconds, 'requests': {'sent': 1, 'accepted': 1, 'rejected': 0}}
    async def sleep(seconds):
        state['time'] += seconds
    result = await catch_up(Browser(), Settings(book_url=Page.url), Stats(values),
                            clock=lambda: state['date'], monotonic=lambda: state['time'], sleep=sleep,
                            sunday_date=sunday_date)
    return result, state['reads']


@pytest.mark.parametrize('before', [TARGET, TARGET+1])
def test_weekly_already_met_does_not_read(before):
    result, reads = asyncio.run(weekly_scenario([before]))
    assert result['status'] == 'confirmed_by_official'
    assert reads == []


def test_weekly_closes_actual_deficit_and_rechecks_official():
    result, reads = asyncio.run(weekly_scenario([TARGET-1000, TARGET-100, TARGET]))
    assert result['status'] == 'confirmed_by_official'
    assert reads == [900, 160]
    assert result['after_seconds'] == TARGET
    assert result['requests']['accepted'] == 2


@pytest.mark.parametrize('values,options,expected', [
    ([StatisticsUnavailable('api_key_unavailable')], {}, 'unavailable'),
    ([0], {}, 'no_official_progress'),
    ([100, 99], {}, 'unavailable'),
    ([0, 100], {'slow': True}, 'budget_exhausted'),
    ([TARGET-1, TARGET], {'rollover': True}, 'day_changed'),
    ([0], {'start': SATURDAY-timedelta(days=1)}, 'not_saturday'),
])
def test_weekly_safety_stops(values, options, expected):
    result, reads = asyncio.run(weekly_scenario(values, **options))
    assert result['status'] == expected
    if expected in ('not_saturday',) or isinstance(values[0], Exception):
        assert reads == []
    if expected == 'no_official_progress':
        assert len(reads) == 3


def test_rewards_probe_does_not_click_unscoped_claim_button(tmp_path):
    from weread.browser import Browser
    from weread.rewards import check_rewards
    async def scenario():
        browser = Browser(tmp_path)
        await browser.open()
        claimed = []
        async def route(request):
            if '/claim' in request.request.url:
                claimed.append(True)
            await request.fulfill(content_type='text/html; charset=utf-8', body='<button onclick="fetch(\'/claim\')">领取</button>')
        await browser.context.route('https://weread.qq.com/**', route)
        try:
            result = await check_rewards(browser)
            assert result['status'] == 'entry_unavailable'
            assert result['claimed'] == 0
            assert not claimed
        finally:
            await browser.close()
    asyncio.run(scenario())


def test_reports_use_only_fixed_status_text():
    result = {'weekly': {'status': 'unavailable', 'reason': 'api_key_unavailable', 'after_seconds': 123},
              'rewards': {'status': 'entry_unavailable', 'reason': 'PRIVATE', 'claimed': 0}}
    text = '\n'.join(feature_lines(result))
    assert 'WEREAD_API_KEY' in text
    assert '0小时2分3秒' in text
    assert '未领取' in text
    assert 'PRIVATE' not in text


def test_weekly_does_not_request_reading_past_beijing_midnight():
    late_saturday = datetime(2026, 11, 14, 15, 59, 30, tzinfo=timezone.utc)
    result, reads = asyncio.run(weekly_scenario([0, 30], start=late_saturday, rollover=True))
    assert reads == [30]
    assert result['status'] == 'day_changed'


def test_manual_makeup_is_limited_to_the_requested_sunday():
    sunday = SATURDAY + timedelta(days=1)
    result, reads = asyncio.run(weekly_scenario([TARGET-60, TARGET], start=sunday, sunday_date=sunday.date()))
    assert result['status'] == 'confirmed_by_official'
    assert reads == [120]
    result, reads = asyncio.run(weekly_scenario([], start=sunday + timedelta(days=7), sunday_date=sunday.date()))
    assert result['status'] == 'manual_date_mismatch'
    assert reads == []
