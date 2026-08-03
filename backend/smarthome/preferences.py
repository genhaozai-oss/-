import re

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
PREFERENCE_SOURCE_LABELS = {
    "explicit": "用户设定",
    "automatic": "自动学习",
}
MESSAGE_PREFERENCES = (
    ("常用风速", "fan_speed"),
    ("风速", "fan_speed"),
    ("常用亮度", "light_brightness"),
    ("亮度", "light_brightness"),
    ("舒适温度", "temperature"),
    ("温度", "temperature"),
    ("舒适湿度", "humidity"),
    ("湿度", "humidity"),
)
PREFERENCE_CONTEXT_WORDS = ("自动化", "规则", "模式", "场景")
PREFERENCE_NEGATION_PATTERN = re.compile(
    r"不|没|未|无|非|别|莫|勿|禁止|取消|避免|拒绝"
)
COMPOUND_ACTION_PATTERN = re.compile(
    r"然后|接着|随后|之后|以及|顺便|同时|而且|另外|还要|也要|"
    r"并(?:且|把|再|打开|开启|关闭|关掉|启动|停止|调|设置)|"
    r"再(?:打开|开启|关闭|关掉|启动|停止|调|设置)|"
    r"打开|开启|关闭|关掉|启动|停止"
)
QUESTION_PATTERN = re.compile(
    r"[?？]|(?:吗|么|嘛|呢)\s*[。！？!?]*$|"
    r"还记得|是否|是不是|可不可以|可以吗|能不能|有没有|"
    r"好不好|行不行|对不对|怎么样|如何"
)


def list_preferences():
    stored = database.get_user_preferences()
    memories = []
    for name, definition in PREFERENCE_DEFINITIONS.items():
        if name not in stored:
            continue
        source = database.get_user_preference_source(name)
        value = float(stored[name])
        memories.append(
            {
                "name": name,
                "label": definition["label"],
                "value": value,
                "unit": definition["unit"],
                "display_value": f"{value:g}{definition['unit']}",
                "source": source,
                "source_label": PREFERENCE_SOURCE_LABELS[source],
            }
        )
    return memories


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
    database.set_user_preference(
        name,
        f"{numeric_value:g}",
        source="explicit",
    )
    from .learning import reset_learning

    reset_learning(name)
    return {
        "name": name,
        "label": definition["label"],
        "value": numeric_value,
        "unit": definition["unit"],
        "display_value": f"{numeric_value:g}{definition['unit']}",
        "source": "explicit",
        "source_label": PREFERENCE_SOURCE_LABELS["explicit"],
    }


def forget_preference(name):
    definition = PREFERENCE_DEFINITIONS.get(name)
    if not definition:
        raise ValueError("这种偏好暂时不支持。")
    from .learning import reset_learning

    reset_learning(name)
    if not database.delete_user_preference(name):
        raise ValueError(f"还没有记住你的{definition['label']}。")
    return definition["label"]


def handle_preference_message(message):
    raw_message = str(message or "").strip()
    normalized = raw_message.strip(" ，。！？,.!?")
    if QUESTION_PATTERN.search(raw_message):
        return None
    if any(word in normalized for word in PREFERENCE_CONTEXT_WORDS):
        return None
    if PREFERENCE_NEGATION_PATTERN.search(normalized):
        return None
    if COMPOUND_ACTION_PATTERN.search(normalized):
        return None

    mentioned_preferences = {
        name for label, name in MESSAGE_PREFERENCES if label in normalized
    }
    if len(mentioned_preferences) != 1:
        return None
    matched = next(
        (
            (label, name)
            for label, name in MESSAGE_PREFERENCES
            if label in normalized
        ),
        None,
    )
    if not matched:
        return None
    matched_label, preference = matched

    if any(word in normalized for word in ("忘记", "忘掉", "删除", "清除")):
        has_preference_qualifier = (
            matched_label.startswith(("常用", "舒适"))
            or any(
                word in normalized
                for word in ("偏好", "我的设置", "我的习惯", "我习惯")
            )
        )
        if not has_preference_qualifier:
            return None
        try:
            label = forget_preference(preference)
        except ValueError as exc:
            return {
                "intent": "forget_preference",
                "reply": str(exc),
                "actions": [],
            }
        message = f"已在本地忘记你的{label}。"
        database.log_event(
            "memory",
            message,
            {"preference": preference, "deleted": True},
        )
        return {
            "intent": "forget_preference",
            "reply": message,
            "actions": [],
            "memories": list_preferences(),
        }

    if not any(
        word in normalized
        for word in ("记住", "记得", "常用", "舒适", "喜欢", "习惯")
    ):
        return None
    if any(word in normalized for word in ("超过", "高于", "低于", "少于")):
        return None
    label_end = normalized.find(matched_label) + len(matched_label)
    suffix = normalized[label_end:]
    number = re.match(
        r"\s*(?:(?:是|为|设为|设置为|调到|大约|约)\s*)?"
        r"(-?\d+(?:\.\d+)?)\s*(?:%|％|℃|度)?\s*"
        r"(?:左右|上下)?\s*$",
        suffix,
    )
    if not number and not suffix.strip():
        number = re.search(
            r"(-?\d+(?:\.\d+)?)\s*(?:%|％|℃|度)?\s*(?:的)?\s*$",
            normalized[: normalized.find(matched_label)],
        )
    if not number:
        return None
    try:
        memory = remember_preference(preference, number.group(1))
    except ValueError as exc:
        return {
            "intent": "remember_preference",
            "reply": f"没有保存这项偏好：{exc}",
            "actions": [],
        }

    message = (
        f"已在本地记住你的{memory['label']}是"
        f"{memory['display_value']}。"
    )
    database.log_event("memory", message, {"memory": memory})
    return {
        "intent": "remember_preference",
        "reply": message,
        "actions": [],
        "memory": memory,
        "memories": list_preferences(),
    }
