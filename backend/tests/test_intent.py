from datetime import datetime, timedelta, timezone

from smarthome.intent import parse_alarm_label, parse_alarm_time


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


def test_parse_relative_alarm_time():
    now = datetime(2026, 7, 27, 10, 0, tzinfo=timezone(timedelta(hours=8)))
    result = parse_alarm_time("十分钟后提醒我喝水", now)
    assert result.isoformat() == "2026-07-27T10:10:00+08:00"


def test_parse_alarm_label_from_reminder_message():
    assert parse_alarm_label("明天晚上九点提醒我交论文") == "交论文"


def test_parse_half_minute_alarm_as_thirty_seconds():
    now = datetime(2026, 7, 27, 10, 0, tzinfo=timezone(timedelta(hours=8)))
    result = parse_alarm_time("半分钟后提醒我喝水", now)
    assert result.isoformat() == "2026-07-27T10:00:30+08:00"


def test_parse_alarm_label_from_named_alarm():
    assert parse_alarm_label("明天晚上九点设置交论文闹钟") == "交论文"
