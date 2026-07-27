from flask import current_app

from . import database


POWER_ON_PREFERENCES = {
    "fan": ("fan_speed", "speed"),
    "light": ("light_brightness", "brightness"),
}


def set_device_state(device_id, state):
    device = database.update_device(device_id, state=state)
    if not device:
        return None

    bridge = current_app.extensions.get("mqtt_bridge")
    if not device["is_virtual"] and bridge:
        bridge.publish_device_command(device_id, state)
    if state == "on":
        preference_mapping = POWER_ON_PREFERENCES.get(device["type"])
        preferences = database.get_user_preferences()
        if preference_mapping:
            preference_name, capability = preference_mapping
            preferred_value = preferences.get(preference_name)
            if preferred_value is not None:
                set_device_capability(device_id, capability, preferred_value)
    return database.get_device(device_id)


def set_device_capability(device_id, capability, value):
    registered = database.update_device_capability(device_id, capability, value)
    if not registered:
        return None

    device = database.get_device(device_id)
    bridge = current_app.extensions.get("mqtt_bridge")
    if device and not device["is_virtual"] and bridge:
        bridge.publish_device_capability(
            device_id,
            capability,
            registered["value"],
        )
    return registered
