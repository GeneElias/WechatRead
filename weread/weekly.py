"""Saturday Beijing catch-up, verified only by official current-week statistics."""
import asyncio
from datetime import datetime
import time

from .official import OfficialStatistics, StatisticsUnavailable
from .store import TZ

TARGET = 5 * 3600
BUDGET = 320 * 60
CHUNK = 15 * 60


async def catch_up(browser, settings, statistics=None, clock=None, monotonic=None, sleep=asyncio.sleep):
    clock = clock or (lambda: datetime.now(TZ))
    monotonic = monotonic or time.monotonic
    started = clock().astimezone(TZ)
    result = {'status': 'not_saturday', 'target_seconds': TARGET, 'before_seconds': None,
              'after_seconds': None, 'elapsed_seconds': 0,
              'requests': {'sent': 0, 'accepted': 0, 'rejected': 0}}
    if started.weekday() != 5:
        return result
    statistics = statistics or OfficialStatistics(browser)
    began = monotonic()
    result['status'] = 'unavailable'

    def same_day():
        return clock().astimezone(TZ).date() == started.date()

    try:
        async with browser.lock:
            before = await statistics.weekly()
            result.update(before_seconds=before, after_seconds=before)
            stalled = 0
            while True:
                if not same_day():
                    result['status'] = 'day_changed'
                    break
                if result['after_seconds'] >= TARGET:
                    result['status'] = 'confirmed_by_official'
                    break
                remaining_budget = BUDGET - (monotonic() - began)
                if remaining_budget <= 0:
                    result['status'] = 'budget_exhausted'
                    break
                response = await browser.page.goto(settings.book_url, wait_until='domcontentloaded')
                if (response and response.status >= 400) or '/web/reader/' not in browser.page.url:
                    raise StatisticsUnavailable('reader_unavailable')
                if not same_day():
                    result['status'] = 'day_changed'
                    break
                seconds = min(CHUNK, TARGET - result['after_seconds'] + 60, remaining_budget)
                # Bound a stalled reader as well as the nominal requested time.
                async with asyncio.timeout(max(1, remaining_budget)):
                    reading = await browser.read(seconds)
                result['elapsed_seconds'] += reading['elapsed_seconds']
                for name in result['requests']:
                    result['requests'][name] += reading.get('requests', {}).get(name, 0)
                previous = result['after_seconds']
                for attempt in range(3):
                    after = await statistics.weekly()
                    if after < previous:
                        raise StatisticsUnavailable('statistics_regressed')
                    result['after_seconds'] = after
                    if after >= TARGET or after > previous:
                        break
                    if attempt < 2:
                        await sleep(20)
                stalled = stalled + 1 if result['after_seconds'] == previous else 0
                if stalled >= 3:
                    result['status'] = 'no_official_progress'
                    break
    except StatisticsUnavailable as error:
        result.update(status='unavailable', reason=error.reason)
    except Exception:
        # Exception text may contain credential-bearing requests or page data.
        result.update(status='unavailable', reason='weekly_execution_failed')
    return result
