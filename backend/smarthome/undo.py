import sqlite3

from . import database
from .devices import set_device_capability, set_device_state


UNDO_HINTS = (
    "撤销刚才",
    "撤销上一步",
    "撤回刚才",
    "恢复刚才",
    "还原刚才",
    "取消刚才",
)


def is_undo_request(message):
    normalized = str(message or "").strip().lower()
    if any(word in normalized for word in ("不要撤销", "别撤销")):
        return False
    if normalized in {"撤销", "撤回", "undo", "反悔了", "再撤销一次"}:
        return True
    return any(hint in normalized for hint in UNDO_HINTS)


def capture_device_snapshot():
    return {
        device["id"]: {
            "device_id": device["id"],
            "name": device["name"],
            "room": device["room"],
            "state": device["state"],
            "capabilities": {
                capability["capability"]: capability["value"]
                for capability in device["capabilities"]
            },
        }
        for device in database.list_devices()
    }


def record_undoable(snapshot, result, source_message=""):
    affected_ids = {
        action.get("device_id")
        for action in result.get("actions", [])
        if action.get("device_id")
    }
    before = [snapshot[device_id] for device_id in affected_ids if device_id in snapshot]
    changed = []
    for item in before:
        current = database.get_device(item["device_id"])
        if not current:
            continue
        current_capabilities = {
            capability["capability"]: capability["value"]
            for capability in current["capabilities"]
        }
        if (
            current["name"] != item["name"]
            or current["room"] != item["room"]
            or current["state"] != item["state"]
            or current_capabilities != item["capabilities"]
        ):
            changed.append(item)
    if not changed:
        return None

    description = str(result.get("reply") or result.get("message") or "设备操作").strip()
    return database.log_event(
        "undoable",
        f"可撤销：{description}",
        {
            "intent": result.get("intent"),
            "source_message": str(source_message or "")[:200],
            "before": changed,
        },
    )


def _latest_undoable_event():
    events = database.list_events(100)
    undone_ids = {
        event["payload"].get("undone_event_id")
        for event in events
        if event["kind"] == "undo"
    }
    return next(
        (
            event
            for event in events
            if event["kind"] == "undoable" and event["id"] not in undone_ids
        ),
        None,
    )


def undo_last_action():
    event = _latest_undoable_event()
    if not event:
        return {
            "ok": False,
            "intent": "undo_last_action",
            "reply": "目前没有可以撤销的设备操作。",
            "actions": [],
        }

    restored = []
    errors = []
    for item in event["payload"].get("before", []):
        device = database.get_device(item["device_id"])
        if not device:
            errors.append("有设备已经不存在")
            continue
        if not device["online"] and not device["is_virtual"]:
            errors.append(f"{device['name']}当前离线")
            continue
        try:
            database.update_device(
                device["id"],
                name=item["name"],
                room=item["room"],
            )
        except sqlite3.IntegrityError:
            errors.append(f"{item['name']}名称已被占用")
            continue

        updated = set_device_state(device["id"], item["state"])
        for capability, value in item.get("capabilities", {}).items():
            if database.get_device_capability(device["id"], capability):
                set_device_capability(device["id"], capability, value)
        restored.append(
            {
                "device_id": device["id"],
                "device_name": updated["name"],
                "state": updated["state"],
                "is_virtual": bool(updated["is_virtual"]),
            }
        )

    original = event["message"].removeprefix("可撤销：")
    if restored:
        names = "、".join(action["device_name"] for action in restored)
        reply = f"已撤销“{original}”，并恢复{names}之前的状态。"
    else:
        reply = f"没能撤销“{original}”。"
    if errors:
        reply += " " + "；".join(errors) + "。"

    database.log_event(
        "undo",
        reply,
        {
            "undone_event_id": event["id"] if restored else None,
            "restored": restored,
            "errors": errors,
        },
    )
    return {
        "ok": bool(restored) and not errors,
        "intent": "undo_last_action",
        "reply": reply,
        "actions": restored,
        "errors": errors,
    }
