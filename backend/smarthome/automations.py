import re

from . import database
from .devices import set_device_capability, set_device_state


SENSOR_LABELS = {
    "temperature": ("温度", "℃", -20, 80),
    "humidity": ("湿度", "%", 0, 100),
}
OPERATOR_LABELS = {"above": "高于", "below": "低于"}
ACTION_LABELS = {"on": "打开", "off": "关闭", "set_level": "调节"}
CONDITION_PATTERN = re.compile(
    r"(温度|湿度)\s*(超过|高于|大于|低于|少于|小于)\s*"
    r"(-?\d+(?:\.\d+)?)\s*(?:℃|度|%|％)?"
)
LEVEL_ACTION_PATTERN = re.compile(
    r"^(?:把)?(.+?)(风速|亮度)\s*"
    r"(?:调到|调成|设为|设置为)\s*"
    r"(\d+(?:\.\d+)?)\s*(?:%|％)?$"
)
STATE_ACTION_WORDS = {
    "打开": "on",
    "开启": "on",
    "启动": "on",
    "关闭": "off",
    "关掉": "off",
    "停止": "off",
}
SENSOR_NAMES = {"温度": "temperature", "湿度": "humidity"}
OPERATOR_NAMES = {
    "超过": "above",
    "高于": "above",
    "大于": "above",
    "低于": "below",
    "少于": "below",
    "小于": "below",
}
CAPABILITY_NAMES = {"风速": "speed", "亮度": "brightness"}
AUTOMATION_NEGATION_PATTERN = re.compile(
    r"不|没|未|无|非|别|莫|勿|禁止|取消|删除|移除|撤销|停用|避免|拒绝"
)
AUTOMATION_CREATION_CUES = (
    "以后",
    "每当",
    "一旦",
    "如果",
    "当",
    "自动",
    "就",
)
AUTOMATION_QUESTION_PATTERN = re.compile(
    r"[?？]|(?:吗|么|嘛|呢)\s*[。！？!?]*$|"
    r"是否|是不是|可不可以|可以吗|能不能|有没有|"
    r"好不好|行不行|对不对|怎么样|如何"
)


def describe_rule(rule):
    sensor_label, sensor_unit, _, _ = SENSOR_LABELS[rule["sensor"]]
    condition = (
        f"{sensor_label}{OPERATOR_LABELS[rule['operator']]}"
        f"{rule['threshold']:g}{sensor_unit}"
    )
    device_name = rule.get("device_name") or "已删除设备"
    if rule["action"] == "set_level":
        capability = database.get_device_capability(
            rule["device_id"], rule["capability"]
        )
        capability_label = (
            capability["display_name"] if capability else rule["capability"]
        )
        unit = capability["unit"] if capability else ""
        action = f"将{device_name}{capability_label}调到{rule['value']:g}{unit}"
    else:
        action = f"{ACTION_LABELS[rule['action']]}{device_name}"
    return f"{condition}时，{action}"


def serialize_rule(rule):
    return {**rule, "description": describe_rule(rule)}


def list_rules():
    return [serialize_rule(rule) for rule in database.list_automation_rules()]


def create_rule(arguments, selected_device_id=None):
    sensor = str(arguments.get("sensor", "")).strip()
    operator = str(arguments.get("operator", "")).strip()
    action = str(arguments.get("action", "")).strip()
    device_name = str(arguments.get("device_name", "")).strip()

    if sensor not in SENSOR_LABELS or operator not in OPERATOR_LABELS:
        raise ValueError("自动化条件只支持温度或湿度的高于、低于判断。")
    if action not in ACTION_LABELS:
        raise ValueError("自动化动作只支持开、关或调节设备能力。")
    try:
        threshold = float(arguments["threshold"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("自动化阈值必须是数字。") from exc
    _, _, minimum, maximum = SENSOR_LABELS[sensor]
    if not minimum <= threshold <= maximum:
        raise ValueError(f"自动化阈值应在 {minimum}～{maximum} 之间。")

    selected_words = {"", "这个", "它", "这个设备", "选中的设备"}
    if device_name in selected_words and selected_device_id:
        device = database.get_device(selected_device_id)
        matches = [device] if device else []
    else:
        matches = database.find_devices(device_name) if device_name else []
    if len(matches) != 1:
        raise ValueError("没有唯一找到自动化要控制的设备。")
    device = matches[0]

    capability = ""
    value = None
    if action == "set_level":
        capability = str(arguments.get("capability", "")).strip()
        registered = database.get_device_capability(device["id"], capability)
        if not registered:
            raise ValueError(f"{device['name']}没有注册这个调节能力。")
        try:
            value = float(arguments["value"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("自动化调节值必须是数字。") from exc
        if not registered["minimum"] <= value <= registered["maximum"]:
            raise ValueError(
                f"{registered['display_name']}应在"
                f"{registered['minimum']:g}～{registered['maximum']:g}"
                f"{registered['unit']}之间。"
            )
        value = (
            round((value - registered["minimum"]) / registered["step"])
            * registered["step"]
            + registered["minimum"]
        )

    rule = database.create_automation_rule(
        sensor,
        operator,
        threshold,
        device["id"],
        action,
        capability,
        value,
    )
    result = serialize_rule(rule)
    database.log_event("automation", f"创建规则：{result['description']}", result)
    return result


def _strip_action_prefix(text):
    value = str(text or "").strip(" ，。！？,.!?")
    prefix = re.compile(
        r"^(?:(?:的时候|的话|时|后|就|则|自动|请帮我|帮我|请)"
        r"[\s，,。]*)+"
    )
    return prefix.sub("", value).strip(" ，。！？,.!?")


def _state_action(text):
    for word, state in STATE_ACTION_WORDS.items():
        if text.startswith(word):
            target = text.removeprefix(word).strip(" ，。！？,.!?")
            if target:
                return target, state
        if text.endswith(word):
            target = text.removesuffix(word).removeprefix("把").strip(
                " ，。！？,.!?"
            )
            if target:
                return target, state
    return None


def handle_automation_message(message, selected_device_id=None):
    raw_message = str(message or "").strip()
    normalized = raw_message.strip(" ，。！？,.!?")
    if AUTOMATION_NEGATION_PATTERN.search(normalized):
        return None
    if AUTOMATION_QUESTION_PATTERN.search(raw_message):
        return None
    condition = CONDITION_PATTERN.search(normalized)
    if not condition:
        return None
    if not any(cue in normalized for cue in AUTOMATION_CREATION_CUES):
        return None
    sensor_label, operator_label, threshold = condition.groups()
    action_text = _strip_action_prefix(normalized[condition.end():])
    if not action_text:
        return None

    level_match = LEVEL_ACTION_PATTERN.match(action_text)
    if level_match:
        device_name, capability_label, value = level_match.groups()
        arguments = {
            "sensor": SENSOR_NAMES[sensor_label],
            "operator": OPERATOR_NAMES[operator_label],
            "threshold": threshold,
            "device_name": device_name.strip(),
            "action": "set_level",
            "capability": CAPABILITY_NAMES[capability_label],
            "value": value,
        }
    else:
        state_action = _state_action(action_text)
        if not state_action:
            return None
        device_name, state = state_action
        arguments = {
            "sensor": SENSOR_NAMES[sensor_label],
            "operator": OPERATOR_NAMES[operator_label],
            "threshold": threshold,
            "device_name": device_name,
            "action": state,
        }

    try:
        rule = create_rule(arguments, selected_device_id)
    except ValueError as exc:
        return {
            "intent": "create_automation",
            "reply": f"没有保存这条自动化：{exc}",
            "actions": [],
        }
    return {
        "intent": "create_automation",
        "reply": (
            f"已在本地保存自动化：{rule['description']}。"
            "云端 AI 暂时不可用时也会继续执行。"
        ),
        "actions": [],
        "automation": rule,
    }


def _condition_matches(rule, environment):
    current = environment[rule["sensor"]]
    if rule["operator"] == "above":
        return current > rule["threshold"]
    return current < rule["threshold"]


def plan_rules(environment=None):
    environment = environment or database.get_environment()
    plans = []
    claimed_device_ids = set()
    for rule in database.list_automation_rules(enabled_only=True):
        if not _condition_matches(rule, environment):
            continue
        device = database.get_device(rule["device_id"])
        if not device or device["id"] in claimed_device_ids:
            continue
        claimed_device_ids.add(device["id"])
        base = {
            "device_id": device["id"],
            "device_name": device["name"],
            "device_type": device["type"],
            "is_virtual": bool(device["is_virtual"]),
            "online": bool(device["online"]),
            "source": "automation",
            "automation_rule_id": rule["id"],
        }
        if rule["action"] in {"on", "off"}:
            if device["state"] != rule["action"]:
                plans.append(
                    {
                        **base,
                        "operation": "state",
                        "state": rule["action"],
                    }
                )
        else:
            if rule["value"] > 0 and device["state"] == "off":
                plans.append(
                    {
                        **base,
                        "operation": "state",
                        "state": "on",
                    }
                )
            capability = database.get_device_capability(
                device["id"], rule["capability"]
            )
            if capability and capability["value"] != rule["value"]:
                plans.append(
                    {
                        **base,
                        "operation": "capability",
                        "capability": rule["capability"],
                        "value": rule["value"],
                        "unit": capability["unit"],
                    }
                )
    return plans


def run_rules(environment=None, excluded_device_ids=None):
    environment = environment or database.get_environment()
    excluded_device_ids = excluded_device_ids or set()
    actions = []
    actions_by_rule = {}
    for plan in plan_rules(environment):
        device = database.get_device(plan["device_id"])
        if (
            not device
            or device["id"] in excluded_device_ids
            or (not device["online"] and not device["is_virtual"])
        ):
            continue
        if (
            not device["is_virtual"]
            and device["type"] in {"humidifier", "dehumidifier"}
            and (
                plan["operation"] == "capability"
                or plan.get("state") == "on"
            )
        ):
            continue

        if plan["operation"] == "state":
            updated = set_device_state(device["id"], plan["state"])
            action = {
                "device_id": device["id"],
                "device_name": updated["name"],
                "state": plan["state"],
                "automation_rule_id": plan["automation_rule_id"],
                "is_virtual": bool(updated["is_virtual"]),
            }
        else:
            updated = set_device_capability(
                device["id"],
                plan["capability"],
                plan["value"],
            )
            action = {
                "device_id": device["id"],
                "device_name": device["name"],
                "capability": plan["capability"],
                "value": updated["value"],
                "unit": updated["unit"],
                "automation_rule_id": plan["automation_rule_id"],
                "is_virtual": bool(device["is_virtual"]),
            }
        actions.append(action)
        actions_by_rule.setdefault(
            plan["automation_rule_id"],
            [],
        ).append(action)

    for rule_id, rule_actions in actions_by_rule.items():
        rule = database.get_automation_rule(rule_id)
        database.update_automation_rule(rule_id, triggered=True)
        database.log_event(
            "automation",
            f"执行规则：{describe_rule(rule)}",
            {"rule_id": rule_id, "actions": rule_actions},
        )
    return actions
