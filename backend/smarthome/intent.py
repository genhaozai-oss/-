import re
import sqlite3
from datetime import datetime, timedelta

from . import database
from .home import run_home_arrival


CONTROL_PATTERN = re.compile(r"^(打开|开启|启动|关掉|关闭|停止)(.+)$")
RENAME_SELECTED_PATTERN = re.compile(
    r"^(?:把)?(?:这个|它)(?:设备|灯|风扇)?(?:叫做|命名为|是)(.+)$"
)
RENAME_NAMED_PATTERN = re.compile(r"^把(.+?)(?:叫做|命名为|改名为)(.+)$")
ALARM_TIME_PATTERN = re.compile(
    r"(?:(今天|明天)\s*)?"
    r"(早上|上午|中午|下午|晚上)?\s*"
    r"([零一二两三四五六七八九十\d]{1,3})\s*(?:点|时|:|：)\s*"
    r"(?:(半|[零一二两三四五六七八九十\d]{1,3})\s*分?)?"
)
CHINESE_DIGITS = {
    "零": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}


def clean_name(name):
    return name.strip(" ，。！？,.!?").replace("一下", "")


def parse_number(text):
    if text.isdigit():
        return int(text)
    if text == "半":
        return 30
    if "十" not in text:
        return CHINESE_DIGITS.get(text)

    tens_text, ones_text = text.split("十", maxsplit=1)
    tens = CHINESE_DIGITS.get(tens_text, 1) if tens_text else 1
    ones = CHINESE_DIGITS.get(ones_text, 0) if ones_text else 0
    return tens * 10 + ones


def parse_alarm_time(message, now=None):
    now = now or datetime.now().astimezone()
    match = ALARM_TIME_PATTERN.search(message)
    if not match:
        return None

    day_word, period, hour_text, minute_text = match.groups()
    hour = parse_number(hour_text)
    minute = parse_number(minute_text) if minute_text else 0
    if hour is None or minute is None:
        return None
    if hour > 23 or minute > 59:
        return None

    if period in {"下午", "晚上"} and hour < 12:
        hour += 12
    elif period == "中午" and hour < 11:
        hour += 12
    elif period in {"早上", "上午"} and hour == 12:
        hour = 0

    scheduled = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if day_word == "明天":
        scheduled += timedelta(days=1)
    elif day_word != "今天" and scheduled <= now:
        scheduled += timedelta(days=1)
    return scheduled


def rename_device(message, selected_device_id=None):
    selected_match = RENAME_SELECTED_PATTERN.match(message)
    if selected_match:
        if not selected_device_id:
            return {
                "intent": "rename_device",
                "reply": "请先在设备列表中选中要命名的设备，我才能知道“这个”指的是谁。",
                "actions": [],
            }
        new_name = clean_name(selected_match.group(1))
        try:
            device = database.update_device(selected_device_id, name=new_name)
        except sqlite3.IntegrityError:
            return {
                "intent": "rename_device",
                "reply": f"已经有设备叫“{new_name}”了，请换一个名称。",
                "actions": [],
            }
        if not device:
            return {
                "intent": "rename_device",
                "reply": "没有找到刚才选中的设备。",
                "actions": [],
            }
        database.log_event("device", f"设备已命名为{new_name}", device)
        return {
            "intent": "rename_device",
            "reply": f"记住了，以后我会把它叫做“{new_name}”。",
            "actions": [{"device_id": device["id"], "name": new_name}],
        }

    named_match = RENAME_NAMED_PATTERN.match(message)
    if not named_match:
        return None

    old_name = clean_name(named_match.group(1))
    new_name = clean_name(named_match.group(2))
    matches = database.find_devices(old_name)
    if len(matches) != 1:
        return {
            "intent": "rename_device",
            "reply": f"没有唯一找到“{old_name}”，请先在设备列表中选中它。",
            "actions": [],
        }
    try:
        device = database.update_device(matches[0]["id"], name=new_name)
    except sqlite3.IntegrityError:
        return {
            "intent": "rename_device",
            "reply": f"已经有设备叫“{new_name}”了，请换一个名称。",
            "actions": [],
        }
    database.log_event("device", f"{old_name}已命名为{new_name}", device)
    return {
        "intent": "rename_device",
        "reply": f"记住了，“{old_name}”现在叫“{new_name}”。",
        "actions": [{"device_id": device["id"], "name": new_name}],
    }


def control_device(message):
    match = CONTROL_PATTERN.match(message)
    if not match:
        return None

    verb, target = match.groups()
    target = clean_name(target)
    matches = database.find_devices(target)
    if not matches:
        return {
            "intent": "control_device",
            "reply": f"我还不认识“{target}”，可以先选中设备并给它命名。",
            "actions": [],
        }
    if len(matches) > 1:
        names = "、".join(device["name"] for device in matches)
        return {
            "intent": "control_device",
            "reply": f"找到了多个设备：{names}。请说出更完整的名字。",
            "actions": [],
        }

    state = "on" if verb in {"打开", "开启", "启动"} else "off"
    device = database.update_device(matches[0]["id"], state=state)
    action = {
        "device_id": device["id"],
        "device_name": device["name"],
        "state": state,
        "is_virtual": bool(device["is_virtual"]),
    }
    database.log_event("device", f"{verb}{device['name']}", action)
    suffix = "，当前为安全模拟控制" if device["is_virtual"] else ""
    return {
        "intent": "control_device",
        "reply": f"好的，已{verb}{device['name']}{suffix}。",
        "actions": [action],
    }


def handle_message(message, selected_device_id=None, now=None):
    message = clean_name(message)
    if not message:
        return {"intent": "empty", "reply": "请告诉我需要做什么。", "actions": []}

    renamed = rename_device(message, selected_device_id)
    if renamed:
        return renamed

    if any(keyword in message for keyword in ("下班回家", "准备回家", "要回家了")):
        return run_home_arrival()

    if "闹钟" in message or "叫我" in message or "提醒我" in message:
        scheduled = parse_alarm_time(message, now)
        if not scheduled:
            return {
                "intent": "create_alarm",
                "reply": "我没有听懂时间，可以说“明天早上七点设置闹钟”。",
                "actions": [],
            }
        alarm = database.create_alarm("起床提醒", scheduled.isoformat(timespec="seconds"))
        return {
            "intent": "create_alarm",
            "reply": f"闹钟已设置在 {scheduled.strftime('%m月%d日 %H:%M')}。",
            "actions": [{"alarm_id": alarm["id"]}],
            "alarm": alarm,
        }

    if any(keyword in message for keyword in ("温度", "湿度", "室内环境")):
        environment = database.get_environment()
        return {
            "intent": "environment_query",
            "reply": (
                f"室内温度 {environment['temperature']:.1f}℃，"
                f"湿度 {environment['humidity']:.0f}%。"
            ),
            "actions": [],
            "environment": environment,
        }

    controlled = control_device(message)
    if controlled:
        return controlled

    if any(keyword in message for keyword in ("你好", "在吗", "早上好", "晚上好")):
        return {
            "intent": "greeting",
            "reply": "我在呢。今天也要照顾好自己，有什么需要就告诉我。",
            "actions": [],
        }

    return {
        "intent": "unknown",
        "reply": (
            "我暂时没理解这个需求。现在可以控制设备、查询温湿度、"
            "设置闹钟，或者执行“我要下班回家了”场景。"
        ),
        "actions": [],
    }
