import re
import sqlite3
from datetime import datetime, timedelta

from . import database
from .devices import set_device_state
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
RELATIVE_ALARM_PATTERN = re.compile(
    r"([一二两三四五六七八九十\d]{1,3}|半)\s*"
    r"(?:个)?(分钟|分|小时)后"
)
ALARM_CANCEL_WORDS = ("取消", "删除", "关掉")
ALARM_CANCEL_PHRASES = (
    "不要这个闹钟",
    "这个闹钟不要",
    "不要这个提醒",
    "这个提醒不要",
)
ENVIRONMENT_INSTRUCTION_WORDS = (
    "记住",
    "忘记",
    "偏好",
    "喜欢",
    "以后",
    "如果",
    "超过",
    "高于",
    "低于",
    "少于",
    "自动",
    "打开",
    "关闭",
    "调到",
    "设为",
    "设置为",
)
ENVIRONMENT_QUERY_WORDS = (
    "多少",
    "怎么样",
    "如何",
    "几度",
    "高吗",
    "低吗",
    "查询",
    "查看",
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
    relative_match = RELATIVE_ALARM_PATTERN.search(message)
    if relative_match:
        amount_text, unit = relative_match.groups()
        if amount_text == "半":
            return now + (
                timedelta(minutes=30)
                if unit == "小时"
                else timedelta(seconds=30)
            )
        amount = parse_number(amount_text)
        if amount is None:
            return None
        delta = (
            timedelta(hours=amount)
            if unit == "小时"
            else timedelta(minutes=amount)
        )
        return now + delta

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


def parse_alarm_label(message):
    normalized = str(message or "").strip(" ，。！？,.!?")
    for marker in ("提醒我", "叫我"):
        if marker not in normalized:
            continue
        label = normalized.split(marker, 1)[1]
        label = RELATIVE_ALARM_PATTERN.sub("", label)
        label = ALARM_TIME_PATTERN.sub("", label)
        label = label.strip(" ，。！？,.!?")
        for prefix in ("记得", "要", "去"):
            if label.startswith(prefix) and len(label) > len(prefix):
                label = label.removeprefix(prefix).strip()
        if label:
            return label[:50]

    label = RELATIVE_ALARM_PATTERN.sub("", normalized)
    label = ALARM_TIME_PATTERN.sub("", label)
    for word in (
        "今天",
        "明天",
        "后天",
        "早上",
        "上午",
        "中午",
        "下午",
        "晚上",
        "凌晨",
    ):
        label = label.replace(word, "")
    named_alarm = re.search(
        r"(?:设置|创建|添加)(?:一个)?(.+?)(?:闹钟|提醒)$",
        label,
    )
    if named_alarm:
        label = named_alarm.group(1).strip(" ，。！？,.!?")
        if label:
            return label[:50]
    return "起床提醒"


def cancel_alarm(message, now=None):
    alarms = database.list_alarms()
    if not alarms:
        return {
            "intent": "cancel_alarm",
            "reply": "现在没有待执行的闹钟。",
            "actions": [],
        }

    normalized = str(message or "").strip(" ，。！？,.!?")
    if any(word in normalized for word in ("全部", "所有")):
        matches = alarms
    else:
        scheduled = parse_alarm_time(normalized, now)
        matches = []
        if scheduled:
            matches = [
                alarm
                for alarm in alarms
                if abs(
                    (
                        datetime.fromisoformat(alarm["scheduled_at"])
                        - scheduled
                    ).total_seconds()
                )
                < 60
            ]

        if not matches:
            label = normalized
            for word in (
                *ALARM_CANCEL_WORDS,
                "不要",
                "这个",
                "闹钟",
                "提醒",
                "全部",
                "所有",
                "了",
            ):
                label = label.replace(word, "")
            label = RELATIVE_ALARM_PATTERN.sub("", label)
            label = ALARM_TIME_PATTERN.sub("", label)
            label = label.strip(" ，。！？,.!?")
            if label:
                matches = [
                    alarm
                    for alarm in alarms
                    if label in alarm["label"] or alarm["label"] in label
                ]
            elif len(alarms) == 1:
                matches = alarms

    if not matches:
        return {
            "intent": "cancel_alarm",
            "reply": "没有找到对应的待执行闹钟，请说出时间或提醒内容。",
            "actions": [],
        }
    if len(matches) > 1 and not any(
        word in normalized for word in ("全部", "所有")
    ):
        return {
            "intent": "cancel_alarm",
            "reply": "找到了多个闹钟，请说出具体时间或提醒内容。",
            "actions": [],
        }

    for alarm in matches:
        database.delete_alarm(alarm["id"])
    labels = "、".join(dict.fromkeys(alarm["label"] for alarm in matches))
    return {
        "intent": "cancel_alarm",
        "reply": f"已取消{labels}。",
        "actions": [
            {"alarm_id": alarm["id"], "label": alarm["label"], "deleted": True}
            for alarm in matches
        ],
    }


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
        current_device = database.get_device(selected_device_id)
        inferred_room = database.infer_room_from_name(new_name)
        try:
            device = database.update_device(
                selected_device_id,
                name=new_name,
                room=inferred_room or (current_device or {}).get("room"),
            )
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
            "actions": [
                {
                    "device_id": device["id"],
                    "name": new_name,
                    "room": device["room"],
                }
            ],
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
    inferred_room = database.infer_room_from_name(new_name)
    try:
        device = database.update_device(
            matches[0]["id"],
            name=new_name,
            room=inferred_room or matches[0]["room"],
        )
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
        "actions": [
            {
                "device_id": device["id"],
                "name": new_name,
                "room": device["room"],
            }
        ],
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
    device = set_device_state(matches[0]["id"], state)
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

    scheduled_alarm = parse_alarm_time(message, now)
    has_cancel_prefix = bool(
        re.match(
            r"^(?:请帮我|帮我|麻烦帮我|麻烦|请)?\s*(?:取消|删除|关掉)",
            message,
        )
    )
    is_alarm_creation = scheduled_alarm and any(
        marker in message for marker in ("提醒我", "叫我")
    ) and not has_cancel_prefix
    is_alarm_cancel = (
        any(word in message for word in ALARM_CANCEL_WORDS)
        and any(word in message for word in ("闹钟", "提醒"))
        and not is_alarm_creation
    ) or any(
        phrase in message for phrase in ALARM_CANCEL_PHRASES
    )
    if (
        "闹钟" in message
        or "叫我" in message
        or "提醒我" in message
        or is_alarm_cancel
    ):
        if is_alarm_cancel:
            return cancel_alarm(message, now)
        scheduled = scheduled_alarm
        if not scheduled:
            return {
                "intent": "create_alarm",
                "reply": "我没有听懂时间，可以说“明天早上七点设置闹钟”。",
                "actions": [],
            }
        label = parse_alarm_label(message)
        alarm = database.create_alarm(label, scheduled.isoformat(timespec="seconds"))
        return {
            "intent": "create_alarm",
            "reply": (
                f"已设置“{label}”提醒："
                f"{scheduled.strftime('%m月%d日 %H:%M')}。"
            ),
            "actions": [{"alarm_id": alarm["id"]}],
            "alarm": alarm,
        }

    if (
        any(keyword in message for keyword in ("温度", "湿度", "室内环境"))
        and any(word in message for word in ENVIRONMENT_QUERY_WORDS)
        and not any(word in message for word in ENVIRONMENT_INSTRUCTION_WORDS)
    ):
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
