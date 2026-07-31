from . import database
from .automations import run_rules
from .devices import set_device_state


TYPE_LABELS = {
    "fan": "风扇",
    "humidifier": "加湿器",
    "dehumidifier": "抽湿器",
    "light": "灯",
}


def set_type_state(device_type, state, excluded_device_ids=None):
    actions = []
    excluded_device_ids = excluded_device_ids or set()
    for device in database.list_devices():
        if (
            device["type"] != device_type
            or device["id"] in excluded_device_ids
            or (not device["online"] and not device["is_virtual"])
        ):
            continue
        if (
            state == "on"
            and not device["is_virtual"]
            and device["type"] in {"humidifier", "dehumidifier"}
        ):
            continue
        if device["state"] == state:
            continue
        updated = set_device_state(device["id"], state)
        actions.append(
            {
                "device_id": device["id"],
                "device_name": device["name"],
                "state": state,
                "is_virtual": bool(updated["is_virtual"]),
            }
        )
    return actions


def run_comfort_rules(thresholds=None, excluded_device_ids=None):
    environment = database.get_environment()
    temperature = environment["temperature"]
    humidity = environment["humidity"]
    actions = []
    thresholds = thresholds or {
        "fan_on": 28,
        "fan_off": 25,
        "humidifier_on": 40,
        "humidity_safe_low": 45,
        "humidity_safe_high": 65,
        "dehumidifier_on": 70,
    }
    excluded_device_ids = excluded_device_ids or set()
    managed = database.automation_managed_device_ids() | excluded_device_ids

    if temperature >= thresholds["fan_on"]:
        actions.extend(set_type_state("fan", "on", managed))
    elif temperature <= thresholds["fan_off"]:
        actions.extend(set_type_state("fan", "off", managed))

    if humidity < thresholds["humidifier_on"]:
        actions.extend(set_type_state("dehumidifier", "off", managed))
        actions.extend(set_type_state("humidifier", "on", managed))
    elif humidity > thresholds["dehumidifier_on"]:
        actions.extend(set_type_state("humidifier", "off", managed))
        actions.extend(set_type_state("dehumidifier", "on", managed))
    elif (
        thresholds["humidity_safe_low"]
        <= humidity
        <= thresholds["humidity_safe_high"]
    ):
        actions.extend(set_type_state("humidifier", "off", managed))
        actions.extend(set_type_state("dehumidifier", "off", managed))

    actions.extend(run_rules(environment, excluded_device_ids))
    return environment, actions


def describe_actions(environment, actions):
    devices = database.list_devices()
    status = []

    if environment["temperature"] >= 28:
        fans = [
            device["name"]
            for device in devices
            if device["type"] == "fan" and device["state"] == "on"
        ]
        status.append(
            f"温度偏高，{'、'.join(fans)}已开启"
            if fans
            else "温度偏高，但没有可用风扇"
        )

    if environment["humidity"] > 70:
        dehumidifiers = [
            device["name"]
            for device in devices
            if device["type"] == "dehumidifier" and device["state"] == "on"
        ]
        status.append(
            f"湿度偏高，{'、'.join(dehumidifiers)}已开启"
            if dehumidifiers
            else "湿度偏高，但没有可用抽湿设备"
        )
    elif environment["humidity"] < 40:
        humidifiers = [
            device["name"]
            for device in devices
            if device["type"] == "humidifier" and device["state"] == "on"
        ]
        status.append(
            f"湿度偏低，{'、'.join(humidifiers)}已开启"
            if humidifiers
            else "湿度偏低，但没有可用加湿设备"
        )

    if status:
        return "；".join(status)
    if not actions:
        return "室内环境目前比较舒适，设备保持原状态"

    descriptions = []
    for action in actions:
        verb = "开启" if action["state"] == "on" else "关闭"
        suffix = "（演示）" if action["is_virtual"] else ""
        descriptions.append(f"{verb}{action['device_name']}{suffix}")
    return "、".join(descriptions)


def run_home_arrival():
    from .autoflow import run_auto_flow

    flow = run_auto_flow(trigger="home_arrival", force=True)
    environment = flow["environment"]
    actions = flow["actions"]
    action_text = describe_actions(environment, actions)
    if flow["status"] in {"blocked", "partial", "stale"}:
        action_text += f"；{flow['summary']}"
    reply = (
        "辛苦啦，回家的路上注意安全。"
        f"家里现在 {environment['temperature']:.1f}℃，"
        f"湿度 {environment['humidity']:.0f}%。"
        f"我已检查环境：{action_text}。"
    )
    database.log_event(
        "scene",
        "执行准备回家场景",
        {
            "environment": environment,
            "actions": actions,
            "auto_flow_status": flow["status"],
        },
    )
    return {
        "intent": "home_arrival",
        "reply": reply,
        "actions": actions,
        "environment": environment,
        "auto_flow": flow,
    }
