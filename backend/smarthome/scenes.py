from . import database
from .devices import set_device_capability, set_device_state


def _resolve_device(name, selected_device_id=None):
    selected_words = {"", "这个", "它", "这个设备", "选中的设备"}
    if name in selected_words and selected_device_id:
        device = database.get_device(selected_device_id)
        matches = [device] if device else []
    else:
        matches = database.find_devices(name) if name else []
    if len(matches) != 1:
        raise ValueError(f"没有唯一找到场景中的设备“{name or '这个设备'}”。")
    return matches[0]


def _validate_action(item, selected_device_id=None):
    if not isinstance(item, dict):
        raise ValueError("场景动作格式不正确。")
    device_name = str(item.get("device_name", "")).strip()
    action = str(item.get("action", "")).strip()
    if action not in {"on", "off", "set_level"}:
        raise ValueError("场景动作只支持打开、关闭或调节设备能力。")
    device = _resolve_device(device_name, selected_device_id)
    result = {"device_id": device["id"], "action": action}

    if action == "set_level":
        capability_name = str(item.get("capability", "")).strip()
        capability = database.get_device_capability(
            device["id"], capability_name
        )
        if not capability:
            raise ValueError(f"{device['name']}没有注册这个调节能力。")
        try:
            value = float(item["value"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("场景调节值必须是数字。") from exc
        if not capability["minimum"] <= value <= capability["maximum"]:
            raise ValueError(
                f"{capability['display_name']}应在"
                f"{capability['minimum']:g}～{capability['maximum']:g}"
                f"{capability['unit']}之间。"
            )
        value = (
            round((value - capability["minimum"]) / capability["step"])
            * capability["step"]
            + capability["minimum"]
        )
        result.update({"capability": capability_name, "value": value})
    return result


def _describe_action(action):
    device = database.get_device(action["device_id"])
    device_name = device["name"] if device else "已删除设备"
    if action["action"] == "on":
        return f"打开{device_name}"
    if action["action"] == "off":
        return f"关闭{device_name}"
    capability = database.get_device_capability(
        action["device_id"], action["capability"]
    )
    label = capability["display_name"] if capability else action["capability"]
    unit = capability["unit"] if capability else ""
    return f"{device_name}{label}{action['value']:g}{unit}"


def serialize_scene(scene):
    actions = [
        {**action, "description": _describe_action(action)}
        for action in scene["actions"]
    ]
    return {
        **scene,
        "actions": actions,
        "description": "、".join(
            action["description"] for action in actions
        ),
    }


def list_scenes():
    return [serialize_scene(scene) for scene in database.list_scenes()]


def save_scene(arguments, selected_device_id=None):
    name = str(arguments.get("name", "")).strip()
    raw_actions = arguments.get("actions")
    if not name or len(name) > 20:
        raise ValueError("场景名称应为1至20个字符。")
    if not isinstance(raw_actions, list) or not 1 <= len(raw_actions) <= 8:
        raise ValueError("一个场景应包含1至8个设备动作。")

    actions = [
        _validate_action(item, selected_device_id)
        for item in raw_actions
    ]
    scene = database.save_scene(name, actions)
    result = serialize_scene(scene)
    database.log_event("scene", f"保存场景：{name}", result)
    return result


def _find_scene(name):
    name = str(name or "").strip()
    exact = database.get_scene_by_name(name)
    if exact:
        return exact
    matches = [
        scene
        for scene in database.list_scenes()
        if name in scene["name"] or scene["name"] in name
    ]
    if len(matches) != 1:
        raise ValueError(f"没有唯一找到场景“{name}”。")
    return matches[0]


def run_scene(name=None, scene_id=None):
    scene = database.get_scene(scene_id) if scene_id else _find_scene(name)
    if not scene:
        raise ValueError("场景不存在。")

    actions = []
    errors = []
    for item in scene["actions"]:
        device = database.get_device(item["device_id"])
        if not device:
            errors.append("场景中的设备已被删除。")
            continue
        if not device["online"] and not device["is_virtual"]:
            errors.append(f"{device['name']}当前离线。")
            continue

        if item["action"] in {"on", "off"}:
            if device["state"] == item["action"]:
                continue
            updated = set_device_state(device["id"], item["action"])
            actions.append(
                {
                    "device_id": device["id"],
                    "device_name": updated["name"],
                    "state": item["action"],
                    "scene_id": scene["id"],
                    "is_virtual": bool(updated["is_virtual"]),
                }
            )
            continue

        if item["value"] > 0 and device["state"] == "off":
            updated = set_device_state(device["id"], "on")
            actions.append(
                {
                    "device_id": device["id"],
                    "device_name": updated["name"],
                    "state": "on",
                    "scene_id": scene["id"],
                    "is_virtual": bool(updated["is_virtual"]),
                }
            )
        capability = database.get_device_capability(
            device["id"], item["capability"]
        )
        if not capability:
            errors.append(f"{device['name']}的调节能力已不存在。")
            continue
        if capability["value"] != item["value"]:
            updated = set_device_capability(
                device["id"], item["capability"], item["value"]
            )
            actions.append(
                {
                    "device_id": device["id"],
                    "device_name": device["name"],
                    "capability": item["capability"],
                    "value": updated["value"],
                    "unit": updated["unit"],
                    "scene_id": scene["id"],
                    "is_virtual": bool(device["is_virtual"]),
                }
            )

    result = serialize_scene(scene)
    database.log_event(
        "scene",
        f"执行场景：{scene['name']}",
        {"scene_id": scene["id"], "actions": actions, "errors": errors},
    )
    return {"scene": result, "actions": actions, "errors": errors}
