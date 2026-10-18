import re
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator, model_validator


class Settings(BaseModel):
    book_url: str = ""
    daily_time: str = "09:00"
    scheduled: bool = False
    target_minutes: int = Field(default=5, ge=1, le=60)
    max_minutes: int = Field(default=8, ge=1, le=90)
    goal: str = "daily"
    statistics_url: str = ""
    statistics_selector: str = ""
    checkin_url: str = ""
    checkin_button: str = ""
    checkin_success: str = ""

    @field_validator("book_url", "statistics_url", "checkin_url")
    @classmethod
    def official_url(cls, value):
        if not value:
            return value
        p = urlsplit(value)
        if p.scheme != "https" or p.hostname != "weread.qq.com" or p.username or p.password or p.port not in (None, 443):
            raise ValueError("只允许 https://weread.qq.com/ 下的官方页面")
        return value

    @field_validator("daily_time")
    @classmethod
    def clock_time(cls, value):
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
            raise ValueError("时间格式须为 HH:MM")
        return value

    @field_validator("goal")
    @classmethod
    def valid_goal(cls, value):
        if value not in ("daily", "additional"):
            raise ValueError("目标须为 daily 或 additional")
        return value

    @model_validator(mode="after")
    def coherent(self):
        if self.max_minutes < self.target_minutes:
            raise ValueError("运行上限不能小于目标时长")
        if self.scheduled and not self.book_url:
            raise ValueError("开启定时前请设置书籍链接")
        if bool(self.statistics_url) != bool(self.statistics_selector):
            raise ValueError("统计页面与统计选择器须一起填写")
        if any((self.checkin_url, self.checkin_button, self.checkin_success)) and not all((self.checkin_url, self.checkin_button, self.checkin_success)):
            raise ValueError("签到页面、按钮文字和成功文字须全部填写")
        return self


def parse_duration(text: str) -> int | None:
    """Parse one daily duration field. Ambiguous numbers are not accepted."""
    text = text.strip()
    if re.fullmatch(r"\d+:\d{2}(?::\d{2})?", text):
        parts = [int(p) for p in text.split(":")]
        if any(p > 59 for p in parts[1:]):
            return None
        return parts[0] * 60 + parts[1] if len(parts) == 2 else parts[0] * 3600 + parts[1] * 60 + parts[2]
    match = re.fullmatch(r"(?:(\d+)\s*(?:小时|时)\s*)?(?:(\d+)\s*(?:分钟|分)\s*)?(?:(\d+)\s*秒)?", text)
    if match and any(v is not None for v in match.groups()):
        h, m, s = (int(v or 0) for v in match.groups())
        return h * 3600 + m * 60 + s
    return None
