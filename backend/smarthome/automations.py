from . import database
from .devices import set_device_capability, set_device_state


SENSOR_LABELS = {
    "temperature": ("温度", "℃", -20, 80),
    "humidity": ("湿度", "%", 0, 100),
}
OPERATOR_LABELS = {"above": "高于", "below": "低于"}
ACTION_LABELS = {"on": "打开", "off": "关闭", "set_level": "调节"}


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


def _condition_matches(rule, environment):
    current = environment[rule["sensor"]]
    if rule["operator"] == "above":
        return current > rule["threshold"]
    return current < rule["threshold"]


def run_rules(environment=None):
    environment = environment or database.get_environment()
    actions = []
    for rule in database.list_automation_rules(enabled_only=True):
        if not _condition_matches(rule, environment):
            continue
        device = database.get_device(rule["device_id"])
        if not device or (not device["online"] and not device["is_virtual"]):
            continue

        rule_actions = []
        if rule["action"] in {"on", "off"}:
            if device["state"] != rule["action"]:
                updated = set_device_state(device["id"], rule["action"])
                rule_actions.append(
                    {
                        "device_id": device["id"],
                        "device_name": updated["name"],
                        "state": rule["action"],
                        "automation_rule_id": rule["id"],
                        "is_virtual": bool(updated["is_virtual"]),
                    }
                )
        else:
            if rule["value"] > 0 and device["state"] == "off":
                updated = set_device_state(device["id"], "on")
                rule_actions.append(
                    {
                        "device_id": device["id"],
                        "device_name": updated["name"],
                        "state": "on",
                        "automation_rule_id": rule["id"],
                        "is_virtual": bool(updated["is_virtual"]),
                    }
                )
            capability = database.get_device_capability(
                device["id"], rule["capability"]
            )
            if capability and capability["value"] != rule["value"]:
                updated = set_device_capability(
                    device["id"], rule["capability"], rule["value"]
                )
                rule_actions.append(
                    {
                        "device_id": device["id"],
                        "device_name": device["name"],
                        "capability": rule["capability"],
                        "value": updated["value"],
                        "unit": updated["unit"],
                        "automation_rule_id": rule["id"],
                        "is_virtual": bool(device["is_virtual"]),
                    }
                )

        if rule_actions:
            database.update_automation_rule(rule["id"], triggered=True)
            database.log_event(
                "automation",
                f"执行规则：{describe_rule(rule)}",
                {"rule_id": rule["id"], "actions": rule_actions},
            )
            actions.extend(rule_actions)
    return actions
