import json
from statistics import median

from . import database


LEARNABLE_CAPABILITIES = {
    ("fan", "speed"): {
        "preference": "fan_speed",
        "label": "常用风速",
        "unit": "%",
        "tolerance": 10,
    },
    ("light", "brightness"): {
        "preference": "light_brightness",
        "label": "常用亮度",
        "unit": "%",
        "tolerance": 10,
    },
}
REQUIRED_OBSERVATIONS = 3


def _samples_key(preference):
    return f"learning.samples.{preference}"


def reset_learning(preference):
    database.set_settings({_samples_key(preference): "[]"})


def observe_capability(device_id, capability, value):
    device = database.get_device(device_id)
    if not device:
        return None
    definition = LEARNABLE_CAPABILITIES.get((device["type"], capability))
    if not definition:
        return None

    preference = definition["preference"]
    preferences = database.get_user_preferences()
    stored_value = preferences.get(preference)
    if (
        stored_value is not None
        and database.get_user_preference_source(preference) != "automatic"
    ):
        return None
    previous_value = (
        float(stored_value) if stored_value is not None else None
    )

    settings = database.get_settings()
    try:
        samples = json.loads(settings.get(_samples_key(preference), "[]"))
        if not isinstance(samples, list):
            samples = []
        samples = [float(sample) for sample in samples[-2:]]
    except (TypeError, ValueError, json.JSONDecodeError):
        samples = []
    samples.append(float(value))

    learned_value = None
    confirmed = False
    if len(samples) >= REQUIRED_OBSERVATIONS:
        recent = samples[-REQUIRED_OBSERVATIONS:]
        if max(recent) - min(recent) <= definition["tolerance"]:
            learned_value = float(median(recent))
            confirmed = (
                previous_value is not None
                and abs(learned_value - previous_value) < 0.001
            )
        else:
            samples = [float(value)]

    database.set_settings(
        {_samples_key(preference): json.dumps(samples, ensure_ascii=False)}
    )
    result = {
        "preference": preference,
        "label": definition["label"],
        "progress": len(samples),
        "required": REQUIRED_OBSERVATIONS,
        "learned": learned_value is not None and not confirmed,
        "confirmed": confirmed,
    }
    if learned_value is None:
        return result

    if confirmed:
        reset_learning(preference)
        result["message"] = (
            f"你的{definition['label']}仍是"
            f"{previous_value:g}{definition['unit']}，无需更新。"
        )
        return result

    saved = database.set_automatic_user_preference(
        preference,
        f"{learned_value:g}",
        expected_value=stored_value,
    )
    reset_learning(preference)
    if not saved:
        result.update(
            {
                "learned": False,
                "cancelled": True,
                "message": "你刚刚明确更新了这项偏好，本次自动学习已取消。",
            }
        )
        return result
    memory = {
        "name": preference,
        "label": definition["label"],
        "value": learned_value,
        "unit": definition["unit"],
        "display_value": f"{learned_value:g}{definition['unit']}",
        "source": "automatic",
        "source_label": "自动学习",
    }
    if previous_value is None:
        message = (
            f"我发现你经常把{device['name']}"
            f"{definition['label'].removeprefix('常用')}"
            f"调到{memory['display_value']}，已自动记住。"
        )
    else:
        message = (
            f"我发现你的{definition['label']}有变化，已从"
            f"{previous_value:g}{definition['unit']}更新为"
            f"{memory['display_value']}。"
        )
    result.update(
        {
            "memory": memory,
            "message": message,
            "updated": previous_value is not None,
        }
    )
    database.log_event(
        "learning",
        message,
        {
            "device_id": device_id,
            "capability": capability,
            "samples": recent,
            "memory": memory,
        },
    )
    return result


def learn_from_result(result):
    if result.get("intent") != "set_device_level":
        return []
    learnings = []
    for action in result.get("actions", []):
        if not all(key in action for key in ("device_id", "capability", "value")):
            continue
        learning = observe_capability(
            action["device_id"],
            action["capability"],
            action["value"],
        )
        if learning:
            learnings.append(learning)
    return learnings
