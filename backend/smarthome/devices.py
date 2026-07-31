from flask import current_app

from . import database


POWER_ON_PREFERENCES = {
    "fan": ("fan_speed", "speed"),
    "light": ("light_brightness", "brightness"),
}


class DeviceCommandError(RuntimeError):
    pass


def set_device_state(device_id, state):
    device = database.get_device(device_id)
    if not device:
        return None

    bridge = current_app.extensions.get("mqtt_bridge")
    if not device["is_virtual"]:
        sent = bridge and bridge.publish_device_command(device_id, state)
        if not sent:
            raise DeviceCommandError(
                f"{device['name']}控制命令发送失败，本地状态未改变。"
            )
    device = database.update_device(device_id, state=state)
    if state == "on":
        preference_mapping = POWER_ON_PREFERENCES.get(device["type"])
        preferences = database.get_user_preferences()
        if preference_mapping:
            preference_name, capability = preference_mapping
            preferred_value = preferences.get(preference_name)
            if preferred_value is not None:
                try:
                    set_device_capability(
                        device_id,
                        capability,
                        preferred_value,
                    )
                except DeviceCommandError:
                    pass
    return database.get_device(device_id)


def set_device_capability(device_id, capability, value):
    registered = database.get_device_capability(device_id, capability)
    if not registered:
        return None

    numeric_value = float(value)
    if not registered["minimum"] <= numeric_value <= registered["maximum"]:
        raise ValueError(
            f"{registered['display_name']}应在 "
            f"{registered['minimum']:g}～{registered['maximum']:g}"
            f"{registered['unit']} 之间。"
        )
    stepped_value = round(
        (numeric_value - registered["minimum"]) / registered["step"]
    ) * registered["step"] + registered["minimum"]
    device = database.get_device(device_id)
    bridge = current_app.extensions.get("mqtt_bridge")
    if device and not device["is_virtual"]:
        sent = bridge and bridge.publish_device_capability(
            device_id,
            capability,
            stepped_value,
        )
        if not sent:
            raise DeviceCommandError(
                f"{device['name']}{registered['display_name']}命令发送失败，"
                "本地数值未改变。"
            )
    return database.update_device_capability(
        device_id,
        capability,
        stepped_value,
    )
