import re

from . import database
from .devices import set_device_capability, set_device_state


CONTEXT_WORDS = ("它", "这个设备", "刚才那个")
STATE_WORDS = {
    "打开": "on",
    "开启": "on",
    "启动": "on",
    "关掉": "off",
    "关闭": "off",
    "停止": "off",
}
RELATIVE_UP = {
    "大一点",
    "高一点",
    "快一点",
    "亮一点",
    "调大一点",
    "调高一点",
    "调快一点",
    "调亮一点",
    "再大一点",
    "再高一点",
    "再快一点",
    "再亮一点",
}
RELATIVE_DOWN = {
    "小一点",
    "低一点",
    "慢一点",
    "暗一点",
    "调小一点",
    "调低一点",
    "调慢一点",
    "调暗一点",
    "再小一点",
    "再低一点",
    "再慢一点",
    "再暗一点",
}
ABSOLUTE_LEVEL_PATTERN = re.compile(
    r"^(?:把)?(?:它|这个设备|刚才那个)?(?:的)?"
    r"(风速|亮度)?(?:调到|设为|设置为)(\d{1,3})(?:%|％)?$"
)
CAPABILITY_BY_LABEL = {"风速": "speed", "亮度": "brightness"}


def resolve_context_device(session_id, selected_device_id=None):
    if selected_device_id and database.get_device(selected_device_id):
        return selected_device_id
    return database.get_session_device(session_id)


def remember_result_device(session_id, result):
    device_ids = {
        action.get("device_id")
        for action in result.get("actions", [])
        if action.get("device_id")
    }
    if len(device_ids) == 1:
        return database.remember_session_device(session_id, device_ids.pop())
    return database.get_session_device(session_id)


def _normalize(message):
    return str(message or "").strip(" ，。！？,.!?")


def _strip_context_prefix(message):
    for word in CONTEXT_WORDS:
        for prefix in (word, f"把{word}", f"请把{word}"):
            if message.startswith(prefix):
                return message.removeprefix(prefix).removeprefix("给我")
    return message


def _state_request(message):
    stripped = _strip_context_prefix(message)
    for word, state in STATE_WORDS.items():
        if stripped in {word, f"{word}一下"}:
            return state, word
        if message in {f"{word}它", f"{word}这个设备", f"{word}刚才那个"}:
            return state, word
    return None


def _select_capability(device, message, label=""):
    capabilities = device["capabilities"]
    desired = None
    if label == "亮度" or any(word in message for word in ("亮", "暗")):
        desired = "brightness"
    elif label == "风速" or any(word in message for word in ("快", "慢", "风速")):
        desired = "speed"
    if desired:
        return next(
            (
                capability
                for capability in capabilities
                if capability["capability"] == desired
            ),
            None,
        )
    return capabilities[0] if len(capabilities) == 1 else None


def _missing_context():
    return {
        "intent": "context_missing",
        "reply": "我还不知道你说的是哪台设备，请先控制或点选一台设备。",
        "actions": [],
    }


def _unique_device_for_label(label):
    capability_name = CAPABILITY_BY_LABEL.get(label)
    if not capability_name:
        return None
    matches = [
        device
        for device in database.list_devices()
        if any(
            capability["capability"] == capability_name
            for capability in device["capabilities"]
        )
    ]
    return matches[0]["id"] if len(matches) == 1 else None


def handle_context_message(message, device_id):
    normalized = _normalize(message)
    state_request = _state_request(normalized)
    absolute_match = ABSOLUTE_LEVEL_PATTERN.match(normalized)
    relative_phrase = _strip_context_prefix(normalized)
    direction = (
        1
        if relative_phrase in RELATIVE_UP
        else -1
        if relative_phrase in RELATIVE_DOWN
        else 0
    )
    if not state_request and not absolute_match and not direction:
        return None
    if not device_id and absolute_match:
        device_id = _unique_device_for_label(absolute_match.group(1))
    if not device_id:
        return _missing_context()

    device = database.get_device(device_id)
    if not device:
        return _missing_context()

    if state_request:
        state, verb = state_request
        updated = set_device_state(device_id, state)
        action = {
            "device_id": device_id,
            "device_name": updated["name"],
            "state": state,
            "is_virtual": bool(updated["is_virtual"]),
        }
        database.log_event("device", f"{verb}{updated['name']}", action)
        suffix = "，当前为安全模拟控制" if updated["is_virtual"] else ""
        return {
            "intent": "control_device",
            "reply": f"好的，已{verb}{updated['name']}{suffix}。",
            "actions": [action],
        }

    label = absolute_match.group(1) if absolute_match else ""
    capability = _select_capability(device, normalized, label)
    if not capability:
        return {
            "intent": "set_device_level",
            "reply": f"{device['name']}没有可用于这句话的调节能力。",
            "actions": [],
        }
    old_value = capability["value"]
    requested = (
        float(absolute_match.group(2))
        if absolute_match
        else old_value + direction * capability["step"]
    )
    if absolute_match and not (
        capability["minimum"] <= requested <= capability["maximum"]
    ):
        return {
            "intent": "set_device_level",
            "reply": (
                f"{device['name']}{capability['display_name']}应在"
                f"{capability['minimum']:g}～{capability['maximum']:g}"
                f"{capability['unit']}之间。"
            ),
            "actions": [],
        }
    value = min(capability["maximum"], max(capability["minimum"], requested))
    if value == old_value and not absolute_match:
        edge = "最高" if direction > 0 else "最低"
        return {
            "intent": "set_device_level",
            "reply": f"{device['name']}{capability['display_name']}已经是{edge}了。",
            "actions": [],
        }

    updated = (
        capability
        if value == old_value
        else set_device_capability(device_id, capability["capability"], value)
    )
    action = {
        "device_id": device_id,
        "device_name": device["name"],
        "capability": capability["capability"],
        "value": updated["value"],
        "unit": updated["unit"],
        "is_virtual": bool(device["is_virtual"]),
    }
    message = (
        f"{device['name']}{updated['display_name']}已经是"
        f"{updated['value']:g}{updated['unit']}。"
        if value == old_value
        else (
            f"已将{device['name']}{updated['display_name']}从"
            f"{old_value:g}{updated['unit']}调到"
            f"{updated['value']:g}{updated['unit']}。"
        )
    )
    database.log_event("device", message, action)
    return {
        "intent": "set_device_level",
        "reply": message,
        "actions": [action],
        "capability": updated,
    }
