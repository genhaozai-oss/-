from . import database


PREFERENCE_DEFINITIONS = {
    "fan_speed": {
        "label": "常用风速",
        "minimum": 0,
        "maximum": 100,
        "unit": "%",
    },
    "light_brightness": {
        "label": "常用亮度",
        "minimum": 0,
        "maximum": 100,
        "unit": "%",
    },
    "temperature": {
        "label": "舒适温度",
        "minimum": 16,
        "maximum": 30,
        "unit": "℃",
    },
    "humidity": {
        "label": "舒适湿度",
        "minimum": 30,
        "maximum": 80,
        "unit": "%",
    },
}


def list_preferences():
    stored = database.get_user_preferences()
    return [
        {
            "name": name,
            "label": definition["label"],
            "value": float(stored[name]),
            "unit": definition["unit"],
            "display_value": f"{float(stored[name]):g}{definition['unit']}",
        }
        for name, definition in PREFERENCE_DEFINITIONS.items()
        if name in stored
    ]


def remember_preference(name, value):
    definition = PREFERENCE_DEFINITIONS.get(name)
    if not definition:
        raise ValueError("这种偏好暂时不支持。")
    try:
        numeric_value = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("偏好值必须是数字。") from exc
    if not definition["minimum"] <= numeric_value <= definition["maximum"]:
        raise ValueError(
            f"偏好值应在 {definition['minimum']}～"
            f"{definition['maximum']}{definition['unit']} 之间。"
        )
    database.set_user_preference(name, f"{numeric_value:g}")
    from .learning import reset_learning

    reset_learning(name)
    return {
        "name": name,
        "label": definition["label"],
        "value": numeric_value,
        "unit": definition["unit"],
        "display_value": f"{numeric_value:g}{definition['unit']}",
    }


def forget_preference(name):
    definition = PREFERENCE_DEFINITIONS.get(name)
    if not definition:
        raise ValueError("这种偏好暂时不支持。")
    if not database.delete_user_preference(name):
        raise ValueError(f"还没有记住你的{definition['label']}。")
    from .learning import reset_learning

    reset_learning(name)
    return definition["label"]
