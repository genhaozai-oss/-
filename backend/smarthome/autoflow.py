from datetime import datetime

from . import database
from .automations import describe_rule, plan_rules
from .devices import (
    DeviceCommandError,
    set_device_capability,
    set_device_state,
)


SETTING_KEY = "auto_flow_enabled"
MAX_SENSOR_AGE_SECONDS = 10 * 60
MANUAL_OVERRIDE_MINUTES = 30
WATER_DEVICE_TYPES = {"humidifier", "dehumidifier"}


def is_enabled():
    value = database.get_settings().get(SETTING_KEY)
    return value != "0"


def _environment_age_seconds(environment):
    try:
        updated_at = datetime.fromisoformat(environment["updated_at"])
    except (KeyError, TypeError, ValueError):
        return None
    return max(
        0,
        (datetime.now().astimezone() - updated_at).total_seconds(),
    )


def _preferred_number(preferences, name, default):
    try:
        return float(preferences.get(name, default))
    except (TypeError, ValueError):
        return float(default)


def comfort_thresholds():
    preferences = database.get_user_preferences()
    target_temperature = min(
        28,
        max(20, _preferred_number(preferences, "temperature", 26)),
    )
    target_humidity = min(
        60,
        max(45, _preferred_number(preferences, "humidity", 55)),
    )
    return {
        "target_temperature": target_temperature,
        "target_humidity": target_humidity,
        "fan_on": target_temperature + 2,
        "fan_off": target_temperature - 1,
        "humidifier_on": target_humidity - 15,
        "humidity_safe_low": target_humidity - 10,
        "humidity_safe_high": target_humidity + 10,
        "dehumidifier_on": min(75, target_humidity + 15),
    }


def _decision_details(environment, thresholds):
    temperature = environment["temperature"]
    humidity = environment["humidity"]
    decisions = []

    if temperature >= thresholds["fan_on"]:
        decisions.append("温度偏高，需要开启风扇")
    elif temperature <= thresholds["fan_off"]:
        decisions.append("温度偏低，需要关闭风扇")
    else:
        decisions.append("温度处于保持区间")

    if humidity < thresholds["humidifier_on"]:
        decisions.append("湿度偏低，需要加湿")
    elif humidity > thresholds["dehumidifier_on"]:
        decisions.append("湿度偏高，需要抽湿")
    elif (
        thresholds["humidity_safe_low"]
        <= humidity
        <= thresholds["humidity_safe_high"]
    ):
        decisions.append("湿度舒适，关闭不必要的加湿与抽湿")
    else:
        decisions.append("湿度处于回差保持区间，避免设备频繁启停")

    return decisions


def _default_plans(environment, thresholds):
    targets = {}
    if environment["temperature"] >= thresholds["fan_on"]:
        targets["fan"] = "on"
    elif environment["temperature"] <= thresholds["fan_off"]:
        targets["fan"] = "off"

    humidity = environment["humidity"]
    if humidity < thresholds["humidifier_on"]:
        targets["dehumidifier"] = "off"
        targets["humidifier"] = "on"
    elif humidity > thresholds["dehumidifier_on"]:
        targets["humidifier"] = "off"
        targets["dehumidifier"] = "on"
    elif (
        thresholds["humidity_safe_low"]
        <= humidity
        <= thresholds["humidity_safe_high"]
    ):
        targets["humidifier"] = "off"
        targets["dehumidifier"] = "off"

    managed = database.automation_managed_device_ids()
    plans = []
    for device in database.list_devices():
        target = targets.get(device["type"])
        if (
            target is None
            or device["id"] in managed
            or device["state"] == target
        ):
            continue
        plans.append(
            {
                "device_id": device["id"],
                "device_name": device["name"],
                "device_type": device["type"],
                "is_virtual": bool(device["is_virtual"]),
                "online": bool(device["online"]),
                "source": "comfort",
                "operation": "state",
                "state": target,
            }
        )
    return plans


def _safety_reason(plan, override_ids):
    if plan["device_id"] in override_ids:
        return "用户手动接管中"
    if not plan["is_virtual"] and not plan["online"]:
        return "实体设备离线"
    if (
        not plan["is_virtual"]
        and plan["device_type"] in WATER_DEVICE_TYPES
        and (
            plan["operation"] == "capability"
            or plan.get("state") == "on"
        )
    ):
        return "实体涉水设备需人工确认"
    return None


def _blocked_item(plan, reason):
    return {
        "device_id": plan["device_id"],
        "device_name": plan["device_name"],
        "operation": plan["operation"],
        "reason": reason,
    }


def _execute_plans(plans):
    actions = []
    blocked = []
    failed_device_ids = set()
    actions_by_rule = {}
    for plan in plans:
        if plan["device_id"] in failed_device_ids:
            blocked.append(
                _blocked_item(plan, "前一条设备命令发送失败")
            )
            continue
        try:
            if plan["operation"] == "state":
                updated = set_device_state(
                    plan["device_id"],
                    plan["state"],
                )
                action = {
                    "device_id": plan["device_id"],
                    "device_name": updated["name"],
                    "state": plan["state"],
                    "is_virtual": bool(updated["is_virtual"]),
                }
            else:
                updated = set_device_capability(
                    plan["device_id"],
                    plan["capability"],
                    plan["value"],
                )
                action = {
                    "device_id": plan["device_id"],
                    "device_name": plan["device_name"],
                    "capability": plan["capability"],
                    "value": updated["value"],
                    "unit": updated["unit"],
                    "is_virtual": bool(plan["is_virtual"]),
                }
        except DeviceCommandError as exc:
            failed_device_ids.add(plan["device_id"])
            blocked.append(_blocked_item(plan, str(exc)))
            continue

        rule_id = plan.get("automation_rule_id")
        if rule_id:
            action["automation_rule_id"] = rule_id
            actions_by_rule.setdefault(rule_id, []).append(action)
        actions.append(action)

    for rule_id, rule_actions in actions_by_rule.items():
        rule = database.get_automation_rule(rule_id)
        database.update_automation_rule(rule_id, triggered=True)
        database.log_event(
            "automation",
            f"执行规则：{describe_rule(rule)}",
            {"rule_id": rule_id, "actions": rule_actions},
        )
    return actions, blocked


def _action_text(action):
    if "capability" in action:
        return (
            f"{action['device_name']}调到"
            f"{action['value']:g}{action.get('unit', '')}"
        )
    verb = "开启" if action.get("state") == "on" else "关闭"
    return f"{verb}{action['device_name']}"


def _base_flow(enabled, environment, thresholds, active_overrides):
    return {
        "enabled": enabled,
        "environment": environment,
        "thresholds": thresholds,
        "manual_overrides": active_overrides,
        "plans": [],
        "actions": [],
        "blocked": [],
        "steps": [],
        "ran_at": database.now_iso(),
    }


def run_auto_flow(trigger="manual", force=False):
    enabled = is_enabled()
    environment = database.get_environment()
    thresholds = comfort_thresholds()
    active_overrides = database.list_active_manual_overrides()
    flow = _base_flow(enabled, environment, thresholds, active_overrides)
    flow.update({"trigger": trigger, "forced": bool(force)})

    age_seconds = _environment_age_seconds(environment)
    is_fresh = (
        age_seconds is not None
        and age_seconds <= MAX_SENSOR_AGE_SECONDS
    )
    age_text = (
        "时间未知"
        if age_seconds is None
        else f"{max(0, round(age_seconds))} 秒前"
    )
    decisions = _decision_details(environment, thresholds)
    plans = [
        *_default_plans(environment, thresholds),
        *plan_rules(environment),
    ]
    flow["plans"] = plans
    blocked = []
    flow["steps"].append(
        {
            "stage": "sense",
            "label": "感知",
            "status": "done" if is_fresh else "warning",
            "detail": (
                f"室内 {environment['temperature']:.1f}℃、"
                f"{environment['humidity']:.0f}%，数据更新于{age_text}"
            ),
        }
    )
    flow["steps"].append(
        {
            "stage": "decide",
            "label": "判断",
            "status": "done",
            "detail": (
                "；".join(decisions)
                + f"；生成 {len(plans)} 个设备调整计划"
            ),
        }
    )

    if not enabled and not force:
        flow["status"] = "paused"
        flow["summary"] = "AI 自动托管已暂停，本次只记录环境，不控制设备。"
        safety_detail = "总开关处于暂停状态"
    elif not is_fresh:
        flow["status"] = "stale"
        flow["summary"] = "环境数据已超过 10 分钟，为避免误操作，本次没有控制设备。"
        safety_detail = "传感器数据过期，安全拦截已生效"
    else:
        override_ids = {
            item["device_id"] for item in active_overrides
        }
        allowed_plans = []
        blocked = []
        for plan in plans:
            reason = _safety_reason(plan, override_ids)
            if reason:
                blocked.append(_blocked_item(plan, reason))
            else:
                allowed_plans.append(plan)

        actions, execution_blocked = _execute_plans(allowed_plans)
        blocked.extend(execution_blocked)
        flow["blocked"] = blocked
        flow["actions"] = actions
        safety_messages = [
            f"{item['device_name']}：{item['reason']}"
            for item in blocked
        ]
        safety_detail = (
            "；".join(safety_messages)
            if safety_messages
            else "数据新鲜，设备在线与安全条件检查通过"
        )
        if force and not enabled:
            safety_detail += "；本次为用户主动巡检，不改变暂停设置"

        if actions and blocked:
            flow["status"] = "partial"
            flow["summary"] = (
                "AI 自动流已部分完成："
                + "、".join(_action_text(action) for action in actions)
                + "；另有设备被安全拦截。"
            )
        elif actions:
            flow["status"] = "executed"
            flow["summary"] = (
                "AI 自动流已完成："
                + "、".join(_action_text(action) for action in actions)
                + "。"
            )
        elif blocked:
            flow["status"] = "blocked"
            flow["summary"] = (
                "AI 已发现调节需求，但安全策略暂不执行："
                + "；".join(
                    f"{item['device_name']}{item['reason']}"
                    for item in blocked
                )
                + "。"
            )
        else:
            flow["status"] = "no_change"
            flow["summary"] = (
                "AI 已完成巡检，设备状态符合当前环境、偏好与自定义规则，"
                "无需重复操作。"
            )

    flow["steps"].append(
        {
            "stage": "safety",
            "label": "安全",
            "status": "warning" if (
                blocked or flow["status"] in {"paused", "stale"}
            ) else "done",
            "detail": safety_detail,
        }
    )
    flow["steps"].append(
        {
            "stage": "execute",
            "label": "执行",
            "status": "done" if flow["actions"] else "idle",
            "detail": (
                "、".join(_action_text(action) for action in flow["actions"])
                if flow["actions"]
                else "未产生设备动作"
            ),
        }
    )
    database.log_event("auto_flow", flow["summary"], flow)
    return flow


def _idle_status(enabled):
    environment = database.get_environment()
    thresholds = comfort_thresholds()
    active_overrides = database.list_active_manual_overrides()
    status = _base_flow(enabled, environment, thresholds, active_overrides)
    status.update(
        {
            "trigger": "waiting",
            "forced": False,
            "status": "ready" if enabled else "paused",
            "summary": (
                "AI 自动托管已开启，等待下一次环境数据。"
                if enabled
                else "AI 自动托管已暂停，只记录环境，不控制设备。"
            ),
            "steps": [
                {
                    "stage": "sense",
                    "label": "感知",
                    "status": "idle",
                    "detail": "等待温湿度更新",
                },
                {
                    "stage": "decide",
                    "label": "判断",
                    "status": "idle",
                    "detail": "将结合舒适偏好与回差阈值",
                },
                {
                    "stage": "safety",
                    "label": "安全",
                    "status": "idle",
                    "detail": "将检查数据时效、设备状态与手动接管",
                },
                {
                    "stage": "execute",
                    "label": "执行",
                    "status": "idle",
                    "detail": "等待安全决策",
                },
            ],
        }
    )
    return status


def auto_flow_status():
    enabled = is_enabled()
    event = database.latest_event("auto_flow")
    status = dict(event["payload"]) if event else _idle_status(enabled)
    status["enabled"] = enabled
    status["manual_overrides"] = database.list_active_manual_overrides()
    return status


def set_auto_flow_enabled(enabled):
    database.set_settings({SETTING_KEY: "1" if enabled else "0"})
    status = _idle_status(enabled)
    database.log_event("auto_flow", status["summary"], status)
    return status


def hold_manual_control(result, reason="用户手动控制"):
    held = []
    for action in result.get("actions", []):
        device_id = action.get("device_id")
        is_control = "state" in action or "capability" in action
        if not device_id or not is_control:
            continue
        override = database.set_device_manual_override(
            device_id,
            MANUAL_OVERRIDE_MINUTES,
            reason,
        )
        if override and device_id not in {
            item["device_id"] for item in held
        }:
            held.append(override)
    if held:
        names = [
            database.get_device(item["device_id"])["name"]
            for item in held
        ]
        database.log_event(
            "manual_override",
            f"手动接管 30 分钟：{'、'.join(names)}",
            {"overrides": held},
        )
    return held


def handle_auto_flow_message(message):
    normalized = str(message or "").strip()
    flow_words = ("自动流", "自动托管", "自动管家", "AI自动", "AI 自动")
    mentions_flow = any(word in normalized for word in flow_words)

    if mentions_flow and any(
        word in normalized for word in ("关闭", "暂停", "停止")
    ):
        flow = set_auto_flow_enabled(False)
        return {
            "intent": "auto_flow_control",
            "reply": flow["summary"],
            "actions": [],
            "auto_flow": flow,
        }

    if mentions_flow and any(
        word in normalized for word in ("开启", "打开", "启用", "恢复")
    ):
        flow = set_auto_flow_enabled(True)
        return {
            "intent": "auto_flow_control",
            "reply": flow["summary"],
            "actions": [],
            "auto_flow": flow,
        }

    run_hints = (
        "立即巡检",
        "马上巡检",
        "运行一次自动流",
        "执行一次自动流",
        "检查并调整",
    )
    if any(hint in normalized for hint in run_hints):
        flow = run_auto_flow(trigger="conversation", force=True)
        return {
            "intent": "run_auto_flow",
            "reply": flow["summary"],
            "actions": flow["actions"],
            "auto_flow": flow,
        }

    if mentions_flow and any(
        word in normalized for word in ("状态", "怎么样", "情况")
    ):
        flow = auto_flow_status()
        return {
            "intent": "auto_flow_status",
            "reply": flow["summary"],
            "actions": [],
            "auto_flow": flow,
        }
    return None
