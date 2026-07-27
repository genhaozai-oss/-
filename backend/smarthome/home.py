from . import database


TYPE_LABELS = {
    "fan": "风扇",
    "humidifier": "加湿器",
    "dehumidifier": "抽湿器",
    "light": "灯",
}


def set_type_state(device_type, state):
    actions = []
    for device in database.list_devices():
        if device["type"] != device_type:
            continue
        if device["state"] == state:
            continue
        updated = database.update_device(device["id"], state=state)
        actions.append(
            {
                "device_id": device["id"],
                "device_name": device["name"],
                "state": state,
                "is_virtual": bool(updated["is_virtual"]),
            }
        )
    return actions


def run_comfort_rules():
    environment = database.get_environment()
    temperature = environment["temperature"]
    humidity = environment["humidity"]
    actions = []

    if temperature >= 28:
        actions.extend(set_type_state("fan", "on"))
    elif temperature <= 25:
        actions.extend(set_type_state("fan", "off"))

    if humidity < 40:
        actions.extend(set_type_state("dehumidifier", "off"))
        actions.extend(set_type_state("humidifier", "on"))
    elif humidity > 70:
        actions.extend(set_type_state("humidifier", "off"))
        actions.extend(set_type_state("dehumidifier", "on"))
    elif 45 <= humidity <= 65:
        actions.extend(set_type_state("humidifier", "off"))
        actions.extend(set_type_state("dehumidifier", "off"))

    return environment, actions


def describe_actions(actions):
    if not actions:
        return "室内环境目前比较舒适，设备保持原状态"

    descriptions = []
    for action in actions:
        verb = "开启" if action["state"] == "on" else "关闭"
        suffix = "（演示）" if action["is_virtual"] else ""
        descriptions.append(f"{verb}{action['device_name']}{suffix}")
    return "、".join(descriptions)


def run_home_arrival():
    environment, actions = run_comfort_rules()
    action_text = describe_actions(actions)
    reply = (
        "辛苦啦，回家的路上注意安全。"
        f"家里现在 {environment['temperature']:.1f}℃，"
        f"湿度 {environment['humidity']:.0f}%。"
        f"我已检查环境：{action_text}。"
    )
    database.log_event(
        "scene",
        "执行准备回家场景",
        {"environment": environment, "actions": actions},
    )
    return {
        "intent": "home_arrival",
        "reply": reply,
        "actions": actions,
        "environment": environment,
    }

