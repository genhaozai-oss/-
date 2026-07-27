from datetime import datetime, timedelta, timezone

from smarthome.intent import parse_alarm_time


def test_parse_tomorrow_morning_alarm():
    now = datetime(2026, 7, 27, 20, 0, tzinfo=timezone(timedelta(hours=8)))
    result = parse_alarm_time("明天早上七点设置闹钟", now)
    assert result.isoformat() == "2026-07-28T07:00:00+08:00"


def test_parse_evening_alarm():
    now = datetime(2026, 7, 27, 10, 0, tzinfo=timezone(timedelta(hours=8)))
    result = parse_alarm_time("晚上八点提醒我", now)
    assert result.hour == 20
    assert result.day == 27


def test_parse_half_past_alarm():
    now = datetime(2026, 7, 27, 10, 0, tzinfo=timezone(timedelta(hours=8)))
    result = parse_alarm_time("下午三点半提醒我", now)
    assert result.hour == 15
    assert result.minute == 30
