from flask import current_app

from . import database


def set_device_state(device_id, state):
    device = database.update_device(device_id, state=state)
    if not device:
        return None

    bridge = current_app.extensions.get("mqtt_bridge")
    if not device["is_virtual"] and bridge:
        bridge.publish_device_command(device_id, state)
    return device

