"""Hosted official features, independent of daily reading/check-in results."""
from .official import OfficialStatistics, StatisticsUnavailable
from .weekly import catch_up, TARGET


async def check_weekly(browser, settings, inspect_only=False, sunday_date=None):
    if not settings.weekly_enabled:
        return {'weekly': {'status': 'disabled'}} if sunday_date is not None else {}
    if not inspect_only:
        return {'weekly': await catch_up(browser, settings, sunday_date=sunday_date)}
    try:
        seconds = await OfficialStatistics(browser).weekly()
        weekly = {'status': 'observed', 'after_seconds': seconds, 'target_seconds': TARGET}
    except StatisticsUnavailable as error:
        weekly = {'status': 'unavailable', 'reason': error.reason, 'target_seconds': TARGET}
    return {'weekly': weekly}
